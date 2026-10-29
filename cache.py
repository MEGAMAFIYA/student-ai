"""
Yengil vaqtinchalik kesh (TTL) — OPTIONAL Redis, Redis'siz ham to'liq ishlaydi.

- REDIS_URL sozlangan va `redis` paketi o'rnatilgan bo'lsa — Redis ishlatiladi
  (Render Free "Key Value" yoki Upstash'ning redis:// / rediss:// manzili).
- Aks holda, yoki Redis o'chib qolsa/uzilsa — jarayon ichidagi (in-memory)
  kesh ishlatiladi. Redis xatosi HECH QACHON chaqiruvchiga ko'tarilmaydi:
  ulanish xatosidan keyin 30 soniya Redis'ga umuman murojaat qilinmaydi
  (circuit breaker), shuning uchun o'lik Redis so'rovlarni sekinlashtirmaydi.

Faqat QAYTA HISOBLASH MUMKIN bo'lgan ma'lumot keshlanadi (haqiqat manbai —
baza). Kesh yo'qolsa, faqat bazaga bitta qo'shimcha murojaat bo'ladi.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "").strip()
_PREFIX = os.getenv("CACHE_PREFIX", "sai:")
_MAX_LOCAL = 5000
_RETRY_AFTER = 30.0   # Redis xatosidan keyin shuncha soniya murojaat qilinmaydi

_local: dict[str, tuple[float, object]] = {}
_local_lock = threading.Lock()

_redis = None
_redis_down_until = 0.0


def _get_redis():
    """Ishlayotgan Redis klientini qaytaradi yoki None (ishlatib bo'lmasa)."""
    global _redis, _redis_down_until
    if not REDIS_URL:
        return None
    if time.monotonic() < _redis_down_until:
        return None
    if _redis is not None:
        return _redis
    try:
        import redis  # ixtiyoriy bog'liqlik
        client = redis.Redis.from_url(
            REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5,
            health_check_interval=30, decode_responses=True,
        )
        client.ping()
        _redis = client
        logger.info("✅ Redis kesh ulandi.")
        return _redis
    except Exception as e:
        _mark_redis_down(e)
        return None


def _mark_redis_down(err: Exception) -> None:
    global _redis, _redis_down_until
    _redis = None
    _redis_down_until = time.monotonic() + _RETRY_AFTER
    logger.warning(f"Redis mavjud emas ({type(err).__name__}) — {int(_RETRY_AFTER)} soniya xotira keshi ishlatiladi.")


def _local_get(key: str):
    with _local_lock:
        item = _local.get(key)
        if not item:
            return None
        expires, value = item
        if expires < time.monotonic():
            _local.pop(key, None)
            return None
        return value


def _local_set(key: str, value, ttl: float) -> None:
    with _local_lock:
        if len(_local) >= _MAX_LOCAL:
            now = time.monotonic()
            for k in [k for k, (exp, _) in _local.items() if exp < now]:
                _local.pop(k, None)
            if len(_local) >= _MAX_LOCAL:      # hali ham to'la — eng eskilarining yarmini tashlaymiz
                for k in sorted(_local, key=lambda k: _local[k][0])[: _MAX_LOCAL // 2]:
                    _local.pop(k, None)
        _local[key] = (time.monotonic() + ttl, value)


def get(key: str):
    """Qiymat (JSON-serializable) yoki None."""
    r = _get_redis()
    if r is not None:
        try:
            raw = r.get(_PREFIX + key)
            return json.loads(raw) if raw is not None else None
        except Exception as e:
            _mark_redis_down(e)
    return _local_get(key)


def set(key: str, value, ttl: float = 60.0) -> None:  # noqa: A001
    ttl = max(1.0, float(ttl))
    r = _get_redis()
    if r is not None:
        try:
            r.set(_PREFIX + key, json.dumps(value, ensure_ascii=False), ex=int(ttl))
            return
        except Exception as e:
            _mark_redis_down(e)
    _local_set(key, value, ttl)


def delete(key: str) -> None:
    with _local_lock:
        _local.pop(key, None)
    r = _get_redis()
    if r is not None:
        try:
            r.delete(_PREFIX + key)
        except Exception as e:
            _mark_redis_down(e)


def clear_local() -> None:
    """Testlar uchun."""
    with _local_lock:
        _local.clear()
