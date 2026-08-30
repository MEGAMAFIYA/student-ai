"""
🔍 INLINE REJIM — foydalanuvchi istalgan chatda (hatto botni a'zo qilmasdan,
hatto ikki oddiy foydalanuvchi orasidagi shaxsiy chatda ham) "@BotUsername
savol" deb yozib, botning javobini o'sha chatga to'g'ridan-to'g'ri yuborishi
uchun handlerlar.

To'rtta xil oqim qo'llab-quvvatlanadi:

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

  C) OG'IR (video/audio yuklab olish — /vid, /qo'shiq) — B bilan BIR XIL
     naqsh, faqat PDF o'rniga video/audio:
     1. "/vid <havola>" — bitta HUJJAT-turidagi placeholder natija.
        Tanlangach, `_handle_vid` video_tools.download_video() bilan
        yuklaydi va `InputMediaVideo` bilan almashtiradi.
     2. "/qo'shiq <so'rov>" — video_tools.search_tracks() orqali topilgan
        HAR BIR qo'shiq uchun ALOHIDA placeholder natija qaytariladi
        (Telegram bularni gorizontal ro'yxat sifatida ko'rsatadi — shu
        orqali foydalanuvchi "qaysi qo'shiqni xohlaysiz" deb TANLAYDI,
        aynan shu tanlov orqali qaysi biri yuklanishi aniqlanadi). Tanlangan
        BITTASI uchun `_handle_qoshiq` yuklab, `InputMediaAudio` bilan
        almashtiradi.
     Ikkalasi ham PUBLIC_BASE_URL talab qiladi (B oqimidagi kabi sabab).

  D) OG'IR (/tabrik) — matn animatsiyasi, A bilan BIR XIL mexanizm (matnni
     matn bilan almashtirish), faqat bitta emas bir nechta bosqichma-bosqich
     `edit_message_text` chaqiruvi bilan (countdown + aylanuvchi doira,
     xuddi handlers/tabrik.py'dagi kabi — mantiq tabrik_logic.py'da umumiy).
     PUBLIC_BASE_URL talab qilinmaydi (fayl emas, faqat matn).

MUHIM SOZLASH (BotFather orqali, kod bilan bog'liq emas):
  /setinline           -> inline rejimni yoqish (placeholder matn so'raladi)
  /setinlinefeedback   -> "Enabled" qilib qo'yish SHART, aks holda 2-3
                           bosqich (chosen_inline_result) ishlamaydi.

MUHIM SOZLASH (config.py / .env orqali):
  PUBLIC_BASE_URL       -> botning ochiq https manzili (Render URL'i).
                           Bo'sh bo'lsa, OG'IR oqimlar (B, C) o'chiriladi va
                           foydalanuvchi botning shaxsiy chatiga yo'naltiriladi
                           — YENGIL oqim (A) va /tabrik (D) baribir to'liq
                           ishlayveradi.
  ffmpeg (serverda)     -> /qo'shiq uchun MP3'ga o'tkazish SHART (video_tools.py).
"""

import asyncio
import logging
import re
import shutil
import tempfile
import time
import uuid

from telegram import (
    Update,
    InlineQueryResultArticle,
    InlineQueryResultDocument,
    InputTextMessageContent,
    InputMediaDocument,
    InputMediaVideo,
    InputMediaAudio,
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

import config
from config import UNIVERSAL_CHAT_AI, PUBLIC_BASE_URL
from ai_clients import ask_ai
from handlers import course_work
import tabrik_logic
import video_tools

logger = logging.getLogger(__name__)

PLACEHOLDER_TEXT = "⏳ Javob tayyorlanmoqda..."
BOT_USERNAME = "Student_ai_uz_bot"  # faqat imzo/havola uchun, username o'zgarsa shu yerni yangilang

_MAX_CACHE = 500  # eslab qolinadigan so'rovlar soni chegarasi (xotira toshib ketmasligi uchun)
MIN_CAPTION_EDIT_INTERVAL = 6  # soniya — progress xabarini juda tez-tez tahrirlamaslik uchun


# ============================================================
# MUHIM:
# Telegram ChosenInlineResult.inline_message_id ni faqat
# inline keyboard biriktirilgan xabar uchun yuboradi.
#
# Shu sababli mavjud tartibni buzmasdan inline natijaga
# bitta tugma qo'shamiz.
# ============================================================

INLINE_MESSAGE_MARKUP = InlineKeyboardMarkup([
    [
        InlineKeyboardButton(
            "🤖 Talaba AI",
            url=f"https://t.me/{BOT_USERNAME}",
        )
    ]
])


# Savol joy/manzil haqida ekanligini aniqlash uchun kalit so'zlar.
LOCATION_HINTS = re.compile(
    r"joylash|qayerda|lokatsiy|manzil|xarita|koordinat",
    re.IGNORECASE
)

LOC_TAG_RE = re.compile(
    r"\[LOC:\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]"
)

# Kurs ishi so'rovini aniqlash: "kurs ishi ... N bet/varoq/sahifa" shakli.
COURSE_WORK_HINTS = re.compile(
    r"kurs ishi|kurs loyihasi|kurs proyekti|diplom ishi",
    re.IGNORECASE
)

PAGE_RE = re.compile(
    r"(\d{1,3})\s*(bet|varoq|sahifa)",
    re.IGNORECASE
)

# Boshqa og'ir (hozircha inline'da qo'llab-quvvatlanmaydigan) vazifalar.
OTHER_HEAVY_HINTS = re.compile(
    r"tarjima qil|pdfni tahrir|pdf ni tahrir|hujjatni tuzat|suratlarni.*pdf|qollanma tayyorla",
    re.IGNORECASE,
)

# ------------------------------------------------------------------
# 🎬🎵🎁 /vid, /qo'shiq, /tabrik — inline matnda ("@Bot ..." qismi Telegram
# tomonidan avtomatik olib tashlanadi, `query`da FAQAT shundan keyingi matn
# keladi) shu buyruqlar bilan boshlanishini aniqlash uchun.
# ------------------------------------------------------------------
VID_BARE_RE = re.compile(r"^/vid(?:@\w+)?\s*$", re.IGNORECASE)
VID_WITH_URL_RE = re.compile(r"^/vid(?:@\w+)?\s+(https?://\S+)", re.IGNORECASE)

# Apostrofning barcha variantlari — handlers/qoshiq.py'dagi bilan bir xil.
QOSHIQ_BARE_RE = re.compile(r"^/(?:qo[`'\u00b4\u2018\u2019\u02bb\u02bc]shiq|qoshiq)(?:@\w+)?\s*$", re.IGNORECASE)
QOSHIQ_WITH_QUERY_RE = re.compile(r"^/(?:qo[`'\u00b4\u2018\u2019\u02bb\u02bc]shiq|qoshiq)(?:@\w+)?\s+(.+)$", re.IGNORECASE)

TABRIK_BARE_RE = re.compile(r"^/tabrik(?:@\w+)?\s*$", re.IGNORECASE)

# /vid, /qo'shiq uchun placeholder — inline "hujjat" natijasi shu manzildan
# xizmat qilinadi (bot.py > HealthHandler), keyin haqiqiy video/audio bilan
# almashtiriladi.
_PLACEHOLDER_TXT_PATH = "/placeholder.txt"


# ============================================================
# 1-BOSQICH: inline so'rovga TEZKOR javob
# ============================================================

async def on_inline_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.inline_query.query.strip()

    if not query:
        return  # foydalanuvchi hali hech narsa yozmagan

    cache = context.bot_data.setdefault("inline_queries", {})
    _trim_cache(cache)

    # --------------------------------------------------------
    # 🎬 OG'IR: /vid
    # --------------------------------------------------------

    if VID_WITH_URL_RE.match(query):
        if not PUBLIC_BASE_URL:
            await _answer_redirect(update, query)
            return

        url = VID_WITH_URL_RE.match(query).group(1)
        result_id = str(uuid.uuid4())
        cache[result_id] = {"type": "vid", "url": url}

        results = [
            InlineQueryResultDocument(
                id=result_id,
                title="🎬 Video yuklab olinadi",
                description=url[:120],
                document_url=f"{PUBLIC_BASE_URL}{_PLACEHOLDER_TXT_PATH}",
                mime_type="text/plain",
                caption=f"⏳ Video yuklab olinmoqda...\n{url}",
                reply_markup=INLINE_MESSAGE_MARKUP,
            )
        ]
        await update.inline_query.answer(results, cache_time=0, is_personal=True)
        return

    if VID_BARE_RE.match(query):
        await _answer_instruction(
            update, "🎬 /vid — video havolasini ham yozing",
            "Masalan: /vid https://www.youtube.com/watch?v=...",
        )
        return

    # --------------------------------------------------------
    # 🎵 OG'IR: /qo'shiq — har bir natija ALOHIDA tanlanadigan qilib
    # qaytariladi (Telegram ularni ro'yxat sifatida ko'rsatadi, foydalanuvchi
    # birini TANLAYDI — aynan shu orqali "qaysi qo'shiqni xohlaysiz" savolига
    # javob beriladi, alohida tugmalar shart emas).
    # --------------------------------------------------------

    if QOSHIQ_WITH_QUERY_RE.match(query):
        if not PUBLIC_BASE_URL:
            await _answer_redirect(update, query)
            return

        search_text = QOSHIQ_WITH_QUERY_RE.match(query).group(1).strip()
        try:
            tracks = await asyncio.to_thread(video_tools.search_tracks, search_text, config.QOSHIQ_SEARCH_COUNT)
        except video_tools.DownloadError as e:
            await _answer_instruction(update, "🎵 Qidiruvda xatolik", str(e))
            return
        except Exception as e:
            logger.error(f"🔍 Inline /qo'shiq qidiruvida kutilmagan xato ('{search_text}'): {type(e).__name__}: {e}", exc_info=True)
            await _answer_instruction(update, "🎵 Qidiruvda xatolik", "Birozdan so'ng qayta urinib ko'ring.")
            return

        results = []
        for t in tracks:
            result_id = str(uuid.uuid4())
            cache[result_id] = {"type": "qoshiq", "url": t["webpage_url"], "title": t["title"], "uploader": t.get("uploader")}
            results.append(
                InlineQueryResultDocument(
                    id=result_id,
                    title=f"{t['source_emoji']} {t['title'][:60]}",
                    description=t.get("uploader") or t["source_label"],
                    document_url=f"{PUBLIC_BASE_URL}{_PLACEHOLDER_TXT_PATH}",
                    mime_type="text/plain",
                    caption=f"⏳ \"{t['title']}\" yuklab olinmoqda...",
                    reply_markup=INLINE_MESSAGE_MARKUP,
                )
            )
        await update.inline_query.answer(results, cache_time=0, is_personal=True)
        return

    if QOSHIQ_BARE_RE.match(query):
        await _answer_instruction(
            update, "🎵 /qo'shiq — ijrochi yoki qo'shiq nomini ham yozing",
            "Masalan: /qo'shiq Ozodbek Nazarbekov",
        )
        return

    # --------------------------------------------------------
    # 🎁 OG'IR (faqat matn): /tabrik
    # --------------------------------------------------------

    if query[:7].lower().startswith("/tabrik") and not TABRIK_BARE_RE.match(query):
        text = tabrik_logic.parse_tabrik_text(query)
        if text:
            result_id = str(uuid.uuid4())
            cache[result_id] = {"type": "tabrik", "text": text}
            results = [
                InlineQueryResultArticle(
                    id=result_id,
                    title="🎁 Tabrik yuborish",
                    description=text[:120],
                    input_message_content=InputTextMessageContent("🎁 Tabrik tayyorlanmoqda..."),
                    reply_markup=INLINE_MESSAGE_MARKUP,
                )
            ]
            await update.inline_query.answer(results, cache_time=0, is_personal=True)
            return

    if TABRIK_BARE_RE.match(query):
        await _answer_instruction(
            update, "🎁 /tabrik — tabrik matnini ham yozing",
            "Masalan: /tabrik Salom mening qadrli insonim...",
        )
        return

    # --------------------------------------------------------
    # OG'IR: kurs ishi
    # --------------------------------------------------------

    if COURSE_WORK_HINTS.search(query):
        m = PAGE_RE.search(query)

        if m and PUBLIC_BASE_URL:
            pages = int(m.group(1))

            topic = course_work.clean_topic(
                (query[:m.start()] + query[m.end():]).strip()
            )

            if topic:
                result_id = str(uuid.uuid4())

                cache[result_id] = {
                    "type": "course_work",
                    "topic": topic,
                    "pages": pages,
                }

                results = [
                    InlineQueryResultDocument(
                        id=result_id,
                        title=f"📄 Kurs ishi: {topic}",
                        description=(
                            f"{pages}+ bet — bosing, "
                            f"generatsiya boshlanadi (bir necha daqiqa)"
                        ),
                        document_url=(
                            f"{PUBLIC_BASE_URL}/placeholder.pdf"
                        ),
                        mime_type="application/pdf",
                        caption=(
                            f"⏳ *{topic}*\n"
                            f"{pages}+ betlik kurs ishi tayyorlanmoqda... "
                            "Bu bir necha daqiqa davom etishi mumkin, "
                            "iltimos kuting."
                        ),
                        parse_mode=ParseMode.MARKDOWN,

                        # MUHIM:
                        # inline_message_id kelishi uchun keyboard
                        # biriktiriladi.
                        reply_markup=INLINE_MESSAGE_MARKUP,
                    )
                ]

                await update.inline_query.answer(
                    results,
                    cache_time=0,
                    is_personal=True
                )

                return

        # PUBLIC_BASE_URL sozlanmagan yoki bet soni topilmadi
        # -> shaxsiy chatga yo'naltiramiz
        await _answer_redirect(update, query)
        return

    # --------------------------------------------------------
    # Hozircha inline'da qo'llab-quvvatlanmaydigan boshqa
    # og'ir vazifalar
    # --------------------------------------------------------

    if OTHER_HEAVY_HINTS.search(query):
        await _answer_redirect(update, query)
        return

    # --------------------------------------------------------
    # YENGIL: oddiy savol-javob
    # --------------------------------------------------------

    result_id = str(uuid.uuid4())

    cache[result_id] = {
        "type": "chat",
        "query": query
    }

    results = [
        InlineQueryResultArticle(
            id=result_id,
            title="💬 Talaba AI javobi",
            description=query[:120],
            input_message_content=InputTextMessageContent(
                PLACEHOLDER_TEXT
            ),

            # MUHIM:
            # Shu keyboard sabab Telegram
            # chosen.inline_message_id beradi.
            reply_markup=INLINE_MESSAGE_MARKUP,
        )
    ]

    await update.inline_query.answer(
        results,
        cache_time=0,
        is_personal=True
    )


async def _answer_redirect(
    update: Update,
    query: str
):
    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title="📄 Bu vazifa botning shaxsiy chatida bajariladi",
            description="Bosing va botga o'ting, u yerda davom etadi",
            input_message_content=InputTextMessageContent(
                f"📄 \"{query}\" — bu vazifani bajarish uchun "
                f"botning shaxsiy chatiga o'ting: "
                f"https://t.me/{BOT_USERNAME}"
            ),
        )
    ]

    await update.inline_query.answer(
        results,
        cache_time=0,
        is_personal=True
    )


async def _answer_instruction(update: Update, title: str, description: str):
    """Foydalanuvchi buyruqni to'liq yozmagan (masalan faqat "/vid",
    havolasiz) holatlarda ko'rsatiladigan, keshlanmaydigan ko'rsatma
    natijasi — tanlansa ham hech qanday og'ir ish boshlanmaydi, faqat
    o'sha ko'rsatma matni chatga joylanadi."""
    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=title,
            description=description,
            input_message_content=InputTextMessageContent(f"{title}\n{description}"),
        )
    ]
    await update.inline_query.answer(results, cache_time=0, is_personal=True)


def _trim_cache(cache: dict):
    if len(cache) > _MAX_CACHE:
        for old_key in list(cache.keys())[
            :len(cache) - _MAX_CACHE
        ]:
            cache.pop(old_key, None)


# ============================================================
# 2-BOSQICH: foydalanuvchi natijani tanlagach
# ============================================================

async def on_chosen_inline_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    chosen = update.chosen_inline_result

    inline_message_id = chosen.inline_message_id

    if not inline_message_id:
        logger.warning(
            "chosen_inline_result keldi, lekin inline_message_id yo'q — "
            "BotFather'da /setinlinefeedback 'Enabled' qilinganini tekshiring."
        )
        return

    cache = context.bot_data.get("inline_queries", {})

    entry = cache.pop(
        chosen.result_id,
        None
    )

    # --------------------------------------------------------
    # KURS ISHI
    # --------------------------------------------------------

    if entry and entry.get("type") == "course_work":
        await _handle_course_work(
            context,
            inline_message_id,
            entry["topic"],
            entry["pages"]
        )
        return

    # --------------------------------------------------------
    # 🎬 /vid
    # --------------------------------------------------------

    if entry and entry.get("type") == "vid":
        await _handle_vid(context, inline_message_id, entry["url"])
        return

    # --------------------------------------------------------
    # 🎵 /qo'shiq
    # --------------------------------------------------------

    if entry and entry.get("type") == "qoshiq":
        await _handle_qoshiq(context, inline_message_id, entry["url"], entry["title"], entry.get("uploader"))
        return

    # --------------------------------------------------------
    # 🎁 /tabrik
    # --------------------------------------------------------

    if entry and entry.get("type") == "tabrik":
        await _handle_tabrik(context, inline_message_id, entry["text"])
        return

    # --------------------------------------------------------
    # ODDIY AI SAVOL
    # --------------------------------------------------------

    query = (
        (entry or {}).get("query")
        or chosen.query
    )

    if not query:
        return

    await _handle_chat(
        context,
        inline_message_id,
        query
    )


# ============================================================
# YENGIL OQIM:
# oddiy savol-javob
# ============================================================

async def _handle_chat(
    context: ContextTypes.DEFAULT_TYPE,
    inline_message_id: str,
    query: str
):
    wants_location = bool(
        LOCATION_HINTS.search(query)
    )

    system = (
        "Siz do'stona va bilimdon AI yordamchisiz "
        "(ismingiz — Dase). O'zbek tilida "
        "(agar savol boshqa tilda bo'lmasa), aniq va "
        "Telegram xabari uchun mos qisqalikda "
        "(taxminan 3-8 gap) javob bering."
    )

    if wants_location:
        system += (
            " Savolda aniq geografik joy so'ralgan bo'lsa, "
            "javob oxiriga ANIQ shu formatda koordinata "
            "qo'shing: [LOC: kenglik,uzunlik] "
            "(masalan [LOC: 41.311081,69.240562]). "
            "Bu texnik belgi — uni matn ichida boshqa joyda "
            "ishlatmang, faqat eng oxiriga bir marta qo'shing."
        )

    answer = await ask_ai(
        UNIVERSAL_CHAT_AI,
        query,
        system
    )

    if not answer:
        logger.error(
            f"🔍 Inline javob QAYTMADI: "
            f"query='{query[:80]}' — sababi yuqoridagi "
            f"ai_clients loglarida."
        )

        await _safe_edit_text(
            context,
            inline_message_id,
            (
                f"❌ Javob berib bo'lmadi. "
                f"Botning shaxsiy chatida qayta urinib ko'ring: "
                f"https://t.me/{BOT_USERNAME}"
            ),
            reply_markup=INLINE_MESSAGE_MARKUP,
        )

        return

    # --------------------------------------------------------
    # LOCATION
    # --------------------------------------------------------

    maps_line = ""

    m = LOC_TAG_RE.search(answer)

    if m:
        lat = m.group(1)
        lon = m.group(2)

        answer = LOC_TAG_RE.sub(
            "",
            answer
        ).strip()

        maps_line = (
            f"\n\n📍 [Xaritada ko'rish]"
            f"(https://www.google.com/maps?q={lat},{lon})"
        )

    # --------------------------------------------------------
    # YAKUNIY JAVOB
    # --------------------------------------------------------

    final_text = (
        f"{answer}"
        f"{maps_line}"
        f"\n\n🤖 Talaba AI — @{BOT_USERNAME}"
    )

    await _safe_edit_text(
        context,
        inline_message_id,
        final_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=INLINE_MESSAGE_MARKUP,
    )


# ============================================================
# XAVFSIZ TEXT EDIT
# ============================================================

async def _safe_edit_text(
    context,
    inline_message_id,
    text,
    parse_mode=None,
    reply_markup=None
):
    try:
        await context.bot.edit_message_text(
            inline_message_id=inline_message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )

    except BadRequest as e:

        if "not modified" in str(e).lower():
            return

        logger.error(
            f"Inline matnni tahrirlashda xato: {e}"
        )

        # Markdown xato bo'lsa oddiy matn bilan qayta urinib ko'ramiz
        if parse_mode:
            try:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=text,
                    reply_markup=reply_markup
                )

            except Exception as e2:
                logger.error(
                    f"Inline matnni tahrirlashda "
                    f"takroriy xato: {e2}"
                )

    except Exception as e:
        logger.error(
            f"Inline matnni tahrirlashda xato: {e}"
        )


# ============================================================
# OG'IR OQIM:
# kurs ishi / PDF
# ============================================================

class _InlineCaptionStatus:
    """
    course_work.generate_course_work() ga 'status_msg' sifatida beriladi —
    Telegram Message obyektiga o'xshab '.edit_text(text, parse_mode=...)'
    metodiga ega, lekin ichida haqiqatda xabarning CAPTION'ini
    tahrirlaydi.

    Telegram edit limitiga tushib qolmaslik uchun tez-tez
    chaqirilgan yangilanishlarni o'zi siqib qoldiradi.
    """

    def __init__(
        self,
        bot,
        inline_message_id: str
    ):
        self._bot = bot
        self._id = inline_message_id
        self._last_edit = 0.0

    async def edit_text(
        self,
        text: str,
        parse_mode=None
    ):
        now = time.monotonic()

        if now - self._last_edit < MIN_CAPTION_EDIT_INTERVAL:
            return

        self._last_edit = now

        try:
            await self._bot.edit_message_caption(
                inline_message_id=self._id,
                caption=text[:1024],
                parse_mode=parse_mode
            )

        except BadRequest as e:

            if "not modified" not in str(e).lower():
                logger.warning(
                    "Inline caption tahrirlashda xato "
                    f"(e'tiborsiz qoldirildi): {e}"
                )

        except Exception as e:
            logger.warning(
                "Inline caption tahrirlashda xato "
                f"(e'tiborsiz qoldirildi): {e}"
            )


async def _handle_course_work(
    context: ContextTypes.DEFAULT_TYPE,
    inline_message_id: str,
    topic: str,
    pages: int
):
    status_proxy = _InlineCaptionStatus(
        context.bot,
        inline_message_id
    )

    try:
        result = await course_work.generate_course_work(
            topic,
            pages,
            status_proxy
        )

    except Exception as e:
        logger.error(
            f"Inline kurs ishi generatsiyasida xato "
            f"('{topic}'): {e}",
            exc_info=True
        )

        result = None

    # --------------------------------------------------------
    # GENERATSIYA XATOSI
    # --------------------------------------------------------

    if not result:

        try:
            await context.bot.edit_message_caption(
                inline_message_id=inline_message_id,
                caption=(
                    f"❌ \"{topic}\" uchun kurs ishini "
                    f"yaratib bo'lmadi — AI xizmatlari hozir "
                    f"javob bermayapti. Botning shaxsiy chatida "
                    f"qayta urinib ko'ring: "
                    f"https://t.me/{BOT_USERNAME}"
                ),
                reply_markup=INLINE_MESSAGE_MARKUP,
            )

        except Exception as e:
            logger.error(
                f"Xato xabarini yozishda muammo: {e}"
            )

        return

    # --------------------------------------------------------
    # TAYYOR PDF
    # --------------------------------------------------------

    sections, pdf_buf, actual_pages = result

    pdf_buf.seek(0)

    try:

        await context.bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaDocument(
                media=InputFile(
                    pdf_buf,
                    filename=f"{topic[:40]}.pdf"
                ),
                caption=(
                    f"📄 {topic}\n"
                    f"📎 {actual_pages} bet "
                    f"(so'ralgan: {pages}+)\n"
                    f"✅ Titul, mundarija, kirish, "
                    f"3 bob, xulosa va adabiyotlar "
                    f"ro'yxati bilan.\n\n"
                    f"🤖 Talaba AI — @{BOT_USERNAME}"
                ),
            ),
            reply_markup=INLINE_MESSAGE_MARKUP,
        )

    except Exception as e:

        logger.error(
            f"Tayyor PDF'ni inline xabarga joylashda xato "
            f"('{topic}'): {e}",
            exc_info=True
        )

        try:
            await context.bot.edit_message_caption(
                inline_message_id=inline_message_id,
                caption=(
                    f"✅ \"{topic}\" kurs ishi tayyor bo'ldi, "
                    f"lekin uni shu yerga joylashda xatolik "
                    f"yuz berdi. Botning shaxsiy chatida "
                    f"qayta urinib ko'ring: "
                    f"https://t.me/{BOT_USERNAME}"
                ),
                reply_markup=INLINE_MESSAGE_MARKUP,
            )

        except Exception:
            pass


# ============================================================
# 🎬 OG'IR OQIM: /vid
# ============================================================

async def _handle_vid(
    context: ContextTypes.DEFAULT_TYPE,
    inline_message_id: str,
    url: str,
):
    dest_dir = tempfile.mkdtemp(prefix="inline_vid_")
    try:
        filepath = await asyncio.to_thread(
            video_tools.download_video, url, dest_dir, config.VID_MAX_MB, config.VID_DOWNLOAD_TIMEOUT_SEC,
        )
        with open(filepath, "rb") as f:
            await context.bot.edit_message_media(
                inline_message_id=inline_message_id,
                media=InputMediaVideo(
                    media=InputFile(f),
                    caption=f"✅ Video tayyor.\n\n🤖 Talaba AI — @{BOT_USERNAME}",
                ),
                reply_markup=INLINE_MESSAGE_MARKUP,
            )
        logger.info(f"🔍 Inline /vid muvaffaqiyatli: url={url}.")
    except video_tools.DownloadError as e:
        try:
            await context.bot.edit_message_caption(
                inline_message_id=inline_message_id, caption=str(e), reply_markup=INLINE_MESSAGE_MARKUP,
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"🔍 Inline /vid kutilmagan xato (url={url}): {type(e).__name__}: {e}", exc_info=True)
        try:
            await context.bot.edit_message_caption(
                inline_message_id=inline_message_id,
                caption="❌ Video yuborishda kutilmagan xatolik yuz berdi.",
                reply_markup=INLINE_MESSAGE_MARKUP,
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(dest_dir, ignore_errors=True)


# ============================================================
# 🎵 OG'IR OQIM: /qo'shiq
# ============================================================

async def _handle_qoshiq(
    context: ContextTypes.DEFAULT_TYPE,
    inline_message_id: str,
    url: str,
    title: str,
    uploader: str | None,
):
    dest_dir = tempfile.mkdtemp(prefix="inline_qoshiq_")
    try:
        filepath = await asyncio.to_thread(
            video_tools.download_audio, url, dest_dir, config.QOSHIQ_MAX_MB, config.QOSHIQ_DOWNLOAD_TIMEOUT_SEC,
        )
        with open(filepath, "rb") as f:
            await context.bot.edit_message_media(
                inline_message_id=inline_message_id,
                media=InputMediaAudio(
                    media=InputFile(f),
                    title=title[:64],
                    performer=uploader or None,
                    caption=f"✅ Tayyor.\n\n🤖 Talaba AI — @{BOT_USERNAME}",
                ),
                reply_markup=INLINE_MESSAGE_MARKUP,
            )
        logger.info(f"🔍 Inline /qo'shiq muvaffaqiyatli: track='{title}'.")
    except video_tools.DownloadError as e:
        try:
            await context.bot.edit_message_caption(
                inline_message_id=inline_message_id, caption=str(e), reply_markup=INLINE_MESSAGE_MARKUP,
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"🔍 Inline /qo'shiq kutilmagan xato (title='{title}'): {type(e).__name__}: {e}", exc_info=True)
        try:
            await context.bot.edit_message_caption(
                inline_message_id=inline_message_id,
                caption="❌ Qo'shiqni yuborishda kutilmagan xatolik yuz berdi.",
                reply_markup=INLINE_MESSAGE_MARKUP,
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(dest_dir, ignore_errors=True)


# ============================================================
# 🎁 OG'IR OQIM: /tabrik (matn animatsiyasi)
# ============================================================

TABRIK_COUNTDOWN_DELAY = 1.0
TABRIK_FRAME_DELAY = 0.45


async def _handle_tabrik(
    context: ContextTypes.DEFAULT_TYPE,
    inline_message_id: str,
    text: str,
):
    try:
        for n in (5, 4, 3, 2, 1):
            await _safe_edit_text(context, inline_message_id, tabrik_logic.build_countdown_frame(n))
            await asyncio.sleep(TABRIK_COUNTDOWN_DELAY)

        for step in range(tabrik_logic.TOTAL_ROTATION_FRAMES):
            await _safe_edit_text(context, inline_message_id, tabrik_logic.build_circle_frame(step))
            await asyncio.sleep(TABRIK_FRAME_DELAY)

        final_text = f"{tabrik_logic.build_final_card(text)}\n\n🤖 Talaba AI — @{BOT_USERNAME}"
        await _safe_edit_text(context, inline_message_id, final_text, reply_markup=INLINE_MESSAGE_MARKUP)
        logger.info("🔍 Inline /tabrik animatsiyasi muvaffaqiyatli yakunlandi.")
    except Exception as e:
        logger.error(f"🔍 Inline /tabrik animatsiyasida kutilmagan xato: {type(e).__name__}: {e}", exc_info=True)
        try:
            await _safe_edit_text(
                context, inline_message_id, f"🎁 {text}\n\n🤖 Talaba AI — @{BOT_USERNAME}",
                reply_markup=INLINE_MESSAGE_MARKUP,
            )
        except Exception:
            pass