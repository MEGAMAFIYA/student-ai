"""
🎨 Rasm yaratish provayderlari: Black Forest Labs (BFL) va Stability AI.

Bu modul MATN AI (ai_clients.py) bilan ARALASHMAYDI — BFL va Stability
OpenAI-mos /chat/completions API emas, shuning uchun alohida klientlar.
Kalitlar esa BOR kalit tizimi (config.KEY_POOLS / user_ai_keys.py) orqali
saqlanadi, ya'ni /developer > 🔑 AI kalitlari va /my > 🔑 Shaxsiy kalitlarim
bilan bir xil uslubda ishlaydi.

RASMIY HUJJATLAR (tekshirilgan):
  BFL       https://docs.bfl.ai/
    - Asosiy manzil: https://api.bfl.ai  (EU: api.eu.bfl.ai, US: api.us.bfl.ai)
    - Autentifikatsiya sarlavhasi: `x-key: <API_KEY>`
    - Yaratish: POST /v1/<model>  (JSON: prompt, width, height) -> {id, polling_url}
    - Natija: GET polling_url (yoki GET /v1/get_result?id=...) -> {status, result.sample}
      status: Pending | Reasoning | Generating | Ready | Error | Failed |
              Request Moderated | Content Moderated | Task not found
    - result.sample — imzolangan URL, FAQAT 10 daqiqa amal qiladi: darhol yuklab olinadi.
    - Balans: GET /v1/credits -> {credits}
    - Xatolar: 402 = kredit tugagan, 429 = faol vazifalar (24) yoki tezlik limiti.
    - ESLATMA: rasmiy API'da FLUX.1 [schnell] YO'Q (faqat ochiq vaznlar sifatida
      mavjud). Hujjatda: flux-dev, flux-pro, flux-pro-1.1(-ultra), flux-kontext-*,
      flux-2-max/pro/pro-preview/flex, flux-2-klein-4b/9b.
  Stability https://platform.stability.ai/docs (docs.stability.ai/api-reference)
    - Asosiy manzil: https://api.stability.ai ; sarlavha: `Authorization: Bearer <KEY>`
    - v2beta (multipart/form-data, `Accept: image/*` -> xom rasm baytlari):
        POST /v2beta/stable-image/generate/ultra | core | sd3 (model=sd3.5-*)
    - v1 (eski, SDXL): POST /v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image (JSON)
    - Balans: GET /v1/user/balance -> {credits}; dvigatellar: GET /v1/engines/list
    - Xatolar: 400 noto'g'ri parametr, 401 kalit, 402 kredit yetarli emas,
      403 moderatsiya, 413 juda katta, 422, 429 (150 so'rov/10 s), 500.

XAVFSIZLIK: API kalit hech qachon logga/xatolik matniga chiqmaydi (_redact),
BFL polling_url'ga kalit faqat *.bfl.ai (https) bo'lsa yuboriladi.
"""

import asyncio
import hashlib
import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# Sozlamalar (ENV orqali o'zgartirish mumkin, hammasi ixtiyoriy)
# ------------------------------------------------------------
BFL_BASE = os.getenv("BFL_API_BASE", "https://api.bfl.ai").rstrip("/")
STABILITY_BASE = os.getenv("STABILITY_API_BASE", "https://api.stability.ai").rstrip("/")

HTTP_TIMEOUT_SEC = max(10.0, float(os.getenv("IMAGE_HTTP_TIMEOUT_SEC", "60")))
POLL_TIMEOUT_SEC = max(20.0, float(os.getenv("IMAGE_POLL_TIMEOUT_SEC", "150")))
POLL_INTERVAL_SEC = max(0.2, float(os.getenv("IMAGE_POLL_INTERVAL_SEC", "1.5")))
MAX_RETRIES = max(0, int(os.getenv("IMAGE_MAX_RETRIES", "2")))
RETRY_BASE_DELAY_SEC = max(0.0, float(os.getenv("IMAGE_RETRY_DELAY_SEC", "2")))
MAX_IMAGE_BYTES = 20 * 1024 * 1024
CONCURRENCY = max(1, int(os.getenv("IMAGE_CONCURRENCY", "2")))

# Natija statuslari (matn AI statuslariga qo'shimcha):
#   ok, quota (tezlik/faol vazifa limiti), no_balance (kredit tugagan),
#   invalid (kalit), model_not_found, bad_request, moderated, timeout, error
STATUS_TEXT_UZ = {
    "ok": "ishlayapti",
    "quota": "limit tugagan",
    "no_balance": "balans tugagan",
    "paid": "model pullik",
    "invalid": "kalit yaroqsiz",
    "model_not_found": "model topilmadi",
    "bad_request": "so'rov noto'g'ri",
    "moderated": "moderatsiya blokladi",
    "timeout": "vaqt tugadi",
    "error": "xato",
}

# ------------------------------------------------------------
# Model katalogi (tanlash tugmalari uchun). "credits" — taxminiy narx
# (1 kredit = $0.01); None = rasmiy hujjatda aniq narx topilmadi / o'zgaruvchan.
# Narxlar o'zgarishi mumkin — haqiqiy narx BFL/Stability panelida.
# ------------------------------------------------------------
BFL_MODELS = [
    {"id": "flux-2-pro", "label": "FLUX.2 [pro]", "credits": None},
    {"id": "flux-2-pro-preview", "label": "FLUX.2 [pro] (preview)", "credits": None},
    {"id": "flux-2-flex", "label": "FLUX.2 [flex]", "credits": None},
    {"id": "flux-2-max", "label": "FLUX.2 [max]", "credits": None},
    {"id": "flux-2-klein-4b", "label": "FLUX.2 [klein] 4B", "credits": None},
    {"id": "flux-2-klein-9b", "label": "FLUX.2 [klein] 9B", "credits": None},
    {"id": "flux-pro-1.1", "label": "FLUX1.1 [pro]", "credits": None},
    {"id": "flux-dev", "label": "FLUX.1 [dev]", "credits": None},
]

STABILITY_V1_SDXL = "stable-diffusion-xl-1024-v1-0"
STABILITY_MODELS = [
    {"id": "core", "label": "Stable Image Core", "credits": 3},
    {"id": "ultra", "label": "Stable Image Ultra", "credits": 8},
    {"id": "sd3.5-large", "label": "Stable Diffusion 3.5 Large", "credits": 6.5},
    {"id": "sd3.5-large-turbo", "label": "SD 3.5 Large Turbo", "credits": 4},
    {"id": "sd3.5-medium", "label": "Stable Diffusion 3.5 Medium", "credits": 3.5},
    {"id": STABILITY_V1_SDXL, "label": "Stable Diffusion XL 1.0 (v1)", "credits": None},
]

MODEL_CATALOG = {"bfl": BFL_MODELS, "stability": STABILITY_MODELS}
DEFAULT_MODELS = {"bfl": "flux-2-pro", "stability": "core"}

_SDXL_ALIASES = {
    "sdxl", "sd-xl", "sd xl", "stable diffusion xl", "stable-diffusion-xl",
    "stable diffusion xl 1.0", "sdxl 1.0", "sdxl-1.0", STABILITY_V1_SDXL,
}
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# En-boy nisbatlari va har bir provayder qabul qiladigan o'lchamlar.
# BFL: tomonlar 32 ga karrali (1.1/dev) va 16 ga karrali (FLUX.2) — 32 ikkalasiga mos.
# Stability v2beta: aspect_ratio matn ("16:9", "3:2", "1:1"...). SDXL v1: faqat
# rasmiy ruxsat etilgan o'lchamlar ro'yxati (64 ga karrali).
_BFL_SIZES = {"16:9": (1344, 768), "3:2": (1152, 768), "1:1": (1024, 1024)}
_SDXL_SIZES = {"16:9": (1344, 768), "3:2": (1216, 832), "1:1": (1024, 1024)}
VALID_ASPECTS = ("16:9", "3:2", "1:1")


def models_for(provider: str) -> list[dict]:
    return list(MODEL_CATALOG.get(provider, []))


def model_label(provider: str, model: str) -> str:
    for m in MODEL_CATALOG.get(provider, []):
        if m["id"] == model:
            return m["label"]
    return model or "—"


def is_image_provider(provider: str) -> bool:
    return provider in MODEL_CATALOG


def normalize_model(provider: str, model: str) -> str:
    """Foydalanuvchi qo'lda kiritgan model nomini tozalaydi (bo'shliq, katta-kichik
    harf, "Stable Diffusion XL" kabi taxalluslar). URL yo'liga qo'yilgani uchun
    faqat xavfsiz belgilarga ruxsat beriladi — aks holda bo'sh string qaytadi."""
    raw = (model or "").strip()
    low = raw.lower()
    if provider == "stability":
        if low in _SDXL_ALIASES:
            return STABILITY_V1_SDXL
        low = {"stable image core": "core", "stable image ultra": "ultra"}.get(low, low)
        raw = low
    elif provider == "bfl":
        raw = low.replace(" ", "-")
        raw = {"flux.1-dev": "flux-dev", "flux1-dev": "flux-dev"}.get(raw, raw)
    return raw if _MODEL_NAME_RE.match(raw) else ""


def stability_kind(model: str) -> str:
    """'ultra' | 'core' | 'sd3' | 'v1' | '' (noma'lum)."""
    m = (model or "").lower()
    if m == "ultra":
        return "ultra"
    if m == "core":
        return "core"
    if m.startswith("sd3"):
        return "sd3"
    if m.startswith("stable-diffusion"):
        return "v1"
    return ""


def size_for(provider: str, model: str, aspect: str = "16:9") -> tuple[int, int]:
    aspect = aspect if aspect in VALID_ASPECTS else "16:9"
    if provider == "stability" and stability_kind(model) == "v1":
        return _SDXL_SIZES[aspect]
    return _BFL_SIZES[aspect]


def estimate_cost(provider: str, model: str, count: int = 1) -> tuple[float | None, str]:
    """(taxminiy kredit yoki None, foydalanuvchiga ko'rsatiladigan matn).
    BFL uchun aniq narx rasmiy hujjatda sahifaga qarab o'zgaradi (megapiksel
    bo'yicha) — shuning uchun to'qib chiqarilmaydi: BFL javobidagi haqiqiy
    `cost` keyin loglanadi."""
    for m in MODEL_CATALOG.get(provider, []):
        if m["id"] == model and m.get("credits") is not None:
            total = float(m["credits"]) * count
            return total, f"~{total:g} kredit (~${total * 0.01:.2f}), {count} ta rasm"
    return None, "narx aniqlanmadi (provayder panelidagi narxlarga qarang)"


# ------------------------------------------------------------
# Natija obyekti
# ------------------------------------------------------------
@dataclass
class ImageResult:
    status: str
    detail: str = ""
    data: bytes = b""
    mime: str = "image/png"
    cost_credits: float | None = None
    provider: str = ""
    model: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.data)


def _fail(status: str, detail: str = "", **kw) -> ImageResult:
    return ImageResult(status=status, detail=detail, **kw)


# ------------------------------------------------------------
# Xavfsizlik va HTTP yordamchilari
# ------------------------------------------------------------
def _redact(text, *secrets) -> str:
    out = str(text)
    for s in secrets:
        if s and len(s) >= 6:
            out = out.replace(s, "***")
    return out


def _host_ok(url: str, suffix: str) -> bool:
    try:
        u = urlparse(url)
    except Exception:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == suffix or host.endswith("." + suffix))


# Testlar uchun: _CLIENT_FACTORY ga `() -> async-context-manager` berib, tarmoqsiz
# sinash mumkin. Ishlab chiqarishda None — haqiqiy httpx.AsyncClient ishlatiladi.
_CLIENT_FACTORY = None


def _make_client():
    if _CLIENT_FACTORY is not None:
        return _CLIENT_FACTORY()
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC, follow_redirects=False)


_TRANSIENT_EXC_NAMES = {
    "TimeoutException", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout",
    "ConnectError", "ReadError", "WriteError", "NetworkError", "RemoteProtocolError",
    "TimeoutError",
}


def _is_transient_exc(e: Exception) -> bool:
    return isinstance(e, asyncio.TimeoutError) or type(e).__name__ in _TRANSIENT_EXC_NAMES


def _is_timeout_exc(e: Exception) -> bool:
    return isinstance(e, asyncio.TimeoutError) or "Timeout" in type(e).__name__


async def _request(client, method: str, url: str, *, retries: int | None = None, secrets=(), **kw):
    """Vaqtinchalik xatolarda (tarmoq/timeout/429/5xx) cheklangan qayta urinish.
    Qaytaradi: httpx.Response. Oxirgi urinishda ham xato bo'lsa — istisno ko'tariladi
    (chaqiruvchi tasniflaydi)."""
    retries = MAX_RETRIES if retries is None else retries
    attempt = 0
    while True:
        try:
            resp = await getattr(client, method)(url, **kw)
        except Exception as e:
            if attempt < retries and _is_transient_exc(e):
                delay = RETRY_BASE_DELAY_SEC * (2 ** attempt)
                logger.warning(f"🎨 {method.upper()} {urlparse(url).path}: vaqtinchalik xato "
                               f"({type(e).__name__}) — {delay:g}s dan keyin qayta uriniladi ({attempt + 1}/{retries}).")
                attempt += 1
                await asyncio.sleep(delay)
                continue
            raise
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            delay = RETRY_BASE_DELAY_SEC * (2 ** attempt)
            logger.warning(f"🎨 {method.upper()} {urlparse(url).path}: HTTP {resp.status_code} — "
                           f"{delay:g}s dan keyin qayta uriniladi ({attempt + 1}/{retries}).")
            attempt += 1
            await asyncio.sleep(delay)
            continue
        return resp


def _error_message(resp, *secrets) -> str:
    """Javob tanasidan qisqa, kalitsiz xato matni."""
    msg = ""
    try:
        js = resp.json()
        if isinstance(js, dict):
            d = js.get("detail")
            if isinstance(d, list):
                d = "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in d[:3])
            errs = js.get("errors")
            if isinstance(errs, list):
                errs = "; ".join(str(x) for x in errs[:3])
            msg = str(d or errs or js.get("message") or js.get("name") or "")
        else:
            msg = str(js)
    except Exception:
        try:
            msg = resp.text or ""
        except Exception:
            msg = ""
    return _redact(msg.strip().replace("\n", " "), *secrets)[:200]


def classify_http(provider: str, status_code: int, message: str = "", legacy_v1: bool = False) -> tuple[str, str]:
    """HTTP xatosini (status, o'zbekcha tafsilot) ga aylantiradi."""
    extra = f" ({message})" if message else ""
    if status_code == 401 or (status_code == 403 and (provider == "bfl" or legacy_v1)):
        return "invalid", "API kalit yaroqsiz, bekor qilingan yoki ruxsat yo'q." + extra
    if status_code == 402:
        return "no_balance", "Balans/kredit tugagan — provayder panelida hisobni to'ldiring." + extra
    if status_code == 403:  # Stability v2beta: moderatsiya
        return "moderated", "So'rov kontent moderatsiyasi tomonidan rad etildi." + extra
    if status_code == 404:
        return "model_not_found", "Model (endpoint) topilmadi — model nomi noto'g'ri yoki eskirgan." + extra
    if status_code in (400, 413, 422):
        return "bad_request", "So'rov parametrlari noto'g'ri yoki juda katta." + extra
    if status_code == 429:
        if provider == "bfl":
            return "quota", "Faol vazifalar (24 tagacha) yoki tezlik limiti — birozdan keyin qayta urining."
        return "quota", "Tezlik limiti (150 so'rov/10 soniya) oshdi — birozdan keyin qayta urining."
    if status_code >= 500:
        return "error", f"Provayder serveri xatosi (HTTP {status_code})." + extra
    return "error", f"Kutilmagan javob (HTTP {status_code})." + extra


def _exc_result(e: Exception, provider: str, model: str, *secrets) -> ImageResult:
    if _is_timeout_exc(e):
        return _fail("timeout", "Provayder vaqtida javob bermadi (timeout).", provider=provider, model=model)
    return _fail("error", f"{type(e).__name__}: {_redact(e, *secrets)[:150]}", provider=provider, model=model)


# ------------------------------------------------------------
# BFL
# ------------------------------------------------------------
def _bfl_headers(api_key: str) -> dict:
    return {"x-key": api_key, "accept": "application/json"}


async def _bfl_generate(client, api_key: str, model: str, prompt: str, width: int, height: int) -> ImageResult:
    P = dict(provider="bfl", model=model)
    resp = await _request(
        client, "post", f"{BFL_BASE}/v1/{model}",
        headers={**_bfl_headers(api_key), "Content-Type": "application/json"},
        json={"prompt": prompt, "width": width, "height": height},
        secrets=(api_key,),
    )
    if resp.status_code != 200:
        st, det = classify_http("bfl", resp.status_code, _error_message(resp, api_key))
        return _fail(st, det, **P)
    try:
        js = resp.json()
    except Exception:
        return _fail("error", "BFL javobi JSON emas.", **P)

    task_id = js.get("id")
    polling_url = js.get("polling_url") or (f"{BFL_BASE}/v1/get_result?id={task_id}" if task_id else "")
    if not polling_url:
        return _fail("error", "BFL javobida polling_url/id yo'q.", **P)
    if not _host_ok(polling_url, "bfl.ai"):
        # Kalitni begona manzilga yubormaymiz.
        logger.error("🎨 BFL polling_url ishonchsiz host — so'rov bekor qilindi.")
        return _fail("error", "BFL polling manzili ishonchsiz — bekor qilindi.", **P)
    cost = js.get("cost")

    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    last_status = ""
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        pr = await _request(client, "get", polling_url, headers=_bfl_headers(api_key), secrets=(api_key,))
        if pr.status_code != 200:
            st, det = classify_http("bfl", pr.status_code, _error_message(pr, api_key))
            return _fail(st, det, **P)
        try:
            pj = pr.json()
        except Exception:
            return _fail("error", "BFL polling javobi JSON emas.", **P)
        status = str(pj.get("status", ""))
        last_status = status
        if status == "Ready":
            sample = (pj.get("result") or {}).get("sample")
            if pj.get("cost") is not None:
                cost = pj.get("cost")
            if not sample or not _host_ok(str(sample), "bfl.ai"):
                return _fail("error", "BFL natija URL'i yo'q yoki ishonchsiz.", **P)
            # Imzolangan URL 10 daqiqa amal qiladi — DARHOL yuklab olamiz (kalit yuborilmaydi).
            dr = await _request(client, "get", str(sample), secrets=(api_key,))
            if dr.status_code != 200 or not dr.content:
                return _fail("error", f"Tayyor rasmni yuklab bo'lmadi (HTTP {dr.status_code}).", **P)
            if len(dr.content) > MAX_IMAGE_BYTES:
                return _fail("error", "Rasm hajmi juda katta.", **P)
            mime = (dr.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
            return ImageResult("ok", "", dr.content, mime if mime.startswith("image/") else "image/jpeg",
                               float(cost) if isinstance(cost, (int, float)) else None, "bfl", model)
        if status in ("Request Moderated", "Content Moderated"):
            return _fail("moderated", f"BFL moderatsiyasi rad etdi ({status}).", **P)
        if status in ("Error", "Failed", "Task not found", "No Card Generated"):
            det = pj.get("details")
            return _fail("error", f"BFL vazifasi muvaffaqiyatsiz: {status}" + (f" ({_redact(det, api_key)[:120]})" if det else ""), **P)
        # Pending / Reasoning / Generating — kutamiz
    return _fail("timeout", f"BFL natijasi {POLL_TIMEOUT_SEC:g}s ichida tayyor bo'lmadi (oxirgi holat: {last_status or '—'}).", **P)


# ------------------------------------------------------------
# Stability AI
# ------------------------------------------------------------
async def _stability_generate(client, api_key: str, model: str, prompt: str, aspect: str, width: int, height: int) -> ImageResult:
    P = dict(provider="stability", model=model)
    kind = stability_kind(model)
    if not kind:
        return _fail("model_not_found", f"'{model}' — noma'lum Stability modeli. Mavjudlari: ultra, core, sd3.5-*, SDXL.", **P)

    auth = {"Authorization": f"Bearer {api_key}"}
    if kind == "v1":
        url = f"{STABILITY_BASE}/v1/generation/{model}/text-to-image"
        resp = await _request(
            client, "post", url,
            headers={**auth, "Accept": "image/png", "Content-Type": "application/json"},
            json={"text_prompts": [{"text": prompt, "weight": 1}], "width": width, "height": height,
                  "cfg_scale": 7, "steps": 30, "samples": 1},
            secrets=(api_key,),
        )
    else:
        path = {"ultra": "ultra", "core": "core", "sd3": "sd3"}[kind]
        data = {"prompt": prompt, "aspect_ratio": aspect, "output_format": "png"}
        if kind == "sd3":
            data["model"] = model
        resp = await _request(
            client, "post", f"{STABILITY_BASE}/v2beta/stable-image/generate/{path}",
            headers={**auth, "Accept": "image/*"},
            data=data, files={"none": ""},  # multipart/form-data majburlash
            secrets=(api_key,),
        )
    if resp.status_code != 200:
        st, det = classify_http("stability", resp.status_code, _error_message(resp, api_key), legacy_v1=(kind == "v1"))
        return _fail(st, det, **P)
    finish = (resp.headers.get("finish-reason") or "").upper()
    if finish == "CONTENT_FILTERED":
        return _fail("moderated", "Stability moderatsiyasi natijani bloklab (xiralashtirib) yubordi.", **P)
    if not resp.content:
        return _fail("error", "Stability bo'sh javob qaytardi.", **P)
    if len(resp.content) > MAX_IMAGE_BYTES:
        return _fail("error", "Rasm hajmi juda katta.", **P)
    mime = (resp.headers.get("content-type") or "image/png").split(";")[0].strip()
    return ImageResult("ok", "", resp.content, mime if mime.startswith("image/") else "image/png", None, "stability", model)


# ------------------------------------------------------------
# Umumiy generatsiya (kesh + parallellik cheklovi)
# ------------------------------------------------------------
_CACHE: "OrderedDict[tuple, ImageResult]" = OrderedDict()
_CACHE_MAX_ITEMS = 48
_CACHE_MAX_BYTES = 48 * 1024 * 1024
_cache_bytes = 0
_sems: dict[int, asyncio.Semaphore] = {}


def _semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    sem = _sems.get(loop_id)
    if sem is None:
        _sems.clear()  # eski loop'lar uchun qoldiq to'planmasin
        sem = _sems[loop_id] = asyncio.Semaphore(CONCURRENCY)
    return sem


def _cache_key(provider: str, model: str, prompt: str, aspect: str) -> tuple:
    return (provider, model, aspect, hashlib.sha256(prompt.strip().lower().encode("utf-8")).hexdigest())


def _cache_put(key: tuple, res: ImageResult) -> None:
    global _cache_bytes
    if key in _CACHE:
        _cache_bytes -= len(_CACHE.pop(key).data)
    _CACHE[key] = res
    _cache_bytes += len(res.data)
    while _CACHE and (len(_CACHE) > _CACHE_MAX_ITEMS or _cache_bytes > _CACHE_MAX_BYTES):
        _, old = _CACHE.popitem(last=False)
        _cache_bytes -= len(old.data)


def clear_cache() -> None:
    global _cache_bytes
    _CACHE.clear()
    _cache_bytes = 0


async def generate_image(provider: str, api_key: str, model: str, prompt: str,
                         aspect: str = "16:9", use_cache: bool = True) -> ImageResult:
    """Bitta rasm yaratadi. HECH QACHON istisno ko'tarmaydi — har doim ImageResult."""
    model = normalize_model(provider, model) or ""
    if provider not in MODEL_CATALOG:
        return _fail("error", f"Noma'lum rasm provayderi: {provider}", provider=provider, model=model)
    if not api_key or not model:
        return _fail("invalid", "API kalit yoki model sozlanmagan.", provider=provider, model=model)
    prompt = (prompt or "").strip()
    if not prompt:
        return _fail("bad_request", "Prompt bo'sh.", provider=provider, model=model)
    if aspect not in VALID_ASPECTS:
        aspect = "16:9"

    key = _cache_key(provider, model, prompt, aspect)
    if use_cache and key in _CACHE:
        _CACHE.move_to_end(key)
        logger.info(f"🎨 {provider}/{model}: bir xil prompt — keshdan olindi (API so'rovi yuborilmadi).")
        return _CACHE[key]

    if provider == "stability" and not stability_kind(model):
        return _fail("model_not_found",
                     f"'{model}' — noma'lum Stability modeli. Mavjudlari: ultra, core, sd3.5-*, SDXL.",
                     provider=provider, model=model)

    width, height = size_for(provider, model, aspect)
    t0 = time.monotonic()
    try:
        async with _semaphore():
            async with _make_client() as client:
                if provider == "bfl":
                    res = await _bfl_generate(client, api_key, model, prompt, width, height)
                else:
                    res = await _stability_generate(client, api_key, model, prompt, aspect, width, height)
    except Exception as e:
        res = _exc_result(e, provider, model, api_key)
    logger.info(f"🎨 {provider}/{model}: {res.status} ({time.monotonic() - t0:.1f}s)"
                + (f", cost={res.cost_credits}" if res.cost_credits is not None else "")
                + (f" — {res.detail}" if res.status != "ok" else ""))
    if res.ok and use_cache:
        _cache_put(key, res)
    return res


# ------------------------------------------------------------
# Kalitni HAQIQIY API so'rovlari orqali tekshirish
# ------------------------------------------------------------
async def test_image_key(provider: str, api_key: str, model: str) -> tuple[str, str]:
    """/developer va /my "🩺 Kalitlarni tekshirish" uchun. PULSIZ so'rovlar:
      1) balans so'rovi (kalit haqiqiyligi + qoldiq kredit),
      2) modelning mavjudligi: BFL — model endpointiga BO'SH tana yuboriladi
         (404 = model yo'q, 422 = model bor va kalit qabul qilindi; rasm
         yaratilmaydi, kredit sarflanmaydi); Stability — v1 uchun dvigatellar
         ro'yxati, v2beta uchun validatsiya xatosi (400) kutiladi.
    Qaytaradi: (status, tafsilot). status "ok" bo'lsa tafsilot — balans matni."""
    if provider not in MODEL_CATALOG:
        return "error", f"Noma'lum rasm provayderi: {provider}"
    model = normalize_model(provider, model)
    if not api_key:
        return "invalid", "API kalit kiritilmagan."
    if not model:
        return "model_not_found", "Model nomi bo'sh yoki noto'g'ri belgilar bor."
    try:
        async with _make_client() as client:
            if provider == "bfl":
                return await _test_bfl(client, api_key, model)
            return await _test_stability(client, api_key, model)
    except Exception as e:
        res = _exc_result(e, provider, model, api_key)
        return res.status, res.detail


async def _test_bfl(client, api_key: str, model: str) -> tuple[str, str]:
    r = await _request(client, "get", f"{BFL_BASE}/v1/credits", headers=_bfl_headers(api_key), secrets=(api_key,))
    if r.status_code != 200:
        return classify_http("bfl", r.status_code, _error_message(r, api_key))
    try:
        credits = float(r.json().get("credits"))
    except Exception:
        return "error", "BFL balans javobini o'qib bo'lmadi."
    if credits <= 0:
        return "no_balance", f"Kalit yaroqli, lekin balans {credits:g} kredit — hisobni to'ldiring."

    p = await _request(client, "post", f"{BFL_BASE}/v1/{model}",
                       headers={**_bfl_headers(api_key), "Content-Type": "application/json"},
                       json={}, retries=0, secrets=(api_key,))
    if p.status_code in (422, 400):
        return "ok", f"Balans: {credits:g} kredit; model '{model}' mavjud."
    if p.status_code == 200:
        return "ok", f"Balans: {credits:g} kredit; model '{model}' so'rovni qabul qildi."
    st, det = classify_http("bfl", p.status_code, _error_message(p, api_key))
    return st, det


async def _test_stability(client, api_key: str, model: str) -> tuple[str, str]:
    auth = {"Authorization": f"Bearer {api_key}"}
    r = await _request(client, "get", f"{STABILITY_BASE}/v1/user/balance", headers=auth, secrets=(api_key,))
    if r.status_code != 200:
        return classify_http("stability", r.status_code, _error_message(r, api_key), legacy_v1=True)
    try:
        credits = float(r.json().get("credits"))
    except Exception:
        return "error", "Stability balans javobini o'qib bo'lmadi."
    if credits <= 0:
        return "no_balance", f"Kalit yaroqli, lekin balans {credits:g} kredit — hisobni to'ldiring."

    kind = stability_kind(model)
    if not kind:
        return "model_not_found", f"'{model}' — noma'lum model. Mavjud: ultra, core, sd3.5-large, sd3.5-large-turbo, sd3.5-medium, {STABILITY_V1_SDXL}."
    if kind == "v1":
        e = await _request(client, "get", f"{STABILITY_BASE}/v1/engines/list", headers=auth, secrets=(api_key,))
        if e.status_code != 200:
            return classify_http("stability", e.status_code, _error_message(e, api_key), legacy_v1=True)
        try:
            ids = [str(x.get("id")) for x in e.json()]
        except Exception:
            return "error", "Stability dvigatellar ro'yxatini o'qib bo'lmadi."
        if model not in ids:
            return "model_not_found", f"Dvigatel '{model}' hisobingizda yo'q."
        return "ok", f"Balans: {credits:g} kredit; dvigatel '{model}' mavjud."

    path = {"ultra": "ultra", "core": "core", "sd3": "sd3"}[kind]
    data = {"prompt": ""}
    if kind == "sd3":
        data["model"] = model
    p = await _request(client, "post", f"{STABILITY_BASE}/v2beta/stable-image/generate/{path}",
                       headers={**auth, "Accept": "image/*"}, data=data, files={"none": ""},
                       retries=0, secrets=(api_key,))
    msg = _error_message(p, api_key)
    if p.status_code in (400, 422):
        if kind == "sd3" and "model" in msg.lower():
            return "model_not_found", f"Model '{model}' qabul qilinmadi: {msg}"
        return "ok", f"Balans: {credits:g} kredit; '{model}' endpointi mavjud."
    if p.status_code == 200:
        return "ok", f"Balans: {credits:g} kredit; '{model}' so'rovni qabul qildi."
    return classify_http("stability", p.status_code, msg)
