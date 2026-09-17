"""Telegram-based authentication for the native Android app."""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote

import config

_LOGIN_TTL = 10 * 60
_SESSION_TTL = 30 * 24 * 60 * 60
_lock = threading.Lock()
_pending: dict[str, dict] = {}
_sessions: dict[str, dict] = {}


@dataclass
class LoginStart:
    login_token: str
    deep_link: str
    expires_in: int


def _cleanup(now: float | None = None) -> None:
    now = now or time.time()
    for token, item in list(_pending.items()):
        if item["expires_at"] <= now:
            _pending.pop(token, None)
    for token, item in list(_sessions.items()):
        if item["expires_at"] <= now:
            _sessions.pop(token, None)


def start_login() -> LoginStart:
    token = secrets.token_urlsafe(24)
    username = (config.BOT_USERNAME_FALLBACK or "Student_ai_uz_bot").lstrip("@").strip()
    expires = time.time() + _LOGIN_TTL
    with _lock:
        _cleanup()
        _pending[token] = {"expires_at": expires, "user": None}
    deep_link = f"https://t.me/{username}?start=login_{quote(token, safe='')}"
    return LoginStart(token, deep_link, _LOGIN_TTL)


def complete_login(login_token: str, user) -> bool:
    if not login_token or user is None:
        return False
    with _lock:
        _cleanup()
        item = _pending.get(login_token)
        if not item:
            return False
        item["user"] = {
            "id": int(user.id),
            "username": getattr(user, "username", None),
            "first_name": getattr(user, "first_name", None),
        }
        item["completed_at"] = time.time()
        return True


def poll(login_token: str) -> dict:
    with _lock:
        _cleanup()
        item = _pending.get(login_token)
        if not item:
            return {"status": "expired"}
        if not item.get("user"):
            return {"status": "pending"}
        user = item["user"]
        session_token = secrets.token_urlsafe(36)
        _sessions[session_token] = {
            "user": user,
            "expires_at": time.time() + _SESSION_TTL,
        }
        _pending.pop(login_token, None)
        return {"status": "ok", "session_token": session_token, "user": user}


def get_user(session_token: str | None) -> dict | None:
    if not session_token:
        return None
    with _lock:
        _cleanup()
        item = _sessions.get(session_token)
        return dict(item["user"]) if item else None
