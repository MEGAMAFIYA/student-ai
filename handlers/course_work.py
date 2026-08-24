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

MAX_SUBSECTION_FILL_ROUNDS = 4   # bitta kichik bo'limni to'ldirish uchun maksimal urinish
MAX_PDF_EXPAND_ROUNDS = 30       # yakuniy PDF sahifa sonini yetkazish uchun maksimal urinish
MIN_ACCEPTABLE_WORDS = 60        # shundan kam so'z yozilsa — AI umuman ishlamagan deb hisoblanadi

# ===== NAZORATCHI (to'liqlik tekshiruvi) sozlamalari =====
MIN_SECTION_WORDS = 40           # kirish/xulosa "bo'sh emas" deb hisoblanishi uchun minimal so'z
MIN_SUBSECTION_WORDS = 40        # har bir kichik bo'lim uchun minimal so'z
MIN_REFERENCES_CHARS = 50        # adabiyotlar ro'yxati "bo'sh emas" deb hisoblanishi uchun
MAX_COMPLETENESS_ROUNDS = 3      # to'liqlikni tekshirish-tuzatish tsikli necha marta takrorlanadi

# ===== YAKUNIY MUHARRIRLIK (ziddiyat/til/raqamlash tuzatish) sozlamalari =====
MAX_HARMONIZE_WORDS = 4000       # shundan uzun bo'lim muharrirlik bosqichida o'tkazib yuboriladi

# RETRY_ATTEMPTS pasaytirildi (3 -> 2): endi /developer > 🔑 AI kalitlari
# orqali qo'shilgan har bir provider (gemini/groq) BIR NECHTA kalitdan
# iborat bo'lishi mumkin — ai_clients.ask_ai() BITTA _ask_retry urinishining
# o'zida shu kalitlarning HAMMASINI birin-ketin sinab chiqadi. Ya'ni asosiy
# "qayta urinish" ishi endi kalitlar darajasida bajariladi, shuning uchun
# tashqi RETRY_ATTEMPTS'ni yuqori tutish keraksiz so'rovlar sonini
# ko'paytirib, bepul limitni tezroq tugatib qo'yardi.
RETRY_ATTEMPTS = 2               # bitta AI so'rovi necha marta qayta urinilishi
RETRY_DELAY_SEC = 3              # urinishlar orasidagi kutish (soniya)
OVERALL_TIMEOUT_SEC = 25 * 60    # butun kurs ishi generatsiyasi uchun yakuniy xavfsizlik chegarasi

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
    "undan chetga chiqmasdan yozing. FAQAT va FAQAT toza, adabiy oʻzbek tilida yozing — "
    "boshqa hech qanday tildan (rus, ozarbayjon, turk, qoraqalpoq va h.k.) hatto bitta "
    "soʻz yoki soʻz shakli ham aralashtirmang; agar biror atama oʻzbek tilida notanish "
    "boʻlsa, uni oʻzbekchalashtirib yoki tushuntirib yozing, xorijiy shaklda qoldirmang. "
    "Ilmiy-akademik uslubda (uchinchi shaxsda, shaxs olmoshlarisiz) yozing. Faqat "
    "soʻralgan boʻlim matnini yozing, boshqa izoh, sarlavha yoki tushuntirish qoʻshmang "
    "— sarlavhani alohida qo'shmang, chunki u allaqachon hujjatda mavjud. MATN ICHIDA "
    "BOʻLIMLARNI OʻZINGIZ SOʻZ BILAN TARTIB RAQAMLAMANG (masalan 'Birinchi vazifa', "
    "'Ikkinchi bosqich', 'Sakkizinchi bo'lim' kabi iboralarni ishlatmang) — raqamlash "
    "allaqachon sarlavhalarda mavjud, matn ichida bunga hojat yo'q. MUHIM: bir xil fikr "
    "yoki jumlani turli soʻzlar bilan qayta-qayta takrorlamang — har bir abzas albatta "
    "yangi, aniq maʼlumot, misol yoki dalil olib kelsin. Umumiy va mavhum gaplar oʻrniga "
    "aniq faktlar, raqamlar, holatlar keltirish tavsiya etiladi, LEKIN: har bir abzasda "
    "majburiy ravishda raqam ixtiro qilishga urinmang — agar aniq son bilmasangiz, "
    "umumiy tavsif bilan cheklaning, notoʻgʻri yoki oʻylab topilgan haddan tashqari "
    "\"aniq\" statistikani (masalan soxta foiz yoki oʻlchov) keltirmang. Agar bir xil "
    "texnik koʻrsatkich (masalan stol balandligi, yoritish darajasi, monitor masofasi) "
    "haqida bir necha marta yozsangiz, HAR SAFAR BIR XIL QIYMATNI ishlating — turli "
    "joyda turlicha raqam bermang. FORMATLASH: hech qanday Markdown belgisi "
    "(**, ##, `, -) yoki LaTeX/matematik formula yozuvi (backslash, jingalak qavslar, "
    "^, _) ishlatmang — "
    "formulalarni oddiy matn ko'rinishida yozing (masalan 'EI = 0.35 x Pfiz + 0.25 x "
    "Pbio'). Faqat oddiy, sodda matn abzaslari yozing."
)

_HARMONIZE_SYSTEM = (
    "Siz ilmiy muharrirsiz. Sizga tayyor matn beriladi. Vazifangiz matnni qayta yozish "
    "EMAS, balki quyidagi aniq muammolarni tuzatishdan iborat: "
    "(1) xuddi shu texnik ko'rsatkich yoki statistik ma'lumot (masalan, stol balandligi, "
    "yoritish darajasi, monitor masofasi, foiz ko'rsatkichlari) matn ichida turli joyda "
    "bir-biriga zid raqamlar bilan berilgan bo'lsa — barchasini bitta izchil, mantiqan "
    "to'g'ri qiymatga moslashtiring (fizik jihatdan mumkin bo'lmagan qiymatlarni ham "
    "(masalan, stol balandligi bir necha santimetr yoki millimetr sifatida ko'rsatilgan "
    "bo'lsa) mantiqiy qiymatga tuzating); "
    "(2) o'zbek tiliga xos bo'lmagan tasodifiy so'z yoki harflar ketma-ketligini to'g'ri "
    "o'zbekcha so'zga almashtiring; "
    "(3) matnda so'z bilan o'z-o'zidan tartib raqamlash (masalan 'Birinchi vazifa', "
    "'Sakkizinchi bo'lim' kabi) noto'g'ri takrorlangan yoki xato ketma-ketlikda bo'lsa, "
    "to'g'rilang yoki bunday iboralarni butunlay olib tashlang; "
    "(4) qolган Markdown yoki LaTeX belgilarini olib tashlang. "
    "Boshqa hech narsani o'zgartirmang — matn mazmuni, misollar, uzunligi va sarlavhalar "
    "deyarli bir xil qolishi shart, faqat yuqoridagi aniq xatolarni tuzating. Faqat "
    "tuzatilgan to'liq matnni qaytaring, hech qanday izoh yoki tushuntirish yozmang."
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


# ===== FAKT REESTRI: bo'limlar bir-birining raqamlariga zid bo'lmasligi uchun =====
# Har bir generatsiya qilingan bo'limdan raqam/o'lchov ichida bo'lgan gaplar ajratib
# olinib, keyingi bo'lim so'rovlariga "avval aytilgan" kontekst sifatida qo'shiladi.
_FACT_SENTENCE_RE = re.compile(r"[^.\n]*\d[^.\n]*\.")
MAX_FACTS_IN_REGISTRY = 60
MAX_FACTS_IN_PROMPT = 25


def _extract_facts(text: str, limit: int = 5) -> list[str]:
    if not text:
        return []
    out = []
    for s in _FACT_SENTENCE_RE.findall(text):
        s = " ".join(s.split()).strip()
        if 20 <= len(s) <= 220:
            out.append(s)
    return out[:limit]


def _register_facts(registry: list[str], text: str) -> None:
    for f in _extract_facts(text):
        if f not in registry:
            registry.append(f)
    del registry[:-MAX_FACTS_IN_REGISTRY]  # eng eski faktlarni siqib chiqarish


def _facts_block(registry: list[str]) -> str:
    if not registry:
        return ""
    items = registry[-MAX_FACTS_IN_PROMPT:]
    bullet_list = "\n".join(f"- {f}" for f in items)
    return (
        "\n\nMUHIM — HUJJATDA AVVAL YOZILGAN ASOSIY RAQAM VA KOʻRSATKICHLAR "
        "(bular bilan ZID kelmang; agar shu turdagi parametrni qayta tilga olsangiz, "
        "aynan shu qiymatlardan foydalaning, yangi/boshqa raqam o'ylab topmang):\n"
        f"{bullet_list}"
    )


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
    user = update.effective_user
    logger.info(f"📘 'Kurs ishi / loyiha' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        context.user_data["flow"] = "course_work"
        await query.edit_message_text(
            "📘 *Kurs ishi / loyiha*\n\n"
            "PDF necha betdan iborat bo'lishi kerak? (masalan: 10, 20, 30)\n"
            "Belgilagan bet sonidan kam bo'lmaydi (ko'proq chiqishi mumkin).",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"📘 Kurs ishi menyusini ochishda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise
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
    user = update.effective_user
    logger.info(f"📘 Kurs ishi so'rovi: user_id={user.id if user else '?'}, mavzu='{topic}', bet={pages}.")

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
    try:
        result = await asyncio.wait_for(generate_course_work(topic, pages, status), timeout=OVERALL_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        logger.error(f"Kurs ishi generatsiyasi {OVERALL_TIMEOUT_SEC}s ichida tugamadi ('{topic}').")
        result = None
    except Exception as e:
        logger.error(f"Kurs ishi generatsiyasida kutilmagan xato ('{topic}'): {type(e).__name__}: {e}", exc_info=True)
        result = None

    if not result:
        logger.error(f"📘 Kurs ishi YAKUNLANMADI ('{topic}') — sababi yuqoridagi [REJA]/[BOB]/[NAZORATCHI] loglarida.")
        await status.edit_text(
            "❌ Kurs ishini yaratib bo'lmadi — AI xizmatlari hozir javob bermayapti "
            "yoki ba'zi bo'limlarni bir necha urinishdan keyin ham to'liq yoza olmadi. "
            "Birozdan so'ng qayta urinib ko'ring."
        )
        return

    sections, pdf_buf, actual_pages = result
    logger.info(f"📘 Kurs ishi muvaffaqiyatli yakunlandi: '{topic}', {actual_pages} bet.")

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


async def _ask_retry(prompt: str, system: str, attempts: int = RETRY_ATTEMPTS, delay: int = RETRY_DELAY_SEC, raw: bool = False, tag: str = "") -> str | None:
    """ask_ai ni bir necha marta qayta urinib chaqiradi — vaqtinchalik limit/tarmoq
    xatolarida bitta muvaffaqiyatsiz urinish butun bo'limni bo'sh qoldirmasligi uchun.
    AI rad javobi (masalan "I'm sorry, I can't fulfill...") aniqlansa ham qayta
    uriniladi — bunday javob hech qachon hujjatga kiritilmaydi. raw=True bo'lsa
    (masalan JSON javoblarda) Markdown/LaTeX tozalash qo'llanilmaydi.

    `tag` — logda ko'rinadigan yorliq (masalan "1.2-bo'lim", "KIRISH"), shu
    orqali log faylida AYNAN qaysi bo'lim so'rovi ketayotgani ko'rinadi."""
    label = tag or "so'rov"
    for i in range(1, attempts + 1):
        logger.info(f"[{label}] AI ga so'rov yuborilmoqda ({i}/{attempts}-urinish, provider={COURSE_WORK_AI.get('provider')}, model={COURSE_WORK_AI.get('model')})...")
        try:
            result = await ask_ai(COURSE_WORK_AI, prompt, system)
        except Exception as e:
            logger.error(f"[{label}] ask_ai chaqiruvida kutilmagan xato ({i}/{attempts}): {e}", exc_info=True)
            result = None

        if result and result.strip() and not _is_refusal(result):
            logger.info(f"[{label}] ✅ Javob qabul qilindi ({i}/{attempts}-urinish, {len(result.split())} so'z).")
            return result if raw else _clean_ai_text(result)

        if not result or not result.strip():
            logger.warning(f"[{label}] ⚠️ Bo'sh javob qaytdi ({i}/{attempts}-urinish).")
        else:
            logger.warning(f"[{label}] ⚠️ AI rad javobi aniqlandi ({i}/{attempts}-urinish): {result[:150]!r}")

        if i < attempts:
            logger.info(f"[{label}] {delay}s kutib, qayta uriniladi...")
            await asyncio.sleep(delay)

    logger.error(f"[{label}] ❌ {attempts} marta urinishdan keyin ham javob olinmadi — bo'lim bo'sh qoladi.")
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
    logger.info(f"===== [KURS ISHI BOSHLANDI] mavzu='{topic}', bet={pages}, maqsad so'z={target_words} =====")

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

    # Butun hujjat davomida to'ldiriladigan umumiy "fakt reestri" — har bir
    # yangi bo'lim avvalgi bo'limlarda qanday raqam/o'lchov aytilganini bilib,
    # ularga zid bo'lmasdan yozadi.
    facts_registry: list[str] = []

    await _status(f"⏳ *{topic}* — kirish yozilmoqda...")
    kirish = await _generate_section(
        topic, "KIRISH", kirish_instruction, int(target_words * SHARE_KIRISH), facts_registry
    ) or ""

    bobs = []
    for i in (1, 2, 3):
        bob_nomi = plan.get(f"bob{i}_nomi") or DEFAULT_PLAN[f"bob{i}_nomi"]
        bolimlari = plan.get(f"bob{i}_bolimlari") or DEFAULT_PLAN[f"bob{i}_bolimlari"]
        bob_target_words = int(target_words * SHARE_BOB)

        logger.info(f"[{_ROMAN[i]}-BOB] '{bob_nomi}' — {len(bolimlari)} ta kichik bo'lim generatsiyasi boshlandi.")
        subsections = await _generate_bob(topic, i, bolimlari, bob_target_words, _status, facts_registry)
        bob = {"title": f"{_ROMAN[i]}-BOB. {bob_nomi.upper()}", "subsections": subsections}
        bob["content"] = _bob_content(bob)
        bobs.append(bob)
        logger.info(f"[{_ROMAN[i]}-BOB] Yakunlandi: {len(bob['content'].split())} so'z.")

    await _status(f"⏳ *{topic}* — xulosa yozilmoqda...")
    xulosa = await _generate_section(
        topic, "XULOSA", xulosa_instruction, int(target_words * SHARE_XULOSA), facts_registry
    ) or ""

    await _status(f"⏳ *{topic}* — adabiyotlar ro'yxati tuzilmoqda...")
    adabiyotlar = await _generate_references(topic) or ""

    sections = {"kirish": kirish, "bobs": bobs, "xulosa": xulosa, "adabiyotlar": adabiyotlar}
    logger.info(f"[NAZORATCHI] Boshlang'ich generatsiya tugadi, jami {_total_words(sections)} so'z. To'liqlik tekshiruvi boshlanmoqda...")

    # ===== NAZORATCHI: to'liqlikni tekshirish va bo'sh qolgan qismlarni tuzatish =====
    complete = await _ensure_complete(
        topic, sections, target_words, kirish_instruction, xulosa_instruction, _status, facts_registry
    )

    if _total_words(sections) < MIN_ACCEPTABLE_WORDS:
        logger.error(
            f"[YAKUNIY XATO] Kurs ishi generatsiyasi deyarli bo'sh natija berdi ('{topic}', "
            f"jami {_total_words(sections)} so'z, minimal {MIN_ACCEPTABLE_WORDS}) — "
            "AI provayderlar (Gemini/Groq/Pollinations) ishlamagan bo'lishi mumkin. "
            "Yuqoridagi [KIRISH]/[1.1...]/[XULOSA] loglaridan aynan qaysi bo'lim va "
            "qaysi provider xato bergani ko'rinadi."
        )
        return None

    if not complete:
        remaining = _find_incomplete(sections)
        logger.error(
            f"[YAKUNIY XATO] Kurs ishining {len(remaining)} ta bo'limi {MAX_COMPLETENESS_ROUNDS} "
            f"marta urinishdan keyin ham to'ldirilmadi ('{topic}'): {remaining}"
        )
        return None

    logger.info(f"[NAZORATCHI] ✅ Barcha bo'limlar to'liq. Muharrirlik bosqichiga o'tilmoqda.")

    # ===== YAKUNIY MUHARRIRLIK: ziddiyatli raqamlar, begona so'zlar, xato =====
    # ===== o'z-o'zidan tartib raqamlash (masalan "Sakkizinchi bo'lim" ikki  =====
    # ===== marta) shu bosqichda tuzatiladi — mazmun deyarli o'zgarmaydi.   =====
    await _status(f"⏳ *{topic}* — matn ziddiyatlari va xatolari tekshirilmoqda...")
    sections["kirish"] = await _harmonize_text(topic, sections["kirish"])
    for bob in sections["bobs"]:
        bob["content"] = await _harmonize_text(topic, bob["content"])
    sections["xulosa"] = await _harmonize_text(topic, sections["xulosa"])

    # ===== HAQIQIY PDF SAHIFA SONIGA QARAB KENGAYTIRISH =====
    # PDF qurish (reportlab, ko'p bosqichli TOC) va sahifa sanash CPU-bog'liq
    # va sekin (ayniqsa 100+ betlik hujjatlarda) — asyncio.to_thread() orqali
    # alohida oqimda bajariladi, shu orqali BOSHQA FOYDALANUVCHILARNING
    # so'rovlari shu vaqtda bloklanib qolmaydi.
    logger.info("[PDF] Birinchi versiya qurilmoqda...")
    pdf_buf = await asyncio.to_thread(build_course_work_pdf, topic, sections)
    actual_pages = await asyncio.to_thread(count_pdf_pages, pdf_buf)
    logger.info(f"[PDF] Birinchi versiya: {actual_pages} bet (so'ralgan: {pages}).")

    rounds = 0
    while actual_pages < pages and rounds < MAX_PDF_EXPAND_ROUNDS:
        angle = _EXPAND_ANGLES[rounds % len(_EXPAND_ANGLES)]
        rounds += 1
        await _status(
            f"⏳ *{topic}* — hajm kengaytirilmoqda ({actual_pages}/{pages} bet, "
            f"{rounds}-urinish)..."
        )
        shortest = min(sections["bobs"], key=lambda b: len(b["content"].split()))
        logger.info(f"[PDF-KENGAYTIRISH {rounds}] Eng qisqa bob: '{shortest['title']}' — yo'nalish: {angle}.")
        addition = await _ask_retry(
            (
                f"'{topic}' mavzusidagi kurs ishining \"{shortest['title']}\" bobiga "
                f"yangi qo'shimcha kichik qism yozing. FAQAT quyidagi yangi jihatga e'tibor "
                f"bering: {angle}. Avvalgi matnda aytilgan fikrlarni HECH QANDAY shaklda "
                "takrorlamang — faqat yangi, qo'shimcha ma'lumot yozing (kamida 400 so'z)."
            )
            + _facts_block(facts_registry),
            _COURSE_SYSTEM.format(topic=topic),
            attempts=2,
            tag=f"PDF-KENGAYTIRISH {rounds}",
        )
        if not addition:
            logger.warning(f"[PDF-KENGAYTIRISH {rounds}] Javob olinmadi — kengaytirish {actual_pages} betda to'xtatildi.")
            break
        _register_facts(facts_registry, addition)
        shortest["content"] = shortest["content"].rstrip() + "\n\n" + addition.strip()

        pdf_buf = await asyncio.to_thread(build_course_work_pdf, topic, sections)
        actual_pages = await asyncio.to_thread(count_pdf_pages, pdf_buf)
        logger.info(f"[PDF-KENGAYTIRISH {rounds}] Yangi hajm: {actual_pages}/{pages} bet.")

    logger.info(f"===== [KURS ISHI TUGADI] mavzu='{topic}', yakuniy hajm={actual_pages} bet =====")
    return sections, pdf_buf, actual_pages


def _bob_content(bob: dict) -> str:
    return "\n\n".join(f"{s['heading']}\n{s['content']}".strip() for s in bob["subsections"])


async def _generate_one_subsection(
    topic: str, heading: str, sub_title: str, target_words: int, facts: list | None = None
) -> str:
    facts = facts if facts is not None else []
    logger.info(f"[{heading}] Generatsiya boshlandi (maqsad: ~{max(target_words, 150)} so'z).")
    content = await _ask_retry(
        f"[{heading}]\nKurs ishining \"{sub_title}\" nomli kichik bo'limini yoz.\n\n"
        f"Taxminan {max(target_words, 150)} so'zdan iborat bo'lsin."
        + _facts_block(facts),
        _COURSE_SYSTEM.format(topic=topic),
        tag=heading,
    ) or ""
    content = _strip_duplicate_heading(content, sub_title)

    if not content:
        logger.error(f"[{heading}] ❌ Boshlang'ich generatsiya muvaffaqiyatsiz — bo'lim bo'sh boshlanadi.")

    fill_rounds = 0
    while len(content.split()) < target_words and fill_rounds < MAX_SUBSECTION_FILL_ROUNDS:
        angle = _EXPAND_ANGLES[fill_rounds % len(_EXPAND_ANGLES)]
        fill_rounds += 1
        logger.info(
            f"[{heading}] Hajm yetarli emas ({len(content.split())}/{target_words} so'z) — "
            f"to'ldirish {fill_rounds}/{MAX_SUBSECTION_FILL_ROUNDS}-urinish (yo'nalish: {angle})..."
        )
        addition = await _ask_retry(
            (
                f"'{topic}' mavzusidagi \"{sub_title}\" nomli bo'limga yangi abzas(lar) "
                f"qo'shing. Bu safar FAQAT quyidagi yangi jihatga e'tibor bering: {angle}. "
                "Avvalgi matnda aytilgan fikrlarni HECH QANDAY shaklda takrorlamang — "
                "faqat yangi, qo'shimcha ma'lumot yozing. Bo'lim sarlavhasini qaytarmang."
            )
            + _facts_block(facts),
            _COURSE_SYSTEM.format(topic=topic),
            attempts=2,
            tag=f"{heading} (to'ldirish {fill_rounds})",
        )
        if not addition:
            logger.warning(f"[{heading}] To'ldirish {fill_rounds}-urinishda javob olinmadi — to'ldirish to'xtatiladi.")
            break
        addition = _strip_duplicate_heading(addition, sub_title)
        content = content.rstrip() + "\n\n" + addition.strip()
        _register_facts(facts, addition)

    logger.info(f"[{heading}] Yakuniy hajm: {len(content.split())} so'z (maqsad: {target_words}).")
    _register_facts(facts, content)
    return content


async def _generate_bob(
    topic: str, bob_num: int, bolimlari: list, bob_target_words: int, status_cb, facts: list | None = None
) -> list:
    """Bobning har bir kichik bo'limini ALOHIDA generatsiya qiladi va har birini
    o'ziga ajratilgan hajmga (taxminan 2.5-3 bet) yetguncha to'ldiradi."""
    facts = facts if facts is not None else []
    n = max(len(bolimlari), 1)
    per_sub_words = max(int(bob_target_words / n), 850)  # ~2.2+ bet minimal

    subsections = []
    for j, sub_title in enumerate(bolimlari, start=1):
        heading = f"{bob_num}.{j}. {sub_title}"
        await status_cb(f"⏳ *{topic}* — {heading} yozilmoqda...")
        content = await _generate_one_subsection(topic, heading, sub_title, per_sub_words, facts)
        subsections.append({"heading": heading, "content": content})

    return subsections


async def _ensure_complete(
    topic, sections, target_words, kirish_instruction, xulosa_instruction, status_cb,
    facts: list | None = None,
) -> bool:
    """NAZORATCHI: har bir bo'limni alohida tekshiradi (kirish, har bir kichik
    bo'lim, xulosa, adabiyotlar). Bo'sh yoki juda qisqa qolgan qismlarni FAQAT
    o'zini qayta yozdiradi (butun hujjatni emas). MAX_COMPLETENESS_ROUNDS marta
    takrorlanadi. Qaytaradi: hammasi to'liqmi (True/False)."""
    facts = facts if facts is not None else []
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
                new_val = await _generate_section(
                    topic, "KIRISH", kirish_instruction, int(target_words * SHARE_KIRISH), facts
                )
                if new_val and len(new_val.split()) >= MIN_SECTION_WORDS:
                    sections["kirish"] = new_val

            elif p["type"] == "xulosa":
                new_val = await _generate_section(
                    topic, "XULOSA", xulosa_instruction, int(target_words * SHARE_XULOSA), facts
                )
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
                    topic, sub["heading"], sub_title, MIN_SUBSECTION_WORDS + 500, facts
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
    logger.info(f"[REJA] '{topic}' mavzusi uchun reja so'ralmoqda...")
    raw = await _ask_retry(prompt, system, attempts=2, raw=True, tag="REJA")
    if not raw:
        logger.warning(f"[REJA] AI javob bermadi — DEFAULT_PLAN (standart reja) ishlatiladi.")
        return DEFAULT_PLAN
    try:
        cleaned = re.sub(r"^```json\s*|^```\s*|```\s*$", "", raw.strip(), flags=re.MULTILINE).strip()
        data = json.loads(cleaned)
        for key, val in DEFAULT_PLAN.items():
            data.setdefault(key, val)
        logger.info(f"[REJA] ✅ Reja muvaffaqiyatli tuzildi: {list(data.keys())}")
        return data
    except Exception as e:
        logger.warning(f"[REJA] JSON parse xato: {e} — xom javob: {raw[:300]!r} — DEFAULT_PLAN ishlatiladi.")
        return DEFAULT_PLAN


async def _generate_section(
    topic: str, section_label: str, instruction: str, target_words: int, facts: list | None = None
) -> str | None:
    facts = facts if facts is not None else []
    prompt = (
        f"[{section_label}]\n{instruction}\n\nTaxminan {max(target_words, 150)} so'zdan iborat bo'lsin."
        + _facts_block(facts)
    )
    logger.info(f"[{section_label}] Generatsiya boshlandi (maqsad: ~{max(target_words, 150)} so'z).")
    result = await _ask_retry(prompt, _COURSE_SYSTEM.format(topic=topic), tag=section_label)
    result = _strip_duplicate_heading(result, section_label) if result else result
    if result:
        _register_facts(facts, result)
        logger.info(f"[{section_label}] Yakunlandi: {len(result.split())} so'z.")
    else:
        logger.error(f"[{section_label}] Generatsiya muvaffaqiyatsiz — bo'lim bo'sh qoladi.")
    return result


async def _harmonize_text(topic: str, text: str) -> str:
    """Yakuniy 'muharrirlik' bosqichi: matnni deyarli o'zgarishsiz qoldirib,
    faqat ziddiyatli raqamlarni, begona so'zlarni va o'z-o'zidan noto'g'ri
    tartib raqamlashni tuzatadi. Juda katta matnlarda (token limitidan
    qochish uchun) o'tkazib yuboriladi."""
    if not text or len(text.split()) > MAX_HARMONIZE_WORDS:
        logger.info(f"[MUHARRIRLIK] O'tkazib yuborildi (bo'sh yoki {MAX_HARMONIZE_WORDS} so'zdan uzun).")
        return text
    logger.info(f"[MUHARRIRLIK] Boshlandi ({len(text.split())} so'zlik matn)...")
    result = await _ask_retry(
        f"MAVZU: '{topic}'\n\nQUYIDAGI MATNNI TUZATING:\n\n{text}",
        _HARMONIZE_SYSTEM,
        attempts=2,
        tag="MUHARRIRLIK",
    )
    # Agar muharrirlik natijasi shubhali darajada qisqarib ketsa (masalan AI
    # xato qilib qisqartirib yuborgan bo'lsa), asl matnni saqlab qolamiz.
    if result and len(result.split()) >= len(text.split()) * 0.7:
        logger.info(f"[MUHARRIRLIK] ✅ Tuzatilgan matn qabul qilindi ({len(result.split())} so'z).")
        return result
    if result:
        logger.warning(f"[MUHARRIRLIK] ⚠️ Natija juda qisqarib ketdi ({len(result.split())} so'z) — asl matn saqlanadi.")
    else:
        logger.warning("[MUHARRIRLIK] ⚠️ AI javob bermadi — asl matn o'zgarishsiz qoladi.")
    return text


async def _generate_references(topic: str) -> str:
    system = (
        "Siz ilmiy adabiyotlar ro'yxati tuzuvchi yordamchisiz. Faqat ro'yxatni "
        "qaytaring, boshqa izoh yozmang. Siz ANIQ qaysi kitob/maqola/sayt "
        "haqiqatda mavjudligini bilmaysiz — shuning uchun ANIQ, tekshirib "
        "bo'lmaydigan URL manzil yoki 'kirilgan sana' o'ylab topib yozish "
        "TAQIQLANADI, chunki bu keyinchalik plagiat/soxtalik tekshiruvida "
        "muammo tug'diradi."
    )
    prompt = (
        f"'{topic}' mavzusidagi kurs ishi uchun FOYDALANILGAN ADABIYOTLAR RO'YXATI tuz. "
        "Kamida 20 ta yozuv bo'lsin, quyidagi 4 toifaga bo'lib, har biri ichida alifbo "
        "tartibida, umumiy uzluksiz raqamlash bilan:\n"
        "I. Qonunlar va me'yoriy-huquqiy hujjatlar (standartlar, sanitariya qoidalari va h.k.)\n"
        "II. Darsliklar, o'quv qo'llanmalar va monografiyalar\n"
        "III. Ilmiy maqolalar va davriy nashrlar\n"
        "IV. Tegishli soha bo'yicha umumiy manbalar\n\n"
        "Har bir yozuvni to'liq bibliografik formatda yoz (muallif, nom, shahar, nashriyot, "
        "yil). IV toifada ANIQ URL manzil (https://...) VA 'kirilgan sana'/'accessed' "
        "yozuvini QO'SHMANG — buning o'rniga faqat tashkilot yoki rasmiy sayt nomini "
        "(masalan: 'ISO tashkilotining rasmiy sayti', 'Xalqaro ergonomika assotsiatsiyasi "
        "portali') va nashr yilini ko'rsating. Faqat ro'yxatni yoz, boshqa izoh berma."
    )
    logger.info("[ADABIYOTLAR] Ro'yxat generatsiyasi boshlandi...")
    result = await _ask_retry(prompt, system, tag="ADABIYOTLAR")
    if result:
        logger.info(f"[ADABIYOTLAR] ✅ Yakunlandi ({len(result)} belgi).")
    else:
        logger.error("[ADABIYOTLAR] ❌ Generatsiya muvaffaqiyatsiz — bo'sh qoladi.")
    return result or ""


def _total_words(sections: dict) -> int:
    n = len(sections.get("kirish", "").split()) + len(sections.get("xulosa", "").split())
    for b in sections.get("bobs", []):
        n += len(b["content"].split())
    return n
