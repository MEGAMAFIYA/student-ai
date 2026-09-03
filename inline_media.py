"""Temporary public media storage for Telegram inline messages.

Telegram Bot API does not allow uploading a new local file while editing an
inline message. The generated media therefore gets copied into this temporary
HTTP-served directory and the inline message is edited to that public URL.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import tempfile
import time
import uuid
from threading import Lock, Timer

import config

logger = logging.getLogger(__name__)

MEDIA_TTL_SEC = int(os.getenv("INLINE_MEDIA_TTL_SEC", "1800"))
MEDIA_DIR = os.path.join(tempfile.gettempdir(), "student_ai_inline_media")
URL_PREFIX = "/inline-media/"

_lock = Lock()
_registry: dict[str, float] = {}


def _safe_ext(filename: str, media_type: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    allowed = {
        ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v",
        ".mp3", ".m4a", ".aac", ".ogg", ".wav", ".opus",
    }
    if ext in allowed:
        return ext
    if media_type == "video":
        return ".mp4"
    if media_type == "audio":
        return ".mp3"
    return ".bin"


def _cleanup_token(token: str) -> None:
    with _lock:
        _registry.pop(token, None)
    for path in (
        os.path.join(MEDIA_DIR, token),
        os.path.join(MEDIA_DIR, f"{token}.mp4"),
        os.path.join(MEDIA_DIR, f"{token}.mp3"),
        os.path.join(MEDIA_DIR, f"{token}.m4a"),
    ):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("🧹 Inline media cleanup xatosi: %s: %s", type(exc).__name__, exc)


def publish(filepath: str, media_type: str) -> str:
    """Copy a generated file to the public inline-media directory.

    Returns the full public URL. The caller must ensure PUBLIC_BASE_URL is set.
    """
    if not config.PUBLIC_BASE_URL:
        raise RuntimeError("PUBLIC_BASE_URL sozlanmagan")
    if not filepath or not os.path.isfile(filepath):
        raise FileNotFoundError(filepath or "<bo'sh filepath>")

    os.makedirs(MEDIA_DIR, exist_ok=True)
    token = uuid.uuid4().hex
    ext = _safe_ext(filepath, media_type)
    target = os.path.join(MEDIA_DIR, token + ext)
    shutil.copyfile(filepath, target)

    now = time.time()
    with _lock:
        _registry[token + ext] = now

    timer = Timer(MEDIA_TTL_SEC, _cleanup_token, args=(token + ext,))
    timer.daemon = True
    timer.start()
    url = f"{config.PUBLIC_BASE_URL}{URL_PREFIX}{token}{ext}"
    logger.info(
        "📦 Inline media published: type=%s, token=%s, size=%d bytes, ttl=%ss, url=%s",
        media_type, token + ext, os.path.getsize(target), MEDIA_TTL_SEC, url,
    )
    return url


def resolve_path(token_with_ext: str) -> tuple[str, str] | None:
    """Return (path, content_type) for a safe token or None if missing."""
    if not token_with_ext or "/" in token_with_ext or "\\" in token_with_ext or len(token_with_ext) > 80:
        return None
    name = os.path.basename(token_with_ext)
    if name != token_with_ext:
        return None
    if not name.endswith((".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".mp3", ".m4a", ".aac", ".ogg", ".wav", ".opus")):
        return None
    with _lock:
        created = _registry.get(name)
    if created is None or time.time() - created > MEDIA_TTL_SEC:
        return None
    path = os.path.join(MEDIA_DIR, name)
    if not os.path.isfile(path):
        return None
    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return path, content_type


def cleanup_expired() -> int:
    now = time.time()
    removed = 0
    with _lock:
        expired = [k for k, created in _registry.items() if now - created > MEDIA_TTL_SEC]
        for key in expired:
            _registry.pop(key, None)
    for key in expired:
        path = os.path.join(MEDIA_DIR, key)
        try:
            os.remove(path)
            removed += 1
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return removed
