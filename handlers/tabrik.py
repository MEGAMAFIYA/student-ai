"""
🎁 /tabrik — foydalanuvchi tabrik matnini yozadi, bot chiroyli xabar +
"🎁 Tabrikni qabul qilish" tugmasini yuboradi. Tugma bosilganda: 5→1
countdown, so'ng faqat ruxsat etilgan belgilardan (- _ ✓ « » ~ +) iborat
"aylanayotgan doira" ASCII animatsiyasi, oxirida yakuniy tabrik kartasi.

MUHIM ARXITEKTURA:
- Sof mantiq (matn parsing, freym generatsiyasi, xotiradagi ombor)
  tabrik_logic.py'da — bu yerda FAQAT Telegram bilan bog'liq kod.
- Har bir tugma bosilishi ALOHIDA, YANGI xabar sifatida javob oladi
  (asl /tabrik xabari — button joylashgan xabar — HECH QACHON edit
  qilinmaydi). Shu tufayli bitta guruhda bir nechta foydalanuvchi bir xil
  tugmani bossa ham, ularning animatsiyalari (turli xabarlarda ketayotgani
  uchun) BIR-BIRIGA UMUMAN TA'SIR QILMAYDI.
- Bitta foydalanuvchi bir vaqtning o'zida ikkinchi marta bossa (masalan
  tez-tez bosaverib) — ikkinchi bosishga oddiy "⏳ allaqachon ketmoqda"
  javobi beriladi (_ACTIVE dagi (chat_id, user_id) kaliti orqali),
  shunda bitta foydalanuvchi uchun ikkita parallel animatsiya xabari
  ustma-ust tushib ketmaydi.
- Telegram flood-control (RetryAfter) va "message is not modified"
  xatolari xavfsiz tarzda ushlanadi — animatsiya Telegram limitlariga
  urilib bot butunlay yiqilib qolmaydi.
"""

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

import tabrik_logic

logger = logging.getLogger(__name__)

# (chat_id, user_id) -> True — hozir shu foydalanuvchi uchun animatsiya
# ketayotganini bildiradi (bir xil foydalanuvchi ikki marta bossa oldini
# olish uchun). Jarayon tugagach albatta o'chiriladi (finally blokida).
_ACTIVE: set[tuple[int, int]] = set()

COUNTDOWN_DELAY = 1.0     # soniya — 5→1 orasidagi kutish
FRAME_DELAY = 0.45        # soniya — doira freym'lari orasidagi kutish


async def tabrik_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, override_text: str | None = None):
    """`override_text` — handlers/mention_dispatch.py orqali (masalan
    "@Bot /tabrik Salom..." xabaridan mention qismi olib tashlangandan
    keyin) chaqirilganda beriladi. Berilmasa (oddiy "/tabrik ..." buyrug'i
    CommandHandler orqali kelganda) xabar matnining o'zi ishlatiladi."""
    if not update.message:
        return
    raw_text = override_text if override_text is not None else (update.message.text or "")
    text = tabrik_logic.parse_tabrik_text(raw_text)
    if not text:
        await update.message.reply_text(
            "🎁 Tabrik matnini ham yozing, masalan:\n\n"
            "`/tabrik Salom mening qadrli insonim, sizni bugungi kun bilan "
            "tabriklayman. Hurmat bilan Davron`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    short_id = tabrik_logic.store_greeting(text)
    escaped = escape_markdown(text, version=1)
    await update.message.reply_text(
        f"🎁 *Sizga tabrik bor!*\n\n{escaped}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎁 Tabrikni qabul qilish", callback_data=f"tabrik:claim:{short_id}")
        ]]),
    )
    logger.info(f"🎁 /tabrik yuborildi: chat_id={update.effective_chat.id}, short_id={short_id}.")


async def _safe_edit(msg, text: str) -> None:
    """Xabarni edit qilishda Telegram flood-control (RetryAfter) va
    'message is not modified' xatolarini xavfsiz tarzda o'tkazib yuboradi
    — animatsiya shu sabablarga ko'ra to'xtab qolmasligi kerak."""
    try:
        await msg.edit_text(text)
    except RetryAfter as e:
        logger.warning(f"🎁 Telegram flood-control: {e.retry_after}s kutilmoqda.")
        await asyncio.sleep(e.retry_after + 0.1)
        try:
            await msg.edit_text(text)
        except Exception:
            pass
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"🎁 Animatsiya freymini edit qilib bo'lmadi: {e}")
    except Exception as e:
        logger.warning(f"🎁 Animatsiya freymini edit qilishda kutilmagan xato: {type(e).__name__}: {e}")


async def tabrik_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    chat_id = update.effective_chat.id
    key = (chat_id, user.id)

    short_id = query.data.split(":", 2)[2]
    greeting = tabrik_logic.get_greeting(short_id)
    if not greeting:
        await query.answer("⚠️ Bu tabrikning muddati o'tgan.", show_alert=True)
        return

    if key in _ACTIVE:
        await query.answer("⏳ Animatsiya allaqachon ketmoqda...", show_alert=False)
        return

    _ACTIVE.add(key)
    await query.answer("🎁 Ochilmoqda...")

    try:
        # Asl /tabrik xabarini EMAS — shu bosishga tegishli YANGI xabarni
        # animatsiya qilamiz, shunda boshqa foydalanuvchilarning holatiga
        # (yoki ular hali bosmagan asl tugmaga) hech qanday ta'sir qilmaydi.
        anim_msg = await context.bot.send_message(
            chat_id,
            tabrik_logic.build_countdown_frame(5),
            reply_to_message_id=query.message.message_id if query.message else None,
        )

        for n in (4, 3, 2, 1):
            await asyncio.sleep(COUNTDOWN_DELAY)
            await _safe_edit(anim_msg, tabrik_logic.build_countdown_frame(n))

        await asyncio.sleep(COUNTDOWN_DELAY)
        for step in range(tabrik_logic.TOTAL_ROTATION_FRAMES):
            await _safe_edit(anim_msg, tabrik_logic.build_circle_frame(step))
            await asyncio.sleep(FRAME_DELAY)

        await _safe_edit(anim_msg, tabrik_logic.build_final_card(greeting))
        logger.info(f"🎁 Animatsiya muvaffaqiyatli yakunlandi: chat_id={chat_id}, user_id={user.id}.")
    except Exception as e:
        logger.error(f"🎁 /tabrik animatsiyasida xato (chat_id={chat_id}, user_id={user.id}): {type(e).__name__}: {e}", exc_info=True)
        try:
            await context.bot.send_message(chat_id, "❌ Animatsiyada xatolik yuz berdi, lekin tabrigingiz shu yerda: \n\n" + greeting)
        except Exception:
            pass
    finally:
        _ACTIVE.discard(key)
