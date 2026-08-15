"""
Barcha funksiyalar shu modul orqali AI ga murojat qiladi.
Har bir chaqiruv o'ziga tegishli konfiguratsiya (provider/model/key) bilan ishlaydi,
asosiy provider ishlamay qolsa, bepul zaxira provayderlarga avtomatik o'tadi.
"""

import asyncio
import logging
from urllib.parse import quote

import httpx
import google.generativeai as genai

from config import DEFAULT_GROQ_KEY

logger = logging.getLogger(__name__)

_gemini_model_cache: dict[tuple[str, str], object] = {}


def _get_gemini_model(api_key: str, model_name: str):
    key = (api_key, model_name)
    if key not in _gemini_model_cache:
        genai.configure(api_key=api_key)
        _gemini_model_cache[key] = genai.GenerativeModel(model_name)
    return _gemini_model_cache[key]


async def _call_gemini(cfg: dict, prompt: str, system: str) -> str | None:
    if not cfg.get("api_key"):
        return None
    try:
        model = _get_gemini_model(cfg["api_key"], cfg["model"])
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        resp = await asyncio.to_thread(model.generate_content, full_prompt)
        return resp.text
    except Exception as e:
        logger.error(f"Gemini xato ({cfg.get('model')}): {e}")
        return None


async def _call_groq(cfg: dict, prompt: str, system: str) -> str | None:
    if not cfg.get("api_key"):
        return None
    base_url = cfg.get("base_url") or "https://api.groq.com/openai/v1"
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json={
                    "model": cfg["model"],
                    "messages": [
                        {
                            "role": "system",
                            "content": system or "Siz foydali yordamchisiz. O'zbek tilida javob bering.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                },
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


async def ask_ai(cfg: dict, prompt: str, system: str = "", allow_fallback: bool = True) -> str | None:
    """
    cfg: config.py dagi *_AI lug'atlaridan biri (provider/model/api_key/base_url).
    Asosiy provider ishlamasa (va allow_fallback=True bo'lsa), Groq -> Pollinations
    tartibida bepul zaxiralarga o'tadi.
    """
    provider = cfg.get("provider", "gemini")

    if provider == "groq":
        result = await _call_groq(cfg, prompt, system)
    else:
        result = await _call_gemini(cfg, prompt, system)

    if result:
        return result

    if not allow_fallback:
        return None

    if provider != "groq" and DEFAULT_GROQ_KEY:
        result = await _call_groq(
            {"api_key": DEFAULT_GROQ_KEY, "model": "llama-3.3-70b-versatile", "base_url": ""},
            prompt,
            system,
        )
        if result:
            return result

    return await _call_pollinations(prompt, system)


async def ask_gemini_vision(cfg: dict, image, caption: str) -> str | None:
    if not cfg.get("api_key"):
        return None
    try:
        model = _get_gemini_model(cfg["api_key"], cfg["model"])
        resp = await asyncio.to_thread(model.generate_content, [caption, image])
        return resp.text
    except Exception as e:
        logger.error(f"Gemini vision xato: {e}")
        return None
