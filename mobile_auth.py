"""Telegram-based authentication for the native Android app.

Login challenges and sessions are stored in SQLite instead of process memory.
For hosted deployments, set MOBILE_AUTH_DB_PATH to a persistent disk path.

Two ways to sign in:
  1. Deep link:   app -> /auth/start -> Telegram "/start login_<token>" -> /auth/poll
  2. Telegram ID: app -> /auth/code/request (bot DMs a 6-digit code) -> /auth/code/verify
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import cache
import config

logger = logging.getLogger(__name__)

_LOGIN_TTL = 10 * 60
_SESSION_TTL = 30 * 24 * 60 * 60
_CODE_TTL = 5 * 60
_CODE_COOLDOWN = 45  # bir Telegram ID'ga kodni qayta yuborish oralig'i (soniya)
_CODE_MAX_ATTEMPTS = 5
_lock = threading.RLock()
_fallback_warned = False


class CodeCooldown(Exception):
    """Kod juda tez-tez so'ralganda ko'tariladi."""

    def __init__(self, retry_after: int):
        super().__init__(f"retry_after={retry_after}")
        self.retry_after = retry_after


@dataclass
class LoginStart:
    login_token: str
    deep_link: str
    expires_in: int


def _writable(path: str) -> bool:
    try:
        parent = Path(path).expanduser().resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
        return os.access(parent, os.W_OK)
    except OSError:
        return False


def _db_path() -> str:
    """SQLite fayl yo'li.

    MOBILE_AUTH_DB_PATH ko'rsatilgan papkaga yozib bo'lmasa (masalan Render'da
    persistent disk ulanmagan bo'lsa) bot import paytida yiqilib tushmasligi
    uchun vaqtinchalik faylga qaytiladi.
    """
    global _fallback_warned
    path = os.getenv("MOBILE_AUTH_DB_PATH", "mobile_auth.sqlite3")
    if path == ":memory:" or _writable(path):
        return path
    fallback = os.path.join(tempfile.gettempdir(), "mobile_auth.sqlite3")
    if not _fallback_warned:
        _fallback_warned = True
        logger.warning(
            "MOBILE_AUTH_DB_PATH=%r ga yozib bo'lmadi; %r ishlatiladi "
            "(qayta deployda sessiyalar yo'qoladi — persistent disk ulang).",
            path, fallback,
        )
    return fallback


_wal_ready: set[str] = set()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")      # WAL bilan xavfsiz; har commit'da fsync kutmaydi
    conn.execute("PRAGMA temp_store=MEMORY")
    if path not in _wal_ready:
        # journal_mode faylda doimiy saqlanadi — har ulanishda qayta o'rnatish shart emas.
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            _wal_ready.add(path)
        except sqlite3.DatabaseError:
            pass
    return conn


@contextmanager
def _connection():
    """Commit on success, rollback on error, ALWAYS close (no leaked handles)."""
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
            CREATE TABLE IF NOT EXISTS mobile_code_challenges (
                telegram_id INTEGER PRIMARY KEY,
                code_hash TEXT NOT NULL,
                expires_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT 0
            )
        """)
        # Eski bazalarda created_at ustuni yo'q — migratsiya.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(mobile_code_challenges)")}
        if "created_at" not in cols:
            conn.execute("ALTER TABLE mobile_code_challenges ADD COLUMN created_at REAL NOT NULL DEFAULT 0")
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


_CLEANUP_EVERY = 60.0
_last_cleanup = 0.0


def _cleanup(now: float | None = None, force: bool = False) -> None:
    """Muddati o'tgan yozuvlarni o'chiradi. Hamma so'rovlarda emas, daqiqada
    bir marta (har so'rovdagi 3 ta DELETE = yozuv-tranzaksiya = ortiqcha lock).
    To'g'rilikka ta'sir qilmaydi: har bir SELECT/UPDATE o'zi `expires_at > now`
    ni tekshiradi."""
    global _last_cleanup
    now = time.time() if now is None else now
    if not force and 0 <= now - _last_cleanup < _CLEANUP_EVERY:
        return
    _last_cleanup = now
    with _connection() as conn:
        conn.execute("DELETE FROM mobile_login_challenges WHERE expires_at <= ?", (now,))
        conn.execute("DELETE FROM mobile_code_challenges WHERE expires_at <= ?", (now,))
        conn.execute("DELETE FROM mobile_sessions WHERE expires_at <= ?", (now,))


_init_db()


def _new_session(conn: sqlite3.Connection, user: dict, now: float) -> str:
    session_token = secrets.token_urlsafe(36)
    conn.execute(
        "INSERT INTO mobile_sessions(token_hash, user_json, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (_token_hash(session_token), json.dumps(user, ensure_ascii=False), now + _SESSION_TTL, now),
    )
    return session_token


def start_login() -> LoginStart:
    token = secrets.token_urlsafe(24)
    username = (config.BOT_USERNAME_FALLBACK or "Student_ai_uz_bot").lstrip("@").strip()
    now = time.time()
    with _lock:
        _cleanup(now)
        with _connection() as conn:
            conn.execute(
                "INSERT INTO mobile_login_challenges(token_hash, expires_at) VALUES (?, ?)",
                (_token_hash(token), now + _LOGIN_TTL),
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
    now = time.time()
    with _lock:
        _cleanup(now)
        with _connection() as conn:
            cur = conn.execute(
                """UPDATE mobile_login_challenges
                   SET user_json = ?, completed_at = ?
                   WHERE token_hash = ? AND expires_at > ? AND user_json IS NULL""",
                (json.dumps(user_data, ensure_ascii=False), now, _token_hash(login_token), now),
            )
            return cur.rowcount == 1


def poll(login_token: str) -> dict:
    if not login_token:
        return {"status": "expired"}
    now = time.time()
    with _lock:
        _cleanup(now)
        with _connection() as conn:
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
            session_token = _new_session(conn, user, now)
            conn.execute("DELETE FROM mobile_login_challenges WHERE token_hash = ?", (_token_hash(login_token),))
            return {"status": "ok", "session_token": session_token, "user": user}


_SESSION_CACHE_TTL = 60.0


def get_user(session_token: str | None) -> dict | None:
    """Har bir mobil API so'rovi shu orqali o'tadi — shuning uchun qisqa muddatli
    kesh (Redis bo'lsa Redis, bo'lmasa xotira) bilan: takroriy so'rov SQLite'ga
    umuman bormaydi. Sessiya tugash vaqti keshda ham tekshiriladi, revoke esa
    keshni darhol tozalaydi. O'qish WAL'da yozuvchilarni bloklamaydi, shuning
    uchun global `_lock` ham, `_cleanup` ham kerak emas."""
    if not session_token:
        return None
    now = time.time()
    token_hash = _token_hash(session_token)
    cache_key = f"msess:{token_hash}"
    hit = cache.get(cache_key)
    if hit and hit.get("exp", 0) > now:
        return hit["user"]
    with _connection() as conn:
        row = conn.execute(
            "SELECT user_json, expires_at FROM mobile_sessions WHERE token_hash = ? AND expires_at > ?",
            (token_hash, now),
        ).fetchone()
    if not row:
        return None
    user = json.loads(row["user_json"])
    cache.set(cache_key, {"user": user, "exp": min(float(row["expires_at"]), now + _SESSION_CACHE_TTL)},
              ttl=_SESSION_CACHE_TTL)
    return user


def revoke_session(session_token: str | None) -> bool:
    """Revoke a session token immediately; token values are never stored in plaintext."""
    if not session_token:
        return False
    token_hash = _token_hash(session_token)
    cache.delete(f"msess:{token_hash}")
    with _lock, _connection() as conn:
        cur = conn.execute("DELETE FROM mobile_sessions WHERE token_hash = ?", (token_hash,))
        revoked = cur.rowcount == 1
    cache.delete(f"msess:{token_hash}")   # DELETE paytida kelgan parallel get_user qayta keshlab qo'ymasin
    return revoked


def create_code_challenge(telegram_id: int) -> str:
    """Create a short-lived one-time code for an existing Telegram bot user.

    Raises CodeCooldown if a code was issued for this ID less than
    _CODE_COOLDOWN seconds ago. Without it an attacker could request a fresh
    code (resetting the attempt counter) over and over to brute-force a
    victim's code and to spam the victim's chat.
    """
    telegram_id = int(telegram_id)
    if telegram_id <= 0:
        raise ValueError("invalid_telegram_id")
    now = time.time()
    with _lock:
        _cleanup(now)
        with _connection() as conn:
            conn.execute("BEGIN IMMEDIATE")   # o'qish->yozish: yozuv qulfini boshidan olamiz
            row = conn.execute(
                "SELECT created_at FROM mobile_code_challenges WHERE telegram_id=?", (telegram_id,)
            ).fetchone()
            if row is not None:
                wait = int(_CODE_COOLDOWN - (now - float(row["created_at"] or 0)))
                if wait > 0:
                    raise CodeCooldown(wait)
            code = f"{secrets.randbelow(1_000_000):06d}"
            conn.execute(
                """INSERT INTO mobile_code_challenges(telegram_id, code_hash, expires_at, attempts, created_at)
                   VALUES (?, ?, ?, 0, ?)
                   ON CONFLICT(telegram_id) DO UPDATE SET
                     code_hash=excluded.code_hash, expires_at=excluded.expires_at,
                     attempts=0, created_at=excluded.created_at""",
                (telegram_id, _token_hash(code), now + _CODE_TTL, now),
            )
    return code


def discard_code_challenge(telegram_id: int) -> None:
    """Kod Telegramga yetkazilmasa, foydalanuvchi qayta urinishi uchun bekor qilinadi."""
    with _lock, _connection() as conn:
        conn.execute("DELETE FROM mobile_code_challenges WHERE telegram_id=?", (int(telegram_id),))


def verify_code(telegram_id: int, code: str, profile: dict | None = None) -> dict | None:
    """Verify once, rate-limit guesses, then create the same kind of app session."""
    try:
        telegram_id = int(telegram_id)
    except (TypeError, ValueError):
        return None
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return None
    now = time.time()
    with _lock, _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT code_hash, expires_at, attempts FROM mobile_code_challenges WHERE telegram_id=?",
            (telegram_id,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= now or row["attempts"] >= _CODE_MAX_ATTEMPTS:
            conn.execute("DELETE FROM mobile_code_challenges WHERE telegram_id=?", (telegram_id,))
            return None
        if not secrets.compare_digest(row["code_hash"], _token_hash(code)):
            conn.execute("UPDATE mobile_code_challenges SET attempts=attempts+1 WHERE telegram_id=?", (telegram_id,))
            return None
        conn.execute("DELETE FROM mobile_code_challenges WHERE telegram_id=?", (telegram_id,))
        profile = profile or {}
        user = {
            "id": telegram_id,
            "username": profile.get("username") or None,
            "first_name": profile.get("first_name") or None,
        }
        session_token = _new_session(conn, user, now)
        return {"status": "ok", "session_token": session_token, "user": user}
