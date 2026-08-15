"""
🖼 Suratlarni PDF qilish — foydalanuvchi bir nechta rasm yuboradi,
tasdiqlagach barchasi ketma-ket PDF ga joylanadi.
"""

import logging
from io import BytesIO

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from pdf_tools import images_to_pdf
from handlers.menu import main_menu_keyboard

logger = logging.getLogger(__name__)

IMG_COLLECTING = 0


def _confirm_keyboard(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Tasdiqlash ({count} ta rasm)", callback_data="imgpdf:confirm")]])


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["flow"] = "images_pdf"
    context.user_data["img_list"] = []
    await query.edit_message_text(
        "🖼 *Suratlarni PDF qilish*\n\n"
        "Suratlarni birma-bir yuboring. Barchasini yuborib bo'lgach, "
        "'✅ Tasdiqlash' tugmasini bosing.",
        parse_mode="Markdown",
    )
    return IMG_COLLECTING


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    bio = BytesIO()
    await file.download_to_memory(out=bio)

    images = context.user_data.setdefault("img_list", [])
    images.append(bio.getvalue())

    await update.message.reply_text(
        f"✅ {len(images)}-rasm qabul qilindi.\nYana surat yuboring yoki tasdiqlang.",
        reply_markup=_confirm_keyboard(len(images)),
    )
    return IMG_COLLECTING


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    images = context.user_data.get("img_list", [])
    if not images:
        await query.edit_message_text("❗️ Hali birorta ham rasm yuborilmagan. Rasm yuboring.")
        return IMG_COLLECTING

    await query.edit_message_text(f"⏳ {len(images)} ta rasmdan PDF tayyorlanmoqda...")

    try:
        pdf_buf = images_to_pdf(images)
        await context.bot.send_document(
            update.effective_chat.id,
            document=InputFile(pdf_buf, filename="suratlar.pdf"),
            caption=f"📄 {len(images)} ta rasmdan tayyorlangan PDF.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as e:
        logger.error(f"Images->PDF xato: {e}")
        await context.bot.send_message(update.effective_chat.id, "❌ PDF yaratishda xatolik yuz berdi.", reply_markup=main_menu_keyboard())

    context.user_data.clear()
    return ConversationHandler.END
