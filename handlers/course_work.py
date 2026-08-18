"""
📘 Kurs ishi / loyiha — bet soni va mavzu so'raladi, shundan so'ng:
1) reja tuziladi (I/II/III bob nomlari va har biriga 3 tadan kichik bo'lim),
2) kirish generatsiya qilinadi,
3) HAR BIR kichik bo'lim (1.1, 1.2, 1.3...) ALOHIDA-ALOHIDA, o'ziga tegishli
   hajmga (taxminan 2.5-3 bet) to'lguncha yozdiriladi, keyin navbatdagi bo'limga o'tiladi,
4) xulosa va adabiyotlar ro'yxati generatsiya qilinadi,
5) NAZORATCHI: har bir kichik bo'lim, kirish, xulosa va adabiyotlar ro'yxati ALOHIDA
   tekshiriladi — biror qism bo'sh yoki juda qisqa chiqqan bo'lsa (AI xatosi/limit
   tufayli), FAQAT o'sha qism bir necha marta qayta yozdiriladi, to'liq hujjat
   qayta yozilmaydi,
6) PDF quriladi va HAQIQIY sahifa soni o'lchanadi — agar so'ralgan sahifadan kam
   bo'lsa, eng qisqa bob avtomatik kengaytirilib qayta quriladi.

Tuzilma va hajm ulushlari (Kirish 5-8%, har bob ~15-25%, Xulosa ~10%) talabalar
uchun rasmiy uslubiy qo'llanmalarga asoslangan.
"""

import asyncio
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

# ===== NAZORATCHI (to'liqlik tekshiruvi) sozlamalari =====
MIN_SECTION_WORDS = 40           # kirish/xulosa "bo'sh emas" deb hisoblanishi uchun minimal so'z
MIN_SUBSECTION_WORDS = 40        # har bir kichik bo'lim uchun minimal so'z
MIN_REFERENCES_CHARS = 50        # adabiyotlar ro'yxati "bo'sh emas" deb hisoblanishi uchun
MAX_COMPLETENESS_ROUNDS = 3      # to'liqlikni tekshirish-tuzatish tsikli necha marta takrorlanadi
RETRY_ATTEMPTS = 3               # bitta AI so'rovi necha marta qayta urinilishi
RETRY_DELAY_SEC = 3              # urinishlar orasidagi kutish (soniya)

_ROMAN = {1: "I", 2: "II", 3: "III"}

# Mavzu matnini tozalashda olib tashlanadigan ortiqcha ibora va so'zlar
# (boshida yoki oxirida bo'lsa). Iterativ tarzda (o'zgarish qolmaguncha)
# qo'llaniladi, chunki bir nechta ibora ketma-ket kelishi mumkin
# (masalan "... mavzusida kurs ishi").
_TOPIC_FILLER_START = re.compile(
    r"^(haqida|mavzusida|shu mavzuda|kurs ishi|kurs loyihasi|kurs proyekti|"
    r"tayyorlab ber|yozib ber|yoz|tayyorla|kerak|iltimos|"
    r"li|ul|ol|div|span|br|p)[\s:,.\-]+",
    re.IGNORECASE,
)
_TOPIC_FILLER_END = re.compile(
    r"[\s:,.\-]+(haqida|mavzusida|shu mavzuda|kurs ishi|kurs loyihasi|kurs proyekti|"
    r"tayyorlab ber|yozib ber|yoz|tayyorla|kerak|iltimos)$",
    re.IGNORECASE,
)
# Matn ichida (o'rtada) uchraydigan yakka "li" so'zi — odatda HTML <li> teg
# qoldig'i, hech qanday o'zbekcha ma'noga ega emas.
_STRAY_LI = re.compile(r"(?<=\s)li(?=\s)", re.IGNORECASE)


def clean_topic(raw: str) -> str:
    """Foydalanuvchi yozgan mavzu matnidan ortiqcha ibora va tasodifiy
    artefaktlarni (masalan HTML teg qoldig'i "li", "mavzusida kurs ishi",
    "shu mavzuda ... tayyorlab ber" kabi buyruq jumlalari) olib tashlaydi.
    O'zgarish qolmaguncha (iterativ) ishlaydi."""
    text = raw.strip().strip("<>").strip()
    text = _STRAY_LI.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
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
    "boshqa izoh, sarlavha yoki tushuntirish qoʻshmang — sarlavhani alohida qo'shmang, "
    "chunki u allaqachon hujjatda mavjud. MUHIM: bir xil fikr yoki jumlani turli "
    "soʻzlar bilan qayta-qayta takrorlamang — har bir abzas albatta yangi, aniq "
    "maʼlumot, misol yoki dalil olib kelsin. Umumiy va mavhum gaplar oʻrniga aniq "
    "faktlar, raqamlar, holatlar keltiring. FORMATLASH: hech qanday Markdown belgisi "
    "(**, ##, `, -) yoki LaTeX/matematik formula yozuvi (\\, {}, ^, _) ishlatmang — "
    "formulalarni oddiy matn ko'rinishida yozing (masalan 'EI = 0.35 x Pfiz + 0.25 x "
    "Pbio'). Faqat oddiy, sodda matn abzaslari yozing."
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

# AI rad javobi berganini aniqlash uchun — bunday javob HECH QACHON hujjatga
# kiritilmasligi kerak, aksincha qayta uriniladi (ask_retry ichida tekshiriladi).
_REFUSAL_RE = re.compile(
    r"^\s*(i'?m sorry|i cannot fulfill|i can'?t fulfill|i am unable to|i'?m unable to|"
    r"as an ai(?: language model)?|i cannot assist|i can'?t assist|i can'?t help|"
    r"kechirasiz,?\s*(lekin|ammo|biroq)|uzr,?\s*(lekin|ammo|biroq)|"
    r"men bunga yordam bera olmayman|bu so'rovni bajara olmayman)",
    re.IGNORECASE,
)

# AI javobida tasodifan chiqib qolishi mumkin bo'lgan Markdown/LaTeX izlarini
# tozalash uchun — promptdagi taqiq yetarli bo'lmagan hollarda ikkinchi himoya.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_LATEX_TEXT_RE = re.compile(r"\\text\{([^}]*)\}")
_LATEX_TIMES_RE = re.compile(r"\\times")
_LATEX_SUBSUP_RE = re.compile(r"[_^]\{([^}]*)\}")
_LATEX_BRACKETS_RE = re.compile(r"\\[\[\]()]")
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+")


def _is_refusal(text: str) -> bool:
    if not text or not text.strip():
        return True
    return bool(_REFUSAL_RE.search(text[:200]))


def _clean_ai_text(text: str) -> str:
    if not text:
        return text
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _LATEX_TEXT_RE.sub(r"\1", text)
    text = _LATEX_TIMES_RE.sub("x", text)
    text = _LATEX_SUBSUP_RE.sub(r"(\1)", text)
    text = _LATEX_BRACKETS_RE.sub("", text)
    text = _LATEX_CMD_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _strip_duplicate_heading(content: str, sub_title: str) -> str:
    """AI ba'zan o'zi ham bo'lim sarlavhasini qaytarib yuboradi — bu holda
    hujjatda sarlavha ikki marta chiqadi. Birinchi qator sarlavhaga mos
    kelsa, uni olib tashlaydi."""
    if not content:
        return content
    parts = content.split("\n", 1)
    first_line = re.sub(r"^[\s*#]*\d*\.?\d*\.?\s*", "", parts[0]).strip().rstrip(".:").lower()
    title_norm = sub_title.strip().rstrip(".:").lower()
    if first_line and (first_line == title_norm or title_norm.startswith(first_line) or first_line.startswith(title_norm[:25])):
        return parts[1].lstrip("\n") if len(parts) > 1 else ""
    return content

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
            "yoki ba'zi bo'limlarni bir necha urinishdan keyin ham to'liq yoza olmadi. "
            "Birozdan so'ng qayta urinib ko'ring."
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


async def _ask_retry(prompt: str, system: str, attempts: int = RETRY_ATTEMPTS, delay: int = RETRY_DELAY_SEC, raw: bool = False) -> str | None:
    """ask_ai ni bir necha marta qayta urinib chaqiradi — vaqtinchalik limit/tarmoq
    xatolarida bitta muvaffaqiyatsiz urinish butun bo'limni bo'sh qoldirmasligi uchun.
    AI rad javobi (masalan "I'm sorry, I can't fulfill...") aniqlansa ham qayta
    uriniladi — bunday javob hech qachon hujjatga kiritilmaydi. raw=True bo'lsa
    (masalan JSON javoblarda) Markdown/LaTeX tozalash qo'llanilmaydi."""
    for i in range(attempts):
        result = await ask_ai(COURSE_WORK_AI, prompt, system)
        if result and result.strip() and not _is_refusal(result):
            return result if raw else _clean_ai_text(result)
        if i < attempts - 1:
            await asyncio.sleep(delay)
    return None


async def generate_course_work(topic: str, pages: int, status_msg=None):
    """
    Kurs ishini tuzilgan holda generatsiya qiladi, NAZORATCHI orqali har bir
    bo'limning to'liqligini tekshirib, bo'sh qolgan qismlarni qayta yozdiradi,
    so'ng HAQIQIY PDF sahifa soni so'ralgan sahifa sonidan kam bo'lmagunicha
    kengaytirib boradi.
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
    kirish_instruction = (
        "Kurs ishining KIRISH qismini yoz: mavzuning dolzarbligi, tadqiqot maqsadi, "
        "tadqiqot vazifalari (3-5 ta), tadqiqot obyekti, tadqiqot predmeti haqida "
        "qisqacha ma'lumot bo'lsin. \"Ishning tuzilishi\" bandida FAQAT quyidagi haqiqiy "
        f"bo'limlarni sanab o'ting va boshqa hech qanday bo'lim nomini o'ylab topmang: "
        f"Kirish; {bob_nomlari_matni}; Xulosa; Foydalanilgan adabiyotlar ro'yxati."
    )
    xulosa_instruction = (
        "Kurs ishining XULOSA qismini yoz: o'rganilgan masala bo'yicha asosiy xulosalar, "
        "aniqlangan kamchiliklar va ularni bartaraf etish yo'llari, taklif etilgan "
        "yechimlarning foydasi, ishning amaliy ahamiyati."
    )

    await _status(f"⏳ *{topic}* — kirish yozilmoqda...")
    kirish = await _generate_section(topic, "KIRISH", kirish_instruction, int(target_words * SHARE_KIRISH)) or ""

    bobs = []
    for i in (1, 2, 3):
        bob_nomi = plan.get(f"bob{i}_nomi") or DEFAULT_PLAN[f"bob{i}_nomi"]
        bolimlari = plan.get(f"bob{i}_bolimlari") or DEFAULT_PLAN[f"bob{i}_bolimlari"]
        bob_target_words = int(target_words * SHARE_BOB)

        subsections = await _generate_bob(topic, i, bolimlari, bob_target_words, _status)
        bob = {"title": f"{_ROMAN[i]}-BOB. {bob_nomi.upper()}", "subsections": subsections}
        bob["content"] = _bob_content(bob)
        bobs.append(bob)

    await _status(f"⏳ *{topic}* — xulosa yozilmoqda...")
    xulosa = await _generate_section(topic, "XULOSA", xulosa_instruction, int(target_words * SHARE_XULOSA)) or ""

    await _status(f"⏳ *{topic}* — adabiyotlar ro'yxati tuzilmoqda...")
    adabiyotlar = await _generate_references(topic) or ""

    sections = {"kirish": kirish, "bobs": bobs, "xulosa": xulosa, "adabiyotlar": adabiyotlar}

    # ===== NAZORATCHI: to'liqlikni tekshirish va bo'sh qolgan qismlarni tuzatish =====
    complete = await _ensure_complete(
        topic, sections, target_words, kirish_instruction, xulosa_instruction, _status
    )

    if _total_words(sections) < MIN_ACCEPTABLE_WORDS:
        logger.error(
            f"Kurs ishi generatsiyasi deyarli bo'sh natija berdi ('{topic}') — "
            "AI provayderlar ishlamagan bo'lishi mumkin."
        )
        return None

    if not complete:
        logger.error(
            f"Kurs ishining ba'zi bo'limlari {MAX_COMPLETENESS_ROUNDS} marta urinishdan "
            f"keyin ham to'ldirilmadi ('{topic}')."
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
        addition = await _ask_retry(
            (
                f"'{topic}' mavzusidagi kurs ishining \"{shortest['title']}\" bobiga "
                f"yangi qo'shimcha kichik qism yozing. FAQAT quyidagi yangi jihatga e'tibor "
                f"bering: {angle}. Avvalgi matnda aytilgan fikrlarni HECH QANDAY shaklda "
                "takrorlamang — faqat yangi, qo'shimcha ma'lumot yozing (kamida 400 so'z)."
            ),
            _COURSE_SYSTEM.format(topic=topic),
            attempts=2,
        )
        if not addition:
            break
        shortest["content"] = shortest["content"].rstrip() + "\n\n" + addition.strip()

        pdf_buf = build_course_work_pdf(topic, sections)
        actual_pages = count_pdf_pages(pdf_buf)

    return sections, pdf_buf, actual_pages


def _bob_content(bob: dict) -> str:
    return "\n\n".join(f"{s['heading']}\n{s['content']}".strip() for s in bob["subsections"])


async def _generate_one_subsection(topic: str, heading: str, sub_title: str, target_words: int) -> str:
    content = await _ask_retry(
        f"[{heading}]\nKurs ishining \"{sub_title}\" nomli kichik bo'limini yoz.\n\n"
        f"Taxminan {max(target_words, 150)} so'zdan iborat bo'lsin.",
        _COURSE_SYSTEM.format(topic=topic),
    ) or ""
    content = _strip_duplicate_heading(content, sub_title)

    fill_rounds = 0
    while len(content.split()) < target_words and fill_rounds < MAX_SUBSECTION_FILL_ROUNDS:
        angle = _EXPAND_ANGLES[fill_rounds % len(_EXPAND_ANGLES)]
        fill_rounds += 1
        addition = await _ask_retry(
            (
                f"'{topic}' mavzusidagi \"{sub_title}\" nomli bo'limga yangi abzas(lar) "
                f"qo'shing. Bu safar FAQAT quyidagi yangi jihatga e'tibor bering: {angle}. "
                "Avvalgi matnda aytilgan fikrlarni HECH QANDAY shaklda takrorlamang — "
                "faqat yangi, qo'shimcha ma'lumot yozing. Bo'lim sarlavhasini qaytarmang."
            ),
            _COURSE_SYSTEM.format(topic=topic),
            attempts=2,
        )
        if not addition:
            break
        addition = _strip_duplicate_heading(addition, sub_title)
        content = content.rstrip() + "\n\n" + addition.strip()

    return content


async def _generate_bob(topic: str, bob_num: int, bolimlari: list, bob_target_words: int, status_cb) -> list:
    """Bobning har bir kichik bo'limini ALOHIDA generatsiya qiladi va har birini
    o'ziga ajratilgan hajmga (taxminan 2.5-3 bet) yetguncha to'ldiradi."""
    n = max(len(bolimlari), 1)
    per_sub_words = max(int(bob_target_words / n), 850)  # ~2.2+ bet minimal

    subsections = []
    for j, sub_title in enumerate(bolimlari, start=1):
        heading = f"{bob_num}.{j}. {sub_title}"
        await status_cb(f"⏳ *{topic}* — {heading} yozilmoqda...")
        content = await _generate_one_subsection(topic, heading, sub_title, per_sub_words)
        subsections.append({"heading": heading, "content": content})

    return subsections


async def _ensure_complete(topic, sections, target_words, kirish_instruction, xulosa_instruction, status_cb) -> bool:
    """NAZORATCHI: har bir bo'limni alohida tekshiradi (kirish, har bir kichik
    bo'lim, xulosa, adabiyotlar). Bo'sh yoki juda qisqa qolgan qismlarni FAQAT
    o'zini qayta yozdiradi (butun hujjatni emas). MAX_COMPLETENESS_ROUNDS marta
    takrorlanadi. Qaytaradi: hammasi to'liqmi (True/False)."""
    for attempt in range(1, MAX_COMPLETENESS_ROUNDS + 1):
        problems = _find_incomplete(sections)
        if not problems:
            return True

        await status_cb(
            f"⏳ *{topic}* — {len(problems)} ta bo'lim to'liq emas, tuzatilmoqda "
            f"({attempt}-tekshiruv)..."
        )
        logger.warning(f"Kurs ishi '{topic}': {len(problems)} ta bo'lim to'liq emas ({attempt}-tekshiruv).")

        for p in problems:
            if p["type"] == "kirish":
                new_val = await _generate_section(topic, "KIRISH", kirish_instruction, int(target_words * SHARE_KIRISH))
                if new_val and len(new_val.split()) >= MIN_SECTION_WORDS:
                    sections["kirish"] = new_val

            elif p["type"] == "xulosa":
                new_val = await _generate_section(topic, "XULOSA", xulosa_instruction, int(target_words * SHARE_XULOSA))
                if new_val and len(new_val.split()) >= MIN_SECTION_WORDS:
                    sections["xulosa"] = new_val

            elif p["type"] == "adabiyotlar":
                new_val = await _generate_references(topic)
                if new_val and len(new_val.strip()) >= MIN_REFERENCES_CHARS:
                    sections["adabiyotlar"] = new_val

            elif p["type"] == "subsection":
                bob = sections["bobs"][p["bob_index"]]
                sub = bob["subsections"][p["sub_index"]]
                sub_title = re.sub(r"^\d+\.\d+\.\s*", "", sub["heading"])
                new_content = await _generate_one_subsection(
                    topic, sub["heading"], sub_title, MIN_SUBSECTION_WORDS + 500
                )
                if new_content and len(new_content.split()) >= MIN_SUBSECTION_WORDS:
                    sub["content"] = new_content
                bob["content"] = _bob_content(bob)

    return not _find_incomplete(sections)


def _find_incomplete(sections: dict) -> list:
    problems = []
    if len(sections.get("kirish", "").split()) < MIN_SECTION_WORDS:
        problems.append({"type": "kirish"})
    for bi, bob in enumerate(sections.get("bobs", [])):
        for si, sub in enumerate(bob["subsections"]):
            if len(sub["content"].split()) < MIN_SUBSECTION_WORDS:
                problems.append({"type": "subsection", "bob_index": bi, "sub_index": si})
    if len(sections.get("xulosa", "").split()) < MIN_SECTION_WORDS:
        problems.append({"type": "xulosa"})
    if len(sections.get("adabiyotlar", "").strip()) < MIN_REFERENCES_CHARS:
        problems.append({"type": "adabiyotlar"})
    return problems


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
    raw = await _ask_retry(prompt, system, attempts=2, raw=True)
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
    result = await _ask_retry(prompt, _COURSE_SYSTEM.format(topic=topic))
    return _strip_duplicate_heading(result, section_label) if result else result


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
    result = await _ask_retry(prompt, system)
    return result or ""


def _total_words(sections: dict) -> int:
    n = len(sections.get("kirish", "").split()) + len(sections.get("xulosa", "").split())
    for b in sections.get("bobs", []):
        n += len(b["content"].split())
    return n
