"""😴 Render "Uxlamaslik" (keep-alive) funksiyasi.

Render'ning bepul (Free) web servislari 15 daqiqa davomida tashqaridan hech
qanday so'rov kelmasa "uxlab" qoladi. Servisning umumiy (public) URL manziliga
har 5 daqiqada oddiy GET so'rov yuborib turilsa, 15 daqiqalik hisoblagich
doim yangilanadi va servis uxlamaydi — qayta deploy qilish SHART EMAS.

Qaysi servislar uchun yoqilgani storage'da (Upstash / app_data.json) saqlanadi,
shuning uchun bot restart/deploy bo'lganda ham ro'yxat yo'qolmaydi: bot
ishga tushganda start() chaqiriladi va fon tsikli saqlangan ro'yxat bilan
davom etadi. Oxirgi ping natijasi esa faqat xotirada turadi (har 5 daqiqada
tashqi saqlashga yozavermaslik uchun).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

import storage

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300          # har 5 daqiqada
_PING_TIMEOUT = httpx.Timeout(connect=15.0, read=75.0, write=15.0, pool=15.0)
_RETRY_DELAY = 20               # 5xx (uyg'onmoqda) bo'lsa, shuncha soniyadan keyin 1 marta qayta urinadi
_START_DELAY = 20               # bot ishga tushgach HTTP server tayyor bo'lishi uchun birinchi pingdan oldingi kutish

# Ixtiyoriy (opt-in): KEEPALIVE_SELF=1 bo'lsa va /developer orqali hech qaysi servis yoqilmagan bo'lsa,
# Render o'zi beradigan RENDER_EXTERNAL_URL (botning o'z manzili) ga ping yuboriladi — RENDER_API_KEY
# sozlanmagan bo'lsa ham ishlaydi. Qo'lda yoqilgan servislar bo'lsa, ular ustuvor.
_SELF_ID = "__self__"

# {service_id: {"ts": float, "ok": bool, "status": int | None, "detail": str}}
_last_ping: dict[str, dict[str, Any]] = {}
_task: asyncio.Task | None = None


async def ping_url(url: str) -> dict[str, Any]:
    """URL ga bitta GET so'rov yuboradi.

    Servis uxlab yotgan bo'lsa, birinchi so'rov uni uyg'otadi (Render
    uyg'otish paytida 5xx qaytarishi mumkin, shuning uchun bir marta qayta
    urinib ko'ramiz). Har qanday 4xx javob ham servis ishlayotganini bildiradi
    (masalan, "/" manzili 404 qaytarsa ham, servis uyg'oq).
    """
    result: dict[str, Any] = {"ts": time.time(), "ok": False, "status": None, "detail": ""}
    async with httpx.AsyncClient(timeout=_PING_TIMEOUT, follow_redirects=True) as client:
        for attempt in range(2):
            try:
                resp = await client.get(url, headers={"User-Agent": "Student-AI-KeepAlive/1.0"})
                result["status"] = resp.status_code
                if resp.status_code < 500:
                    result["ok"] = True
                    result["detail"] = f"HTTP {resp.status_code}"
                    break
                result["detail"] = f"HTTP {resp.status_code} (servis uyg'onmoqda)"
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
                result["detail"] = f"{type(exc).__name__}"
            if attempt == 0:
                await asyncio.sleep(_RETRY_DELAY)
    result["ts"] = time.time()
    return result


def _targets() -> dict[str, dict[str, Any]]:
    """Ping qilinadigan servislar: storage'dagi ro'yxat (yoki KEEPALIVE_SELF=1 bo'lsa botning o'z URL i)."""
    items = storage.get_all_keepalive()
    if items:
        return items
    if os.getenv("KEEPALIVE_SELF", "").strip().lower() in ("1", "true", "yes", "on"):
        url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
        if url:
            return {_SELF_ID: {"name": "Bot (o'z manzili)", "url": url}}
    return {}


async def _ping_one(service_id: str, item: dict[str, Any]) -> None:
    url = str(item.get("url") or "").strip()
    if not url:
        return
    try:
        result = await ping_url(url)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # fon tsikli hech qachon yiqilmasligi kerak
        logger.warning("😴 Keep-alive ping xatosi (%s): %s: %s", service_id, type(exc).__name__, exc)
        result = {"ts": time.time(), "ok": False, "status": None, "detail": type(exc).__name__}
    prev = _last_ping.get(service_id)
    _last_ping[service_id] = result
    name = item.get("name") or service_id
    if not result["ok"]:
        logger.warning("😴 Keep-alive muvaffaqiyatsiz: %s → %s", name, result["detail"])
    elif prev is None or not prev.get("ok"):
        # Faqat birinchi muvaffaqiyat yoki xatodan keyin tiklanish INFO; qolganlari DEBUG (kuniga 288 qator spam bo'lmasin).
        logger.info("😴 Keep-alive OK: %s → %s", name, result["detail"])
    else:
        logger.debug("😴 Keep-alive OK: %s → %s", name, result["detail"])


async def ping_now(service_id: str) -> None:
    """Bitta servisni darhol ping qiladi (yoqilgan zahoti holatni ko'rsatish uchun)."""
    item = storage.get_keepalive(service_id)
    if item:
        await _ping_one(service_id, item)


async def ping_all_once() -> None:
    items = _targets()
    if not items:
        return
    await asyncio.gather(*(_ping_one(sid, item) for sid, item in items.items()))


async def _loop() -> None:
    logger.info("😴 Keep-alive tsikli ishga tushdi (har %d soniyada).", INTERVAL_SECONDS)
    loop = asyncio.get_running_loop()
    inflight: dict[str, asyncio.Task] = {}
    try:
        await asyncio.sleep(_START_DELAY)
        next_tick = loop.time()
        while True:
            try:
                items = _targets()
                # O'chirilgan servislarning eski natijalari xotirada yig'ilib qolmasin.
                for sid in [k for k in _last_ping if k not in items]:
                    _last_ping.pop(sid, None)
                # Har bir servis o'z vazifasida: bittasi sekin/uzilgan bo'lsa, boshqalarning pingi kechikmaydi,
                # va oldingi ping hali tugamagan bo'lsa ustiga yangisi yig'ilmaydi.
                for sid, item in items.items():
                    task = inflight.get(sid)
                    if task is None or task.done():
                        inflight[sid] = loop.create_task(_ping_one(sid, item))
                for sid in [k for k, t in inflight.items() if t.done() and k not in items]:
                    inflight.pop(sid, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("😴 Keep-alive tsiklida kutilmagan xato")
            # Qat'iy jadval: ping davomiyligi intervalga qo'shilib, vaqt siljib ketmaydi.
            next_tick += INTERVAL_SECONDS
            now = loop.time()
            if next_tick <= now:
                next_tick = now + INTERVAL_SECONDS
            await asyncio.sleep(next_tick - now)
    finally:
        for task in inflight.values():
            task.cancel()


def start() -> None:
    """Fon tsiklini ishga tushiradi (bot.py > _post_init ichida chaqiriladi).
    Ikki marta chaqirilsa, ikkinchisi e'tiborsiz qoldiriladi."""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.get_running_loop().create_task(_loop())


def stop() -> None:
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
    _task = None


def get_last_ping(service_id: str) -> dict[str, Any] | None:
    return _last_ping.get(str(service_id))


def forget(service_id: str) -> None:
    _last_ping.pop(str(service_id), None)
