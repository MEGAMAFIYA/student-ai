"""
Barcha funksiyalar shu modul orqali AI ga murojat qiladi.
Har bir chaqiruv o'ziga tegishli konfiguratsiya (provider/model/key) bilan ishlaydi,
asosiy provider ishlamay qolsa, bepul zaxira provayderlarga avtomatik o'tadi.
Ixtiyoriy 'history' parametri orqali ko'p bosqichli (kontekstli) suhbat ham qo'llab-quvvatlanadi.
"""

import asyncio
import logging
from urllib.parse import quote

import httpx
import google.generativeai as genai

from config import DEFAULT_GROQ_KEY, GROQ_FALLBACK_MODEL

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT_SEC = 90  # Gemini javob bermasa, cheksiz kutmaslik uchun

_gemini_model_cache: dict[tuple[str, str], object] = {}
# google-generativeai kutubxonasining genai.configure() chaqiruvi JARAYON
# DARAJASIDA GLOBAL holatni o'zgartiradi (bitta API kalitni "joriy" qilib
# qo'yadi). Loyihada har bir funksiya (kurs ishi, tarjima va h.k.) uchun
# ALOHIDA Gemini kalit sozlash imkoniyati borligi sababli (.env'dagi
# *_API_KEY), agar ikki foydalanuvchi BIR VAQTDA turli kalitga ega
# funksiyalarni birinchi marta chaqirsa, configure()+model yaratish
# oralig'ida ular bir-biriga aralashib ketishi (noto'g'ri kalit bilan
# so'rov ketishi) nazariy jihatdan mumkin edi. Bu qulf FAQAT shu juda
# qisqa (millisoniyalik) konfiguratsiya bosqichini qulflaydi — asosiy,
# UZOQ davom etadigan tarmoq so'rovi (generate_content/send_message)
# qulfdan TASHQARIDA, to'liq parallel ishlaydi, shuning uchun bu
# boshqa foydalanuvchilarni BLOKLAMAYDI.
_gemini_config_lock = asyncio.Lock()

# history formati: [{"role": "user"|"assistant", "content": "..."}, ...]


async def _get_gemini_model_safe(api_key: str, model_name: str):
    key = (api_key, model_name)
    cached = _gemini_model_cache.get(key)
    if cached is not None:
        return cached
    async with _gemini_config_lock:
        cached = _gemini_model_cache.get(key)  # boshqa task shu orada tayyorlab bo'lgan bo'lishi mumkin
        if cached is not None:
            return cached
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        _gemini_model_cache[key] = model
        return model


async def _call_gemini(cfg: dict, prompt: str, system: str, history: list | None) -> str | None:
    if not cfg.get("api_key"):
        logger.warning(f"Gemini ({cfg.get('model')}): API kalit sozlanmagan — chaqiruv o'tkazib yuborildi.")
        return None
    logger.info(f"Gemini ({cfg.get('model')}) ga so'rov yuborilmoqda (prompt: {len(prompt)} belgi)...")
    try:
        model = await _get_gemini_model_safe(cfg["api_key"], cfg["model"])
        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        if history:
            gemini_history = [
                {"role": "user" if turn["role"] == "user" else "model", "parts": [turn["content"]]}
                for turn in history
            ]
            chat = model.start_chat(history=gemini_history)
            resp = await asyncio.wait_for(
                asyncio.to_thread(chat.send_message, full_prompt), timeout=GEMINI_TIMEOUT_SEC
            )
        else:
            resp = await asyncio.wait_for(
                asyncio.to_thread(model.generate_content, full_prompt), timeout=GEMINI_TIMEOUT_SEC
            )

        logger.info(f"Gemini ({cfg.get('model')}): ✅ javob qabul qilindi ({len(resp.text or '')} belgi).")
        return resp.text
    except asyncio.TimeoutError:
        logger.error(f"Gemini timeout ({cfg.get('model')}): {GEMINI_TIMEOUT_SEC}s ichida javob kelmadi.")
        return None
    except Exception as e:
        logger.error(f"Gemini xato ({cfg.get('model')}): {type(e).__name__}: {e}")
        return None


async def _call_groq(cfg: dict, prompt: str, system: str, history: list | None) -> str | None:
    if not cfg.get("api_key"):
        logger.warning(f"Groq ({cfg.get('model')}): API kalit sozlanmagan — chaqiruv o'tkazib yuborildi.")
        return None
    base_url = cfg.get("base_url") or "https://api.groq.com/openai/v1"
    logger.info(f"Groq ({cfg.get('model')}) ga so'rov yuborilmoqda (prompt: {len(prompt)} belgi)...")

    messages = [{"role": "system", "content": system or "Siz foydali yordamchisiz. O'zbek tilida javob bering."}]
    if history:
        for turn in history:
            role = "user" if turn["role"] == "user" else "assistant"
            messages.append({"role": role, "content": turn["content"]})
    messages.append({"role": "user", "content": prompt})

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json={"model": cfg["model"], "messages": messages},
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            logger.info(f"Groq ({cfg.get('model')}): ✅ javob qabul qilindi ({len(content or '')} belgi).")
            return content
    except httpx.HTTPStatusError as e:
        logger.error(f"Groq HTTP xato ({cfg.get('model')}): {e.response.status_code} — {e.response.text[:300]}")
        return None
    except Exception as e:
        logger.error(f"Groq xato ({cfg.get('model')}): {type(e).__name__}: {e}")
        return None


async def _call_pollinations(prompt: str, system: str) -> str | None:
    """Kalitsiz, to'liq bepul zaxira AI (pollinations.ai matn API'si). Oxirgi chora sifatida ishlatiladi."""
    logger.info("Pollinations (bepul zaxira) ga so'rov yuborilmoqda...")
    try:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.get(f"https://text.pollinations.ai/{quote(full_prompt)}")
            r.raise_for_status()
            logger.info(f"Pollinations: ✅ javob qabul qilindi ({len(r.text or '')} belgi).")
            return r.text
    except Exception as e:
        logger.error(f"Pollinations text xato: {type(e).__name__}: {e}")
        return None


async def ask_ai(
    cfg: dict,
    prompt: str,
    system: str = "",
    allow_fallback: bool = True,
    history: list | None = None,
) -> str | None:
    """
    cfg: config.py dagi *_AI lug'atlaridan biri (provider/model/api_key/base_url).
    history: [{"role": "user"|"assistant", "content": "..."}] — ixtiyoriy, kontekstli
             suhbat uchun (masalan universal chat).
    Asosiy provider ishlamasa (va allow_fallback=True bo'lsa), Groq -> Pollinations
    tartibida bepul zaxiralarga o'tadi (bu holda tarix hisobga olinmasligi mumkin).
    """
    provider = cfg.get("provider", "gemini")

    if provider == "groq":
        result = await _call_groq(cfg, prompt, system, history)
    else:
        result = await _call_gemini(cfg, prompt, system, history)

    if result:
        return result

    if not allow_fallback:
        logger.warning(f"Asosiy provider ({provider}) ishlamadi, fallback O'CHIRILGAN (allow_fallback=False) — None qaytariladi.")
        return None

    logger.warning(f"Asosiy provider ({provider}, model={cfg.get('model')}) ishlamadi — zaxira provayderga o'tilmoqda...")

    if provider != "groq" and DEFAULT_GROQ_KEY:
        result = await _call_groq(
            {"api_key": DEFAULT_GROQ_KEY, "model": GROQ_FALLBACK_MODEL, "base_url": ""},
            prompt, system, history,
        )
        if result:
            logger.info("✅ Zaxira Groq muvaffaqiyatli javob berdi.")
            return result
        logger.warning("Zaxira Groq ham ishlamadi — oxirgi chora Pollinations'ga o'tilmoqda...")
    elif provider != "groq":
        logger.warning("DEFAULT_GROQ_KEY sozlanmagan — Groq zaxirasi o'tkazib yuborildi, to'g'ridan-to'g'ri Pollinations'ga o'tilmoqda...")

    result = await _call_pollinations(prompt, system)
    if not result:
        logger.error(f"❌ Barcha AI provayderlar (asosiy={provider}, Groq zaxira, Pollinations) ishlamadi — None qaytarilmoqda.")
    return result


async def ask_gemini_vision(cfg: dict, image, caption: str) -> str | None:
    if not cfg.get("api_key"):
        return None
    try:
        model = await _get_gemini_model_safe(cfg["api_key"], cfg["model"])
        resp = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, [caption, image]), timeout=GEMINI_TIMEOUT_SEC
        )
        return resp.text
    except asyncio.TimeoutError:
        logger.error(f"Gemini vision timeout: {GEMINI_TIMEOUT_SEC}s ichida javob kelmadi.")
        return None
    except Exception as e:
        logger.error(f"Gemini vision xato: {e}")
        return None
