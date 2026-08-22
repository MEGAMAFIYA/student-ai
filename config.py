"""
Sozlamalar: har bir funksiya uchun alohida AI provider/model/key.

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
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

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


def _cfg(prefix: str, default_model: str, default_provider: str = "gemini") -> dict:
    """
    <PREFIX>_PROVIDER, <PREFIX>_MODEL, <PREFIX>_API_KEY, <PREFIX>_BASE_URL
    environment o'zgaruvchilaridan har bir funksiya uchun sozlama yig'adi.
    Bular FAQAT ishga tushishdagi BOSHLANG'ICH (fallback) qiymatlar —
    keyinchalik /developer orqali o'zgartirilgan bo'lsa, runtime_ai_config.json
    dagi qiymatlar bular ustidan qo'yiladi (pastga, _load_runtime_overrides()
    ga qarang).
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

# /developer menyusida ko'rinadigan nom va tartib shu yerdan olinadi.
AI_FUNCTION_LABELS = {
    "UNIVERSAL_CHAT": "💬 Universal chat",
    "COURSE_WORK": "📘 Kurs ishi",
    "TRANSLATE": "🌐 Tarjima",
    "EDIT_PDF": "📝 PDF tahrirlash",
    "GUIDE": "📖 Qo'llanma",
    "VISION": "👁 Rasm tahlili (Vision)",
}

# Prefiks -> tegishli cfg dict. /developer shu orqali ishlaydi.
AI_FUNCTIONS = {
    "UNIVERSAL_CHAT": UNIVERSAL_CHAT_AI,
    "COURSE_WORK": COURSE_WORK_AI,
    "TRANSLATE": TRANSLATE_AI,
    "EDIT_PDF": EDIT_PDF_AI,
    "GUIDE": GUIDE_AI,
    "VISION": VISION_AI,
}

# Hozircha qo'llab-quvvatlanadigan AI provayderlar ("Barcha modellar"
# bo'limidagi tanlov tugmalari shular asosida chiqadi).
SUPPORTED_PROVIDERS = ["gemini", "groq"]

MAX_TELEGRAM_TEXT = 3800

# ============================================================
# RUNTIME AI SOZLAMALARI (/developer orqali o'zgartiriladi, .env EMAS)
# ============================================================
_RUNTIME_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_ai_config.json")

_EDITABLE_FIELDS = ("provider", "model", "api_key", "base_url")


def _load_runtime_overrides() -> None:
    """Bot ishga tushganda chaqiriladi: agar runtime_ai_config.json mavjud
    bo'lsa, undagi qiymatlar .env'dan o'qilgan BOSHLANG'ICH qiymatlar ustidan
    qo'yiladi — shu orqali /developer orqali qilingan o'zgarishlar bot qayta
    ishga tushganda ham yo'qolmaydi."""
    if not os.path.exists(_RUNTIME_CONFIG_PATH):
        return
    try:
        with open(_RUNTIME_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"runtime_ai_config.json o'qishda xato: {e} — .env qiymatlari ishlatiladi.")
        return

    for prefix, values in data.items():
        cfg = AI_FUNCTIONS.get(prefix)
        if cfg is None or not isinstance(values, dict):
            continue
        for field in _EDITABLE_FIELDS:
            if field in values:
                cfg[field] = values[field]
    logger.info(f"runtime_ai_config.json dan {len(data)} ta funksiya sozlamasi yuklandi.")


def _save_runtime_overrides() -> None:
    """Joriy AI_FUNCTIONS holatini to'liq runtime_ai_config.json ga yozadi."""
    data = {prefix: {f: cfg.get(f, "") for f in _EDITABLE_FIELDS} for prefix, cfg in AI_FUNCTIONS.items()}
    try:
        with open(_RUNTIME_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"runtime_ai_config.json ga yozishda xato: {e}")


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


_load_runtime_overrides()
