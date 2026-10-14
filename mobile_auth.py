"""Telegram-based authentication for the native Android app.

Login challenges and sessions are stored in SQLite instead of process memory.
For hosted deployments, set MOBILE_AUTH_DB_PATH to a persistent disk path.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import config

_LOGIN_TTL = 10 * 60
_SESSION_TTL = 30 * 24 * 60 * 60
_lock = threading.RLock()


@dataclass
class LoginStart:
    login_token: str
    deep_link: str
    expires_in: int


def _db_path() -> str:
    return os.getenv("MOBILE_AUTH_DB_PATH", "mobile_auth.sqlite3")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    if path != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def _connection():
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()



def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _init_db() -> None:
    with _connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_login_challenges (
                token_hash TEXT PRIMARY KEY,
                expires_at REAL NOT NULL,
                user_json TEXT,
                completed_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mobile_sessions (
                token_hash TEXT PRIMARY KEY,
                user_json TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_login_expiry ON mobile_login_challenges(expires_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_session_expiry ON mobile_sessions(expires_at)")


def _cleanup(now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _connection() as conn:
        conn.execute("DELETE FROM mobile_login_challenges WHERE expires_at <= ?", (now,))
        conn.execute("DELETE FROM mobile_sessions WHERE expires_at <= ?", (now,))


_init_db()


def start_login() -> LoginStart:
    token = secrets.token_urlsafe(24)
    username = (config.BOT_USERNAME_FALLBACK or "Student_ai_uz_bot").lstrip("@").strip()
    expires = time.time() + _LOGIN_TTL
    with _lock, _connect() as conn:
        _cleanup()
        conn.execute(
            "INSERT INTO mobile_login_challenges(token_hash, expires_at) VALUES (?, ?)",
            (_token_hash(token), expires),
        )
    deep_link = f"https://t.me/{username}?start=login_{quote(token, safe='')}"
    return LoginStart(token, deep_link, _LOGIN_TTL)


def complete_login(login_token: str, user) -> bool:
    if not login_token or user is None:
        return False
    user_data = {
        "id": int(user.id),
        "username": getattr(user, "username", None),
        "first_name": getattr(user, "first_name", None),
    }
    with _lock, _connect() as conn:
        _cleanup()
        cur = conn.execute(
            """UPDATE mobile_login_challenges
               SET user_json = ?, completed_at = ?
               WHERE token_hash = ? AND expires_at > ? AND user_json IS NULL""",
            (json.dumps(user_data, ensure_ascii=False), time.time(), _token_hash(login_token), time.time()),
        )
        return cur.rowcount == 1


def poll(login_token: str) -> dict:
    if not login_token:
        return {"status": "expired"}
    now = time.time()
    with _lock, _connect() as conn:
        _cleanup(now)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT user_json FROM mobile_login_challenges WHERE token_hash = ? AND expires_at > ?",
            (_token_hash(login_token), now),
        ).fetchone()
        if row is None:
            return {"status": "expired"}
        if not row["user_json"]:
            return {"status": "pending"}
        user = json.loads(row["user_json"])
        session_token = secrets.token_urlsafe(36)
        conn.execute(
            "INSERT INTO mobile_sessions(token_hash, user_json, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (_token_hash(session_token), json.dumps(user, ensure_ascii=False), now + _SESSION_TTL, now),
        )
        conn.execute("DELETE FROM mobile_login_challenges WHERE token_hash = ?", (_token_hash(login_token),))
        return {"status": "ok", "session_token": session_token, "user": user}


def get_user(session_token: str | None) -> dict | None:
    if not session_token:
        return None
    now = time.time()
    with _lock, _connect() as conn:
        _cleanup(now)
        row = conn.execute(
            "SELECT user_json FROM mobile_sessions WHERE token_hash = ? AND expires_at > ?",
            (_token_hash(session_token), now),
        ).fetchone()
        return json.loads(row["user_json"]) if row else None


def revoke_session(session_token: str | None) -> bool:
    """Revoke a session token immediately; token values are never stored in plaintext."""
    if not session_token:
        return False
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM mobile_sessions WHERE token_hash = ?", (_token_hash(session_token),))
        return cur.rowcount == 1
