"""
🧠 AL YORDAMCHI uchun foydalanuvchining shaxsiy PostgreSQL xotirasi.

Har bir Business akkaunt egasi o'zining PostgreSQL connection string'ini beradi.
Bu odatda Neon yoki Supabase kabi bepul PostgreSQL xizmatidan olinadi.
Bot shu connection'da faqat `al_*` jadvallaridan foydalanadi va barcha qatorlarni
Telegram user_id bilan ajratadi.

Connection string foydalanuvchining shaxsiy AI-key faylida saqlanadi; bu modul
uni logga chiqarmaydi.
"""
import asyncio
import logging
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("al_yordamchi_db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS al_memories (
    id BIGSERIAL PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    memory TEXT NOT NULL,
    tags TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_al_memories_owner ON al_memories(owner_id);

CREATE TABLE IF NOT EXISTS al_peers (
    owner_id BIGINT NOT NULL,
    peer_id BIGINT NOT NULL,
    display_name TEXT DEFAULT '',
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(owner_id, peer_id)
);
CREATE INDEX IF NOT EXISTS idx_al_peers_owner ON al_peers(owner_id);

CREATE TABLE IF NOT EXISTS al_chat_history (
    id BIGSERIAL PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    peer_id BIGINT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_al_history_peer
    ON al_chat_history(owner_id, peer_id, created_at DESC);
"""


def _connect(dsn: str):
    if not dsn or not str(dsn).strip():
        raise ValueError("PostgreSQL connection string bo'sh.")
    return psycopg2.connect(str(dsn).strip(), connect_timeout=10)


def _now():
    return datetime.now(timezone.utc)


def init_schema(dsn: str) -> None:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()


def test_connection(dsn: str) -> tuple[bool, str]:
    try:
        init_schema(dsn)
        return True, "PostgreSQL ulanishi va AL YORDAMCHI jadvallari tayyor."
    except Exception as exc:
        logger.warning("DB test failed: %s", type(exc).__name__)
        return False, f"{type(exc).__name__}: {str(exc)[:240]}"


def add_memory(dsn: str, owner_id: int, memory: str, tags: str = "") -> None:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO al_memories(owner_id,memory,tags) VALUES(%s,%s,%s)",
                (int(owner_id), memory.strip(), tags[:500]),
            )
        conn.commit()


def list_memories(dsn: str, owner_id: int, limit: int = 40) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    with _connect(dsn) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT memory,tags,created_at FROM al_memories "
                "WHERE owner_id=%s ORDER BY updated_at DESC, id DESC LIMIT %s",
                (int(owner_id), limit),
            )
            return [dict(r) for r in cur.fetchall()]


def upsert_peer(dsn: str, owner_id: int, peer_id: int, display_name: str = "") -> bool:
    """Peer mavjud bo'lsa False, yangi yozilsa True qaytaradi."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM al_peers WHERE owner_id=%s AND peer_id=%s",
                (int(owner_id), int(peer_id)),
            )
            existed = cur.fetchone() is not None
            cur.execute(
                """
                INSERT INTO al_peers(owner_id,peer_id,display_name,first_seen,last_seen)
                VALUES(%s,%s,%s,NOW(),NOW())
                ON CONFLICT(owner_id,peer_id)
                DO UPDATE SET display_name=EXCLUDED.display_name,last_seen=NOW()
                """,
                (int(owner_id), int(peer_id), (display_name or "")[:300]),
            )
        conn.commit()
    return not existed


def peer_exists(dsn: str, owner_id: int, peer_id: int) -> bool:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM al_peers WHERE owner_id=%s AND peer_id=%s",
                (int(owner_id), int(peer_id)),
            )
            return cur.fetchone() is not None


def seed_old_peers(dsn: str, owner_id: int, peer_ids: list[int]) -> None:
    if not peer_ids:
        return
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            for peer_id in peer_ids:
                cur.execute(
                    """
                    INSERT INTO al_peers(owner_id,peer_id,display_name,first_seen,last_seen)
                    VALUES(%s,%s,'',NOW(),NOW())
                    ON CONFLICT(owner_id,peer_id) DO NOTHING
                    """,
                    (int(owner_id), int(peer_id)),
                )
        conn.commit()


def append_history(dsn: str, owner_id: int, peer_id: int, role: str, content: str) -> None:
    if role not in ("user", "assistant") or not content.strip():
        return
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO al_chat_history(owner_id,peer_id,role,content) VALUES(%s,%s,%s,%s)",
                (int(owner_id), int(peer_id), role, content.strip()[:8000]),
            )
        conn.commit()


def get_history(dsn: str, owner_id: int, peer_id: int, limit: int = 16) -> list[dict]:
    limit = max(1, min(int(limit), 40))
    with _connect(dsn) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT role,content FROM al_chat_history "
                "WHERE owner_id=%s AND peer_id=%s ORDER BY id DESC LIMIT %s",
                (int(owner_id), int(peer_id), limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
            rows.reverse()
            return rows


async def a_test_connection(dsn: str):
    return await asyncio.to_thread(test_connection, dsn)


async def a_add_memory(dsn: str, owner_id: int, memory: str, tags: str = ""):
    return await asyncio.to_thread(add_memory, dsn, owner_id, memory, tags)


async def a_list_memories(dsn: str, owner_id: int, limit: int = 40):
    return await asyncio.to_thread(list_memories, dsn, owner_id, limit)


async def a_upsert_peer(dsn: str, owner_id: int, peer_id: int, display_name: str = ""):
    return await asyncio.to_thread(upsert_peer, dsn, owner_id, peer_id, display_name)


async def a_peer_exists(dsn: str, owner_id: int, peer_id: int):
    return await asyncio.to_thread(peer_exists, dsn, owner_id, peer_id)


async def a_seed_old_peers(dsn: str, owner_id: int, peer_ids: list[int]):
    return await asyncio.to_thread(seed_old_peers, dsn, owner_id, peer_ids)


async def a_append_history(dsn: str, owner_id: int, peer_id: int, role: str, content: str):
    return await asyncio.to_thread(append_history, dsn, owner_id, peer_id, role, content)


async def a_get_history(dsn: str, owner_id: int, peer_id: int, limit: int = 16):
    return await asyncio.to_thread(get_history, dsn, owner_id, peer_id, limit)
