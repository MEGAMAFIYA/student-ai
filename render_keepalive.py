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
import time
from typing import Any

import httpx

import storage

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300          # har 5 daqiqada
_PING_TIMEOUT = httpx.Timeout(connect=15.0, read=75.0, write=15.0, pool=15.0)
_RETRY_DELAY = 20               # 5xx (uyg'onmoqda) bo'lsa, shuncha soniyadan keyin 1 marta qayta urinadi

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


async def _ping_one(service_id: str, item: dict[str, Any]) -> None:
    url = str(item.get("url") or "").strip()
    if not url:
        return
    try:
        result = await ping_url(url)
    except Exception as exc:  # fon tsikli hech qachon yiqilmasligi kerak
        logger.warning("😴 Keep-alive ping xatosi (%s): %s: %s", service_id, type(exc).__name__, exc)
        result = {"ts": time.time(), "ok": False, "status": None, "detail": type(exc).__name__}
    _last_ping[service_id] = result
    if result["ok"]:
        logger.info("😴 Keep-alive OK: %s → %s", item.get("name") or service_id, result["detail"])
    else:
        logger.warning("😴 Keep-alive muvaffaqiyatsiz: %s → %s", item.get("name") or service_id, result["detail"])


async def ping_all_once() -> None:
    items = storage.get_all_keepalive()
    if not items:
        return
    await asyncio.gather(*(_ping_one(sid, item) for sid, item in items.items()))


async def _loop() -> None:
    logger.info("😴 Keep-alive tsikli ishga tushdi (har %d soniyada).", INTERVAL_SECONDS)
    while True:
        try:
            await ping_all_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("😴 Keep-alive tsiklida kutilmagan xato")
        await asyncio.sleep(INTERVAL_SECONDS)


def start() -> None:
    """Fon tsiklini ishga tushiradi (bot.py > _post_init ichida chaqiriladi).
    Ikki marta chaqirilsa, ikkinchisi e'tiborsiz qoldiriladi."""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.get_running_loop().create_task(_loop())


def get_last_ping(service_id: str) -> dict[str, Any] | None:
    return _last_ping.get(str(service_id))


def forget(service_id: str) -> None:
    _last_ping.pop(str(service_id), None)
