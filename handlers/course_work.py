"""
📘 Kurs ishi / loyiha — bet soni va mavzu so'raladi, shundan so'ng:
1) reja tuziladi (I/II/III bob nomlari va kichik bo'limlari),
2) kirish, har bir bob va xulosa alohida-alohida generatsiya qilinadi,
3) adabiyotlar ro'yxati tuziladi,
4) hajm yetmasa eng qisqa bob avtomatik kengaytiriladi,
5) titul, avtomatik mundarija (TOC), boblar, xulosa va adabiyotlar bilan PDF yasaladi.

Tuzilma va hajm ulushlari (Kirish 5-8%, har bob ~15-25%, Xulosa ~10%) talabalar
uchun rasmiy uslubiy qo'llanmalarga asoslangan.
"""

import json
import logging
import re

from telegram import Update, InputFile
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode, ChatAction

from config import COURSE_WORK_AI
from ai_clients import ask_ai
from pdf_tools import build_course_work_pdf, count_pdf_pages
from handlers.menu import main_menu_keyboard

logger = logging.getLogger(__name__)

CW_PAGES, CW_TOPIC = range(2)

WORDS_PER_PAGE = 380
MAX_PAGES = 150
MAX_EXPAND_ROUNDS = 8

SHARE_KIRISH = 0.07
SHARE_BOB = 0.24        # har bir bob uchun (3 ta bob => taxminan 72%)
SHARE_XULOSA = 0.10

_ROMAN = {1: "I", 2: "II", 3: "III"}

_COURSE_SYSTEM = (
    "Siz tajribali oʻqituvchi va ilmiy muharrirsiz. Faqat '{topic}' mavzusi doirasida, "
    "undan chetga chiqmasdan yozing. Oʻzbek tilida, ilmiy-akademik uslubda (uchinchi "
    "shaxsda, shaxs olmoshlarisiz) yozing. Faqat soʻralgan boʻlim matnini yozing, "
    "boshqa izoh, sarlavha yoki tushuntirish qoʻshmang."
)

DEFAULT_PLAN = {
    "bob1_nomi": "Mavzuning nazariy va meʼyoriy asoslari",
    "bob1_bolimlari": [
        "Mavzuga oid asosiy tushunchalar va ularning mohiyati",
        "Sohaga oid meʼyoriy-huquqiy hujjatlar va standartlar tahlili",
        "Mavzuning ilmiy-nazariy jihatlari",
    ],
    "bob2_nomi": "Amaliy tahlil",
    "bob2_bolimlari": [
        "Tadqiqot obyekti haqida umumiy maʼlumot",
        "Mavjud holatning tahlili",
        "Aniqlangan kamchiliklar",
    ],
    "bob3_nomi": "Takomillashtirish boʻyicha tavsiyalar",
    "bob3_bolimlari": [
        "Aniqlangan muammolarni bartaraf etish yoʻllari",
        "Taklif etilayotgan yechimlar",
        "Kutilayotgan samaradorlik",
    ],
}


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["flow"] = "course_work"
    await query.edit_message_text(
        "📘 *Kurs ishi / loyiha*\n\n"
        "PDF necha betdan iborat bo'lishi kerak? (masalan: 10, 20, 30)\n"
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
        f"⏳ *{topic}* mavzusida {pages} betlik kurs ishi tayyorlanmoqda...\nReja tuzilmoqda...",
        parse_mode=ParseMode.MARKDOWN,
    )

    await _generate_and_send(update, context, topic, pages, status)
    context.user_data.clear()
    return ConversationHandler.END


async def _generate_and_send(update, context, topic: str, pages: int, status):
    sections = await generate_course_work(topic, pages, status)
    if not sections:
        await status.edit_text("❌ Kurs ishini yaratib bo'lmadi. Birozdan so'ng qayta urinib ko'ring.")
        return

    pdf_buf = build_course_work_pdf(topic, sections)
    actual_pages = count_pdf_pages(pdf_buf)

    await update.message.reply_document(
        document=InputFile(pdf_buf, filename=f"{topic[:40]}.pdf"),
        caption=(
            f"📄 {topic}\n"
            f"📎 {actual_pages} bet (so'ralgan: {pages}+)\n"
            "✅ Titul, mundarija, kirish, 3 bob, xulosa va adabiyotlar ro'yxati bilan."
        ),
        reply_markup=main_menu_keyboard(),
    )
    try:
        await status.delete()
    except Exception:
        pass


async def generate_course_work(topic: str, pages: int, status_msg=None) -> dict | None:
    """
    Kurs ishini tuzilgan holda generatsiya qiladi: reja -> kirish -> 3 bob -> xulosa
    -> adabiyotlar. Natija build_course_work_pdf() ga to'g'ridan-to'g'ri beriladi.
    Boshqa modullar (masalan universal_chat) ham shu funksiyadan foydalanadi.
    """
    target_words = pages * WORDS_PER_PAGE

    async def _status(text):
        if status_msg:
            try:
                await status_msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass

    plan = await _generate_plan(topic)

    await _status(f"⏳ *{topic}* — kirish yozilmoqda...")
    kirish = await _generate_section(
        topic, "KIRISH",
        "Kurs ishining KIRISH qismini yoz: mavzuning dolzarbligi, tadqiqot maqsadi, "
        "tadqiqot vazifalari (3-5 ta), tadqiqot obyekti, tadqiqot predmeti va ishning "
        "tuzilishi haqida qisqacha ma'lumot bo'lsin.",
        int(target_words * SHARE_KIRISH),
    )

    bobs = []
    for i in (1, 2, 3):
        await _status(f"⏳ *{topic}* — {_ROMAN[i]}-bob yozilmoqda...")
        bob_nomi = plan.get(f"bob{i}_nomi", DEFAULT_PLAN[f"bob{i}_nomi"])
        bolimlari = plan.get(f"bob{i}_bolimlari", DEFAULT_PLAN[f"bob{i}_bolimlari"]) or DEFAULT_PLAN[f"bob{i}_bolimlari"]
        content = await _generate_section(
            topic, f"{_ROMAN[i]}-BOB. {bob_nomi}",
            (
                f"Kurs ishining {i}-bobini yoz. Bob nomi: \"{bob_nomi}\". "
                f"Bob quyidagi {len(bolimlari)} ta kichik bo'limdan iborat bo'lsin, "
                "har birini ALOHIDA QATORDA aynan shu ko'rinishda sarlavha bilan boshla "
                f"(masalan \"{i}.1. <sarlavha matni>\"), so'ng shu bo'lim matnini yoz:\n"
                + "\n".join(f"{i}.{j + 1}. {b}" for j, b in enumerate(bolimlari))
            ),
            int(target_words * SHARE_BOB),
        )
        bobs.append({"title": f"{_ROMAN[i]}-BOB. {bob_nomi.upper()}", "content": content or ""})

    await _status(f"⏳ *{topic}* — xulosa yozilmoqda...")
    xulosa = await _generate_section(
        topic, "XULOSA",
        "Kurs ishining XULOSA qismini yoz: o'rganilgan masala bo'yicha asosiy xulosalar, "
        "aniqlangan kamchiliklar va ularni bartaraf etish yo'llari, taklif etilgan "
        "yechimlarning foydasi, ishning amaliy ahamiyati.",
        int(target_words * SHARE_XULOSA),
    )

    await _status(f"⏳ *{topic}* — adabiyotlar ro'yxati tuzilmoqda...")
    adabiyotlar = await _generate_references(topic)

    sections = {
        "kirish": kirish or "",
        "bobs": bobs,
        "xulosa": xulosa or "",
        "adabiyotlar": adabiyotlar or "",
    }

    rounds = 0
    while _total_words(sections) < target_words and rounds < MAX_EXPAND_ROUNDS and sections["bobs"]:
        rounds += 1
        shortest = min(sections["bobs"], key=lambda b: len(b["content"].split()))
        await _status(f"⏳ *{topic}* — matn kengaytirilmoqda ({rounds}-urinish)...")
        addition = await ask_ai(
            COURSE_WORK_AI,
            (
                f"'{topic}' mavzusidagi kurs ishining \"{shortest['title']}\" bo'limini davom "
                "ettiring: mavzuga oid qo'shimcha tahlil, misol yoki tushuntirish qo'shing. "
                "Mavzudan chiqmang, avvalgi matnni takrorlamang. Faqat yangi matnni yozing."
            ),
            _COURSE_SYSTEM.format(topic=topic),
        )
        if not addition:
            break
        shortest["content"] = shortest["content"].rstrip() + "\n\n" + addition.strip()

    return sections


async def _generate_plan(topic: str) -> dict:
    system = "Siz ilmiy-uslubiy kurs ishi rejalashtiruvchisiz. Faqat JSON qaytaring, boshqa hech narsa yozmang."
    prompt = (
        f"'{topic}' mavzusida uch bobdan iborat kurs ishi rejasini tuz. "
        "Har bir bob nomi qisqa va aniq (bir jumladan iborat) bo'lsin, va uning ichida "
        "aniq 3 tadan kichik bo'lim nomini yoz. 1-bob nazariy asoslar, 2-bob amaliy "
        "tahlil, 3-bob tavsiyalar/takomillashtirish yo'nalishida bo'lsin. "
        "Javobni FAQAT quyidagi JSON formatida qaytar, boshqa hech narsa yozma:\n"
        '{"bob1_nomi": "...", "bob1_bolimlari": ["...", "...", "..."], '
        '"bob2_nomi": "...", "bob2_bolimlari": ["...", "...", "..."], '
        '"bob3_nomi": "...", "bob3_bolimlari": ["...", "...", "..."]}'
    )
    raw = await ask_ai(COURSE_WORK_AI, prompt, system)
    if not raw:
        return DEFAULT_PLAN
    try:
        cleaned = re.sub(r"^```json\s*|^```\s*|```\s*$", "", raw.strip(), flags=re.MULTILINE).strip()
        data = json.loads(cleaned)
        for key, val in DEFAULT_PLAN.items():
            data.setdefault(key, val)
        return data
    except Exception as e:
        logger.warning(f"Kurs ishi rejasi JSON parse xato: {e}")
        return DEFAULT_PLAN


async def _generate_section(topic: str, section_label: str, instruction: str, target_words: int) -> str | None:
    prompt = f"[{section_label}]\n{instruction}\n\nTaxminan {max(target_words, 150)} so'zdan iborat bo'lsin."
    return await ask_ai(COURSE_WORK_AI, prompt, _COURSE_SYSTEM.format(topic=topic))


async def _generate_references(topic: str) -> str:
    system = "Siz ilmiy adabiyotlar ro'yxati tuzuvchi yordamchisiz. Faqat ro'yxatni qaytaring, boshqa izoh yozmang."
    prompt = (
        f"'{topic}' mavzusidagi kurs ishi uchun FOYDALANILGAN ADABIYOTLAR RO'YXATI tuz. "
        "Kamida 20 ta yozuv bo'lsin, quyidagi 4 toifaga bo'lib, har biri ichida alifbo "
        "tartibida, umumiy uzluksiz raqamlash bilan:\n"
        "I. Qonunlar va me'yoriy-huquqiy hujjatlar (standartlar, sanitariya qoidalari va h.k.)\n"
        "II. Darsliklar, o'quv qo'llanmalar va monografiyalar\n"
        "III. Ilmiy maqolalar va davriy nashrlar\n"
        "IV. Internet manbalari\n\n"
        "Har bir yozuvni to'liq bibliografik formatda yoz (muallif, nom, shahar, nashriyot, "
        "yil). Faqat ro'yxatni yoz, boshqa izoh berma."
    )
    result = await ask_ai(COURSE_WORK_AI, prompt, system)
    return result or ""


def _total_words(sections: dict) -> int:
    n = len(sections.get("kirish", "").split()) + len(sections.get("xulosa", "").split())
    for b in sections.get("bobs", []):
        n += len(b["content"].split())
    return n
