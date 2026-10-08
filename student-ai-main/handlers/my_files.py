"""
🗂 Mening fayllarim — foydalanuvchi oldin bot orqali yaratgan barcha
fayllarni (kurs ishi, referat, taqdimot, tarjima PDF va h.k.) ro'yxat
qilib ko'rsatadi va tugma bosilganda file_id orqali QAYTA YUBORADI
(qaytadan generatsiya qilmasdan — Telegram file_id orqali serverda saqlangan
faylni to'g'ridan-to'g'ri qayta yuboradi, tezkor va bepul).
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

from handlers.menu import main_menu_keyboard
import storage

logger = logging.getLogger(__name__)

_TYPE_ICONS = {
    "course_work": "📘", "essay": "🗒", "pptx": "📊", "quiz_pdf": "📋",
    "summarize": "📑", "grammar": "✅", "citation": "📚", "solve": "🧮",
    "edit_pdf": "📝", "images_pdf": "🖼", "guide": "📖",
}

_MAX_SHOWN = 12


def _list_keyboard(files: list) -> InlineKeyboardMarkup:
    rows = []
    for i, f in enumerate(files[:_MAX_SHOWN]):
        icon = _TYPE_ICONS.get(f["type"], "📄")
        label = f"{icon} {f['title'][:35]}"
        rows.append([InlineKeyboardButton(label, callback_data=f"myfiles:open:{i}")])
    rows.append([InlineKeyboardButton("🏠 Bosh menyu", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    user_id = user.id if user else 0
    logger.info(f"🗂 'Mening fayllarim' tugmasi bosildi: user_id={user_id}.")
    try:
        await query.answer()
        files = storage.get_user_files(user_id)
        context.user_data["myfiles_list"] = files

        if not files:
            await query.edit_message_text(
                "🗂 *Mening fayllarim*\n\nHozircha hech qanday fayl yaratmagansiz. "
                "Kurs ishi, referat, taqdimot va boshqa funksiyalardan foydalansangiz, "
                "fayllar shu yerda ko'rinadi.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu_keyboard(),
            )
            return

        note = f"\n\n<i>(oxirgi {min(len(files), _MAX_SHOWN)} tasi ko'rsatilmoqda)</i>" if len(files) > _MAX_SHOWN else ""
        await query.edit_message_text(
            f"🗂 <b>Mening fayllarim</b>\n\nQaysi faylni qayta olishni xohlaysiz?{note}",
            parse_mode=ParseMode.HTML,
            reply_markup=_list_keyboard(files),
        )
    except Exception as e:
        logger.error(f"🗂 Fayllar ro'yxatini ko'rsatishda xato (user_id={user_id}): {type(e).__name__}: {e}", exc_info=True)
        raise


async def open_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id if update.effective_user else 0
    idx = int(query.data.split(":")[-1])

    files = context.user_data.get("myfiles_list") or storage.get_user_files(user_id)
    if idx >= len(files):
        await query.answer("⚠️ Fayl topilmadi.", show_alert=True)
        return

    f = files[idx]
    logger.info(f"🗂 Fayl qayta so'raldi: user_id={user_id}, turi={f['type']}, sarlavha='{f['title'][:60]}'.")
    try:
        await context.bot.send_document(
            update.effective_chat.id,
            document=f["file_id"],
            caption=f"{_TYPE_ICONS.get(f['type'], '📄')} {f['title']}",
        )
        logger.info(f"🗂 ✅ Fayl muvaffaqiyatli qayta yuborildi: user_id={user_id}.")
    except BadRequest as e:
        logger.error(f"🗂 ❌ Faylni qayta yuborishda xato (file_id eskirgan/yaroqsiz bo'lishi mumkin): user_id={user_id}, xato={e}")
        await query.answer("❌ Bu fayl endi mavjud emas (juda eski bo'lishi mumkin). Uni qaytadan yarating.", show_alert=True)
    except Exception as e:
        logger.error(f"🗂 ❌ Faylni qayta yuborishda kutilmagan xato: user_id={user_id}, {type(e).__name__}: {e}", exc_info=True)
        await query.answer("❌ Xatolik yuz berdi.", show_alert=True)
