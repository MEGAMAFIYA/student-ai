"""
🌐 Tarjima qilish — matn yoki PDF qabul qilinadi, til tanlanadi,
tarjima asl format (matn/PDF) da qaytariladi.
"""

import asyncio
import logging
from io import BytesIO

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from config import TRANSLATE_AI
from ai_clients import ask_ai
from pdf_tools import make_pdf, extract_pdf_text
from handlers.menu import main_menu_keyboard

logger = logging.getLogger(__name__)

TR_WAIT_CONTENT, TR_WAIT_LANG, TR_WAIT_CUSTOM_LANG = range(3)

LANGUAGES = {
    "ru": "Ruscha",
    "cyr": "Kirilcha (o'zbek, krill)",
    "lat": "Lotincha (o'zbek)",
    "en": "Inglizcha",
    "other": "✍️ Boshqa til",
}


def _lang_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=f"trlang:{code}")] for code, label in LANGUAGES.items()]
    return InlineKeyboardMarkup(rows)


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"🌐 'Tarjima qilish' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        context.user_data["flow"] = "translate"
        await query.edit_message_text(
            "🌐 *Tarjima qilish*\n\nTarjima qilinishi kerak bo'lgan matn yoki PDF faylni yuboring.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"🌐 Tarjima menyusini ochishda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise
    return TR_WAIT_CONTENT


async def receive_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if msg.document and msg.document.file_name and msg.document.file_name.lower().endswith(".pdf"):
        file = await context.bot.get_file(msg.document.file_id)
        bio = BytesIO()
        await file.download_to_memory(out=bio)
        bio.seek(0)
        text = await asyncio.to_thread(extract_pdf_text, bio.read())
        if not text:
            await msg.reply_text("❌ PDF dan matn o'qib bo'lmadi (skanerlangan bo'lishi mumkin).")
            return TR_WAIT_CONTENT
        context.user_data["tr_source_type"] = "pdf"
        context.user_data["tr_source_text"] = text
        context.user_data["tr_filename"] = msg.document.file_name.rsplit(".", 1)[0]

    elif msg.text:
        context.user_data["tr_source_type"] = "text"
        context.user_data["tr_source_text"] = msg.text.strip()

    else:
        await msg.reply_text("❗️ Faqat matn yoki PDF fayl yuboring.")
        return TR_WAIT_CONTENT

    await msg.reply_text("🌍 Qaysi tilga tarjima qilinsin?", reply_markup=_lang_keyboard())
    return TR_WAIT_LANG


async def lang_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
        code = query.data.split(":", 1)[1]

        if code == "other":
            await query.edit_message_text("✍️ Tilni yozib yuboring (masalan: Turkcha, Fransuzcha):")
            return TR_WAIT_CUSTOM_LANG

        await query.edit_message_text(f"⏳ {LANGUAGES[code]} tiliga tarjima qilinmoqda...")
        await _do_translate(update, context, LANGUAGES[code], edit_query=query)
    except Exception as e:
        logger.error(f"🌐 Til tanlashda xato: {type(e).__name__}: {e}", exc_info=True)
        raise
    return ConversationHandler.END


async def custom_lang_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang_name = update.message.text.strip()
    status = await update.message.reply_text(f"⏳ {lang_name} tiliga tarjima qilinmoqda...")
    await _do_translate(update, context, lang_name, status_msg=status)
    return ConversationHandler.END


async def _do_translate(update, context, target_lang: str, edit_query=None, status_msg=None):
    source_text = context.user_data.get("tr_source_text", "")
    source_type = context.user_data.get("tr_source_type", "text")
    chat_id = update.effective_chat.id

    system = (
        "Siz professional tarjimonsiz. Berilgan matnni so'ralgan tilga aniq, ravon va "
        "tabiiy tarzda tarjima qiling. Faqat tarjimani qaytaring, izoh yozmang."
    )
    prompt = f"Quyidagi matnni {target_lang} tiliga tarjima qil:\n\n{source_text}"

    logger.info(
        f"🌐 Tarjima so'rovi: chat_id={chat_id}, manba={source_type}, "
        f"til={target_lang}, uzunlik={len(source_text)} belgi."
    )
    translated = await ask_ai(TRANSLATE_AI, prompt, system)

    if not translated:
        logger.error(
            f"🌐 Tarjima ISHLAMADI: chat_id={chat_id}, provider={TRANSLATE_AI.get('provider')} — "
            "barcha AI provayderlar/kalitlar javob bermadi. Aniq sabab (limit/kalit yaroqsiz/xato) "
            "yuqoridagi loglarda ai_clients moduli tomonidan yozilgan bo'lishi kerak."
        )
        await context.bot.send_message(chat_id, "❌ Tarjima qilib bo'lmadi. Qayta urinib ko'ring.", reply_markup=main_menu_keyboard())
        context.user_data.clear()
        return

    logger.info(f"🌐 Tarjima muvaffaqiyatli yakunlandi: chat_id={chat_id}, natija uzunligi={len(translated)} belgi.")

    if source_type == "pdf":
        filename = context.user_data.get("tr_filename", "tarjima")
        pdf_buf = await asyncio.to_thread(make_pdf, f"{filename} ({target_lang})", translated)
        await context.bot.send_document(
            chat_id,
            document=InputFile(pdf_buf, filename=f"{filename}_{target_lang}.pdf"),
            caption=f"✅ {target_lang} tiliga tarjima qilindi.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        if len(translated) > 3800:
            bio = BytesIO(translated.encode("utf-8"))
            await context.bot.send_document(
                chat_id,
                document=InputFile(bio, filename="tarjima.txt"),
                caption=f"✅ {target_lang} tiliga tarjima qilindi (matn uzun bo'lgani uchun fayl sifatida).",
                reply_markup=main_menu_keyboard(),
            )
        else:
            await context.bot.send_message(
                chat_id,
                f"✅ *{target_lang}*:\n\n{translated}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu_keyboard(),
            )

    if edit_query:
        try:
            await edit_query.delete_message()
        except Exception:
            pass
    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass

    context.user_data.clear()
