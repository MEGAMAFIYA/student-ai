"""
Sozlamalar: har bir funksiya uchun alohida AI provider/model/key, PLYUS har
bir provider (gemini, groq) uchun bir nechta API kalitdan iborat "kalitlar
to'plami" (KEY_POOLS) — biri kunlik/oylik limitga urilganda ai_clients.py
avtomatik ravishda navbatdagi kalitga o'tadi.

Ishga tushganda .env fayldan (yoki Render Environment Variables'dan) o'qiladi,
so'ng runtime_ai_config.json (agar mavjud bo'lsa) ustidan qo'yiladi — bu fayl
Telegram botidagi /developer buyrug'i orqali AI sozlamalari o'zgartirilganda
avtomatik yaratiladi/yangilanadi, shunda o'zgarishlar bot qayta ishga tushganda
ham saqlanib qoladi. Ya'ni AI kalit/model/provider'larni ENDI to'g'ridan-to'g'ri
.env faylni tahrirlamasdan, botdagi /developer menyusi orqali boshqarish mumkin.
"""

import json
import logging
import os
import base64
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

# ============================================================
# ☁️ RENDER API — /developer > RENDER
# ============================================================
# Render API key Account Settings > API Keys'dan olinadi. Hech qachon
# kodga hardcode qilinmaydi yoki GitHub'ga commit qilinmaydi.
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "").strip()
# Ixtiyoriy: tea-... workspace ID. Bo'sh bo'lsa /developer > RENDER
# API orqali ko'rinadigan servislarning ownerId qiymatidan avtomatik aniqlaydi.
RENDER_OWNER_ID = os.getenv("RENDER_OWNER_ID", "").strip()
# Ixtiyoriy: standart servisni oldindan tanlash uchun.
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID", "").strip()
# PDF uchun qancha vaqt ichidagi Render loglari olinadi. Render log retention
# workspace rejasiga bog'liq; API mavjud bo'lgan davr ichidan shu oynani oladi.
RENDER_LOG_PDF_HOURS = max(1, int(os.getenv("RENDER_LOG_PDF_HOURS", "168")))


# /developer buyrug'iga faqat shu Telegram user_id'larga ruxsat beriladi.
# .env da: ADMIN_IDS=123456789,987654321 (vergul bilan, bo'sh joysiz ham bo'ladi)
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip()
}

# Standart (umumiy) bepul kalitlar — funksiyaga alohida kalit berilmasa shular ishlatiladi
DEFAULT_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
DEFAULT_GROQ_KEY = os.getenv("GROQ_API_KEY", "")
# Groq modellari tez-tez eskirib(deprecated) qolishi mumkin (masalan
# llama-3.3-70b-versatile 2026-yil iyunida o'chirildi) — shuning uchun
# zaxira model .env orqali sozlanadi, kodni o'zgartirish shart emas.
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-120b")

# Botning ochiq (https://...) manzili — Render'da "Settings" sahifasida
# ko'rsatilgan URL (masalan https://talaba-ai.onrender.com, OXIRIDA "/" YO'Q).
# Bu FAQAT inline rejimda "og'ir" vazifalar (kurs ishi/PDF) uchun kerak —
# Telegram vaqtinchalik PDF faylni shu manzildan yuklab oladi. Agar bo'sh
# qoldirilsa, inline orqali PDF generatsiyasi ishlamaydi (foydalanuvchi
# botning shaxsiy chatiga yo'naltiriladi), lekin qolgan hamma narsa
# (oddiy savol-javob) baribir ishlayveradi.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# ============================================================
# 🎬 /kino — kino katalogi + Mini App
# ============================================================
# Telegram Cloud Bot API orqali qabul qilinadigan kino hajmi uchun xavfsiz
# limit. Fayl Telegramdagi file_id bilan saqlanadi, shuning uchun kino
# qayta yuklanmaydi.
KINO_MAX_UPLOAD_MB = int(os.getenv("KINO_MAX_UPLOAD_MB", "20"))
KINO_ROOM_TTL_SEC = int(os.getenv("KINO_ROOM_TTL_SEC", str(6 * 60 * 60)))
# Ixtiyoriy: BotFather > Mini App uchun short name. Bo'sh bo'lsa Main Mini App ishlatiladi.
KINO_APP_SHORT_NAME = os.getenv("KINO_APP_SHORT_NAME", "").strip()
# Ixtiyoriy WebRTC TURN. STUN ko‘p tarmoqlarda yetarli, lekin mobil/operator NAT
# holatlarida TURN relay kerak bo‘lishi mumkin.
KINO_TURN_URL = os.getenv("KINO_TURN_URL", "").strip()
KINO_TURN_USERNAME = os.getenv("KINO_TURN_USERNAME", "").strip()
KINO_TURN_CREDENTIAL = os.getenv("KINO_TURN_CREDENTIAL", "").strip()
GAME_TURN_URL = os.getenv("GAME_TURN_URL", "").strip()
GAME_TURN_USERNAME = os.getenv("GAME_TURN_USERNAME", "").strip()
GAME_TURN_CREDENTIAL = os.getenv("GAME_TURN_CREDENTIAL", "").strip()


# ============================================================
# 🤖 Bot mention orqali kelgan buyruqlarni aniqlash uchun username.
# ============================================================
# Ishga tushganda bot.py (_post_init) `application.bot.get_me()` orqali
# HAQIQIY (Telegram'dan tasdiqlangan) usernameni oladi va
# handlers/mention_dispatch.py ichiga yozadi — bu yerdagi qiymat FAQAT
# o'sha so'rov biror sababga ko'ra muvaffaqiyatsiz bo'lsa ishlatiladigan
# zaxira (fallback) qiymat.
BOT_USERNAME_FALLBACK = os.getenv("BOT_USERNAME", "Student_ai_uz_bot").lstrip("@")

# ============================================================
# 🎬 /vid — video yuklab olish (yt-dlp)
# ============================================================
# Telegram Bot API oddiy (local bot API server'siz) botlar uchun ~50MB
# fayl yuklash chegarasini qo'yadi — shuning uchun undan xavfsiz pastroq
# chegara bilan cheklaymiz, aks holda yuklab olingandan keyin yuborishda
# xato chiqadi (vaqt va trafik behuda ketadi).
VID_MAX_MB = int(os.getenv("VID_MAX_MB", "45"))
VID_DOWNLOAD_TIMEOUT_SEC = int(os.getenv("VID_DOWNLOAD_TIMEOUT_SEC", "180"))

# ============================================================
# 🎵 /qo'shiq — musiqa qidirish va audio yuborish (yt-dlp, ytsearch)
# ============================================================
# MUHIM: bu son FAOL manba TOIFALARI orasida taqsimlanadi (video_tools.py >
# search_tracks), keyin dublikatlar olib tashlanadi va oxirida shu son
# bilan cheklanadi — shuning uchun "kamida 10 ta natija" maqsadiga
# yetish uchun bufer bilan (10 dan sal yuqori) belgilangan: ba'zi
# natijalar dublikat/yaroqsiz (masalan bloklangan SoundCloud treki)
# sifatida chiqarib tashlanishi mumkin.
QOSHIQ_SEARCH_COUNT = int(os.getenv("QOSHIQ_SEARCH_COUNT", "14"))
QOSHIQ_DOWNLOAD_TIMEOUT_SEC = int(os.getenv("QOSHIQ_DOWNLOAD_TIMEOUT_SEC", "120"))
# Inline /qo'shiq qidiruvi Telegram inline query muddati tugashidan oldin
# javob berishi kerak. 8.5 soniya — qidiruvni 7 soniyada bekor qilib yuborishdan
# ko'ra biroz ko'proq imkon beradi, lekin Telegram javob muddati uchun xavfsiz
# zaxira qoldiradi. Render/Telegram tarmog'i sekin bo'lsa ham, qiymatni ENV orqali
# 5-9 soniya oralig'ida sozlash mumkin.
try:
    QOSHIQ_INLINE_SEARCH_TIMEOUT_SEC = min(
        9.0, max(5.0, float(os.getenv("QOSHIQ_INLINE_SEARCH_TIMEOUT_SEC", "8.5")))
    )
except (TypeError, ValueError):
    QOSHIQ_INLINE_SEARCH_TIMEOUT_SEC = 8.5

# Render'da yangi deploy ishga tushayotgan paytda eski instance bir necha soniya
# hali getUpdates qilayotgan bo'lishi mumkin. PTB bootstrapping bosqichida qayta
# urinish shu qisqa overlap sababli keladigan Telegram Conflict'ni yumshatadi.
# Bu duplicate bot instance'ni yashirmaydi: retries tugasa xato yana ko'rinadi.
try:
    POLLING_BOOTSTRAP_RETRIES = max(
        0, int(os.getenv("POLLING_BOOTSTRAP_RETRIES", "10"))
    )
except (TypeError, ValueError):
    POLLING_BOOTSTRAP_RETRIES = 10
# 🐞 MUHIM TUZATISH: bu qiymat avval BU YERDA UMUMAN YO'Q edi, lekin
# handlers/qoshiq.py va handlers/inline_query.py uni `config.QOSHIQ_MAX_MB`
# sifatida ishlatadi — natijada HAR BIR "/qo'shiq" yuklab olish urinishi
# (natija tanlangan zahoti) `AttributeError: module 'config' has no
# attribute 'QOSHIQ_MAX_MB'` bilan yiqilardi (bu argument sifatida
# `asyncio.to_thread(...)`ga uzatilishidan OLDIN, chaqiruv joyida
# baholanadi). Telegram Bot API oddiy bot uchun ~50MB yuborish chegarasi
# qo'yadi (qarang: VID_MAX_MB yuqorida) — audio uchun ham xuddi shu
# xavfsiz chegara qo'llaniladi.
QOSHIQ_MAX_MB = int(os.getenv("QOSHIQ_MAX_MB", "45"))
# Qidiruv natijalari xotirada shuncha soniya saqlanadi (tugmalar shundan
# keyin "muddati o'tgan" bo'lib qoladi) — xotira cheksiz o'sib ketmasligi uchun.
QOSHIQ_SESSION_TTL_SEC = int(os.getenv("QOSHIQ_SESSION_TTL_SEC", "600"))

# ============================================================
# ⏳ "/qoshiq" va "/vid" — ikki bosqichli kiritish
# ============================================================
# "/qoshiq" yoki "/vid" argumentsiz yuborilsa, bot so'rov matnini
# so'raydi va KEYINGI oddiy xabarni shu son soniya davomida kutadi
# (qarang: pending_input.py). Vaqt o'tsa, kutish holati bekor bo'ladi —
# foydalanuvchi qaytadan buyruqni yozishi kerak bo'ladi.
PENDING_INPUT_TTL_SEC = int(os.getenv("PENDING_INPUT_TTL_SEC", "300"))

# ============================================================
# 📡 Telegram public kontentidan qidirish (MTProto, Telethon) — IXTIYORIY
# ============================================================
# Bu FAQAT foydalanuvchi (bot EMAS) hisobi orqali, Telegram tomonidan
# PUBLIC deb belgilangan kanal/guruhlarni qidirish uchun — yopiq/xususiy
# guruhlarga ruxsatsiz kirish YOKI Telegram cheklovlarini chetlab
# o'tish UCHUN EMAS. Quyidagi 4 ta sozlama HAMMASI to'ldirilmaguncha
# (TG_API_ID, TG_API_HASH, TG_SESSION, TG_SEARCH_CHANNELS) bu manba
# butunlay O'CHIRILGAN holda qoladi — "/qo'shiq" avvalgidek faqat
# YouTube + SoundCloud bilan ishlayveradi, hech narsa buzilmaydi.
#
#   TG_API_ID / TG_API_HASH — https://my.telegram.org/apps dan olinadigan,
#       SHAXSIY Telegram user account'ga tegishli MTProto ilova
#       kalitlari (bot tokeni EMAS).
#   TG_SESSION — Telethon StringSession qiymati (oldindan, LOKAL
#       muhitda, bir marta login qilib olinadi — pastdagi eslatmaga
#       qarang). Hech qachon kodga hardcode qilinmaydi, faqat Render
#       Environment Variable / Secret sifatida saqlanadi.
#   TG_SEARCH_CHANNELS — qidiriladigan PUBLIC kanal/guruh username'lari,
#       vergul bilan ajratilgan (masalan: "ozbek_qoshiqlari,musiqa_arxivi").
#       Aniq shu ro'yxatdagilar (yoki foydalanuvchi hisobi allaqachon
#       a'zo bo'lgan public kanallar) qidiriladi — Telegram'da GLOBAL
#       qidiruv MTProto orqali ham imkonsiz, shu sababli ro'yxat SHART.
TG_API_ID = os.getenv("TG_API_ID", "")
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION = os.getenv("TG_SESSION", "")
TG_SEARCH_CHANNELS = [
    c.strip().lstrip("@") for c in os.getenv("TG_SEARCH_CHANNELS", "").split(",") if c.strip()
]
TG_SEARCH_TIMEOUT_SEC = int(os.getenv("TG_SEARCH_TIMEOUT_SEC", "15"))
TG_SEARCH_ENABLED = bool(TG_API_ID and TG_API_HASH and TG_SESSION and TG_SEARCH_CHANNELS)

# ============================================================
# 🎵 /qo'shiq — qidiruv manbalarini alohida YOQISH/O'CHIRISH
# (/developer > 🎵 Qo'shiq qidirish)
# ============================================================
# Admin har bir manbani (YouTube, Web — hozircha SoundCloud, Telegram)
# mustaqil yoqib/o'chira oladi. Bu FAQAT ko'rinish emas — video_tools.py
# ichidagi search_tracks() OFF qilingan manbaga UMUMAN so'rov yubormaydi
# (qarang: video_tools.py > SEARCH_SOURCES va search_tracks()).
#
# MUHIM: Telegram manbasi bu yerda ON bo'lsa ham, u baribir
# TG_SEARCH_ENABLED (yuqoridagi 4 ta ENV to'liq sozlanganmi) bilan BIRGA
# ishlaydi — ikkalasi ham True bo'lgandagina haqiqatan qidiriladi.
#
# Qiymatlar boshqa runtime AI sozlamalari bilan bir xil mexanizm orqali
# (_load_runtime_overrides/_save_runtime_overrides, pastga qarang) doimiy
# saqlanadi — bot restart/redeploy qilinganda yo'qolmaydi (agar Upstash/
# Neon/GitHub sozlangan bo'lsa).
MUSIC_SEARCH_SOURCE_IDS = ("youtube", "web", "telegram")
MUSIC_SEARCH_SOURCE_LABELS = {
    "youtube": "🎬 YouTube",
    "web": "🌐 Web",
    "telegram": "📱 Telegram",
}
# Default holat — hammasi YOQILGAN.
MUSIC_SEARCH_SOURCES: dict[str, bool] = {sid: True for sid in MUSIC_SEARCH_SOURCE_IDS}

# ============================================================
# 🎁 /tabrik + /pro — umumiy admin sozlamalari
# ============================================================
# Bu sozlamalar /developer > 💎 Pro/Tabrik orqali boshqariladi va
# runtime_ai_config.json/Upstash/Neon/GitHub orqali doimiy saqlanadi.
DEFAULT_TABRIK_EMOJIS = ["😍", "🥳", "🎉", "❤️", "✨"]
TABRIK_EMOJI_DELAY_SEC = int(os.getenv("TABRIK_EMOJI_DELAY_SEC", "2"))
TABRIK_REVERT_MINUTES = int(os.getenv("TABRIK_REVERT_MINUTES", "2"))
TABRIK_AUDIO_FILE_ID = os.getenv("TABRIK_AUDIO_FILE_ID", "")

def get_tabrik_settings() -> dict:
    return {
        "emojis": list(DEFAULT_TABRIK_EMOJIS),
        "emoji_delay": TABRIK_EMOJI_DELAY_SEC,
        "revert_minutes": TABRIK_REVERT_MINUTES,
        "audio_file_id": TABRIK_AUDIO_FILE_ID,
    }

def set_tabrik_setting(key: str, value) -> bool:
    global TABRIK_EMOJI_DELAY_SEC, TABRIK_REVERT_MINUTES, TABRIK_AUDIO_FILE_ID
    if key == "emojis":
        if not isinstance(value, list) or len(value) != 5 or any(not str(x).strip() for x in value):
            return False
        DEFAULT_TABRIK_EMOJIS[:] = [str(x) for x in value]
    elif key == "emoji_delay":
        value = int(value)
        if value not in range(1, 7): return False
        TABRIK_EMOJI_DELAY_SEC = value
    elif key == "revert_minutes":
        value = int(value)
        if value not in range(1, 5): return False
        TABRIK_REVERT_MINUTES = value
    elif key == "audio_file_id":
        TABRIK_AUDIO_FILE_ID = str(value or "")
    else:
        return False
    _save_runtime_overrides()
    logger.info(f"[DEVELOPER] tabrik/pro sozlamasi '{key}' yangilandi.")
    return True


def is_music_source_enabled(source_id: str) -> bool:
    """Noma'lum source_id uchun ham xavfsiz — ON deb hisoblaydi (yangi
    manba qo'shilib, sozlama hali saqlanmagan bo'lsa ham /qo'shiq
    ishlashda davom etishi uchun)."""
    return bool(MUSIC_SEARCH_SOURCES.get(source_id, True))


def set_music_search_source(source_id: str, enabled: bool) -> bool:
    """/developer > 🎵 Qo'shiq qidirish orqali bitta manbani yoqadi/
    o'chiradi va darhol doimiy saqlashga yozadi. Muvaffaqiyatli bo'lsa True."""
    if source_id not in MUSIC_SEARCH_SOURCES:
        return False
    MUSIC_SEARCH_SOURCES[source_id] = bool(enabled)
    _save_runtime_overrides()
    logger.info(f"[DEVELOPER] Qo'shiq qidirish manbasi '{source_id}' -> {'ON' if enabled else 'OFF'}.")
    return True


def _cfg(prefix: str, default_model: str, default_provider: str = "gemini") -> dict:
    """
    <PREFIX>_PROVIDER, <PREFIX>_MODEL, <PREFIX>_API_KEY, <PREFIX>_BASE_URL
    environment o'zgaruvchilaridan har bir funksiya uchun sozlama yig'adi.
    Bular FAQAT ishga tushishdagi BOSHLANG'ICH (fallback) qiymatlar —
    keyinchalik /developer orqali o'zgartirilgan bo'lsa, runtime_ai_config.json
    dagi qiymatlar bular ustidan qo'yiladi (pastga, _load_runtime_overrides()
    ga qarang). Bu qiymatlar faqat KEY_POOLS bo'sh bo'lgan providerlar uchun
    ishlatiladi — agar providerga kalitlar to'plami qo'shilgan bo'lsa, ai_clients.py
    o'sha to'plamni ustuvor qiladi (pastga qarang).
    """
    provider = os.getenv(f"{prefix}_PROVIDER", default_provider).lower()

    if provider == "groq":
        default_key = DEFAULT_GROQ_KEY
    else:
        default_key = DEFAULT_GEMINI_KEY

    return {
        "provider": provider,
        "api_key": os.getenv(f"{prefix}_API_KEY", default_key),
        "model": os.getenv(f"{prefix}_MODEL", default_model),
        "base_url": os.getenv(f"{prefix}_BASE_URL", ""),
    }


# Har bir funksiya uchun mustaqil AI sozlamasi (dict OBYEKTLARI — boshqa
# modullar shu OBYEKTNI import qiladi, shuning uchun keyinchalik dict ICHIDAGI
# qiymatlarni o'zgartirish [cfg["model"] = ...] barcha joyda avtomatik ko'rinadi,
# lekin dict'ni BUTUNLAY qayta yaratib almashtirish (config.X_AI = {...}) BOSHQA
# modullardagi eski referensga ta'sir qilmaydi — shu sabab update_ai_field()
# har doim MAVJUD dict'ni .update() bilan o'zgartiradi, hech qachon qayta
# yaratmaydi.)
UNIVERSAL_CHAT_AI = _cfg("UNIVERSAL_CHAT", "gemini-3.6-flash")
COURSE_WORK_AI = _cfg("COURSE_WORK", "gemini-3.6-flash")
TRANSLATE_AI = _cfg("TRANSLATE", "gemini-3.6-flash")
EDIT_PDF_AI = _cfg("EDIT_PDF", "gemini-3.6-flash")
GUIDE_AI = _cfg("GUIDE", "gemini-3.6-flash")
VISION_AI = _cfg("VISION", "gemini-3.6-flash")

# ---- Yangi funksiyalar uchun AI sozlamalari ----
# PPTX/QUIZ/ESSAY/SUMMARY/GRAMMAR/CITATION — matn asosida ishlaydi, istalgan
# providerga o'tkazish mumkin (/developer orqali). SOLVE va VOICE — rasm/audio
# qabul qiladi, shuning uchun HAR DOIM Gemini bo'lishi kerak (multimodal —
# boshqa provayderlar bu loyihada rasm/audio qabul qilmaydi), lekin ADMIN
# xohlasa boshqa Gemini kalitiga/modeliga almashtirishi mumkin.
PPTX_AI = _cfg("PPTX", "gemini-3.6-flash")
QUIZ_AI = _cfg("QUIZ", "gemini-3.6-flash")
ESSAY_AI = _cfg("ESSAY", "gemini-3.6-flash")
SUMMARY_AI = _cfg("SUMMARY", "gemini-3.6-flash")
GRAMMAR_AI = _cfg("GRAMMAR", "gemini-3.6-flash")
CITATION_AI = _cfg("CITATION", "gemini-3.6-flash")
SOLVE_AI = _cfg("SOLVE", "gemini-3.6-flash", default_provider="gemini")
VOICE_AI = _cfg("VOICE", "gemini-3.6-flash", default_provider="gemini")

# /developer menyusida ko'rinadigan nom va tartib shu yerdan olinadi.
AI_FUNCTION_LABELS = {
    "UNIVERSAL_CHAT": "💬 Universal chat",
    "COURSE_WORK": "📘 Kurs ishi",
    "TRANSLATE": "🌐 Tarjima",
    "EDIT_PDF": "📝 PDF tahrirlash",
    "GUIDE": "📖 Qo'llanma",
    "VISION": "👁 Rasm tahlili (Vision)",
    "PPTX": "📊 Taqdimot (PPTX)",
    "QUIZ": "📋 Test/Viktorina",
    "ESSAY": "🗒 Referat/Insho",
    "SUMMARY": "📑 Konspekt qisqartirish",
    "GRAMMAR": "✅ Imlo tekshirish",
    "CITATION": "📚 Iqtibos generatori",
    "SOLVE": "🧮 Masala yechish",
    "VOICE": "🎙 Ovozli xabar",
}

# Prefiks -> tegishli cfg dict. /developer shu orqali ishlaydi.
AI_FUNCTIONS = {
    "UNIVERSAL_CHAT": UNIVERSAL_CHAT_AI,
    "COURSE_WORK": COURSE_WORK_AI,
    "TRANSLATE": TRANSLATE_AI,
    "EDIT_PDF": EDIT_PDF_AI,
    "GUIDE": GUIDE_AI,
    "VISION": VISION_AI,
    "PPTX": PPTX_AI,
    "QUIZ": QUIZ_AI,
    "ESSAY": ESSAY_AI,
    "SUMMARY": SUMMARY_AI,
    "GRAMMAR": GRAMMAR_AI,
    "CITATION": CITATION_AI,
    "SOLVE": SOLVE_AI,
    "VOICE": VOICE_AI,
}

# Qo'llab-quvvatlanadigan AI provayderlar. "gemini" — Google SDK orqali
# (google.generativeai). "cloudflare" — maxsus (account_id + api_key
# birgalikda kerak). Qolganlarning BARCHASI OpenAI-mos (compatible)
# /chat/completions API'ga ega — bittа umumiy funksiya orqali ishlaydi
# (ai_clients._call_openai_compatible), faqat BASE_URL farq qiladi.
SUPPORTED_PROVIDERS = [
    "gemini", "groq", "mistral", "openrouter", "cerebras",
    "cloudflare", "sambanova", "cohere", "huggingface", "nvidia", "vercel",
]

# /developer menyusida tugma va matnlarda ko'rsatiladigan to'liq nom
# (berilmagan provider uchun oddiy .capitalize() ishlatiladi).
PROVIDER_LABELS = {
    "gemini": "Gemini",
    "groq": "Groq",
    "mistral": "Mistral",
    "openrouter": "OpenRouter",
    "cerebras": "Cerebras",
    "cloudflare": "Cloudflare",
    "sambanova": "SambaNova",
    "cohere": "Cohere",
    "huggingface": "Hugging Face",
    "nvidia": "NVIDIA NIM",
    "vercel": "Vercel AI Gateway",
}

# Har bir provider uchun BEPUL API kalit olinadigan rasmiy sahifa —
# /developer dagi "Yangi kalit qo'shish" ekranida ko'rsatiladi.
PROVIDER_KEY_LINKS = {
    "gemini": "https://aistudio.google.com/apikey",
    "groq": "https://console.groq.com/keys",
    "mistral": "https://console.mistral.ai/api-keys",
    "openrouter": "https://openrouter.ai/keys",
    "cerebras": "https://cloud.cerebras.ai",
    "cloudflare": "https://dash.cloudflare.com (Account ID + Workers AI API Token)",
    "sambanova": "https://cloud.sambanova.ai/apis",
    "cohere": "https://dashboard.cohere.com/api-keys",
    "huggingface": "https://huggingface.co/settings/tokens",
    "nvidia": "https://build.nvidia.com",
    "vercel": "https://vercel.com/docs/ai-gateway",
}

# OpenAI-mos provayderlarning standart /v1 bazaviy manzili. Gemini google SDK
# orqali ishlagani uchun bu yerda yo'q; Cloudflare account_id'ga bog'liq
# bo'lgani uchun dinamik tuziladi (ai_clients._resolve_cloudflare ga qarang).
PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "sambanova": "https://api.sambanova.ai/v1",
    "cohere": "https://api.cohere.ai/compatibility/v1",
    "huggingface": "https://router.huggingface.co/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "vercel": "https://ai-gateway.vercel.sh/v1",
}

# Yangi kalit qo'shilganda, model alohida so'ralmaydi — shu standart model
# tayinlanadi (keyin kalitni ochib "Modelni o'zgartirish" bilan o'zgartirish
# mumkin — bu yerdagi nomlar faqat boshlang'ich taklif, hech biri "abadiy
# to'g'ri" degani emas, chunki bepul model nomlari tez-tez o'zgaradi).
DEFAULT_MODEL_BY_PROVIDER = {
    "gemini": "gemini-3.6-flash",
    "groq": GROQ_FALLBACK_MODEL,
    "mistral": "mistral-small-latest",
    "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
    "cerebras": "llama3.1-8b",
    "cloudflare": "@cf/meta/llama-3.1-8b-instruct",
    "sambanova": "Meta-Llama-3.1-8B-Instruct",
    "cohere": "command-r-plus",
    "huggingface": "meta-llama/Llama-3.1-8B-Instruct",
    "nvidia": "meta/llama-3.1-8b-instruct",
    "vercel": "openai/gpt-4o-mini",
}

MAX_TELEGRAM_TEXT = 3800

# ⏰ Eslatmalar funksiyasi vaqtlarni shu (soat) siljish bilan LOKAL vaqt deb
# tushunadi (Telegram foydalanuvchining aniq timezone'ini bermaydi, shuning
# uchun butun bot BITTA umumiy timezone bilan ishlaydi — standart: O'zbekiston,
# Toshkent, UTC+5). Kerak bo'lsa .env orqali o'zgartiring.
REMINDER_TZ_OFFSET_HOURS = float(os.getenv("REMINDER_TZ_OFFSET_HOURS", "5"))

# ============================================================
# KALITLAR TO'PLAMI (KEY POOLS) — /developer > 🔑 AI kalitlari orqali
# ============================================================
# Har bir provider uchun ro'yxat: [{"key": "...", "model": "..."}, ...]
# Ro'yxat TARTIBI muhim — 1-kalit, 2-kalit... shu tartibda birin-ketin
# sinaladi (ai_clients.ask_ai ichida). Bo'sh ro'yxat — pool ishlatilmaydi,
# o'sha holda AI_FUNCTIONS'dagi eski bitta-kalit rejimi ishlaydi (moslik uchun).
KEY_POOLS: dict[str, list[dict]] = {p: [] for p in SUPPORTED_PROVIDERS}

# ============================================================
# RUNTIME AI SOZLAMALARI (/developer orqali o'zgartiriladi, .env EMAS)
# ============================================================
# MUHIM: Render (va ko'pchilik "serverless"/bepul hosting)da disk vaqtinchalik
# (ephemeral) — har safar YANGI DEPLOY qilinganda butun fayl tizimi git
# repodan qaytadan tiklanadi, shuning uchun runtime paytida yozilgan har
# qanday fayl (masalan runtime_ai_config.json) O'CHIB KETADI. /developer
# orqali qo'shilgan kalitlar shu sababli deploydan keyin yo'qolib turardi.
#
# Buni tuzatish uchun tashqi, DOIMIY (persistent) saqlash — Upstash Redis
# (bepul, cheksiz muddatli tarif) ishlatiladi. Agar quyidagi ikki .env
# o'zgaruvchi sozlangan bo'lsa, hamma narsa Upstash'da saqlanadi (deploy
# qilsangiz ham o'chmaydi):
#
#   UPSTASH_REDIS_REST_URL=https://xxxx.upstash.io
#   UPSTASH_REDIS_REST_TOKEN=********
#
# Ularni https://console.upstash.com dan bepul Redis database yaratib,
# "REST API" bo'limidan olasiz (ro'yxatdan o'tish GitHub/Google orqali,
# karta shart emas). Agar bu ikkisi sozlanmagan bo'lsa, kod avtomatik
# ravishda ESKI (mahalliy fayl) rejimga qaytadi — lokal/rivojlanish uchun
# ishlaydi, lekin Render'da deploy qilinganda o'chib ketadi degani.
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
USE_UPSTASH = bool(UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN)
_UPSTASH_KEY = "student_ai_runtime_config"

# ------------------------------------------------------------
# IKKINCHI VARIANT (Upstash sozlanmasa): GitHub repo'ga avtomatik yozish.
# GITHUB_TOKEN (repo yozish huquqiga ega "Fine-grained" yoki "classic"
# personal access token) va GITHUB_REPO ("username/repo" ko'rinishida)
# .env/Render Environment'da sozlansa, /developer orqali qilingan HAR BIR
# o'zgarish (kalit qo'shish, model o'zgartirish va h.k.) avtomatik ravishda
# shu GitHub repo'siga (GitHub Contents API orqali) COMMIT qilinadi —
# shuning uchun Render qayta deploy qilinganda ham (git repodan qaytadan
# tiklanganda ham) o'zgarishlar YO'QOLMAYDI, chunki ular endi repo'ning
# o'zida saqlanadi.
#
#   GITHUB_TOKEN=ghp_xxxxxxxxxxxx      (Settings > Developer settings >
#                                        Personal access tokens; "repo" yoki
#                                        "Contents: Read and write" huquqi bilan)
#   GITHUB_REPO=foydalanuvchi/repo-nomi
#   GITHUB_BRANCH=main                  (ixtiyoriy, standart "main")
#
# MUHIM: bu usul HAR BIR o'zgarishda repo'ga bitta commit qo'shadi — agar
# kalitlar juda tez-tez o'zgartirilsa, commit tarixi tez to'lishi mumkin
# (zararsiz, lekin bilib qo'ying). Ustuvorlik tartibi: Upstash sozlangan
# bo'lsa — Upstash, aks holda GitHub sozlangan bo'lsa — GitHub, aks holda
# oxirgi chora — mahalliy fayl (Render'da bu FAQAT joriy deploy davomida
# ishlaydi, qayta deployda o'chadi).
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip().strip("/")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip()
USE_GITHUB = bool(GITHUB_TOKEN and GITHUB_REPO)
# Ma'lumot fayllari GitHub repo ichida shu papkada saqlanadi (kod fayllari
# bilan aralashmasligi uchun) — repo'da avtomatik yaratiladi, qo'lda papka
# ochish shart emas.
GITHUB_DATA_DIR = os.getenv("GITHUB_DATA_DIR", "bot_data").strip().strip("/")

# ------------------------------------------------------------
# UCHINCHI VARIANT: Neon (yoki istalgan boshqa) Postgres — HAQIQIY database.
# https://neon.tech da bepul loyiha yarating -> "Connection string" ni
# nusxalang -> Render Environment'ga DATABASE_URL (yoki NEON_DATABASE_URL)
# nomi bilan qo'ying. Kod BIRINCHI ishlatilganda avtomatik ravishda kerakli
# jadvalni ("student_ai_kv") o'zi yaratadi — qo'lda SQL yozish shart emas.
#
#   DATABASE_URL=postgresql://user:pass@ep-xxxx.neon.tech/dbname?sslmode=require
#
# (Ko'p hostinglar, jumladan Render'ning o'z Postgres qo'shimchasi ham, shu
# DATABASE_URL nomini avtomatik beradi — shuning uchun ikkala nom ham
# qo'llab-quvvatlanadi.)
NEON_DATABASE_URL = os.getenv("DATABASE_URL", "") or os.getenv("NEON_DATABASE_URL", "")
USE_NEON = bool(NEON_DATABASE_URL)
_neon_table_ready = False

_RUNTIME_CONFIG_FILENAME = "runtime_ai_config.json"

_EDITABLE_FIELDS = ("provider", "model", "api_key", "base_url")
_KEY_FIELDS = ("key", "model")


def _neon_connect():
    """Har chaqiriqda yangi ulanish ochadi (bot kam trafikli bo'lgani uchun
    connection pool shart emas — soddaligi ustunlik). psycopg2 o'rnatilmagan
    bo'lsa, tushunarli xato bilan to'xtaydi (requirements.txt'ga qarang)."""
    try:
        import psycopg2
    except ImportError:
        logger.error(
            "❌ DATABASE_URL sozlangan, lekin 'psycopg2-binary' o'rnatilmagan! "
            "requirements.txt faylida borligini va Render qayta deploy qilinganini tekshiring."
        )
        return None
    try:
        return psycopg2.connect(NEON_DATABASE_URL, connect_timeout=10)
    except Exception as e:
        logger.error(f"❌ Neon/Postgres'ga ulanib bo'lmadi: {type(e).__name__}: {e}")
        return None


def _neon_ensure_table(conn) -> None:
    global _neon_table_ready
    if _neon_table_ready:
        return
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS student_ai_kv ("
            "  key TEXT PRIMARY KEY,"
            "  value TEXT NOT NULL,"
            "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        )
    conn.commit()
    _neon_table_ready = True


def _neon_get(key: str) -> str | None:
    conn = _neon_connect()
    if conn is None:
        return None
    try:
        _neon_ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM student_ai_kv WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"❌ Neon/Postgres'dan '{key}' o'qishda xato: {type(e).__name__}: {e}")
        return None
    finally:
        conn.close()


def _neon_set(key: str, value: str) -> bool:
    conn = _neon_connect()
    if conn is None:
        return False
    try:
        _neon_ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO student_ai_kv (key, value, updated_at) VALUES (%s, %s, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (key, value),
            )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Neon/Postgres'ga '{key}' yozishda xato: {type(e).__name__}: {e}")
        return False
    finally:
        conn.close()


def _upstash_request(command: list) -> dict | None:
    """Upstash Redis REST API'ga bitta buyruq yuboradi (masalan
    ["GET", key] yoki ["SET", key, value]). Xato bo'lsa None qaytaradi —
    chaqiruvchi kod bunga qarab mahalliy faylga qaytishi mumkin."""
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                UPSTASH_REDIS_REST_URL,
                headers={"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"},
                json=command,
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.error(f"Upstash Redis so'rovida xato ({command[0] if command else '?'}): {e}")
        return None


def _github_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_path(filename: str) -> str:
    return f"{GITHUB_DATA_DIR}/{filename}" if GITHUB_DATA_DIR else filename


def _github_contents_url(path: str) -> str:
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"


def _github_get_sha(url: str) -> str | None:
    """Contents API orqali faylning JORIY "sha" qiymatini o'qiydi (PUT
    qilishdan OLDIN chaqiriladi — mavjud faylni yangilash uchun GitHub
    aynan shu "sha"ni talab qiladi, aks holda 409 Conflict qaytaradi).
    Fayl mavjud bo'lmasa (404 — hali yaratilmagan) yoki boshqa xato
    bo'lsa None qaytaradi (chaqiruvchi buni "yangi fayl" deb talqin
    qiladi — 404 uchun bu to'g'ri, boshqa xatolar uchun ham xavfsiz
    tomonga og'ish, chunki keyingi PUT baribir xatoni ko'rsatadi)."""
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(url, headers=_github_headers(), params={"ref": GITHUB_BRANCH})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json().get("sha")
    except Exception as e:
        logger.error(f"GitHub'dan 'sha' o'qishda xato ({url}): {type(e).__name__}: {e}")
        return None


def _github_put_content(path: str, content_bytes: bytes, message: str, max_attempts: int = 3) -> bool:
    """GitHub Contents API orqali BITTA fayl yozadigan YAGONA umumiy
    funksiya — barcha GitHub'ga yozuvchi joylar (`_github_write_file`,
    `github_upload_binary`, `github_write_text_file`) shu orqali ishlaydi,
    409 Conflict xatosi FAQAT shu bitta joyda tuzatiladi.

    Mantiq (fayl mavjud bo'lsa/bo'lmasa ikkalasi uchun ham):
      1. Avval GET bilan faylning JORIY "sha"sini olamiz (mavjud bo'lsa).
      2. PUT so'rovida: fayl mavjud bo'lsa "sha" YUBORILADI (YANGILASH),
         mavjud bo'lmasa "sha" YUBORILMAYDI (YANGI FAYL YARATISH).
      3. Agar GitHub 409 Conflict qaytarsa — bu SHA ESKIRGANI (masalan
         parallel ikkita so'rov bir vaqtda yozganda) degani. Bunday holda
         faylni QAYTA GET qilib YANGI "sha" olamiz va PUT'ni qayta
         urinamiz (`max_attempts` marta, so'nggisida ham 409 bo'lsa xato
         qaytariladi va log yoziladi)."""
    if not USE_GITHUB:
        return False
    url = _github_contents_url(path)
    encoded_content = base64.b64encode(content_bytes).decode("ascii")

    import random
    import time
    effective_attempts = max(6, max_attempts)
    for attempt in range(1, effective_attempts + 1):
        sha = _github_get_sha(url)
        body = {
            "message": message,
            "content": encoded_content,
            "branch": GITHUB_BRANCH,
        }
        if sha:
            body["sha"] = sha
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.put(url, headers=_github_headers(), json=body)
            if r.status_code == 409:
                delay = min(0.25 * (2 ** min(attempt - 1, 4)), 4.0) + random.uniform(0.05, 0.25)
                logger.warning(
                    f"⚠️ GitHub 409 Conflict ('{path}') — SHA eskirgan yoki parallel yozuv bor. "
                    f"Yangi SHA olinib {delay:.2f}s dan keyin qayta uriniladi "
                    f"({attempt}/{effective_attempts})..."
                )
                time.sleep(delay)
                continue
            r.raise_for_status()
            return True
        except httpx.HTTPStatusError as e:
            logger.error(
                f"❌ GitHub'ga '{path}' yozishda xato: {e.response.status_code} "
                f"{e.response.text[:300]}"
            )
            return False
        except Exception as e:
            logger.error(f"❌ GitHub'ga '{path}' yozishda kutilmagan xato: {type(e).__name__}: {e}")
            return False

    logger.error(
        f"❌ GitHub'ga '{path}' yozib bo'lmadi — {effective_attempts} urinishdan keyin ham "
        "409 Conflict davom etmoqda (SHA doimo eskirib qolmoqda, ehtimol bir nechta "
        "so'rov juda tez-tez yozmoqda)."
    )
    return False


def _github_read_file(filename: str) -> str | None:
    """GitHub Contents API orqali repo'dagi faylni o'qiydi. Fayl mavjud
    bo'lmasa (404 — birinchi marta ishga tushirilyapti) yoki xato bo'lsa
    None qaytaradi."""
    url = _github_contents_url(_github_path(filename))
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(url, headers=_github_headers(), params={"ref": GITHUB_BRANCH})
            if r.status_code == 404:
                logger.info(f"GitHub'da '{filename}' hali mavjud emas (birinchi marta ishga tushirilyapti).")
                return None
            r.raise_for_status()
            data = r.json()
            return base64.b64decode(data["content"]).decode("utf-8")
    except Exception as e:
        logger.error(f"GitHub'dan '{filename}' o'qishda xato: {type(e).__name__}: {e}")
        return None


def _github_write_file(filename: str, content: str, message: str) -> bool:
    """GitHub Contents API orqali repo'ga fayl yozadi (mavjud bo'lsa
    YANGILAYDI, aks holda YARATADI) — bitta YANGI COMMIT sifatida.
    409 Conflict kelsa `_github_put_content` avtomatik qayta urinadi
    (qarang: yuqoridagi funksiya izohi)."""
    return _github_put_content(_github_path(filename), content.encode("utf-8"), message)


def persist_read(local_filename: str, upstash_key: str) -> tuple[str | None, str]:
    """Umumiy o'qish funksiyasi — config.py (AI sozlamalari) VA storage.py
    (fayllar tarixi/statistika/eslatmalar) shu orqali ishlaydi. Ustuvorlik:
    Upstash -> Neon (Postgres) -> GitHub -> mahalliy fayl. Qaytaradi:
    (xom matn yoki None, manba nomi)."""
    if USE_UPSTASH:
        resp = _upstash_request(["GET", upstash_key])
        if resp is not None and resp.get("result"):
            return resp["result"], "Upstash Redis"
        if resp is None:
            logger.error(f"Upstash Redis'ga ulanib bo'lmadi ('{local_filename}') — boshqa manbaga o'tilmoqda.")
        else:
            logger.info(f"Upstash Redis'da hali '{local_filename}' uchun ma'lumot yo'q.")

    if USE_NEON:
        raw = _neon_get(upstash_key)
        if raw:
            return raw, "Neon (Postgres)"
        logger.info(f"Neon/Postgres'da hali '{local_filename}' uchun ma'lumot yo'q (yoki ulanish xatosi — yuqoridagi logga qarang).")

    if USE_GITHUB:
        raw = _github_read_file(local_filename)
        if raw:
            return raw, f"GitHub ({GITHUB_REPO})"

    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), local_filename)
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                return f.read(), "mahalliy fayl"
        except Exception as e:
            logger.error(f"'{local_filename}' mahalliy fayldan o'qishda xato: {e}")
    return None, ""


def persist_write(local_filename: str, upstash_key: str, raw: str, commit_message: str = "") -> None:
    """Umumiy yozish funksiyasi — xuddi shu ustuvorlik bilan (Upstash ->
    Neon -> GitHub -> mahalliy fayl) ma'lumotni saqlaydi."""
    if USE_UPSTASH:
        resp = _upstash_request(["SET", upstash_key, raw])
        if resp is None or resp.get("result") != "OK":
            logger.error(f"❌ Upstash Redis'ga '{local_filename}' yozib bo'lmadi — o'zgarish DOIMIY saqlanmagan bo'lishi mumkin!")
        return

    if USE_NEON:
        ok = _neon_set(upstash_key, raw)
        if ok:
            logger.info(f"✅ '{local_filename}' Neon/Postgres'ga muvaffaqiyatli yozildi.")
        else:
            logger.error(f"❌ '{local_filename}' Neon/Postgres'ga yozilmadi — o'zgarish DOIMIY saqlanmagan bo'lishi mumkin! Mahalliy faylga zaxira sifatida yozib qo'yiladi.")
            _write_local_fallback(local_filename, raw)
        return

    if USE_GITHUB:
        ok = _github_write_file(local_filename, raw, commit_message or f"Auto-update {local_filename}")
        if ok:
            logger.info(f"✅ '{local_filename}' GitHub repo'ga ({GITHUB_REPO}, branch={GITHUB_BRANCH}) muvaffaqiyatli yozildi.")
        else:
            logger.error(f"❌ '{local_filename}' GitHub'ga yozilmadi — o'zgarish DOIMIY saqlanmagan bo'lishi mumkin! Mahalliy faylga zaxira sifatida yozib qo'yiladi.")
            _write_local_fallback(local_filename, raw)
        return

    _write_local_fallback(local_filename, raw)


def _write_local_fallback(local_filename: str, raw: str) -> None:
    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), local_filename)
    try:
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(raw)
    except Exception as e:
        logger.error(f"'{local_filename}' mahalliy faylga yozishda xato: {e}")


def _load_runtime_overrides() -> None:
    global TABRIK_EMOJI_DELAY_SEC, TABRIK_REVERT_MINUTES, TABRIK_AUDIO_FILE_ID
    """Bot ishga tushganda chaqiriladi: persist_read() orqali (Upstash ->
    GitHub -> mahalliy fayl ustuvorligida) saqlangan konfiguratsiyani
    o'qiydi. Topilgan qiymatlar .env'dan o'qilgan BOSHLANG'ICH qiymatlar
    ustidan qo'yiladi — shu orqali /developer orqali qilingan o'zgarishlar
    (funksiya sozlamalari HAM, kalitlar to'plami HAM) bot qayta ishga
    tushganda (QAYTA DEPLOY qilinganda ham, agar Upstash yoki GitHub
    sozlangan bo'lsa) yo'qolmaydi.

    Format:
        {"functions": {PREFIX: {provider, model, api_key, base_url}, ...},
         "key_pools": {provider: [{"key":..., "model":...}, ...], ...}}

    Eski (bu funksiya qo'shilishidan oldingi) fayllar "functions" o'rniga
    to'g'ridan-to'g'ri {PREFIX: {...}} shaklida edi — shu format ham
    o'qib qo'llab-quvvatlanadi (key_pools bo'sh deb olinadi)."""
    if not USE_UPSTASH and not USE_NEON and not USE_GITHUB:
        logger.warning(
            "❗️ Hech qanday DOIMIY saqlash (UPSTASH_REDIS_REST_URL/TOKEN, DATABASE_URL "
            "yoki GITHUB_TOKEN/GITHUB_REPO) sozlanmagan — runtime_ai_config.json MAHALLIY "
            "faylga yoziladi. Render kabi vaqtinchalik-disk hostinglarda bu fayl HAR BIR "
            "QAYTA DEPLOYDA o'chib ketadi, ya'ni /developer orqali qo'shilgan kalitlar "
            "YO'QOLADI! Buni oldini olish uchun UCH VARIANTDAN BIRINI sozlang: "
            "(1) Neon Postgres (neon.tech, bepul) — DATABASE_URL, "
            "(2) Upstash Redis (console.upstash.com, bepul) yoki "
            "(3) GITHUB_TOKEN + GITHUB_REPO — shunda o'zgarishlar avtomatik GitHub "
            "repo'siga commit qilinadi."
        )

    raw, source = persist_read(_RUNTIME_CONFIG_FILENAME, _UPSTASH_KEY)
    if not raw:
        return

    try:
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"Runtime konfiguratsiyani JSON qilib o'qishda xato: {e} — .env qiymatlari ishlatiladi.")
        return

    if "functions" in data or "key_pools" in data or "music_search_sources" in data:
        functions_data = data.get("functions", {})
        pools_data = data.get("key_pools", {})
        music_data = data.get("music_search_sources", {})
        tabrik_data = data.get("tabrik_settings", {})
    else:
        functions_data = data  # eski format
        pools_data = {}
        music_data = {}
        tabrik_data = {}

    for sid, enabled in music_data.items():
        if sid in MUSIC_SEARCH_SOURCES and isinstance(enabled, bool):
            MUSIC_SEARCH_SOURCES[sid] = enabled

    if isinstance(tabrik_data, dict):
        if isinstance(tabrik_data.get("emojis"), list) and len(tabrik_data["emojis"]) == 5:
            DEFAULT_TABRIK_EMOJIS[:] = [str(x) for x in tabrik_data["emojis"]]
        if str(tabrik_data.get("emoji_delay", "")).isdigit() and int(tabrik_data["emoji_delay"]) in range(1, 7):
            TABRIK_EMOJI_DELAY_SEC = int(tabrik_data["emoji_delay"])
        if str(tabrik_data.get("revert_minutes", "")).isdigit() and int(tabrik_data["revert_minutes"]) in range(1, 5):
            TABRIK_REVERT_MINUTES = int(tabrik_data["revert_minutes"])
        if "audio_file_id" in tabrik_data:
            TABRIK_AUDIO_FILE_ID = str(tabrik_data.get("audio_file_id") or "")

    for prefix, values in functions_data.items():
        cfg = AI_FUNCTIONS.get(prefix)
        if cfg is None or not isinstance(values, dict):
            continue
        for field in _EDITABLE_FIELDS:
            if field in values:
                cfg[field] = values[field]

    total_keys = 0
    for provider, entries in pools_data.items():
        if provider in KEY_POOLS and isinstance(entries, list):
            KEY_POOLS[provider] = [
                {"key": e.get("key", ""), "model": e.get("model", "")}
                for e in entries if isinstance(e, dict)
            ]
            total_keys += len(KEY_POOLS[provider])

    logger.info(
        f"{source} dan {len(functions_data)} ta funksiya sozlamasi "
        f"va {total_keys} ta AI kalit yuklandi."
    )


def _save_runtime_overrides() -> None:
    """Joriy AI_FUNCTIONS + KEY_POOLS holatini Upstash Redis'ga (sozlangan
    bo'lsa) yoki mahalliy runtime_ai_config.json fayliga yozadi."""
    data = {
        "functions": {prefix: {f: cfg.get(f, "") for f in _EDITABLE_FIELDS} for prefix, cfg in AI_FUNCTIONS.items()},
        "key_pools": {
            provider: [{"key": e.get("key", ""), "model": e.get("model", "")} for e in entries]
            for provider, entries in KEY_POOLS.items()
        },
        "music_search_sources": dict(MUSIC_SEARCH_SOURCES),
        "tabrik_settings": {
            "emojis": list(DEFAULT_TABRIK_EMOJIS),
            "emoji_delay": TABRIK_EMOJI_DELAY_SEC,
            "revert_minutes": TABRIK_REVERT_MINUTES,
            "audio_file_id": TABRIK_AUDIO_FILE_ID,
        },
    }
    raw = json.dumps(data, ensure_ascii=False)
    persist_write(_RUNTIME_CONFIG_FILENAME, _UPSTASH_KEY, raw, commit_message="🔧 /developer: AI sozlamalari/kalitlari yangilandi")


def update_ai_field(prefix: str, field: str, value: str) -> bool:
    """Bitta funksiyaning bitta maydonini (provider/model/api_key/base_url)
    o'zgartiradi va darhol diskka saqlaydi. Muvaffaqiyatli bo'lsa True."""
    if prefix not in AI_FUNCTIONS or field not in _EDITABLE_FIELDS:
        return False
    AI_FUNCTIONS[prefix][field] = value.strip()
    _save_runtime_overrides()
    logger.info(f"[DEVELOPER] {prefix}.{field} o'zgartirildi.")
    return True


def bulk_update_model_by_provider(provider: str, model: str) -> list[str]:
    """Berilgan provider'ga ega BARCHA funksiyalarning MODEL maydonini
    berilgan qiymatga o'zgartiradi. Qaysi funksiyalar o'zgargani (prefikslar
    ro'yxati) qaytariladi."""
    updated = []
    for prefix, cfg in AI_FUNCTIONS.items():
        if cfg.get("provider") == provider:
            cfg["model"] = model.strip()
            updated.append(prefix)
    if updated:
        _save_runtime_overrides()
        logger.info(f"[DEVELOPER] Barcha '{provider}' provider funksiyalari uchun model '{model}' ga o'zgartirildi: {updated}")
    return updated


# ---------- Kalitlar to'plami (KEY_POOLS) bilan ishlash ----------

def add_key(provider: str, key: str, model: str) -> int:
    """Provider kalitlar to'plamiga yangi kalitni RO'YXAT OXIRIGA qo'shadi.
    Qo'shilgan kalitning 1-based tartib raqamini qaytaradi."""
    if provider not in KEY_POOLS:
        KEY_POOLS[provider] = []
    KEY_POOLS[provider].append({"key": key.strip(), "model": model.strip()})
    _save_runtime_overrides()
    idx = len(KEY_POOLS[provider])
    logger.info(f"[DEVELOPER] {provider} kalitlar to'plamiga yangi kalit qo'shildi (#{idx}).")
    return idx


def update_key_field(provider: str, index: int, field: str, value: str) -> bool:
    """index — 1-based. field — 'key' yoki 'model'."""
    pool = KEY_POOLS.get(provider, [])
    if not (1 <= index <= len(pool)) or field not in _KEY_FIELDS:
        return False
    pool[index - 1][field] = value.strip()
    _save_runtime_overrides()
    logger.info(f"[DEVELOPER] {provider} kalit #{index} ning {field} maydoni yangilandi.")
    return True


def delete_key(provider: str, index: int) -> bool:
    """index — 1-based. O'chirilgandan keyin qolgan kalitlar avtomatik qayta
    raqamlanadi (masalan #2 o'chsa, eski #3 endi #2 bo'ladi)."""
    pool = KEY_POOLS.get(provider, [])
    if not (1 <= index <= len(pool)):
        return False
    pool.pop(index - 1)
    _save_runtime_overrides()
    logger.info(f"[DEVELOPER] {provider} kalit #{index} o'chirildi.")
    return True


def bulk_update_pool_models(provider: str, scope: str, model: str) -> list[int]:
    """scope: 'all' (barchasi) | 'odd' (toq: 1,3,5...) | 'even' (juft: 2,4,6...).
    Shu provider kalitlar to'plamidagi mos keladigan kalitlarning MODEL
    maydonini yangilaydi. Qaysi tartib raqamlari (1-based) o'zgargani qaytariladi.

    Maqsad: bitta provider ichida 2 xil model o'rnatish — masalan toq
    kalitlarga model A, juft kalitlarga model B — shunda model A pullik/limitga
    o'tib qolsa, model B bilan ishlaydigan kalitlar baribir ishlab turadi."""
    pool = KEY_POOLS.get(provider, [])
    updated = []
    for i, entry in enumerate(pool, start=1):
        if scope == "all" or (scope == "odd" and i % 2 == 1) or (scope == "even" and i % 2 == 0):
            entry["model"] = model.strip()
            updated.append(i)
    if updated:
        _save_runtime_overrides()
        logger.info(f"[DEVELOPER] {provider} — {scope} kalitlar modeli '{model}' ga o'zgartirildi: {updated}")
    return updated


_load_runtime_overrides()


# ============================================================
# 💳 TO'LOV TIZIMI — Kapitalbank sozlamalari (FAQAT konfiguratsiya interfeysi)
# ============================================================
# MUHIM: bu yerda Kapitalbank'ning haqiqiy API endpoint'lari, so'rov/javob
# formati yoki avtorizatsiya sxemasi HECH QACHON o'ylab topilmagan — faqat
# .env orqali beriladigan SOZLAMA NOMLARI shu yerda e'lon qilingan. Haqiqiy
# qiymatlar (agar mavjud bo'lsa) rasmiy Kapitalbank hujjatlaridan olinishi
# kerak (payment_providers.py'dagi KapitalbankPaymentProvider va
# KapitalbankTransactionVerifier'ga qarang — ular shu sozlamalar orqali
# ishlaydi, lekin haqiqiy HTTP chaqiruvlari hali TODO sifatida qoldirilgan).
#
# Kerakli environment variables (hali BIRORTASI ham majburiy emas — bo'sh
# bo'lsa, adapter "sozlanmagan" holatda ishlaydi va har doim manual_review'ga
# yo'naltiradi):
#   KAPITALBANK_MERCHANT_ID       — savdo nuqtasi/merchant identifikatori
#   KAPITALBANK_TERMINAL_ID       — terminal identifikatori (agar kerak bo'lsa)
#   KAPITALBANK_API_BASE_URL      — API bazaviy manzili (masalan https://...)
#   KAPITALBANK_API_KEY           — API kalit/token
#   KAPITALBANK_API_SECRET        — (agar imzo/HMAC kerak bo'lsa)
#   KAPITALBANK_WEBHOOK_SECRET    — webhook imzosini tekshirish uchun maxfiy kalit
KAPITALBANK_MERCHANT_ID = os.getenv("KAPITALBANK_MERCHANT_ID", "")
KAPITALBANK_TERMINAL_ID = os.getenv("KAPITALBANK_TERMINAL_ID", "")
KAPITALBANK_API_BASE_URL = os.getenv("KAPITALBANK_API_BASE_URL", "")
KAPITALBANK_API_KEY = os.getenv("KAPITALBANK_API_KEY", "")
KAPITALBANK_API_SECRET = os.getenv("KAPITALBANK_API_SECRET", "")
KAPITALBANK_WEBHOOK_SECRET = os.getenv("KAPITALBANK_WEBHOOK_SECRET", "")

# To'lov (bank/paynet) rekvizitlari — foydalanuvchiga "🟡 Bank/Paynet orqali
# o'tkazma" tanlaganda ko'rsatiladi. .env orqali sozlanadi (kod ichida
# to'qib chiqarilmagan haqiqiy hisob raqami/karta bo'lishi kerak).
PAYMENT_CARD_NUMBER = os.getenv("PAYMENT_CARD_NUMBER", "")
PAYMENT_CARD_HOLDER = os.getenv("PAYMENT_CARD_HOLDER", "")
PAYMENT_RECEIVER_NOTE = os.getenv("PAYMENT_RECEIVER_NOTE", "")

# ============================================================
# 💎 Pro obuna (👤 /my — "Mening kabinetim")
# ============================================================
PRO_SUBSCRIPTION_PRICE_SUM = int(os.getenv("PRO_SUBSCRIPTION_PRICE_SUM", "10000"))
PRO_SUBSCRIPTION_DAYS = int(os.getenv("PRO_SUBSCRIPTION_DAYS", "30"))

# ============================================================
# 🖼 "Mening kabinetim" — foydalanuvchi rasmlari GitHub repo'da shu papka
# ostida saqlanadi: {MENING_KABINETIM_DIR}/{user_id}/rasimlar/{fayl}.
# Bu — persist_read/write uchun ishlatiladigan GITHUB_DATA_DIR'dan ATAYLAB
# ALOHIDA (u yerda bot_data JSON fayllari, bu yerda esa foydalanuvchi
# rasmlari — ikkalasi aralashib ketmasligi uchun).
# ============================================================
MENING_KABINETIM_DIR = os.getenv("MENING_KABINETIM_DIR", "mening_kabinetim").strip().strip("/")


def github_upload_binary(path: str, data: bytes, message: str) -> str | None:
    """GitHub Contents API orqali BINARY faylni (masalan JPEG rasm) repo'ga
    yozadi va muvaffaqiyatli bo'lsa OCHIQ (public) "raw" URL'ini qaytaradi
    — bu URL to'g'ridan-to'g'ri Telegram'ga (`InputMediaPhoto(media=url)`,
    `InlineQueryResultPhoto(photo_url=...)` va h.k.) berilishi mumkin.

    MUHIM: bu `_github_write_file`dan FARQLI — u UTF-8 matn (JSON) uchun,
    bu esa xom BINARY baytlar uchun (base64 orqali, dekodlashsiz). Fayl
    nomi tasodifiy UUID bo'lgani uchun deyarli har doim YANGI fayl bo'ladi,
    lekin xavfsizlik uchun baribir `_github_put_content` orqali (sha
    mavjud bo'lsa yuboriladi, 409 kelsa qayta uriniladi) yoziladi."""
    if not USE_GITHUB:
        return None
    ok = _github_put_content(path, data, message)
    if not ok:
        return None
    return f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{path}"


def github_list_directory(path: str) -> list[str]:
    """GitHub Contents API orqali papka ichidagi fayllarni sanab, har biri
    uchun OCHIQ "raw" URL qaytaradi (fayl nomi bo'yicha, eng eskisi
    birinchi bo'lib turadigan tartibda). Papka mavjud bo'lmasa (404) yoki
    GitHub sozlanmagan bo'lsa — bo'sh ro'yxat qaytaradi (xato ko'tarilmaydi,
    chaqiruvchi "hali rasm yo'q" deb talqin qilishi kerak)."""
    if not USE_GITHUB:
        return []
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(url, headers=_github_headers(), params={"ref": GITHUB_BRANCH})
            if r.status_code == 404:
                return []
            r.raise_for_status()
            items = r.json()
        if not isinstance(items, list):
            return []
        names = sorted(item["name"] for item in items if item.get("type") == "file")
        return [f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{path}/{name}" for name in names]
    except Exception as e:
        logger.error(f"❌ GitHub papkasini o'qishda xato ('{path}'): {type(e).__name__}: {e}")
        return []


def github_read_text_file(path: str) -> str | None:
    """`_github_read_file`ga o'xshaydi, lekin GITHUB_DATA_DIR prefiksisiz,
    ISTALGAN to'liq yo'l bilan ishlaydi — foydalanuvchi-maxsus fayllar
    (masalan shaxsiy AI kalitlari, "mening_kabinetim/{user_id}/...")
    uchun. Fayl topilmasa yoki GitHub sozlanmagan bo'lsa — None."""
    if not USE_GITHUB:
        return None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(url, headers=_github_headers(), params={"ref": GITHUB_BRANCH})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
        return base64.b64decode(data["content"]).decode("utf-8")
    except Exception as e:
        logger.error(f"❌ GitHub'dan matn fayl o'qishda xato ('{path}'): {type(e).__name__}: {e}")
        return None


def github_write_text_file(path: str, content: str, message: str) -> bool:
    """`github_read_text_file`ning yozish jufti — mavjud faylni yangilaydi
    (kerakli "sha" ni avval o'qib) yoki yangi fayl yaratadi. UTF-8 matn
    sifatida kodlanadi (masalan JSON — foydalanuvchining shaxsiy AI
    kalitlari, "mening_kabinetim/{user_id}/mening_kalitlarim.json").
    409 Conflict kelsa `_github_put_content` avtomatik qayta urinadi."""
    if not USE_GITHUB:
        return False
    return _github_put_content(path, content.encode("utf-8"), message)
