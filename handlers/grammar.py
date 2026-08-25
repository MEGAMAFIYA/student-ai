"""
✅ Imlo/Grammatika tekshirish — foydalanuvchi matn yuboradi, AI imlo va
grammatik xatolarni topib, tuzatilgan matnni va asosiy xatolar ro'yxatini
qaytaradi.
"""

import asyncio
import logging

from telegram import Update, InputFile
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode, ChatAction

from config import GRAMMAR_AI, MAX_TELEGRAM_TEXT
from ai_clients import ask_ai
from pdf_tools import make_pdf
from handlers.menu import main_menu_keyboard
import storage

logger = logging.getLogger(__name__)

GR_WAIT = 0

_SYSTEM = (
    "Siz o'zbek (yoki matn boshqa tilda bo'lsa, o'sha til) tili imlo va grammatika "
    "muharririsiz. Berilgan matndagi imlo, tinish belgilari va grammatik xatolarni "
    "toping va tuzating. Javobingizni AYNAN quyidagi ikki qismga bo'lib bering:\n\n"
    "TUZATILGAN MATN:\n(bu yerga to'liq tuzatilgan matn)\n\n"
    "ASOSIY XATOLAR:\n(bu yerga har bir muhim xato uchun bitta qator: "
    "\"noto'g'ri\" -> \"to'g'ri\" (qisqa sabab), agar xato bo'lmasa \"Xato topilmadi, matn to'g'ri yozilgan.\" deb yozing)\n\n"
    "Boshqa hech qanday izoh yoki Markdown belgisi qo'shmang."
)


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"✅ 'Imlo tekshirish' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        context.user_data["flow"] = "grammar"
        await query.edit_message_text(
            "✅ *Imlo/Grammatika tekshirish*\n\nTekshirilishi kerak bo'lgan matnni yuboring:",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"✅ Imlo tekshirish menyusini ochishda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise
    return GR_WAIT


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0
    logger.info(f"✅ Imlo tekshirish so'rovi: chat_id={chat_id}, uzunlik={len(text)} belgi.")

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    status = await update.message.reply_text("⏳ Matn tekshirilmoqda...")

    result = await ask_ai(GRAMMAR_AI, text, _SYSTEM)

    if not result:
        logger.error(f"✅ Imlo tekshiruvi BAJARILMADI: chat_id={chat_id} — sababi yuqoridagi ai_clients loglarida.")
        await status.edit_text("❌ Matnni tekshirib bo'lmadi. Qayta urinib ko'ring.")
        context.user_data.clear()
        return ConversationHandler.END

    logger.info(f"✅ Imlo tekshiruvi muvaffaqiyatli yakunlandi: chat_id={chat_id}, natija uzunligi={len(result)} belgi.")
    if user_id:
        storage.record_usage("grammar", user_id)

    try:
        await status.delete()
    except Exception:
        pass

    if len(result) <= MAX_TELEGRAM_TEXT:
        await context.bot.send_message(chat_id, f"✅ {result}", reply_markup=main_menu_keyboard())
    else:
        pdf_buf = await asyncio.to_thread(make_pdf, "Imlo tekshiruvi", result)
        msg = await context.bot.send_document(
            chat_id, document=InputFile(pdf_buf, filename="imlo_tekshiruvi.pdf"),
            caption="✅ Imlo tekshiruvi natijasi (uzun bo'lgani uchun PDF qilib berildi).",
            reply_markup=main_menu_keyboard(),
        )
        if user_id and msg.document:
            storage.record_file(user_id, "grammar", "Imlo tekshiruvi", msg.document.file_id)

    context.user_data.clear()
    return ConversationHandler.END
