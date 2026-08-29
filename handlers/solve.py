"""
🧮 Masala/misol yechish — foydalanuvchi matn (masala matni) yoki RASM
(qo'lda yozilgan/kitobdan olingan masala fotosurati) yuboradi. Rasm bo'lsa
Gemini'ning multimodal (vision) qobiliyati orqali to'g'ridan-to'g'ri
tahlil qilinadi (OCR alohida qadam sifatida kerak emas). Yechim
bosqichma-bosqich, tushunarli tarzda beriladi.
"""

import asyncio
import logging

from telegram import Update, InputFile
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode, ChatAction

from config import SOLVE_AI, MAX_TELEGRAM_TEXT
from ai_clients import ask_ai, ask_gemini_multimodal
from pdf_tools import make_pdf
from handlers.menu import main_menu_keyboard
from handlers import wallet_ui
import storage

logger = logging.getLogger(__name__)

SV_WAIT = 0

_SYSTEM = (
    "Siz matematika/fizika/kimyo/iqtisodiyot fanlaridan tajribali o'qituvchisiz. "
    "Berilgan masala/misolni ANIQ, BOSQICHMA-BOSQICH yeching — har bir qadamda "
    "nima qilinayotgani va NEGA shunday qilinayotgani tushuntiring. Oxirida "
    "'Javob:' deb yakuniy natijani aniq ko'rsating. Hech qanday Markdown (**, "
    "##, `) yoki LaTeX belgisi ishlatmang — oddiy matn va oddiy matematik "
    "belgilar (+, -, *, /, ^, =, √) bilan yozing. FAQAT o'zbek tilida yozing."
)


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"🧮 'Masala yechish' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        context.user_data["flow"] = "solve"
        await query.edit_message_text(
            "🧮 *Masala/misol yechish*\n\n"
            "Masalani MATN qilib yozing YOKI uning RASMINI (fotosuratini) yuboring "
            "— qo'lda yozilgan yoki kitobdan olingan bo'lsa ham bo'ladi.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"🧮 Masala yechish menyusini ochishda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise
    return SV_WAIT


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    problem = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0
    logger.info(f"🧮 Masala so'rovi (matn): chat_id={chat_id}, uzunlik={len(problem)} belgi.")

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    status = await update.message.reply_text("⏳ Masala yechilmoqda...")

    solution = await ask_ai(SOLVE_AI, problem, _SYSTEM)
    await _send_solution(update, context, status, "Masala (matn)", solution, chat_id, user_id)
    context.user_data.clear()
    return ConversationHandler.END


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0
    logger.info(f"🧮 Masala so'rovi (rasm): chat_id={chat_id}.")

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    status = await update.message.reply_text("⏳ Rasmdagi masala tahlil qilinmoqda...")

    photo = update.message.photo[-1]  # eng yuqori sifatdagi versiya
    file = await context.bot.get_file(photo.file_id)
    from io import BytesIO
    bio = BytesIO()
    await file.download_to_memory(out=bio)
    image_bytes = bio.getvalue()

    prompt = (
        f"{_SYSTEM}\n\nRasmdagi masala/misolni o'qib, yechib bering. "
        "Agar rasmda bir nechta masala bo'lsa, barchasini alohida-alohida yeching."
    )
    solution, status_code, detail = await ask_gemini_multimodal(SOLVE_AI, prompt, image_bytes, "image/jpeg", label="Masala (rasm)")
    if not solution:
        logger.error(f"🧮 Masala (rasm) YECHILMADI: chat_id={chat_id}, status={status_code}, sabab={detail}.")
    await _send_solution(update, context, status, "Masala (rasm)", solution, chat_id, user_id)
    context.user_data.clear()
    return ConversationHandler.END


async def _send_solution(update, context, status_msg, label, solution, chat_id, user_id):
    if not solution:
        logger.error(f"🧮 {label} YECHILMADI: chat_id={chat_id} — sababi yuqoridagi ai_clients loglarida.")
        await status_msg.edit_text("❌ Masalani yechib bo'lmadi. Qayta urinib ko'ring yoki masalani aniqroq yozing/suratga oling.")
        await wallet_ui.finalize_failure(context, update=update, chat_id=chat_id, reason="solve_ai_failed")
        return

    logger.info(f"🧮 {label} muvaffaqiyatli yechildi: chat_id={chat_id}, javob uzunligi={len(solution)} belgi.")
    if user_id:
        storage.record_usage("solve", user_id)

    try:
        await status_msg.delete()
    except Exception:
        pass

    if len(solution) <= MAX_TELEGRAM_TEXT:
        await context.bot.send_message(chat_id, f"🧮 *Yechim:*\n\n{solution}", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())
    else:
        pdf_buf = await asyncio.to_thread(make_pdf, "Masala yechimi", solution)
        msg = await context.bot.send_document(
            chat_id, document=InputFile(pdf_buf, filename="masala_yechimi.pdf"),
            caption="🧮 Yechim (javob uzun bo'lgani uchun PDF qilib berildi).",
            reply_markup=main_menu_keyboard(),
        )
        if user_id and msg.document:
            storage.record_file(user_id, "solve", "Masala yechimi", msg.document.file_id)

    await wallet_ui.finalize_success(context, update=update, chat_id=chat_id)
