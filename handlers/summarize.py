"""
📑 Konspekt qisqartirish — foydalanuvchi uzun matn yoki PDF yuboradi, AI
uni asosiy fikrlarni saqlab qolgan, ixcham konspekt (bullet nuqtali)
ko'rinishga qisqartiradi.
"""

import asyncio
import logging

from telegram import Update, InputFile
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode, ChatAction

from config import SUMMARY_AI, MAX_TELEGRAM_TEXT
from ai_clients import ask_ai
from pdf_tools import make_pdf, extract_pdf_text
from handlers.menu import main_menu_keyboard
from handlers import wallet_ui
import storage

logger = logging.getLogger(__name__)

SM_WAIT = 0

_SYSTEM = (
    "Siz konspekt tuzuvchi mutaxassissiz. Berilgan matnni ASOSIY fikrlarni "
    "yo'qotmagan holda, ixcham konspekt shakliga keltiring. Har bir asosiy "
    "fikrni '# ' bilan boshlangan qisqa sarlavha ostida, keyin 2-4 gaplik "
    "izoh bilan bering. Ortiqcha tafsilotlarni, takrorlarni tashlab yuboring, "
    "lekin muhim raqam/sana/atama/formulalarni SAQLANG. Hech qanday Markdown "
    "(**, `) ishlatmang, faqat '# Sarlavha' formatidan foydalaning. FAQAT "
    "o'zbek tilida yozing (matn boshqa tilda bo'lsa ham)."
)

MAX_INPUT_CHARS = 40_000


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"📑 'Konspekt qisqartirish' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        context.user_data["flow"] = "summarize"
        await query.edit_message_text(
            "📑 *Konspekt qisqartirish*\n\nQisqartirilishi kerak bo'lgan matnni yozing YOKI PDF faylni yuboring.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"📑 Konspekt menyusini ochishda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise
    return SM_WAIT


async def receive_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0

    if update.message.document:
        doc = update.message.document
        if not doc.file_name.lower().endswith(".pdf"):
            await update.message.reply_text("❗️ Iltimos, faqat PDF fayl yuboring (yoki matn qilib yozing).")
            return SM_WAIT
        file = await context.bot.get_file(doc.file_id)
        from io import BytesIO
        bio = BytesIO()
        await file.download_to_memory(out=bio)
        bio.seek(0)
        text = await asyncio.to_thread(extract_pdf_text, bio.read())
        source_desc = f"PDF fayl ('{doc.file_name}')"
        if not text:
            logger.warning(f"📑 PDF dan matn o'qib bo'lmadi: '{doc.file_name}', chat_id={chat_id} (skanerlangan bo'lishi mumkin).")
            await update.message.reply_text("❌ PDF dan matn o'qib bo'lmadi (skanerlangan bo'lishi mumkin).")
            return SM_WAIT
    else:
        text = update.message.text.strip()
        source_desc = "matn"

    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
        logger.warning(f"📑 Matn {MAX_INPUT_CHARS} belgigacha qisqartirildi (chat_id={chat_id}).")

    logger.info(f"📑 Konspekt so'rovi: chat_id={chat_id}, manba={source_desc}, uzunlik={len(text)} belgi.")
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    status = await update.message.reply_text("⏳ Konspekt tuzilmoqda...")

    summary = await ask_ai(SUMMARY_AI, text, _SYSTEM)

    if not summary:
        logger.error(f"📑 Konspekt YARATILMADI: chat_id={chat_id}, manba={source_desc} — sababi yuqoridagi ai_clients loglarida.")
        await status.edit_text("❌ Konspekt tuzib bo'lmadi. Qayta urinib ko'ring.")
        await wallet_ui.finalize_failure(context, update=update, reason="summarize_ai_failed")
        context.user_data.clear()
        return ConversationHandler.END

    logger.info(f"📑 Konspekt muvaffaqiyatli tuzildi: chat_id={chat_id}, natija uzunligi={len(summary)} belgi.")
    if user_id:
        storage.record_usage("summarize", user_id)

    try:
        await status.delete()
    except Exception:
        pass

    if len(summary) <= MAX_TELEGRAM_TEXT:
        await context.bot.send_message(chat_id, f"📑 *Konspekt:*\n\n{summary}", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())
    else:
        pdf_buf = await asyncio.to_thread(make_pdf, "Konspekt", summary)
        msg = await context.bot.send_document(
            chat_id, document=InputFile(pdf_buf, filename="konspekt.pdf"),
            caption="📑 Konspekt (uzun bo'lgani uchun PDF qilib berildi).",
            reply_markup=main_menu_keyboard(),
        )
        if user_id and msg.document:
            storage.record_file(user_id, "summarize", "Konspekt", msg.document.file_id)

    await wallet_ui.finalize_success(context, update=update, chat_id=chat_id)
    context.user_data.clear()
    return ConversationHandler.END
