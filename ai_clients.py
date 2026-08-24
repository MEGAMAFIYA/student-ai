"""
Barcha funksiyalar shu modul orqali AI ga murojat qiladi.

Qo'llab-quvvatlanadigan provayderlar (config.SUPPORTED_PROVIDERS):
  - "gemini"      — Google generativeai SDK orqali.
  - "cloudflare"  — Cloudflare Workers AI (account_id + api_key kerak).
  - qolganlari (groq, mistral, openrouter, cerebras, sambanova, cohere,
    huggingface, nvidia, vercel) — barchasi OpenAI-mos (/chat/completions)
    API'ga ega, shuning uchun BITTA umumiy funksiya (_call_openai_compatible)
    orqali ishlaydi, faqat BASE_URL farq qiladi (config.PROVIDER_BASE_URLS).

Agar provider uchun config.KEY_POOLS da bir nechta kalit qo'shilgan bo'lsa
(/developer > 🔑 AI kalitlari orqali), ular BIRIN-KETIN sinaladi — biri
kunlik/daqiqalik limitga yoki "pullik" holatga o'tib qolsa, avtomatik
ravishda navbatdagi kalitga o'tiladi. Pool bo'sh bo'lsa, funksiyaning o'zining
bitta api_key/model sozlamasi ishlatiladi (eski, oddiy rejim).

Provider to'plamining BARCHA kalitlari ham ishlamasa, bepul zaxira
provayderlarga (Groq -> Pollinations) avtomatik o'tiladi.

Ixtiyoriy 'history' parametri orqali ko'p bosqichli (kontekstli) suhbat ham
qo'llab-quvvatlanadi.
"""

import asyncio
import logging
import re
from urllib.parse import quote

import httpx
import google.generativeai as genai

import config

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT_SEC = 90  # Gemini javob bermasa, cheksiz kutmaslik uchun

_gemini_model_cache: dict[tuple[str, str], object] = {}
# google-generativeai kutubxonasining genai.configure() chaqiruvi JARAYON
# DARAJASIDA GLOBAL holatni o'zgartiradi (bitta API kalitni "joriy" qilib
# qo'yadi). Loyihada bir nechta turli Gemini kalit bir vaqtda ishlatilishi
# mumkinligi sababli (funksiyalarning o'z kaliti + kalitlar to'plami), agar
# ikki foydalanuvchi BIR VAQTDA turli kalitga ega chaqiruvlarni birinchi
# marta amalga oshirsa, configure()+model yaratish oralig'ida ular bir-biriga
# aralashib ketishi (noto'g'ri kalit bilan so'rov ketishi) nazariy jihatdan
# mumkin edi. Bu qulf FAQAT shu juda qisqa (millisoniyalik) konfiguratsiya
# bosqichini qulflaydi — asosiy, UZOQ davom etadigan tarmoq so'rovi
# (generate_content/send_message) qulfdan TASHQARIDA, to'liq parallel
# ishlaydi, shuning uchun bu boshqa foydalanuvchilarni BLOKLAMAYDI.
_gemini_config_lock = asyncio.Lock()

# history formati: [{"role": "user"|"assistant", "content": "..."}, ...]

# ask_ai/test_key qaytaradigan status kodlari:
#   "ok"      — muvaffaqiyatli javob keldi
#   "quota"   — bepul kunlik/daqiqalik limit tugagan (vaqtinchalik, o'zi tiklanadi)
#   "paid"    — bu model/xizmat endi (yoki umuman) pullik, bepul tarifda mavjud emas
#   "invalid" — kalit yaroqsiz/bekor qilingan yoki model nomi noto'g'ri
#   "error"   — boshqa kutilmagan xato (tarmoq, timeout va h.k.)


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


def _classify_gemini_error(e: Exception) -> tuple[str, str]:
    """Gemini xatosini (status, tafsilot) shakliga keltiradi."""
    msg = str(e)
    type_name = type(e).__name__
    if "ResourceExhausted" in type_name or "RESOURCE_EXHAUSTED" in msg or "429" in msg or "quota" in msg.lower():
        m = re.search(r"quota_value:\s*(\d+)", msg)
        if m and m.group(1) == "0":
            return "paid", "Bu model bepul tarifda mavjud emas — billing (to'lov usuli) talab qilinadi."
        return "quota", "Bepul kunlik/daqiqalik limit tugagan — birozdan keyin yoki ertaga tiklanadi."
    if (
        "API_KEY_INVALID" in msg or "API key not valid" in msg
        or "PermissionDenied" in type_name or "Unauthenticated" in type_name
        or "401" in msg or "403" in msg
    ):
        return "invalid", "Kalit yaroqsiz, bekor qilingan yoki model nomi noto'g'ri."
    return "error", msg[:200]


def _classify_openai_compatible_error(status_code: int, body_text: str) -> tuple[str, str]:
    """Groq/Mistral/OpenRouter/... (OpenAI-mos) HTTP xatosini
    (status, tafsilot) shakliga keltiradi."""
    if status_code == 429:
        return "quota", "Bepul kunlik/daqiqalik limit tugagan — birozdan keyin yoki ertaga tiklanadi."
    if status_code == 402:
        return "paid", "Bu xizmat endi pullik (to'lov talab qilinadi)."
    if status_code in (401, 403):
        return "invalid", "Kalit yaroqsiz yoki bekor qilingan."
    if status_code == 404:
        return "invalid", "Model topilmadi — model nomi noto'g'ri yoki eskirgan bo'lishi mumkin."
    return "error", body_text[:200]


async def _call_gemini(
    api_key: str, model: str, prompt: str, system: str, history: list | None, label: str = "Gemini"
) -> tuple[str | None, str, str]:
    """Qaytaradi: (natija yoki None, status, tafsilot)."""
    if not api_key or not model:
        return None, "invalid", "Kalit yoki model sozlanmagan."
    logger.info(f"{label} ({model}) ga so'rov yuborilmoqda (prompt: {len(prompt)} belgi)...")
    try:
        gmodel = await _get_gemini_model_safe(api_key, model)
        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        if history:
            gemini_history = [
                {"role": "user" if turn["role"] == "user" else "model", "parts": [turn["content"]]}
                for turn in history
            ]
            chat = gmodel.start_chat(history=gemini_history)
            resp = await asyncio.wait_for(
                asyncio.to_thread(chat.send_message, full_prompt), timeout=GEMINI_TIMEOUT_SEC
            )
        else:
            resp = await asyncio.wait_for(
                asyncio.to_thread(gmodel.generate_content, full_prompt), timeout=GEMINI_TIMEOUT_SEC
            )

        logger.info(f"{label} ({model}): ✅ javob qabul qilindi ({len(resp.text or '')} belgi).")
        return resp.text, "ok", ""
    except asyncio.TimeoutError:
        logger.error(f"{label} timeout ({model}): {GEMINI_TIMEOUT_SEC}s ichida javob kelmadi.")
        return None, "error", "Vaqt tugadi (timeout)."
    except Exception as e:
        status, detail = _classify_gemini_error(e)
        logger.error(f"{label} xato ({model}) [{status}]: {type(e).__name__}: {e}")
        return None, status, detail


async def _call_openai_compatible(
    api_key: str, model: str, base_url: str, prompt: str, system: str, history: list | None, label: str = "AI"
) -> tuple[str | None, str, str]:
    """Groq, Mistral, OpenRouter, Cerebras, SambaNova, Cohere, Hugging Face,
    NVIDIA NIM, Vercel AI Gateway va Cloudflare kabi barcha OpenAI-mos
    (/chat/completions) API'lar uchun UMUMIY chaqiruvchi. Qaytaradi:
    (natija yoki None, status, tafsilot)."""
    if not api_key or not model or not base_url:
        return None, "invalid", "Kalit, model yoki bazaviy URL sozlanmagan."
    logger.info(f"{label} ({model}) ga so'rov yuborilmoqda (prompt: {len(prompt)} belgi)...")

    messages = [{"role": "system", "content": system or "Siz foydali yordamchisiz. O'zbek tilida javob bering."}]
    if history:
        for turn in history:
            role = "user" if turn["role"] == "user" else "assistant"
            messages.append({"role": role, "content": turn["content"]})
    messages.append({"role": "user", "content": prompt})

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": messages},
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            logger.info(f"{label} ({model}): ✅ javob qabul qilindi ({len(content or '')} belgi).")
            return content, "ok", ""
    except httpx.HTTPStatusError as e:
        status, detail = _classify_openai_compatible_error(e.response.status_code, e.response.text)
        logger.error(f"{label} HTTP xato ({model}) [{status}]: {e.response.status_code} — {e.response.text[:300]}")
        return None, status, detail
    except Exception as e:
        logger.error(f"{label} xato ({model}): {type(e).__name__}: {e}")
        return None, "error", str(e)[:200]


def _resolve_cloudflare(raw_key: str) -> tuple[str, str]:
    """Cloudflare Workers AI uchun bitta API kalit yetarli emas — hisob
    (account) ID ham kerak. Shuning uchun kalit maydonida ikkalasi
    'account_id:api_key' shaklida birga saqlanadi. Qaytaradi:
    (haqiqiy api_key, shu account uchun to'liq bazaviy URL) — format
    noto'g'ri bo'lsa ("", "")."""
    if ":" not in raw_key:
        return "", ""
    account_id, api_key = raw_key.split(":", 1)
    account_id, api_key = account_id.strip(), api_key.strip()
    if not account_id or not api_key:
        return "", ""
    return api_key, f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"


async def _dispatch(
    provider: str, key: str, model: str, base_url_override: str,
    prompt: str, system: str, history: list | None, label: str,
) -> tuple[str | None, str, str]:
    """Provider nomiga qarab to'g'ri chaqiruvchiga yo'naltiradi."""
    if provider == "gemini":
        return await _call_gemini(key, model, prompt, system, history, label)
    if provider == "cloudflare":
        real_key, cf_base_url = _resolve_cloudflare(key)
        if not real_key or not cf_base_url:
            return None, "invalid", "Format noto'g'ri — 'account_id:api_key' shaklida bo'lishi kerak."
        return await _call_openai_compatible(real_key, model, cf_base_url, prompt, system, history, label)
    base_url = base_url_override or config.PROVIDER_BASE_URLS.get(provider, "")
    return await _call_openai_compatible(key, model, base_url, prompt, system, history, label)


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


_STATUS_LABELS = {
    "quota": "limit tugagan",
    "paid": "pullik",
    "invalid": "kalit yaroqsiz",
    "error": "xato",
}


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

    Ishlash tartibi:
    1. ASOSIY provider (cfg["provider"]) sinaladi:
       - Agar shu provider uchun config.KEY_POOLS da kalitlar bo'lsa — ular
         RO'YXAT TARTIBIDA birin-ketin sinaladi (har birining o'z modeli bilan).
       - Aks holda cfg["api_key"]/cfg["model"] bilan ESKI (bitta kalit)
         rejimda so'rov yuboriladi (orqaga moslik uchun).
    2. Agar (1) BUTUNLAY ishlamasa (barcha kalitlar limit/pullik/yaroqsiz
       chiqsa) va allow_fallback=True bo'lsa — /developer > 🔑 AI kalitlari
       orqali qo'shilgan BOSHQA BARCHA provayderlar (config.SUPPORTED_PROVIDERS
       tartibida, asosiysi o'tkazib yuborilib) birma-bir sinaladi. Masalan
       Gemini butunlay ishlamay qolsa-yu, lekin Mistral yoki Groq'ga kalit
       qo'shilgan bo'lsa, so'rov avtomatik o'sha yerga ketadi.
    3. Yuqoridagilarning BARCHASI (asosiy + qo'shilgan barcha zaxira
       provayderlar) ishlamasa, oxirgi chora sifatida .env dagi standart Groq
       kaliti, so'ng to'liq kalitsiz Pollinations sinaladi.
    """
    provider = cfg.get("provider", "gemini")
    tried_providers: set[str] = set()

    async def _try_provider(prov: str, use_single_key_fallback: bool) -> str | None:
        tried_providers.add(prov)
        pool = config.KEY_POOLS.get(prov, [])
        if pool:
            for idx, entry in enumerate(pool, start=1):
                key, model = entry.get("key", ""), entry.get("model", "")
                if not key or not model:
                    continue
                label = f"{config.PROVIDER_LABELS.get(prov, prov.capitalize())} kalit #{idx}"
                result, status, detail = await _dispatch(prov, key, model, "", prompt, system, history, label)
                if result:
                    return result
                logger.warning(f"{label} ishlamadi ({_STATUS_LABELS.get(status, status)}) — navbatdagi kalitga o'tilmoqda...")
            logger.warning(f"{config.PROVIDER_LABELS.get(prov, prov.capitalize())} kalitlar to'plamidagi barcha {len(pool)} ta kalit ishlamadi.")
            return None
        if use_single_key_fallback:
            key, model = cfg.get("api_key", ""), cfg.get("model", "")
            label = config.PROVIDER_LABELS.get(prov, prov.capitalize())
            result, status, detail = await _dispatch(prov, key, model, cfg.get("base_url", ""), prompt, system, history, label)
            return result
        return None  # bu provider uchun na pool, na bitta-kalit sozlamasi bor — sinab bo'lmaydi

    result = await _try_provider(provider, use_single_key_fallback=True)
    if result:
        return result

    if not allow_fallback:
        logger.warning(f"Asosiy provider ({provider}) ishlamadi, fallback O'CHIRILGAN (allow_fallback=False) — None qaytariladi.")
        return None

    logger.warning(f"Asosiy provider ({provider}) ishlamadi — boshqa qo'shilgan AI provayderlarga o'tilmoqda...")

    for other in config.SUPPORTED_PROVIDERS:
        if other in tried_providers or not config.KEY_POOLS.get(other):
            continue
        other_label = config.PROVIDER_LABELS.get(other, other.capitalize())
        logger.info(f"Zaxira sifatida {other_label} sinalmoqda...")
        result = await _try_provider(other, use_single_key_fallback=False)
        if result:
            logger.info(f"✅ Zaxira {other_label} muvaffaqiyatli javob berdi.")
            return result

    if "groq" not in tried_providers and config.DEFAULT_GROQ_KEY:
        result, status, detail = await _call_openai_compatible(
            config.DEFAULT_GROQ_KEY, config.GROQ_FALLBACK_MODEL,
            config.PROVIDER_BASE_URLS["groq"], prompt, system, history, "Standart Groq (.env)",
        )
        if result:
            logger.info("✅ Standart Groq kaliti (.env) muvaffaqiyatli javob berdi.")
            return result
        logger.warning("Standart Groq (.env) ham ishlamadi — oxirgi chora Pollinations'ga o'tilmoqda...")

    result = await _call_pollinations(prompt, system)
    if not result:
        logger.error(
            "❌ Barcha sozlangan AI provayderlar (asosiy + /developer orqali qo'shilgan "
            "barcha zaxiralar + standart Groq + Pollinations) ishlamadi — None qaytarilmoqda."
        )
    return result


async def test_key(provider: str, api_key: str, model: str) -> tuple[str, str]:
    """/developer > 🔑 AI kalitlari > 🩺 Kalitlarni tekshirish uchun — bitta
    kalit/model juftligini juda qisqa so'rov bilan sinaydi (narxni minimal
    qilish uchun). Qaytaradi: (status, tafsilot) — status "ok" bo'lsa
    tafsilot bo'sh string."""
    label = config.PROVIDER_LABELS.get(provider, provider.capitalize())
    result, status, detail = await _dispatch(provider, api_key, model, "", "Salom", "", None, f"Tekshiruv ({label})")
    if result:
        return "ok", ""
    return status, detail


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
