"""
🔍 INLINE REJIM — foydalanuvchi istalgan chatda (hatto botni a'zo qilmasdan,
hatto ikki oddiy foydalanuvchi orasidagi shaxsiy chatda ham) "@BotUsername
savol" deb yozib, botning javobini o'sha chatga to'g'ridan-to'g'ri yuborishi
uchun handlerlar.

Ikki xil oqim qo'llab-quvvatlanadi:

  A) YENGIL (oddiy savol-javob) — TEZKOR:
     1. `on_inline_query` bitta "matn" natijasi (placeholder) qaytaradi.
     2. Foydalanuvchi tanlaydi -> Telegram placeholder matnni chatga joylaydi.
     3. `on_chosen_inline_result` AI'dan javob olib, matnni yangilaydi
        (`edit_message_text`).

  B) OG'IR (kurs ishi / PDF generatsiyasi) — UZOQ DAVOM ETADI:
     1. `on_inline_query` bitta "HUJJAT" turidagi natija qaytaradi — bu
        vaqtinchalik "⏳ tayyorlanmoqda..." PDF (bot.py'dagi health-server
        orqali xizmat qilinadi). MUHIM: Telegram matn xabarini keyinchalik
        hujjatga aylantirishga ruxsat BERMAYDI, shuning uchun boshidanoq
        "hujjat" turida natija berish SHART.
     2. Foydalanuvchi tanlaydi -> Telegram vaqtinchalik PDF'ni chatga
        joylaydi (jo'natuvchi sifatida so'ragan foydalanuvchi ko'rinadi).
     3. `on_chosen_inline_result` haqiqiy generatsiyani (course_work.py)
        ishga tushiradi — bu bir necha daqiqa davom etishi mumkin. Shu vaqt
        davomida xabarning "izohi" (caption) progress bilan yangilanib
        turadi (`edit_message_caption`, tez-tez emas — Telegram limitiga
        tushib qolmaslik uchun har necha soniyada bir marta).
     4. Generatsiya tugagach, vaqtinchalik PDF haqiqiy tayyor PDF bilan
        BIR MARTA almashtiriladi (`edit_message_media`) — shu bosqichda
        fayl to'g'ridan-to'g'ri (URL orqali emas) yuklanadi.

MUHIM SOZLASH (BotFather orqali, kod bilan bog'liq emas):
  /setinline           -> inline rejimni yoqish (placeholder matn so'raladi)
  /setinlinefeedback    -> "Enabled" qilib qo'yish SHART, aks holda 2-3
                           bosqich (chosen_inline_result) ishlamaydi.

MUHIM SOZLASH (config.py / .env orqali):
  PUBLIC_BASE_URL       -> botning ochiq https manzili (Render URL'i).
                           Bo'sh bo'lsa, OG'IR oqim (B) o'chiriladi va
                           foydalanuvchi botning shaxsiy chatiga yo'naltiriladi
                           — YENGIL oqim (A) baribir to'liq ishlayveradi.
"""

import logging
import re
import time
import uuid

from telegram import (
    Update,
    InlineQueryResultArticle,
    InlineQueryResultDocument,
    InputTextMessageContent,
    InputMediaDocument,
    InputFile,
)
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

from config import UNIVERSAL_CHAT_AI, PUBLIC_BASE_URL
from ai_clients import ask_ai
from handlers import course_work

logger = logging.getLogger(__name__)

PLACEHOLDER_TEXT = "⏳ Javob tayyorlanmoqda..."
BOT_USERNAME = "Student_ai_uz_bot"  # faqat imzo/havola uchun, username o'zgarsa shu yerni yangilang

_MAX_CACHE = 500  # eslab qolinadigan so'rovlar soni chegarasi (xotira toshib ketmasligi uchun)
MIN_CAPTION_EDIT_INTERVAL = 6  # soniya — progress xabarini juda tez-tez tahrirlamaslik uchun

# Savol joy/manzil haqida ekanligini aniqlash uchun kalit so'zlar.
LOCATION_HINTS = re.compile(r"joylash|qayerda|lokatsiy|manzil|xarita|koordinat", re.IGNORECASE)
LOC_TAG_RE = re.compile(r"\[LOC:\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]")

# Kurs ishi so'rovini aniqlash: "kurs ishi ... N bet/varoq/sahifa" shakli.
COURSE_WORK_HINTS = re.compile(r"kurs ishi|kurs loyihasi|kurs proyekti|diplom ishi", re.IGNORECASE)
PAGE_RE = re.compile(r"(\d{1,3})\s*(bet|varoq|sahifa)", re.IGNORECASE)

# Boshqa og'ir (hozircha inline'da qo'llab-quvvatlanmaydigan) vazifalar.
OTHER_HEAVY_HINTS = re.compile(
    r"tarjima qil|pdfni tahrir|pdf ni tahrir|hujjatni tuzat|suratlarni.*pdf|qollanma tayyorla",
    re.IGNORECASE,
)


# ============================================================
# 1-BOSQICH: inline so'rovga TEZKOR javob
# ============================================================

async def on_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    if not query:
        return  # foydalanuvchi hali hech narsa yozmagan

    cache = context.bot_data.setdefault("inline_queries", {})
    _trim_cache(cache)

    # ---- OG'IR: kurs ishi (agar PUBLIC_BASE_URL sozlangan bo'lsa) ----
    if COURSE_WORK_HINTS.search(query):
        m = PAGE_RE.search(query)
        if m and PUBLIC_BASE_URL:
            pages = int(m.group(1))
            topic = course_work.clean_topic((query[: m.start()] + query[m.end():]).strip())
            if topic:
                result_id = str(uuid.uuid4())
                cache[result_id] = {"type": "course_work", "topic": topic, "pages": pages}
                results = [
                    InlineQueryResultDocument(
                        id=result_id,
                        title=f"📄 Kurs ishi: {topic}",
                        description=f"{pages}+ bet — bosing, generatsiya boshlanadi (bir necha daqiqa)",
                        document_url=f"{PUBLIC_BASE_URL}/placeholder.pdf",
                        mime_type="application/pdf",
                        caption=(
                            f"⏳ *{topic}*\n{pages}+ betlik kurs ishi tayyorlanmoqda... "
                            "Bu bir necha daqiqa davom etishi mumkin, iltimos kuting."
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                ]
                await update.inline_query.answer(results, cache_time=0, is_personal=True)
                return
        # PUBLIC_BASE_URL sozlanmagan yoki bet soni topilmadi -> shaxsiy chatga yo'naltiramiz
        await _answer_redirect(update, query)
        return

    # ---- Hozircha inline'da qo'llab-quvvatlanmaydigan boshqa og'ir vazifalar ----
    if OTHER_HEAVY_HINTS.search(query):
        await _answer_redirect(update, query)
        return

    # ---- YENGIL: oddiy savol-javob ----
    result_id = str(uuid.uuid4())
    cache[result_id] = {"type": "chat", "query": query}
    results = [
        InlineQueryResultArticle(
            id=result_id,
            title="💬 Talaba AI javobi",
            description=query[:120],
            input_message_content=InputTextMessageContent(PLACEHOLDER_TEXT),
        )
    ]
    await update.inline_query.answer(results, cache_time=0, is_personal=True)


async def _answer_redirect(update: Update, query: str):
    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title="📄 Bu vazifa botning shaxsiy chatida bajariladi",
            description="Bosing va botga o'ting, u yerda davom etadi",
            input_message_content=InputTextMessageContent(
                f"📄 \"{query}\" — bu vazifani bajarish uchun botning shaxsiy chatiga o'ting: "
                f"https://t.me/{BOT_USERNAME}"
            ),
        )
    ]
    await update.inline_query.answer(results, cache_time=0, is_personal=True)


def _trim_cache(cache: dict):
    if len(cache) > _MAX_CACHE:
        for old_key in list(cache.keys())[: len(cache) - _MAX_CACHE]:
            cache.pop(old_key, None)


# ============================================================
# 2-BOSQICH: foydalanuvchi natijani tanlagach
# ============================================================

async def on_chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chosen = update.chosen_inline_result
    inline_message_id = chosen.inline_message_id
    if not inline_message_id:
        logger.warning(
            "chosen_inline_result keldi, lekin inline_message_id yo'q — "
            "BotFather'da /setinlinefeedback 'Enabled' qilinganini tekshiring."
        )
        return

    cache = context.bot_data.get("inline_queries", {})
    entry = cache.pop(chosen.result_id, None)

    if entry and entry.get("type") == "course_work":
        await _handle_course_work(context, inline_message_id, entry["topic"], entry["pages"])
        return

    query = (entry or {}).get("query") or chosen.query
    if not query:
        return
    await _handle_chat(context, inline_message_id, query)


# ---- YENGIL oqim: oddiy savol-javob ----

async def _handle_chat(context: ContextTypes.DEFAULT_TYPE, inline_message_id: str, query: str):
    wants_location = bool(LOCATION_HINTS.search(query))

    system = (
        "Siz do'stona va bilimdon AI yordamchisiz (ismingiz — Dase). O'zbek "
        "tilida (agar savol boshqa tilda bo'lmasa), aniq va Telegram xabari "
        "uchun mos qisqalikda (taxminan 3-8 gap) javob bering."
    )
    if wants_location:
        system += (
            " Savolda aniq geografik joy so'ralgan bo'lsa, javob oxiriga ANIQ "
            "shu formatda koordinata qo'shing: [LOC: kenglik,uzunlik] "
            "(masalan [LOC: 41.311081,69.240562]). Bu texnik belgi — uni matn "
            "ichida boshqa joyda ishlatmang, faqat eng oxiriga bir marta qo'shing."
        )

    answer = await ask_ai(UNIVERSAL_CHAT_AI, query, system)

    if not answer:
        await _safe_edit_text(
            context, inline_message_id,
            f"❌ Javob berib bo'lmadi. Botning shaxsiy chatida qayta urinib ko'ring: https://t.me/{BOT_USERNAME}",
        )
        return

    maps_line = ""
    m = LOC_TAG_RE.search(answer)
    if m:
        lat, lon = m.group(1), m.group(2)
        answer = LOC_TAG_RE.sub("", answer).strip()
        maps_line = f"\n\n📍 [Xaritada ko'rish](https://www.google.com/maps?q={lat},{lon})"

    final_text = f"{answer}{maps_line}\n\n🤖 _Talaba AI — @{BOT_USERNAME}_"
    await _safe_edit_text(context, inline_message_id, final_text, parse_mode=ParseMode.MARKDOWN)


async def _safe_edit_text(context, inline_message_id, text, parse_mode=None):
    try:
        await context.bot.edit_message_text(inline_message_id=inline_message_id, text=text, parse_mode=parse_mode)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        logger.error(f"Inline matnni tahrirlashda xato: {e}")
        if parse_mode:
            try:
                await context.bot.edit_message_text(inline_message_id=inline_message_id, text=text)
            except Exception as e2:
                logger.error(f"Inline matnni tahrirlashda takroriy xato: {e2}")
    except Exception as e:
        logger.error(f"Inline matnni tahrirlashda xato: {e}")


# ---- OG'IR oqim: kurs ishi / PDF ----

class _InlineCaptionStatus:
    """course_work.generate_course_work() ga 'status_msg' sifatida beriladi —
    Telegram Message obyektiga o'xshab '.edit_text(text, parse_mode=...)'
    metodiga ega, lekin ichida haqiqatda xabarning CAPTION'ini (hujjat hali
    almashtirilmagan, faqat izohi yangilanadi) tahrirlaydi. Telegram edit
    limitiga tushib qolmaslik uchun tez-tez chaqirilgan yangilanishlarni
    o'zi siqib (throttling) qoldiradi."""

    def __init__(self, bot, inline_message_id: str):
        self._bot = bot
        self._id = inline_message_id
        self._last_edit = 0.0

    async def edit_text(self, text: str, parse_mode=None):
        now = time.monotonic()
        if now - self._last_edit < MIN_CAPTION_EDIT_INTERVAL:
            return  # juda tez-tez — bu yangilanishni o'tkazib yuboramiz
        self._last_edit = now
        try:
            await self._bot.edit_message_caption(
                inline_message_id=self._id, caption=text[:1024], parse_mode=parse_mode
            )
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                logger.warning(f"Inline caption tahrirlashda xato (e'tiborsiz qoldirildi): {e}")
        except Exception as e:
            logger.warning(f"Inline caption tahrirlashda xato (e'tiborsiz qoldirildi): {e}")


async def _handle_course_work(context: ContextTypes.DEFAULT_TYPE, inline_message_id: str, topic: str, pages: int):
    status_proxy = _InlineCaptionStatus(context.bot, inline_message_id)

    try:
        result = await course_work.generate_course_work(topic, pages, status_proxy)
    except Exception as e:
        logger.error(f"Inline kurs ishi generatsiyasida xato ('{topic}'): {e}", exc_info=True)
        result = None

    if not result:
        try:
            await context.bot.edit_message_caption(
                inline_message_id=inline_message_id,
                caption=(
                    f"❌ \"{topic}\" uchun kurs ishini yaratib bo'lmadi — AI xizmatlari hozir "
                    f"javob bermayapti. Botning shaxsiy chatida qayta urinib ko'ring: "
                    f"https://t.me/{BOT_USERNAME}"
                ),
            )
        except Exception as e:
            logger.error(f"Xato xabarini yozishda muammo: {e}")
        return

    sections, pdf_buf, actual_pages = result
    pdf_buf.seek(0)

    try:
        await context.bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaDocument(
                media=InputFile(pdf_buf, filename=f"{topic[:40]}.pdf"),
                caption=(
                    f"📄 {topic}\n📎 {actual_pages} bet (so'ralgan: {pages}+)\n"
                    f"✅ Titul, mundarija, kirish, 3 bob, xulosa va adabiyotlar ro'yxati bilan.\n\n"
                    f"🤖 Talaba AI — @{BOT_USERNAME}"
                ),
            ),
        )
    except Exception as e:
        logger.error(f"Tayyor PDF'ni inline xabarga joylashda xato ('{topic}'): {e}", exc_info=True)
        try:
            await context.bot.edit_message_caption(
                inline_message_id=inline_message_id,
                caption=(
                    f"✅ \"{topic}\" kurs ishi tayyor bo'ldi, lekin uni shu yerga joylashda "
                    f"xatolik yuz berdi. Botning shaxsiy chatida qayta urinib ko'ring: "
                    f"https://t.me/{BOT_USERNAME}"
                ),
            )
        except Exception:
            pass
