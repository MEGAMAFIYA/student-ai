"""
📖 Qo'llanma tayyorlash — foydalanuvchi savollar yuboradi,
AI har biriga javob yozib, kichik harflarda PDF qo'llanma qilib beradi.
"""

import asyncio
import logging

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import GUIDE_AI
from ai_clients import ask_ai
from pdf_tools import make_pdf
from handlers.menu import main_menu_keyboard

logger = logging.getLogger(__name__)

GD_COLLECTING = 0


def _finish_keyboard(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Tayyor ({count} ta savol)", callback_data="guide:finish")]])


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["flow"] = "guide"
    context.user_data["gd_questions"] = []
    await query.edit_message_text(
        "📖 *Qo'llanma tayyorlash*\n\n"
        "Savollaringizni yuboring (bitta xabarda bir nechta savol bo'lsa, "
        "har birini alohida qatorga yozing). Tugagach, '✅ Tayyor' tugmasini bosing.",
        parse_mode="Markdown",
    )
    return GD_COLLECTING


async def receive_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [l.strip() for l in update.message.text.split("\n") if l.strip()]
    questions = context.user_data.setdefault("gd_questions", [])
    questions.extend(lines)

    await update.message.reply_text(
        f"✅ Qabul qilindi. Jami: {len(questions)} ta savol.\n"
        "Yana savol yuboring yoki tayyor bo'lsa tugmani bosing.",
        reply_markup=_finish_keyboard(len(questions)),
    )
    return GD_COLLECTING


async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    questions = context.user_data.get("gd_questions", [])
    if not questions:
        await query.edit_message_text("❗️ Hali birorta ham savol yuborilmagan. Savol yuboring.")
        return GD_COLLECTING

    await query.edit_message_text(f"⏳ {len(questions)} ta savolga javob tayyorlanmoqda...")

    system = (
        "Siz bilimdon o'qituvchisiz. Berilgan savolga aniq, qisqa va to'g'ri javob bering. "
        "Faqat javobni yozing, qo'shimcha izoh kerak emas."
    )

    parts = []
    for i, q in enumerate(questions, start=1):
        answer = await ask_ai(GUIDE_AI, q, system)
        answer = answer.strip() if answer else "Javob topilmadi."
        parts.append(f"# {q}\n{q} - {answer}")

        try:
            await query.edit_message_text(f"⏳ Javoblar tayyorlanmoqda... ({i}/{len(questions)})")
        except Exception:
            pass

    content = "\n\n".join(parts)
    pdf_buf = await asyncio.to_thread(make_pdf, "Qo'llanma", content, lowercase=True)

    await context.bot.send_document(
        update.effective_chat.id,
        document=InputFile(pdf_buf, filename="qollanma.pdf"),
        caption=f"📖 {len(questions)} ta savol-javobdan iborat qo'llanma tayyor.",
        reply_markup=main_menu_keyboard(),
    )

    context.user_data.clear()
    return ConversationHandler.END
