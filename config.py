"""
Sozlamalar: har bir funksiya uchun alohida AI provider/model/key.
Barchasi .env fayl (yoki Render Environment Variables) orqali boshqariladi.
"""

import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

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


# Har bir funksiya uchun mustaqil AI sozlamasi
UNIVERSAL_CHAT_AI = _cfg("UNIVERSAL_CHAT", "gemini-3.6-flash")
COURSE_WORK_AI = _cfg("COURSE_WORK", "gemini-3.6-flash")
TRANSLATE_AI = _cfg("TRANSLATE", "gemini-3.6-flash")
EDIT_PDF_AI = _cfg("EDIT_PDF", "gemini-3.6-flash")
GUIDE_AI = _cfg("GUIDE", "gemini-3.6-flash")
VISION_AI = _cfg("VISION", "gemini-3.6-flash")

MAX_TELEGRAM_TEXT = 3800
