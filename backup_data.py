"""Zaxira nusxa: Neon/Postgres (student_ai_kv) + SQLite (mobile_auth) + mahalliy JSON.

Ishlatish (o'zgartirishlardan OLDIN va muntazam):
    python backup_data.py                # ./backups/<vaqt>/ papkasiga
    python backup_data.py /yo/l/papka

DATABASE_URL muhit o'zgaruvchisidan o'qiladi (kodga yozilmaydi). Faqat O'QIYDI —
hech narsani o'zgartirmaydi/o'chirmaydi. Tiklash: har bir <key>.json fayl
student_ai_kv.value ustunining aynan o'zi.
"""
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def backup_postgres(dest: str) -> int:
    url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not url:
        print("• DATABASE_URL yo'q — Postgres zaxirasi o'tkazib yuborildi.")
        return 0
    import psycopg2
    conn = psycopg2.connect(url, connect_timeout=15)
    n = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key, value FROM student_ai_kv")
            for key, value in cur.fetchall():
                safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
                with open(os.path.join(dest, f"pg_{safe}.json"), "w", encoding="utf-8") as f:
                    f.write(value)
                n += 1
    finally:
        conn.close()
    print(f"✅ Postgres: {n} ta kalit saqlandi.")
    return n


def backup_sqlite(dest: str) -> None:
    path = os.getenv("MOBILE_AUTH_DB_PATH", os.path.join(HERE, "mobile_auth.sqlite3"))
    if not os.path.exists(path):
        print(f"• SQLite fayl yo'q ({path}) — o'tkazib yuborildi.")
        return
    src = sqlite3.connect(path)
    dst = sqlite3.connect(os.path.join(dest, "mobile_auth.sqlite3"))
    with dst:
        src.backup(dst)   # ishlayotgan (WAL) bazadan ham izchil nusxa oladi
    src.close(); dst.close()
    print("✅ SQLite: mobile_auth.sqlite3 saqlandi.")


def backup_local_json(dest: str) -> None:
    for name in ("app_data.json", "game_rooms.json", "wallet.json", "runtime_ai_config.json"):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(dest, name))
            print(f"✅ Mahalliy: {name}")


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "backups")
    dest = os.path.join(base, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dest, exist_ok=True)
    backup_postgres(dest)
    backup_sqlite(dest)
    backup_local_json(dest)
    print(f"Zaxira: {dest}")


if __name__ == "__main__":
    main()
