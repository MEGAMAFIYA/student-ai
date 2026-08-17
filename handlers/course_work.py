"""
📘 Kurs ishi / loyiha — bet soni va mavzu so'raladi, shundan so'ng:
1) reja tuziladi (I/II/III bob nomlari va har biriga 3 tadan kichik bo'lim),
2) kirish generatsiya qilinadi,
3) HAR BIR kichik bo'lim (1.1, 1.2, 1.3...) ALOHIDA-ALOHIDA, o'ziga tegishli
   hajmga (taxminan 2.5-3 bet) to'lguncha yozdiriladi, keyin navbatdagi bo'limga o'tiladi,
4) xulosa va adabiyotlar ro'yxati generatsiya qilinadi,
5) PDF quriladi va HAQIQIY sahifa soni o'lchanadi — agar so'ralgan sahifadan kam
   bo'lsa, eng qisqa bob avtomatik kengaytirilib qayta quriladi (shu tariqa natija
   har doim so'ralgan sahifa sonidan KAM bo'lmasligi kafolatlanadi).

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

# Xavfsizlik zahirasi: so'z hisobi bilan sahifa hisobi orasidagi tafovutni
# qoplash uchun boshlang'ich generatsiyada biroz ko'proq maqsad qo'yiladi.
SAFETY_MARGIN = 1.10

SHARE_KIRISH = 0.07
SHARE_BOB = 0.24        # har bir bob uchun (3 ta bob => taxminan 72%)
SHARE_XULOSA = 0.10

MAX_SUBSECTION_FILL_ROUNDS = 6   # bitta kichik bo'limni to'ldirish uchun maksimal urinish
MAX_PDF_EXPAND_ROUNDS = 30       # yakuniy PDF sahifa sonini yetkazish uchun maksimal urinish
MIN_ACCEPTABLE_WORDS = 60        # shundan kam so'z yozilsa — AI umuman ishlamagan deb hisoblanadi

_ROMAN = {1: "I", 2: "II", 3: "III"}

# Mavzu matnini tozalashda olib tashlanadigan ortiqcha ibora va so'zlar
# (boshida yoki oxirida bo'lsa). Iterativ tarzda (o'zgarish qolmaguncha)
# qo'llaniladi, chunki bir nechta ibora ketma-ket kelishi mumkin
# (masalan "... mavzusida kurs ishi").
_TOPIC_FILLER_START = re.compile(
    r"^(haqida|mavzusida|kurs ishi|kurs loyihasi|kurs proyekti|yoz|tayyorla|"
    r"li|ul|ol|div|span|br|p)[\s:,.\-]+",
    re.IGNORECASE,
)
_TOPIC_FILLER_END = re.compile(
    r"[\s:,.\-]+(haqida|mavzusida|kurs ishi|kurs loyihasi|kurs proyekti|yoz|tayyorla)$",
    re.IGNORECASE,
)


def clean_topic(raw: str) -> str:
    """Foydalanuvchi yozgan mavzu matnidan ortiqcha ibora va tasodifiy
    artefaktlarni (masalan HTML teg qoldig'i "li", "mavzusida kurs ishi"
    kabi takroriy jumlalar) olib tashlaydi. O'zgarish qolmaguncha
    (iterativ) ishlaydi, shu bilan ketma-ket kelgan bir necha ortiqcha
    iborani ham to'liq tozalaydi."""
    text = raw.strip().strip("<>").strip()
    for _ in range(6):
        new_text = _TOPIC_FILLER_START.sub("", text)
        new_text = _TOPIC_FILLER_END.sub("", new_text).strip()
        if new_text == text or not new_text:
            break
        text = new_text
    return text or raw.strip()

_COURSE_SYSTEM = (
    "Siz tajribali oʻqituvchi va ilmiy muharrirsiz. Faqat '{topic}' mavzusi doirasida, "
    "undan chetga chiqmasdan yozing. Oʻzbek tilida, ilmiy-akademik uslubda (uchinchi "
    "shaxsda, shaxs olmoshlarisiz) yozing. Faqat soʻralgan boʻlim matnini yozing, "
    "boshqa izoh, sarlavha yoki tushuntirish qoʻshmang. MUHIM: bir xil fikr yoki "
    "jumlani turli soʻzlar bilan qayta-qayta takrorlamang — har bir abzas albatta "
    "yangi, aniq maʼlumot, misol yoki dalil olib kelsin. Umumiy va mavhum gaplar "
    "oʻrniga aniq faktlar, raqamlar, holatlar keltiring."
)

# Bo'limni kengaytirishda har safar boshqa jihatga urg'u berish uchun —
# shu orqali "davom ettir" so'rovlari bir xil fikrni takrorlamaydi.
_EXPAND_ANGLES = [
    "amaliy misollar va real holatlar (case study)",
    "aniq raqamlar, me'yorlar yoki tadqiqot natijalari",
    "xalqaro tajriba yoki qiyosiy tahlil",
    "muammoning sabab-oqibat bog'liqligi",
    "amaliy tavsiya va yechimlar",
    "ushbu sohadagi zamonaviy tendensiyalar",
]

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
        "Belgilagan bet sonidan kam bo'lmaydi (ko'proq chiqishi mumkin).",
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
    topic = clean_topic(update.message.text.strip())
    pages = context.user_data.get("cw_pages", 10)

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    status = await update.message.reply_text(
        f"⏳ *{topic}* mavzusida {pages}+ betlik kurs ishi tayyorlanmoqda...\n"
        "Bu bir necha daqiqa vaqt olishi mumkin (bo'lim-bo'lim yozib chiqiladi). "
        "Reja tuzilmoqda...",
        parse_mode=ParseMode.MARKDOWN,
    )

    await _generate_and_send(update, context, topic, pages, status)
    context.user_data.clear()
    return ConversationHandler.END


async def _generate_and_send(update, context, topic: str, pages: int, status):
    result = await generate_course_work(topic, pages, status)
    if not result:
        await status.edit_text(
            "❌ Kurs ishini yaratib bo'lmadi — AI xizmatlari hozir javob bermayapti "
            "(yuklama yoki texnik uzilish bo'lishi mumkin). Birozdan so'ng qayta urinib ko'ring."
        )
        return

    sections, pdf_buf, actual_pages = result

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


async def generate_course_work(topic: str, pages: int, status_msg=None):
    """
    Kurs ishini tuzilgan holda generatsiya qiladi va HAQIQIY PDF sahifa soni
    so'ralgan sahifa sonidan kam bo'lmagunicha kengaytirib boradi.
    Qaytaradi: (sections dict, pdf_buffer, actual_pages) yoki None (xato bo'lsa).
    Boshqa modullar (masalan universal_chat) ham shu funksiyadan foydalanadi.
    """
    target_words = int(pages * WORDS_PER_PAGE * SAFETY_MARGIN)

    async def _status(text):
        if status_msg:
            try:
                await status_msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass

    plan = await _generate_plan(topic)

    bob_nomlari_matni = "; ".join(
        f"{_ROMAN[i]}-bob – {plan.get(f'bob{i}_nomi') or DEFAULT_PLAN[f'bob{i}_nomi']}"
        for i in (1, 2, 3)
    )

    await _status(f"⏳ *{topic}* — kirish yozilmoqda...")
    kirish = await _generate_section(
        topic, "KIRISH",
        "Kurs ishining KIRISH qismini yoz: mavzuning dolzarbligi, tadqiqot maqsadi, "
        "tadqiqot vazifalari (3-5 ta), tadqiqot obyekti, tadqiqot predmeti haqida "
        "qisqacha ma'lumot bo'lsin. \"Ishning tuzilishi\" bandida FAQAT quyidagi haqiqiy "
        f"bo'limlarni sanab o'ting va boshqa hech qanday bo'lim nomini o'ylab topmang: "
        f"Kirish; {bob_nomlari_matni}; Xulosa; Foydalanilgan adabiyotlar ro'yxati.",
        int(target_words * SHARE_KIRISH),
    )

    bobs = []
    for i in (1, 2, 3):
        bob_nomi = plan.get(f"bob{i}_nomi") or DEFAULT_PLAN[f"bob{i}_nomi"]
        bolimlari = plan.get(f"bob{i}_bolimlari") or DEFAULT_PLAN[f"bob{i}_bolimlari"]
        bob_target_words = int(target_words * SHARE_BOB)

        content = await _generate_bob(topic, i, bob_nomi, bolimlari, bob_target_words, _status)
        bobs.append({"title": f"{_ROMAN[i]}-BOB. {bob_nomi.upper()}", "content": content})

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

    # ===== AI UMUMAN ISHLAMAGANMI — TEKSHIRISH =====
    # Agar barcha AI provayderlar (Gemini/Groq/Pollinations) ishlamay qolgan bo'lsa,
    # yozilgan matn deyarli bo'sh qoladi. Bunday holda BO'SH/BUZUQ PDF yubormasdan,
    # generatsiya muvaffaqiyatsiz bo'lganini aniq bildiramiz.
    if _total_words(sections) < MIN_ACCEPTABLE_WORDS:
        logger.error(
            f"Kurs ishi generatsiyasi deyarli bo'sh natija berdi ('{topic}') — "
            "AI provayderlar ishlamagan bo'lishi mumkin."
        )
        return None

    # ===== HAQIQIY PDF SAHIFA SONIGA QARAB KENGAYTIRISH =====
    pdf_buf = build_course_work_pdf(topic, sections)
    actual_pages = count_pdf_pages(pdf_buf)

    rounds = 0
    while actual_pages < pages and rounds < MAX_PDF_EXPAND_ROUNDS:
        angle = _EXPAND_ANGLES[rounds % len(_EXPAND_ANGLES)]
        rounds += 1
        await _status(
            f"⏳ *{topic}* — hajm kengaytirilmoqda ({actual_pages}/{pages} bet, "
            f"{rounds}-urinish)..."
        )
        shortest = min(sections["bobs"], key=lambda b: len(b["content"].split()))
        addition = await ask_ai(
            COURSE_WORK_AI,
            (
                f"'{topic}' mavzusidagi kurs ishining \"{shortest['title']}\" bobiga "
                f"yangi qo'shimcha kichik qism yozing. FAQAT quyidagi yangi jihatga e'tibor "
                f"bering: {angle}. Avvalgi matnda aytilgan fikrlarni HECH QANDAY shaklda "
                "takrorlamang — faqat yangi, qo'shimcha ma'lumot yozing (kamida 400 so'z)."
            ),
            _COURSE_SYSTEM.format(topic=topic),
        )
        if not addition:
            break
        shortest["content"] = shortest["content"].rstrip() + "\n\n" + addition.strip()

        pdf_buf = build_course_work_pdf(topic, sections)
        actual_pages = count_pdf_pages(pdf_buf)

    return sections, pdf_buf, actual_pages


async def _generate_bob(topic: str, bob_num: int, bob_nomi: str, bolimlari: list, bob_target_words: int, status_cb) -> str:
    """Bobning har bir kichik bo'limini ALOHIDA generatsiya qiladi va har birini
    o'ziga ajratilgan hajmga (taxminan 2.5-3 bet) yetguncha to'ldiradi."""
    n = max(len(bolimlari), 1)
    per_sub_words = max(int(bob_target_words / n), 850)  # ~2.2+ bet minimal

    parts = []
    for j, sub_title in enumerate(bolimlari, start=1):
        label = f"{bob_num}.{j}. {sub_title}"
        await status_cb(f"⏳ *{topic}* — {label} yozilmoqda...")

        content = await _generate_section(
            topic, label,
            f"Kurs ishining \"{sub_title}\" nomli kichik bo'limini yoz.",
            per_sub_words,
        ) or ""

        fill_rounds = 0
        while len(content.split()) < per_sub_words and fill_rounds < MAX_SUBSECTION_FILL_ROUNDS:
            angle = _EXPAND_ANGLES[fill_rounds % len(_EXPAND_ANGLES)]
            fill_rounds += 1
            addition = await ask_ai(
                COURSE_WORK_AI,
                (
                    f"'{topic}' mavzusidagi \"{sub_title}\" nomli bo'limga yangi abzas(lar) "
                    f"qo'shing. Bu safar FAQAT quyidagi yangi jihatga e'tibor bering: {angle}. "
                    "Avvalgi matnda aytilgan fikrlarni HECH QANDAY shaklda takrorlamang — "
                    "faqat yangi, qo'shimcha ma'lumot yozing."
                ),
                _COURSE_SYSTEM.format(topic=topic),
            )
            if not addition:
                break
            content = content.rstrip() + "\n\n" + addition.strip()

        parts.append(f"{bob_num}.{j}. {sub_title}\n{content}")

    return "\n\n".join(parts)


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
