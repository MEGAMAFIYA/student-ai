"""Qotish/lock tuzatishlari uchun testlar: kechiktirilgan yozish, Neon ulanishini
qayta ishlatish, Redis'siz kesh, signal'ning bazaga yozmasligi, SQLite parallel yuk."""
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cache
import config
import game
import mobile_auth


class DeferredWriteTests(unittest.TestCase):
    def test_many_saves_coalesce_into_few_writes_with_latest_value(self):
        writes = []
        counter = {"n": 0}

        def supplier():
            return str(counter["n"])

        with patch.object(config, "persist_write", side_effect=lambda f, k, raw, commit_message="": writes.append(raw)), \
             patch.object(config, "PERSIST_DEBOUNCE_SEC", 0.2), patch.object(config, "PERSIST_DEFERRED", True):
            t0 = time.perf_counter()
            for i in range(200):
                counter["n"] = i
                config.persist_write_deferred("x.json", "test_coalesce", supplier)
            self.assertLess(time.perf_counter() - t0, 0.2, "chaqiruvchi bloklanmasligi kerak")
            self.assertTrue(config.flush_deferred(5))
        self.assertLessEqual(len(writes), 3)
        self.assertEqual(writes[-1], "199")

    def test_flush_writes_pending_immediately(self):
        writes = []
        with patch.object(config, "persist_write", side_effect=lambda f, k, raw, commit_message="": writes.append(raw)), \
             patch.object(config, "PERSIST_DEBOUNCE_SEC", 30), patch.object(config, "PERSIST_DEFERRED", True):
            config.persist_write_deferred("y.json", "test_flush", lambda: "final")
            config.flush_deferred(5)
        self.assertEqual(writes, ["final"])

    def test_sync_mode_when_disabled_does_not_deadlock(self):
        import storage
        with patch.object(config, "PERSIST_DEFERRED", False), patch.object(config, "persist_write") as w:
            storage.record_usage("unit_test_fn", 1)   # _lock ichida _save() -> supplier() (RLock)
        self.assertTrue(w.called)


class GameSignalTests(unittest.TestCase):
    def test_signal_does_not_trigger_db_write(self):
        rid = "r" * 24
        game.ROOMS[rid] = {"id": rid, "game": "chess", "created_at": time.time(), "updated_at": time.time(),
                           "players": {"1": {}, "2": {}}, "signals": {}, "version": 0}
        try:
            with patch.object(game, "_verify", return_value={"id": 1}), patch.object(game, "_save") as save:
                ok, err = game.signal(rid, "init", 2, {"type": "ice", "c": 1})
                self.assertTrue(ok, err)
                save.assert_not_called()
            with patch.object(game, "_verify", return_value={"id": 2}):
                items, _ = game.get_signals(rid, "init")
            self.assertEqual(len(items), 1)
        finally:
            game.ROOMS.pop(rid, None)


class CacheFallbackTests(unittest.TestCase):
    def setUp(self):
        cache.clear_local()

    def test_works_without_redis(self):
        cache.set("k", {"a": 1}, ttl=5)
        self.assertEqual(cache.get("k"), {"a": 1})
        cache.delete("k")
        self.assertIsNone(cache.get("k"))

    def test_unreachable_redis_never_raises_and_is_not_retried_every_call(self):
        fake = types.ModuleType("redis")
        calls = {"n": 0}

        class _R:
            @staticmethod
            def from_url(*a, **k):
                calls["n"] += 1
                raise ConnectionError("down")
        fake.Redis = _R
        with patch.dict(sys.modules, {"redis": fake}), patch.object(cache, "REDIS_URL", "redis://127.0.0.1:1/0"), \
             patch.object(cache, "_redis", None), patch.object(cache, "_redis_down_until", 0.0):
            for i in range(50):
                cache.set(f"k{i}", i, ttl=5)
                self.assertEqual(cache.get(f"k{i}"), i)
        self.assertEqual(calls["n"], 1)


class NeonReconnectTests(unittest.TestCase):
    def test_connection_reused_and_reconnects_once_after_drop(self):
        fake = types.ModuleType("psycopg2")

        class OperationalError(Exception): pass
        class InterfaceError(Exception): pass
        fake.OperationalError, fake.InterfaceError = OperationalError, InterfaceError
        opened = []

        class Conn:
            closed = 0
            def __init__(self, broken): self.broken = broken
            def close(self): self.closed = 1

        def connect():
            c = Conn(broken=(len(opened) == 0))
            opened.append(c)
            return c

        def work(conn):
            if conn.broken:
                raise OperationalError("server closed the connection")
            return "ok"

        with patch.dict(sys.modules, {"psycopg2": fake}), patch.object(config, "_neon_connect", side_effect=connect), \
             patch.object(config, "_neon_conn", None):
            self.assertEqual(config._neon_run(work), (True, "ok"))
            self.assertEqual(config._neon_run(work), (True, "ok"))
            self.assertEqual(config._neon_run(work), (True, "ok"))
        self.assertEqual(len(opened), 2)   # 1 ta buzilgan + 1 ta yangi, keyin qayta ishlatilgan


class SqliteConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "s.sqlite3")
        self.p = patch.object(mobile_auth, "_db_path", return_value=self.path)
        self.p.start()
        mobile_auth._wal_ready.clear()
        mobile_auth._init_db()
        cache.clear_local()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_wal_enabled(self):
        with mobile_auth._connection() as c:
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_parallel_writers_and_readers_no_database_locked(self):
        errors = []

        def worker(i):
            try:
                for j in range(25):
                    s = mobile_auth.start_login()
                    mobile_auth.complete_login(s.login_token, SimpleNamespace(id=i * 1000 + j, username="u", first_name="n"))
                    r = mobile_auth.poll(s.login_token)
                    assert r["status"] == "ok"
                    for _ in range(5):
                        assert mobile_auth.get_user(r["session_token"])["id"] == i * 1000 + j
                    if j % 5 == 0:
                        mobile_auth.revoke_session(r["session_token"])
                        assert mobile_auth.get_user(r["session_token"]) is None
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        self.assertEqual(errors, [])

    def test_repeated_get_user_hits_cache_not_db(self):
        s = mobile_auth.start_login()
        mobile_auth.complete_login(s.login_token, SimpleNamespace(id=5, username="a", first_name="b"))
        tok = mobile_auth.poll(s.login_token)["session_token"]
        mobile_auth.get_user(tok)
        with patch.object(mobile_auth, "_connection", side_effect=AssertionError("DB'ga murojaat qilmasligi kerak")):
            for _ in range(20):
                self.assertEqual(mobile_auth.get_user(tok)["id"], 5)

    def test_expired_session_not_served_from_cache(self):
        s = mobile_auth.start_login()
        mobile_auth.complete_login(s.login_token, SimpleNamespace(id=6, username="a", first_name="b"))
        tok = mobile_auth.poll(s.login_token)["session_token"]
        self.assertIsNotNone(mobile_auth.get_user(tok))
        with mobile_auth._connection() as c:
            c.execute("UPDATE mobile_sessions SET expires_at = ?", (time.time() - 1,))
        cache.clear_local()
        self.assertIsNone(mobile_auth.get_user(tok))


if __name__ == "__main__":
    unittest.main()
