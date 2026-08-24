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
import httpx
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

_RUNTIME_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_ai_config.json")

_EDITABLE_FIELDS = ("provider", "model", "api_key", "base_url")
_KEY_FIELDS = ("key", "model")


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


def _load_runtime_overrides() -> None:
    """Bot ishga tushganda chaqiriladi: agar Upstash sozlangan bo'lsa —
    o'sha yerdan, aks holda mahalliy runtime_ai_config.json fayldan (agar
    mavjud bo'lsa) o'qiladi. Topilgan qiymatlar .env'dan o'qilgan
    BOSHLANG'ICH qiymatlar ustidan qo'yiladi — shu orqali /developer orqali
    qilingan o'zgarishlar (funksiya sozlamalari HAM, kalitlar to'plami HAM)
    bot qayta ishga tushganda (Upstash bilan — QAYTA DEPLOY qilinganda ham)
    yo'qolmaydi.

    Format:
        {"functions": {PREFIX: {provider, model, api_key, base_url}, ...},
         "key_pools": {provider: [{"key":..., "model":...}, ...], ...}}

    Eski (bu funksiya qo'shilishidan oldingi) fayllar "functions" o'rniga
    to'g'ridan-to'g'ri {PREFIX: {...}} shaklida edi — shu format ham
    o'qib qo'llab-quvvatlanadi (key_pools bo'sh deb olinadi)."""
    raw, source = None, ""

    if USE_UPSTASH:
        resp = _upstash_request(["GET", _UPSTASH_KEY])
        if resp is not None and resp.get("result"):
            raw, source = resp["result"], "Upstash Redis"
        elif resp is None:
            logger.error(
                "Upstash Redis'ga ulanib bo'lmadi — .env qiymatlari bilan davom etiladi "
                "(kalitlar VAQTINCHA yo'qolgan bo'lishi mumkin, lekin Upstash tuzalganda qayta saqlansa tiklanadi)."
            )
        else:
            logger.info("Upstash Redis'da hali saqlangan konfiguratsiya yo'q (birinchi marta ishga tushirilyapti).")
    else:
        logger.warning(
            "UPSTASH_REDIS_REST_URL/TOKEN sozlanmagan — runtime_ai_config.json MAHALLIY faylga "
            "yoziladi. Render kabi vaqtinchalik-disk hostinglarda bu fayl HAR BIR DEPLOYDA "
            "o'chib ketadi! Doimiy saqlash uchun Upstash Redis sozlashni tavsiya qilamiz."
        )
        if os.path.exists(_RUNTIME_CONFIG_PATH):
            try:
                with open(_RUNTIME_CONFIG_PATH, "r", encoding="utf-8") as f:
                    raw = f.read()
                source = "mahalliy fayl"
            except Exception as e:
                logger.error(f"runtime_ai_config.json o'qishda xato: {e}")

    if not raw:
        return

    try:
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"Runtime konfiguratsiyani JSON qilib o'qishda xato: {e} — .env qiymatlari ishlatiladi.")
        return

    if "functions" in data or "key_pools" in data:
        functions_data = data.get("functions", {})
        pools_data = data.get("key_pools", {})
    else:
        functions_data = data  # eski format
        pools_data = {}

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
    }
    raw = json.dumps(data, ensure_ascii=False)

    if USE_UPSTASH:
        resp = _upstash_request(["SET", _UPSTASH_KEY, raw])
        if resp is None or resp.get("result") != "OK":
            logger.error("❌ Upstash Redis'ga yozib bo'lmadi — o'zgarish DOIMIY saqlanmadi (qayta deployda yo'qolishi mumkin)!")
        return

    try:
        with open(_RUNTIME_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(raw)
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
