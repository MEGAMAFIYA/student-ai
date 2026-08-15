"""
📘 Kurs ishi / loyiha — bet soni va mavzu so'raladi, shu mavzu doirasida,
belgilangan bet sonidan kam bo'lmagan hajmda kurs ishi yoziladi.
"""

import logging
import re

from telegram import Update, InputFile
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode, ChatAction

from config import COURSE_WORK_AI
from ai_clients import ask_ai
from pdf_tools import make_pdf, count_pdf_pages
from handlers.menu import main_menu_keyboard

logger = logging.getLogger(__name__)

CW_PAGES, CW_TOPIC = range(2)

WORDS_PER_PAGE = 380       # A4, 12pt uchun taxminiy so'z zichligi
MAX_GENERATION_ROUNDS = 6  # cheksiz siklni oldini olish uchun chegara
MAX_PAGES = 150


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["flow"] = "course_work"
    await query.edit_message_text(
        "📘 *Kurs ishi / loyiha*\n\n"
        "PDF necha betdan iborat bo'lishi kerak? (masalan: 10, 20, 50)\n"
        "Belgilagan bet sonidan kam bo'lmaydi.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return CW_PAGES


async def receive_pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    digits = re.sub(r"[^0-9]", "", update.message.text.strip())
    if not digits or int(digits) <= 0:
        await update.message.reply_text("❗️ Iltimos, faqat son yuboring. Masalan: 15")
        return CW_PAGES

    pages = int(digits)
    if pages > MAX_PAGES:
        await update.message.reply_text(
            f"❗️ {MAX_PAGES} betdan katta hajm juda uzoq vaqt talab qiladi. "
            f"Iltimos, {MAX_PAGES} yoki undan kichik son kiriting."
        )
        return CW_PAGES

    context.user_data["cw_pages"] = pages
    await update.message.reply_text(
        f"✅ {pages} bet.\n\nEndi kurs ishining *mavzusini* yuboring:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return CW_TOPIC


async def receive_topic_and_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = update.message.text.strip()
    pages = context.user_data.get("cw_pages", 10)

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    status = await update.message.reply_text(
        f"⏳ *{topic}* mavzusida {pages} betlik kurs ishi tayyorlanmoqda...\n"
        "Bu bir necha daqiqa vaqt olishi mumkin.",
        parse_mode=ParseMode.MARKDOWN,
    )

    content = await generate_course_work(topic, pages, status, context)

    if not content:
        await status.edit_text("❌ Kurs ishini yaratib bo'lmadi. Birozdan so'ng qayta urinib ko'ring.")
        context.user_data.clear()
        return ConversationHandler.END

    pdf_buf = make_pdf(topic.title(), content)
    actual_pages = count_pdf_pages(pdf_buf)

    await update.message.reply_document(
        document=InputFile(pdf_buf, filename=f"{topic[:40]}.pdf"),
        caption=f"📄 {topic}\n📎 {actual_pages} bet (so'ralgan: {pages}+)",
        reply_markup=main_menu_keyboard(),
    )

    try:
        await status.delete()
    except Exception:
        pass

    context.user_data.clear()
    return ConversationHandler.END


async def generate_course_work(topic: str, pages: int, status_msg, context) -> str | None:
    """Boshqa modullar (masalan universal_chat) ham shu funksiyadan foydalanadi."""
    target_words = pages * WORDS_PER_PAGE

    system = (
        "Siz tajribali o'qituvchi va ilmiy muharrirsiz. "
        f"Faqat '{topic}' mavzusi doirasida, undan chetga chiqmasdan yozing. "
        "Kurs ishi tuzilishi: # Kirish, # Asosiy qism (bir necha bo'lim, har biri # bilan boshlansin), "
        "# Xulosa, # Foydalanilgan adabiyotlar. "
        "O'zbek tilida, ilmiy-akademik uslubda, aniq va mazmunli yozing. "
        "Har bir bo'limni batafsil, misollar va tushuntirishlar bilan yoying."
    )

    content = await ask_ai(
        COURSE_WORK_AI,
        f"'{topic}' mavzusida, taxminan {target_words} so'zdan iborat, to'liq kurs ishi yoz.",
        system,
    )
    if not content:
        return None

    rounds = 0
    while _word_count(content) < target_words and rounds < MAX_GENERATION_ROUNDS:
        rounds += 1
        try:
            await status_msg.edit_text(
                f"⏳ *{topic}* — matn kengaytirilmoqda ({rounds}-urinish)...",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

        addition = await ask_ai(
            COURSE_WORK_AI,
            (
                f"Yuqorida boshlangan '{topic}' mavzusidagi kurs ishini davom ettiring: "
                "yangi kichik bo'lim yoki mavzuga oid qo'shimcha tahlil, misol yoki amaliy "
                "qism qo'shing. Mavzudan chiqmang. Faqat yangi matnni yozing, avvalgisini "
                "takrorlamang. Zarur bo'lsa # bilan yangi bo'lim sarlavhasi qo'ying."
            ),
            system,
        )
        if not addition:
            break
        content = content.rstrip() + "\n\n" + addition.strip()

    return content


def _word_count(text: str) -> int:
    return len(text.split())
