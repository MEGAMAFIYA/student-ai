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

# history formati: [{"role": "user"|"assistant", "content": "..."}, ...]


def _get_gemini_model(api_key: str, model_name: str):
    key = (api_key, model_name)
    if key not in _gemini_model_cache:
        genai.configure(api_key=api_key)
        _gemini_model_cache[key] = genai.GenerativeModel(model_name)
    return _gemini_model_cache[key]


async def _call_gemini(cfg: dict, prompt: str, system: str, history: list | None) -> str | None:
    if not cfg.get("api_key"):
        return None
    try:
        model = _get_gemini_model(cfg["api_key"], cfg["model"])
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

        return resp.text
    except asyncio.TimeoutError:
        logger.error(f"Gemini timeout ({cfg.get('model')}): {GEMINI_TIMEOUT_SEC}s ichida javob kelmadi.")
        return None
    except Exception as e:
        logger.error(f"Gemini xato ({cfg.get('model')}): {e}")
        return None


async def _call_groq(cfg: dict, prompt: str, system: str, history: list | None) -> str | None:
    if not cfg.get("api_key"):
        return None
    base_url = cfg.get("base_url") or "https://api.groq.com/openai/v1"

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
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Groq xato ({cfg.get('model')}): {e}")
        return None


async def _call_pollinations(prompt: str, system: str) -> str | None:
    """Kalitsiz, to'liq bepul zaxira AI (pollinations.ai matn API'si). Oxirgi chora sifatida ishlatiladi."""
    try:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.get(f"https://text.pollinations.ai/{quote(full_prompt)}")
            r.raise_for_status()
            return r.text
    except Exception as e:
        logger.error(f"Pollinations text xato: {e}")
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
        return None

    if provider != "groq" and DEFAULT_GROQ_KEY:
        result = await _call_groq(
            {"api_key": DEFAULT_GROQ_KEY, "model": GROQ_FALLBACK_MODEL, "base_url": ""},
            prompt, system, history,
        )
        if result:
            return result

    return await _call_pollinations(prompt, system)


async def ask_gemini_vision(cfg: dict, image, caption: str) -> str | None:
    if not cfg.get("api_key"):
        return None
    try:
        model = _get_gemini_model(cfg["api_key"], cfg["model"])
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
