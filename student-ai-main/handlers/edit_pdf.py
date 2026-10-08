"""
📝 PDF ni tahrirlash — PDF va kamchilik tavsifi qabul qilinadi,
AI matnni ko'rsatmaga muvofiq tuzatib, qayta PDF qilib qaytaradi.
"""

import asyncio
import logging
from io import BytesIO

from telegram import Update, InputFile
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ChatAction

from config import EDIT_PDF_AI
from ai_clients import ask_ai
from pdf_tools import make_pdf, extract_pdf_text
from handlers.menu import main_menu_keyboard
from handlers import wallet_ui

logger = logging.getLogger(__name__)

EP_WAIT_PDF, EP_WAIT_INSTRUCTION = range(2)


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"📝 'PDF ni tahrirlash' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        context.user_data["flow"] = "edit_pdf"
        await query.edit_message_text(
            "📝 *PDF ni tahrirlash*\n\n"
            "Tahrirlanishi kerak bo'lgan PDF faylni yuboring.\n\n"
            "⚠️ Eslatma: hujjat matn asosida qayta tuziladi, shuning uchun "
            "original grafik dizayn saqlanmaydi, faqat matn mazmuni saqlanadi.",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"📝 PDF tahrirlash menyusini ochishda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise
    return EP_WAIT_PDF


async def receive_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name or not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❗️ Iltimos, PDF fayl yuboring.")
        return EP_WAIT_PDF

    file = await context.bot.get_file(doc.file_id)
    bio = BytesIO()
    await file.download_to_memory(out=bio)
    bio.seek(0)

    text = await asyncio.to_thread(extract_pdf_text, bio.read())
    if not text:
        logger.warning(f"📝 PDF dan matn o'qib bo'lmadi: '{doc.file_name}', chat_id={update.effective_chat.id} (skanerlangan bo'lishi mumkin).")
        await update.message.reply_text("❌ PDF dan matn o'qib bo'lmadi (skanerlangan bo'lishi mumkin).")
        return EP_WAIT_PDF

    context.user_data["ep_text"] = text
    context.user_data["ep_filename"] = doc.file_name.rsplit(".", 1)[0]

    await update.message.reply_text(
        "✅ Hujjat qabul qilindi.\n\n"
        "Endi nimani tuzatish yoki qo'shish kerakligini yozing.\n"
        "Masalan: \"3-rejadagi mavzuni almashtir\" yoki \"oxiriga xulosa qo'sh\"."
    )
    return EP_WAIT_INSTRUCTION


async def receive_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instruction = update.message.text.strip()
    original_text = context.user_data.get("ep_text", "")
    filename = context.user_data.get("ep_filename", "hujjat")
    chat_id = update.effective_chat.id
    logger.info(f"📝 Tahrirlash so'rovi: chat_id={chat_id}, fayl='{filename}', ko'rsatma='{instruction[:100]}'.")

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    status = await update.message.reply_text("⏳ Hujjat tahrirlanmoqda...")

    system = (
        "Siz hujjatlarni tahrirlovchi yordamchisiz. Sizga hujjatning to'liq matni va "
        "foydalanuvchining tahrir bo'yicha ko'rsatmasi beriladi. Faqat ko'rsatmaga "
        "tegishli qismlarni o'zgartiring yoki qo'shing, qolgan matnni imkon qadar "
        "saqlab qoling. Javob sifatida hujjatning to'liq, tahrirlangan yakuniy matnini "
        "qaytaring, boshqa hech qanday izoh yozmang."
    )
    prompt = (
        f"Hujjat matni:\n---\n{original_text}\n---\n\n"
        f"Foydalanuvchi ko'rsatmasi: {instruction}\n\n"
        "Shu ko'rsatma asosida hujjatning to'liq yangilangan matnini yoz."
    )

    edited = await ask_ai(EDIT_PDF_AI, prompt, system)

    if not edited:
        logger.error(f"📝 Tahrirlash ISHLAMADI: chat_id={chat_id}, provider={EDIT_PDF_AI.get('provider')} — AI javob bermadi (sababi yuqoridagi ai_clients loglarida).")
        await status.edit_text("❌ Tahrirlab bo'lmadi. Qayta urinib ko'ring.")
        await wallet_ui.finalize_failure(context, update=update, reason="edit_pdf_ai_failed")
        context.user_data.clear()
        return ConversationHandler.END

    logger.info(f"📝 Tahrirlash muvaffaqiyatli yakunlandi: chat_id={chat_id}, fayl='{filename}'.")
    pdf_buf = await asyncio.to_thread(make_pdf, filename.title(), edited)
    await update.message.reply_document(
        document=InputFile(pdf_buf, filename=f"{filename}_tahrirlangan.pdf"),
        caption="✅ Hujjat tahrirlandi.",
        reply_markup=main_menu_keyboard(),
    )

    try:
        await status.delete()
    except Exception:
        pass

    await wallet_ui.finalize_success(context, update=update)
    context.user_data.clear()
    return ConversationHandler.END
