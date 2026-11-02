"""
🎨 Universal chatdan AI rasm chizdirish.

Foydalanuvchi "mushuk rasmini chizib ber" kabi yozsa — bot rasm narxini
(admin belgilagan, standart 3000 so'm) aytib, balansdan yechish uchun
TASDIQ so'raydi. Tasdiqlansa: pul band qilinadi → rasm chiziladi → rasm
yuborilsa pul yechiladi (chizilmasa — yechilmaydi).
"""

import io
import logging
import re
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction

import ai_draw

logger = logging.getLogger(__name__)

_NOUN = r"(?:rasm|rasim|surat|tasvir|illyustratsiya|kartina)\w*"
_VERB = r"(?:chiz|chizib|chizing|chizdir|yarat|yaratib|yasa|yasab|generatsiya|generate|draw)\w*"
_IMAGE_RE = re.compile(rf"\b{_NOUN}\b.*\b{_VERB}|\b{_VERB}\b.*\b{_NOUN}", re.IGNORECASE | re.DOTALL)
# "rasm" so'zi bilan boshlanadigan to'g'ridan-to'g'ri buyruq: "rasm quyosh botayotgan dengiz"
_IMAGE_PREFIX_RE = re.compile(r"^\s*(?:rasm|rasim|surat)\s*[:\-]?\s+(.+)$", re.IGNORECASE | re.DOTALL)

MAX_PENDING = 200


def is_image_request(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) > 600:
        return False
    return bool(_IMAGE_RE.search(t) or _IMAGE_PREFIX_RE.match(t))


def clean_description(text: str) -> str:
    t = (text or "").strip()
    m = _IMAGE_PREFIX_RE.match(t)
    return (m.group(1) if m else t).strip()


def _confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    price = ai_draw.image_price()
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Ha, {ai_draw.fmt_sum(price)} yechilsin", callback_data=f"aiimg:yes:{token}"),
        InlineKeyboardButton("❌ Yo'q", callback_data=f"aiimg:no:{token}"),
    ]])


async def ask_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Universal chatda rasm so'ralganda: narxni aytib, tasdiq so'raydi."""
    user = update.effective_user
    desc = clean_description(text)
    if not ai_draw.feature_enabled(ai_draw.IMAGE_FEATURE_ID):
        await update.message.reply_text("🚫 Rasm chizdirish hozircha o'chirilgan.")
        return
    if not ai_draw.drawing_available(user.id):
        await update.message.reply_text("❌ Rasm chizish hozircha sozlanmagan (AI kalit yo'q). Administratorga xabar bering.")
        return
    price = ai_draw.image_price()
    if price <= 0:
        await _draw_and_send(context, update.effective_chat.id, user.id, desc, reply_to=update.message)
        return
    pending = context.bot_data.setdefault("ai_img_pending", {})
    if len(pending) > MAX_PENDING:
        for k in list(pending)[: len(pending) - MAX_PENDING]:
            pending.pop(k, None)
    token = uuid.uuid4().hex[:12]
    pending[token] = {"user_id": user.id, "desc": desc}
    await update.message.reply_text(
        f"🎨 Rasm chiziladi:\n«{desc[:300]}»\n\n💰 Balansingizdan {ai_draw.fmt_sum(price)} yechiladi. Tasdiqlaysizmi?",
        reply_markup=_confirm_keyboard(token),
    )


async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer()
        return
    _, action, token = parts
    pending = context.bot_data.setdefault("ai_img_pending", {})
    entry = pending.get(token)
    if not entry:
        await query.answer("Bu so'rov eskirgan. Qaytadan yozing.", show_alert=True)
        return
    if entry["user_id"] != query.from_user.id:
        await query.answer("Bu so'rov sizniki emas.", show_alert=True)
        return
    pending.pop(token, None)  # bir martalik — qayta bosilsa pul ikki marta yechilmaydi
    if action != "yes":
        await query.answer("Bekor qilindi")
        await query.edit_message_text("❌ Rasm chizish bekor qilindi.")
        return
    await query.answer()
    await query.edit_message_text("⏳ Rasm chizilmoqda... (1 daqiqagacha vaqt olishi mumkin)")
    chat_id = query.message.chat_id
    await _draw_and_send(context, chat_id, query.from_user.id, entry["desc"], status_msg=query.message)


async def _draw_and_send(context, chat_id: int, user_id: int, desc: str, status_msg=None, reply_to=None):
    hold = ai_draw.reserve(user_id, ai_draw.IMAGE_FEATURE_ID, ai_draw.image_price())
    if hold.status == "insufficient":
        text = ai_draw.insufficient_text(hold)
        if status_msg:
            await status_msg.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return
    if hold.status == "disabled":
        await context.bot.send_message(chat_id, "🚫 Rasm chizdirish hozircha o'chirilgan.")
        return
    try:
        await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    except Exception:
        pass
    data = await ai_draw.draw_image(user_id, desc)
    if not data:
        ai_draw.release(hold, "rasm chizilmadi")
        text = "❌ Rasmni chizib bo'lmadi (AI xizmati javob bermadi yoki so'rov rad etildi). Pul yechilmadi."
        if status_msg:
            await status_msg.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return
    try:
        await context.bot.send_photo(chat_id, io.BytesIO(data),
                                     caption=f"🎨 {desc[:200]}" + (f"\n💰 {ai_draw.fmt_sum(hold.amount)} yechildi." if hold.amount else ""))
    except Exception as e:
        logger.error(f"🎨 Rasmni yuborishda xato: {type(e).__name__}: {e}", exc_info=True)
        ai_draw.release(hold, "rasm yuborilmadi")
        await context.bot.send_message(chat_id, "❌ Rasmni yuborib bo'lmadi. Pul yechilmadi.")
        return
    ai_draw.commit(hold)
    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass
