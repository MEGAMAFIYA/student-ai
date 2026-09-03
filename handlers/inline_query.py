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

  E) MINI APP (/rasim) — A/B/C/D'dan BUTUNLAY BOSHQACHA mexanizm, chunki
     bu yerda "natija" oldindan noma'lum (foydalanuvchi Mini App'da nima
     chizishini bot bilmaydi):
     1. `on_inline_query` odatdagidek "natijalar" ro'yxati QAYTARMAYDI —
        buning o'rniga `answer_inline_query`ning maxsus `button` parametri
        orqali BITTA "🎨 Rasm chizish" tugmasini ko'rsatadi
        (`InlineQueryResultsButton(web_app=WebAppInfo(...))`). Bu tugma
        natijalar ro'yxatidan TASHQARIDA, alohida joylashadi.
     2. Foydalanuvchi tugmani bosadi -> Mini App (webapp/rasim/) OCHILADI
        (hech qanday xabar hali yuborilmaydi!). Telegram Mini App'ga
        maxfiy `query_id` beradi (initData ichida, imzolangan holda).
     3. Foydalanuvchi chizib "📤 Uzatish"ni bosadi -> rasm bizning
        serverga (`/miniapp/rasim/upload`) yuboriladi — xuddi oddiy
        /rasim kabi, lekin `rid` "in_" prefiksi bilan farqlanadi va
        chat_id o'RNIGA `query_id` ishlatiladi.
     4. Server rasmni vaqtincha OCHIQ URL orqali xizmat qiladi va
        Telegram'ning `answer_web_app_query(query_id, InlineQueryResultPhoto)`
        metodini chaqiradi — Telegram rasmni AVTOMATIK ravishda TO'G'RI
        (do'st bilan) chatga, foydalanuvchi nomidan joylaydi. Chat_id
        HECH QACHON bizga ma'lum bo'lmaydi va kerak ham emas.
     Bu oqim `chosen_inline_result` orqali UMUMAN ishlamaydi (shuning
     uchun on_chosen_inline_result'da /rasim uchun alohida branch YO'Q).

MUHIM SOZLASH (BotFather orqali, kod bilan bog'liq emas):
  /setinline           -> inline rejimni yoqish (placeholder matn so'raladi)
  /setinlinefeedback   -> "Enabled" qilib qo'yish SHART, aks holda 2-3
                           bosqich (chosen_inline_result) ishlamaydi.

MUHIM SOZLASH (config.py / .env orqali):
  PUBLIC_BASE_URL       -> botning ochiq https manzili (Render URL'i).
                           Bo'sh bo'lsa, OG'IR oqimlar (B, C, E) o'chiriladi va
                           foydalanuvchi botning shaxsiy chatiga yo'naltiriladi
                           — YENGIL oqim (A) va /tabrik (D) baribir to'liq
                           ishlayveradi.
  ffmpeg (serverda)     -> /qo'shiq uchun MP3'ga o'tkazish SHART (video_tools.py).
"""

import asyncio
import io
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
    InlineQueryResultsButton,
    WebAppInfo,
    InputTextMessageContent,
    InputMediaDocument,
    InputMediaVideo,
    InputMediaAudio,
    InputMediaPhoto,
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.helpers import escape_markdown

import config
from config import UNIVERSAL_CHAT_AI, PUBLIC_BASE_URL
from ai_clients import ask_ai
from handlers import course_work
import github_storage
import pro_tabrik_logic
import storage
import tabrik_logic
import tabrik_business
import video_tools
import webapp_security

logger = logging.getLogger(__name__)

PLACEHOLDER_TEXT = "⏳ Javob tayyorlanmoqda..."
BOT_USERNAME = "Student_ai_uz_bot"  # faqat imzo/havola uchun, username o'zgarsa shu yerni yangilang


# ------------------------------------------------------------------
# 🔍📜 /developer > "🔍 Inline jurnali" uchun — foydalanuvchi botni
# GURUHGA A'ZO QILMASDAN yoki SHAXSIY chatda "@Student_ai_uz_bot ..."
# deb ishlatganda (inline rejim) shu yordamchilar orqali storage.py'ga
# qayd etiladi (qarang: storage.record_inline_log). Har bir chaqiruv
# nuqtasida "ishladimi/ishlamadimi va nima sababdan" aniq ko'rinadi.
# ------------------------------------------------------------------

def _user_label(user) -> str:
    if not user:
        return ""
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


def _log_inline(user, query: str, status: str, detail: str = "") -> None:
    if not user:
        return
    try:
        storage.record_inline_log(user.id, _user_label(user), query, status, detail)
    except Exception as e:
        logger.error(f"🔍 Inline jurnalga yozishda xato (e'tiborsiz qoldirildi): {type(e).__name__}: {e}")

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

# 🎨 /rasim — ATAYLAB bo'sh query'ni ham qabul qiladi: do'st bilan chatda
# shunchaki "@Student_ai_uz_bot" deb yozib to'xtash eng qulay/tabiiy
# harakat (bo'sh inline so'rovning boshqa mazmunli natijasi yo'q — AI
# chatga bo'sh matn yuborish foydasiz), shuning uchun buni ham "rasm
# chizish" tugmasiga yo'naltiramiz. Aniq "/rasim" ham bir xil ishlaydi.
RASIM_RE = re.compile(r"^$|^/rasim(?:@\w+)?\s*$", re.IGNORECASE)

# 💎 /pro — /tabrik'ning shaxsiy-rasmli versiyasi (qarang: handlers/pro_tabrik.py)
PRO_WITH_TEXT_RE = re.compile(r"^/pro(?:@\w+)?\s+(.+)$", re.IGNORECASE)
PRO_BARE_RE = re.compile(r"^/pro(?:@\w+)?\s*$", re.IGNORECASE)


# ============================================================
# 1-BOSQICH: inline so'rovga TEZKOR javob
# ============================================================

async def on_inline_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.inline_query.query.strip()

    # --------------------------------------------------------
    # 🎨 MINI APP: /rasim (bo'sh query HAM shu yerga tushadi — yuqoridagi
    # RASIM_RE'ga izohga qarang). Boshqa hamma tekshiruvdan OLDIN, chunki
    # bo'sh query boshqa hech qanday regexga mos kelmaydi va aks holda
    # pastdagi "if not query: return" uni jimgina e'tiborsiz qoldirar edi.
    # --------------------------------------------------------

    if RASIM_RE.match(query):
        await _answer_rasim(update)
        return

    if not query:
        return  # foydalanuvchi hali hech narsa yozmagan

    # --------------------------------------------------------
    # 🛑 QATTIQ HIMOYA (hamma narsadan OLDIN): "/tabrik" bilan boshlangan
    # HAR QANDAY so'rov faqat /tabrik funksiyasiga tegishli — pastdagi
    # boshqa hech qanday tarmoqqa (jumladan oxiridagi umumiy AI-javob
    # "chat" natijasiga) HECH QACHON tushmasligi kerak. Bu tekshiruv
    # pastdagi (asosiy) /tabrik blokidan MUSTAQIL, ATAYLAB DUBLIKAT —
    # agar u yerdagi shart negadir ishlamay qolsa ham (masalan kelajakda
    # kimdir kod tuzatib, chegara shartini buzib qo'ysa), so'rov baribir
    # shu yerda ushlanib qoladi va AI'ga umuman yuborilmaydi.
    # --------------------------------------------------------
    if re.match(r"^/tabrik(?:@\w+)?(\s|$)", query, re.IGNORECASE):
        raw_text = tabrik_logic.parse_tabrik_text(query)
        if not raw_text:
            await _answer_instruction(
                update, "🎁 /tabrik — tabrik matnini ham yozing",
                "Masalan: /tabrik Salom mening qadrli insonim...",
                query=query,
            )
            return
        custom_emojis, text = tabrik_logic.extract_emojis(raw_text)
        emojis = custom_emojis or tabrik_business.DEFAULT_EMOJIS
        short_id = tabrik_logic.store_greeting(text, emojis=emojis)
        tabrik_business.register_celebration(short_id, sender_user_id=update.inline_query.from_user.id)
        results = [
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="🎁 Tabrik yuborish",
                description=text[:120],
                input_message_content=InputTextMessageContent(tabrik_logic.build_ready_card()),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🎁 Tabriknomani qabul qilish", callback_data=f"itabrik:claim:{short_id}")
                ]]),
            )
        ]
        await update.inline_query.answer(results, cache_time=0, is_personal=True)
        return

    cache = context.bot_data.setdefault("inline_queries", {})
    _trim_cache(cache)

    # --------------------------------------------------------
    # 🎬 OG'IR: /vid
    # --------------------------------------------------------

    if VID_WITH_URL_RE.match(query):
        if not PUBLIC_BASE_URL:
            await _answer_redirect(update, query, "PUBLIC_BASE_URL sozlanmagan — /vid inline rejimda ishlay olmaydi")
            return

        url = VID_WITH_URL_RE.match(query).group(1)
        result_id = str(uuid.uuid4())
        cache[result_id] = {"type": "vid", "url": url}

        results = [
            InlineQueryResultDocument(
                id=result_id,
                title="🎬 Video yuklab olinadi",
                description=url[:120],
                document_url=f"{PUBLIC_BASE_URL}/placeholder.pdf",
                mime_type="application/pdf",
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
            query=query,
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
            await _answer_redirect(update, query, "PUBLIC_BASE_URL sozlanmagan — /qo'shiq inline rejimda ishlay olmaydi")
            return

        search_text = QOSHIQ_WITH_QUERY_RE.match(query).group(1).strip()

        if re.match(r"^https?://\S+$", search_text, re.IGNORECASE):
            await _answer_instruction(
                update, "🎵 Bu havolaga o'xshaydi",
                "Video/audio havolasi uchun /qo'shiq emas, /vid dan foydalaning.",
                query=query,
            )
            return

        try:
            tracks = await asyncio.to_thread(video_tools.search_tracks, search_text, config.QOSHIQ_SEARCH_COUNT)
        except video_tools.DownloadError as e:
            await _answer_instruction(update, "🎵 Qidiruvda xatolik", str(e), query=query)
            return
        except Exception as e:
            logger.error(f"🔍 Inline /qo'shiq qidiruvida kutilmagan xato ('{search_text}'): {type(e).__name__}: {e}", exc_info=True)
            await _answer_instruction(update, "🎵 Qidiruvda xatolik", f"Sabab: {type(e).__name__}: {e}", query=query)
            return

        results = []
        for t in tracks:
            result_id = str(uuid.uuid4())
            cache[result_id] = {"type": "qoshiq", "url": t["webpage_url"], "title": t["title"], "uploader": t.get("uploader")}
            channel = video_tools.display_channel(t)
            # 🎧 Haqiqiy ("20-30 soniyalik") audio preview'ni bot ichida
            # ijro etib bo'lmaydi (Telegram inline natijalari faqat
            # HUJJAT/matn qaytara oladi, alohida audio-stream imkoniyati
            # yo'q) — shu sabab, foydalanuvchi hali yuklamasdan turib
            # eshitib ko'rishi uchun, ENG YAQIN haqiqiy yechim: manbaning
            # o'zidagi sahifaga (YouTube/SoundCloud/Telegram post) to'g'ridan
            # to'g'ri havola beramiz (talab #4 — "preview mavjud emas"
            # holatini soxta imkoniyat bilan almashtirmaslik).
            preview_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎧 Manbada eshitish", url=t["webpage_url"])],
                [InlineKeyboardButton("🤖 Talaba AI", url=f"https://t.me/{BOT_USERNAME}")],
            ])
            results.append(
                InlineQueryResultDocument(
                    id=result_id,
                    title=f"🎵 {video_tools.format_track_label(t, max_len=60)}",
                    description=f"{t['source_emoji']} {channel or t['source_label']}",
                    document_url=f"{PUBLIC_BASE_URL}/placeholder.pdf",
                    mime_type="application/pdf",
                    thumbnail_url=f"{PUBLIC_BASE_URL}/music_icon.png",
                    caption=f"⏳ \"{t['title']}\" yuklab olinmoqda...",
                    reply_markup=preview_markup,
                )
            )
        await update.inline_query.answer(results, cache_time=0, is_personal=True)
        return

    if QOSHIQ_BARE_RE.match(query):
        await _answer_instruction(
            update, "🎵 /qo'shiq — ijrochi yoki qo'shiq nomini ham yozing",
            "Masalan: /qo'shiq Ozodbek Nazarbekov",
            query=query,
        )
        return

    # --------------------------------------------------------
    # 🎁 /tabrik — GURUH bilan BIR XIL tartibda: avval FAQAT
    # "🎁 Tabriknomani qabul qilish" tugmasi chiqadi (matn ko'rinmaydi),
    # animatsiya FAQAT tugma bosilgandan keyin (inline_tabrik_claim_callback)
    # boshlanadi — chosen_inline_result'da AVTOMATIK boshlanmaydi.
    # --------------------------------------------------------

    if query[:7].lower().startswith("/tabrik") and not TABRIK_BARE_RE.match(query):
        raw_text = tabrik_logic.parse_tabrik_text(query)
        if raw_text:
            # 🎁 Business oqimi uchun: foydalanuvchi matn boshida o'z
            # emojilarini yozgan bo'lsa o'shalar, aks holda Business
            # default ketma-ketligi (😍🥳🎉❤️✨) ishlatiladi — bu
            # tabrik_logic.DEFAULT_EMOJIS (🎉🎊🥳🎁✨) dan ATAYLAB farqli,
            # chunki u eski /pro va guruh /tabrik oqimlari uchun umumiy va
            # o'zgartirilmadi (21-band: /pro'ni buzma).
            custom_emojis, text = tabrik_logic.extract_emojis(raw_text)
            emojis = custom_emojis or tabrik_business.DEFAULT_EMOJIS
            short_id = tabrik_logic.store_greeting(text, emojis=emojis)
            tabrik_business.register_celebration(short_id, sender_user_id=update.inline_query.from_user.id)
            results = [
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="🎁 Tabrik yuborish",
                    description=text[:120],
                    input_message_content=InputTextMessageContent(tabrik_logic.build_ready_card()),
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🎁 Tabriknomani qabul qilish", callback_data=f"itabrik:claim:{short_id}")
                    ]]),
                )
            ]
            await update.inline_query.answer(results, cache_time=0, is_personal=True)
            return

    if TABRIK_BARE_RE.match(query):
        await _answer_instruction(
            update, "🎁 /tabrik — tabrik matnini ham yozing",
            "Masalan: /tabrik Salom mening qadrli insonim...",
            query=query,
        )
        return

    # --------------------------------------------------------
    # 💎 /pro — /tabrik'ning shaxsiy-rasmli versiyasi. Xuddi /tabrik kabi,
    # avval FAQAT "🎁 Tabriknomani qabul qilish" tugmasi chiqadi (rasm
    # HALI ko'rsatilmaydi) — lekin placeholder HUJJAT (PDF) turida
    # bo'lishi kerak, chunki keyinroq RASMGA almashtiriladi (matn buni
    # qila olmas edi, qarang: handlers/pro_tabrik.py boshidagi izoh).
    # --------------------------------------------------------

    if PRO_WITH_TEXT_RE.match(query):
        text = pro_tabrik_logic.parse_pro_text(query)
        if text:
            if not PUBLIC_BASE_URL:
                await _answer_redirect(update, query, "PUBLIC_BASE_URL sozlanmagan — /pro inline rejimda ishlay olmaydi")
                return
            sender_id = update.inline_query.from_user.id
            photos = github_storage.list_user_photos(sender_id) if github_storage.is_configured() else []
            short_id = pro_tabrik_logic.store_pro_greeting(text, sender_id, photos)
            results = [
                InlineQueryResultDocument(
                    id=str(uuid.uuid4()),
                    title="💎 Pro tabriknoma yuborish",
                    description=text[:120],
                    document_url=f"{PUBLIC_BASE_URL}/placeholder.pdf",
                    mime_type="application/pdf",
                    caption=pro_tabrik_logic.build_ready_card(),
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🎁 Tabriknomani qabul qilish", callback_data=f"iprotabrik:claim:{short_id}")
                    ]]),
                )
            ]
            await update.inline_query.answer(results, cache_time=0, is_personal=True)
            return

    if PRO_BARE_RE.match(query):
        await _answer_instruction(
            update, "💎 /pro — tabrik matnini ham yozing",
            "Masalan: /pro Salom mening aziz do'stim...",
            query=query,
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
        reason = (
            "PUBLIC_BASE_URL sozlanmagan" if not PUBLIC_BASE_URL
            else "kurs ishi so'rovida bet soni yoki mavzu aniqlanmadi"
        )
        await _answer_redirect(update, query, reason)
        return

    # --------------------------------------------------------
    # Hozircha inline'da qo'llab-quvvatlanmaydigan boshqa
    # og'ir vazifalar
    # --------------------------------------------------------

    if OTHER_HEAVY_HINTS.search(query):
        await _answer_redirect(update, query, "bu vazifa turi hozircha inline rejimda qo'llab-quvvatlanmaydi")
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
    query: str,
    reason: str = "og'ir vazifa inline rejimda bajarilmaydi — shaxsiy chatga yo'naltirildi",
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
    _log_inline(update.inline_query.from_user, query, "redirect", reason)


async def _answer_instruction(update: Update, title: str, description: str, query: str | None = None):
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
    _log_inline(update.inline_query.from_user, query if query is not None else title, "instruction", description)


async def _answer_rasim(update: Update) -> None:
    """/rasim (yoki bo'sh mention) — natijalar ro'yxati o'RNIGA, alohida
    "🎨 Rasm chizish" TUGMASINI ko'rsatadi (qarang: fayl boshidagi E oqim
    izohi). Bu tugma Mini App'ni ochadi; Mini App'dan qaytish
    bot.py > _handle_rasim_upload_inline orqali `answer_web_app_query`
    bilan yakunlanadi — shu funksiya faqat tugmani ko'rsatishga javobgar."""
    if not PUBLIC_BASE_URL:
        await _answer_redirect(update, "/rasim")
        return

    user_id = update.inline_query.from_user.id
    rid = webapp_security.create_inline_request(user_id)
    webapp_url = f"{PUBLIC_BASE_URL}/miniapp/rasim/?rid={rid}"

    await update.inline_query.answer(
        [],
        button=InlineQueryResultsButton(
            text="🎨 Rasm chizish",
            web_app=WebAppInfo(url=webapp_url),
        ),
        cache_time=0,
        is_personal=True,
    )


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

    # 🔍📜 /developer > "🔍 Inline jurnali" uchun — natijani TANLAGAN
    # foydalanuvchi (ChosenInlineResult'ning o'zida keladi, qayta so'rash
    # shart emas).
    user = chosen.from_user

    # --------------------------------------------------------
    # KURS ISHI
    # --------------------------------------------------------

    if entry and entry.get("type") == "course_work":
        await _handle_course_work(
            context,
            inline_message_id,
            entry["topic"],
            entry["pages"],
            user,
        )
        return

    # --------------------------------------------------------
    # 🎬 /vid
    # --------------------------------------------------------

    if entry and entry.get("type") == "vid":
        await _handle_vid(context, inline_message_id, entry["url"], user)
        return

    # --------------------------------------------------------
    # 🎵 /qo'shiq
    # --------------------------------------------------------

    if entry and entry.get("type") == "qoshiq":
        await _handle_qoshiq(context, inline_message_id, entry["url"], entry["title"], entry.get("uploader"), user)
        return

    # 🎁 /tabrik uchun bu yerda ATAYLAB hech narsa yo'q — animatsiya endi
    # xabar TANLANGANDA emas, faqat "🎁 Tabriknomani qabul qilish" tugmasi
    # bosilganda (inline_tabrik_claim_callback) boshlanadi.

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
        query,
        user,
    )


# ============================================================
# YENGIL OQIM:
# oddiy savol-javob
# ============================================================

async def _handle_chat(
    context: ContextTypes.DEFAULT_TYPE,
    inline_message_id: str,
    query: str,
    user=None,
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

        _log_inline(user, query, "error", "AI javob qaytarmadi (ai_clients loglariga qarang)")
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
    _log_inline(user, query, "ok")


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
    pages: int,
    user=None,
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

        _log_inline(user, f"kurs ishi: {topic} ({pages}+ bet)", "error", "AI xizmatlari javob bermadi (generatsiya muvaffaqiyatsiz)")
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
        _log_inline(user, f"kurs ishi: {topic} ({pages}+ bet)", "ok")

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

        _log_inline(user, f"kurs ishi: {topic} ({pages}+ bet)", "error", f"PDF tayyor bo'ldi, lekin joylashda xatolik: {type(e).__name__}: {e}")


# ============================================================
# 🎬 OG'IR OQIM: /vid
# ============================================================

async def _fail_inline_media(context: ContextTypes.DEFAULT_TYPE, inline_message_id: str, error_text: str) -> None:
    """/vid va /qo'shiq OG'IR oqimlarida yuklab olish/konvertatsiya
    MUVAFFAQIYATSIZ bo'lganda chaqiriladi. MUHIM: bu yerda ATAYLAB faqat
    `edit_message_caption` EMAS, `edit_message_media` ishlatiladi —
    aks holda xabar hali ham boshlang'ich "placeholder.pdf" hujjati
    bo'lib qolaverar edi (foydalanuvchiga xato tagida chalkash/soxta
    "placeholder.pdf" fayli ko'rinar edi, qarang: talab #5). Bu yerda
    o'sha placeholder MEDIA'ning o'zi kichik, aniq nomli ("xatolik.txt")
    matn hujjati bilan ALMASHTIRILADI — foydalanuvchi hech qachon
    "placeholder.pdf" nomli faylni oxirgi natija sifatida ko'rmaydi."""
    try:
        await context.bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaDocument(
                media=InputFile(io.BytesIO(error_text.encode("utf-8")), filename="xatolik.txt"),
                caption=error_text[:1024],
            ),
            reply_markup=INLINE_MESSAGE_MARKUP,
        )
        return
    except Exception as e:
        # ⚠️ ANIQ sababni to'liq (traceback bilan) logga yozamiz — talab
        # #11/#20: "muammoni yashirma". Bu chaqiruv muvaffaqiyatsiz bo'lsa,
        # keyingi urinish(lar)dan OLDIN sabab albatta ko'rinadi.
        logger.error(f"🔍 Inline xato xabarini media orqali ko'rsatib bo'lmadi (birinchi urinish): {type(e).__name__}: {e}", exc_info=True)

    # 🔁 IKKINCHI urinish — YANGI BytesIO obyekti bilan (birinchisi allaqachon
    # o'qilgan/tugagan bo'lishi mumkin) va soddaroq (caption'siz) media —
    # ba'zan aynan caption uzunligi/parse xatosi sabab bo'lishi mumkin.
    try:
        await context.bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaDocument(
                media=InputFile(io.BytesIO(error_text.encode("utf-8")), filename="xatolik.txt"),
            ),
            reply_markup=INLINE_MESSAGE_MARKUP,
        )
        return
    except Exception as e:
        logger.error(f"🔍 Inline xato xabarini media orqali ko'rsatib bo'lmadi (ikkinchi urinish): {type(e).__name__}: {e}", exc_info=True)

    # 🆘 OXIRGI CHORA: faqat caption'ni yangilaymiz. MUHIM CHEKLOV: bu
    # holatda foydalanuvchi baribir asl "placeholder.pdf" faylini (xato
    # matni bilan birga) ko'rishi mumkin — Telegram API'da buni to'liq
    # oldini olishning boshqa yo'li yo'q (agar edit_message_media ikki
    # marta ham muvaffaqiyatsiz bo'lsa). Shu sabab bu holat Render
    # loglarida albatta ko'rinadi (yuqoridagi ikkita logger.error) — admin
    # aniq sababni topa oladi.
    try:
        await context.bot.edit_message_caption(
            inline_message_id=inline_message_id, caption=error_text[:1024], reply_markup=INLINE_MESSAGE_MARKUP,
        )
    except Exception as e:
        logger.error(f"🔍 Inline xato xabarini caption orqali ham yangilab bo'lmadi: {type(e).__name__}: {e}", exc_info=True)


async def _handle_vid(
    context: ContextTypes.DEFAULT_TYPE,
    inline_message_id: str,
    url: str,
    user=None,
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
        _log_inline(user, f"/vid {url}", "ok")
    except video_tools.DownloadError as e:
        await _fail_inline_media(context, inline_message_id, str(e))
        _log_inline(user, f"/vid {url}", "error", str(e))
    except Exception as e:
        logger.error(f"🔍 Inline /vid kutilmagan xato (url={url}): {type(e).__name__}: {e}", exc_info=True)
        await _fail_inline_media(context, inline_message_id, "❌ Video yuborishda kutilmagan xatolik yuz berdi.")
        _log_inline(user, f"/vid {url}", "error", f"kutilmagan xato: {type(e).__name__}: {e}")
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
    user=None,
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
        _log_inline(user, f"/qo'shiq {title}", "ok")
    except video_tools.DownloadError as e:
        await _fail_inline_media(context, inline_message_id, str(e))
        _log_inline(user, f"/qo'shiq {title}", "error", str(e))
    except Exception as e:
        logger.error(f"🔍 Inline /qo'shiq kutilmagan xato (title='{title}'): {type(e).__name__}: {e}", exc_info=True)
        await _fail_inline_media(context, inline_message_id, "❌ Qo'shiqni yuborishda kutilmagan xatolik yuz berdi.")
        _log_inline(user, f"/qo'shiq {title}", "error", f"kutilmagan xato: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(dest_dir, ignore_errors=True)


# ============================================================
# 🎁 OG'IR OQIM: /tabrik (matn animatsiyasi) — GURUH bilan BIR XIL
# tartibda: on_inline_query FAQAT tugmani ko'rsatadi (yuqoriga qarang),
# animatsiya FAQAT quyidagi `inline_tabrik_claim_callback` orqali,
# "🎁 Tabriknomani qabul qilish" bosilganda boshlanadi.
# ============================================================

# NOTE: TABRIK_COUNTDOWN_DELAY / TABRIK_FRAME_DELAY konstantalari va
# _ACTIVE_INLINE_TABRIK / _INLINE_TABRIK_REVERT_TASKS ombori endi bu yerda
# YO'Q — ular faqat eski (countdown+aylanuvchi-naqsh) inline /tabrik
# animatsiyasi uchun kerak edi, u endi tabrik_business.py'ga topshirildi.
# `/pro` OQIMI HALI HAM shu ikkita delay konstantasidan foydalanadi —
# ular pastroqda, PRO_SLIDESHOW_DELAY bilan bir joyda TABRIK_COUNTDOWN_DELAY /
# TABRIK_FRAME_DELAY nomi bilan saqlanib qoldi (grep bilan tekshirilgan:
# faqat inline_pro_claim_callback ishlatadi) — 21-band: /pro'ni buzmaslik.
TABRIK_COUNTDOWN_DELAY = 1.0
TABRIK_FRAME_DELAY = 0.45


async def inline_tabrik_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """"🎁 Tabriknomani qabul qilish" tugmasi INLINE xabarda bosilganda
    (do'st bilan chatda) chaqiriladi.

    ESKI (countdown/aylanuvchi-naqsh, `edit_message_text` bilan emoji
    almashtirish) mexanizm BU YERDA ENDI ISHLATILMAYDI — u faqat
    `handlers/tabrik.py` (guruh/oddiy chat) va `/pro` oqimlarida, ular
    o'zlarining bevosita `Message` obyektiga ega bo'lgani va Business API
    kerak bo'lmagani uchun qoladi. Inline (do'stlar orasidagi private chat)
    oqimi endi to'liq `tabrik_business.handle_claim`ga topshiriladi — u
    Telegram Business API orqali AYNAN o'sha private chatga audio + 5
    emoji (message_effect_id bilan, ketma-ket o'chiriladigan) + yakuniy
    matn yuboradi (batafsili: tabrik_business.py docstring'i).
    """
    await tabrik_business.handle_claim(update, context)

# ============================================================
# 💎 OG'IR OQIM: /pro (inline) — /tabrik bilan BIR XIL naqsh, lekin
# yakuniy bosqichdan OLDIN foydalanuvchining shaxsiy rasmlari (agar
# bo'lsa) slайд-shou qilinadi. Qarang: handlers/pro_tabrik.py'dagi
# batafsil arxitektura izohi (nega placeholder HUJJAT turida ekani).
# ============================================================

PRO_SLIDESHOW_DELAY = 1.0
PRO_REVERT_DELAY_SEC = 120

_ACTIVE_INLINE_PRO: set[str] = set()
_INLINE_PRO_REVERT_TASKS: dict[str, asyncio.Task] = {}


def _pro_ready_markup(short_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎁 Tabriknomani qabul qilish", callback_data=f"iprotabrik:claim:{short_id}")
    ]])


async def inline_pro_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    inline_message_id = query.inline_message_id
    if not inline_message_id:
        await query.answer("⚠️ Bu tugma faqat inline xabarlar uchun.", show_alert=True)
        return

    short_id = query.data.split(":", 2)[2]
    entry = pro_tabrik_logic.get_pro_greeting(short_id)
    if not entry:
        await query.answer("⚠️ Bu tabrikning muddati o'tgan.", show_alert=True)
        return

    if inline_message_id in _ACTIVE_INLINE_PRO:
        await query.answer("⏳ Animatsiya allaqachon ketmoqda...", show_alert=False)
        return

    pending_revert = _INLINE_PRO_REVERT_TASKS.pop(inline_message_id, None)
    if pending_revert:
        pending_revert.cancel()

    pro_tabrik_logic.touch_pro_greeting(short_id)
    _ACTIVE_INLINE_PRO.add(inline_message_id)
    await query.answer("💎 Ochilmoqda...")

    try:
        for n in (5, 4, 3, 2, 1):
            await _safe_edit_caption_inline(context, inline_message_id, tabrik_logic.build_countdown_frame(n))
            await asyncio.sleep(TABRIK_COUNTDOWN_DELAY)

        for step in range(tabrik_logic.TOTAL_ROTATION_FRAMES):
            await _safe_edit_caption_inline(context, inline_message_id, tabrik_logic.build_circle_frame(step))
            await asyncio.sleep(TABRIK_FRAME_DELAY)

        for i, url in enumerate(entry["photos"]):
            try:
                await context.bot.edit_message_media(
                    inline_message_id=inline_message_id,
                    media=InputMediaPhoto(media=url, caption=f"📷 {i + 1}/{len(entry['photos'])}"),
                )
            except Exception as e:
                logger.warning(f"🔍 Inline /pro slайд-shou: rasmni ko'rsatib bo'lmadi ({url}): {type(e).__name__}: {e}")
            await asyncio.sleep(PRO_SLIDESHOW_DELAY)

        final_text = f"{tabrik_logic.build_final_card(escape_markdown(entry['text'], version=1))}\n\n🤖 Talaba AI — @{BOT_USERNAME}"
        await _safe_edit_caption_inline(context, inline_message_id, final_text, reply_markup=INLINE_MESSAGE_MARKUP)
        logger.info(f"🔍 Inline /pro animatsiyasi muvaffaqiyatli yakunlandi (inline_message_id={inline_message_id}).")

        task = asyncio.create_task(_schedule_inline_pro_revert(context, inline_message_id, short_id))
        _INLINE_PRO_REVERT_TASKS[inline_message_id] = task
    except Exception as e:
        logger.error(f"🔍 Inline /pro animatsiyasida kutilmagan xato: {type(e).__name__}: {e}", exc_info=True)
        try:
            await _safe_edit_caption_inline(
                context, inline_message_id, f"🎁 {escape_markdown(entry['text'], version=1)}\n\n🤖 Talaba AI — @{BOT_USERNAME}",
                reply_markup=INLINE_MESSAGE_MARKUP,
            )
        except Exception:
            pass
    finally:
        _ACTIVE_INLINE_PRO.discard(inline_message_id)


async def _safe_edit_caption_inline(context: ContextTypes.DEFAULT_TYPE, inline_message_id: str, caption: str, reply_markup=None) -> None:
    """_safe_edit_text bilan BIR XIL maqsad, lekin caption uchun (chunki
    /pro placeholder har doim MEDIA — hujjat/rasm — bo'lib qoladi, matn
    emas)."""
    try:
        await context.bot.edit_message_caption(
            inline_message_id=inline_message_id, caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup,
        )
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"🔍 Inline /pro caption edit qilib bo'lmadi: {e}")
    except Exception as e:
        logger.warning(f"🔍 Inline /pro caption edit qilishda kutilmagan xato: {type(e).__name__}: {e}")


async def _schedule_inline_pro_revert(context: ContextTypes.DEFAULT_TYPE, inline_message_id: str, short_id: str) -> None:
    try:
        await asyncio.sleep(PRO_REVERT_DELAY_SEC)
        await context.bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaDocument(
                media=f"{PUBLIC_BASE_URL}/placeholder.pdf",
                caption=pro_tabrik_logic.build_ready_card(),
            ),
            reply_markup=_pro_ready_markup(short_id),
        )
        logger.info(f"🔍 Inline /pro xabari asl holatga qaytarildi (inline_message_id={inline_message_id}).")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"🔍 Inline /pro xabarini asl holatga qaytarishda xato: {type(e).__name__}: {e}")
    finally:
        _INLINE_PRO_REVERT_TASKS.pop(inline_message_id, None)