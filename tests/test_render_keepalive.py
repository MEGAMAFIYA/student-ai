import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

import render_api
import render_keepalive
import storage


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeClient:
    """httpx.AsyncClient o'rnini bosadi; har chaqiruvda navbatdagi status qaytaradi."""

    statuses: list = []
    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        _FakeClient.calls.append(url)
        status = _FakeClient.statuses.pop(0)
        if isinstance(status, Exception):
            raise status
        return _FakeResponse(status)


class PublicUrlTests(unittest.TestCase):
    def test_url_from_service_details(self):
        svc = {"serviceDetails": {"url": "https://app.onrender.com"}}
        self.assertEqual(render_api.get_public_url(svc), "https://app.onrender.com")

    def test_url_without_scheme_gets_https(self):
        self.assertEqual(render_api.get_public_url({"url": "app.onrender.com"}), "https://app.onrender.com")

    def test_worker_has_no_url(self):
        self.assertEqual(render_api.get_public_url({"type": "background_worker", "serviceDetails": {}}), "")

    def test_plan(self):
        self.assertEqual(render_api.get_plan({"serviceDetails": {"plan": "Free"}}), "free")


class PingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _FakeClient.statuses = []
        _FakeClient.calls = []

    async def test_200_is_ok(self):
        _FakeClient.statuses = [200]
        with patch.object(render_keepalive.httpx, "AsyncClient", _FakeClient):
            result = await render_keepalive.ping_url("https://x.onrender.com")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)

    async def test_404_still_means_awake(self):
        _FakeClient.statuses = [404]
        with patch.object(render_keepalive.httpx, "AsyncClient", _FakeClient):
            result = await render_keepalive.ping_url("https://x.onrender.com")
        self.assertTrue(result["ok"])

    async def test_503_then_200_retries_once(self):
        _FakeClient.statuses = [503, 200]
        with patch.object(render_keepalive.httpx, "AsyncClient", _FakeClient), \
                patch.object(render_keepalive, "_RETRY_DELAY", 0):
            result = await render_keepalive.ping_url("https://x.onrender.com")
        self.assertTrue(result["ok"])
        self.assertEqual(len(_FakeClient.calls), 2)

    async def test_persistent_503_fails(self):
        _FakeClient.statuses = [503, 502]
        with patch.object(render_keepalive.httpx, "AsyncClient", _FakeClient), \
                patch.object(render_keepalive, "_RETRY_DELAY", 0):
            result = await render_keepalive.ping_url("https://x.onrender.com")
        self.assertFalse(result["ok"])


class KeepaliveStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = dict(storage._data["keepalive"])
        storage._data["keepalive"] = {}
        self._patch = patch.object(storage, "_save", lambda: None)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        storage._data["keepalive"] = self._saved
        render_keepalive._last_ping.clear()

    def test_set_get_remove(self):
        storage.set_keepalive("srv-1", "My App", "https://a.onrender.com")
        self.assertEqual(storage.get_keepalive("srv-1")["url"], "https://a.onrender.com")
        self.assertIn("srv-1", storage.get_all_keepalive())
        self.assertTrue(storage.remove_keepalive("srv-1"))
        self.assertIsNone(storage.get_keepalive("srv-1"))
        self.assertFalse(storage.remove_keepalive("srv-1"))

    async def test_ping_all_once_pings_every_enabled_service(self):
        storage.set_keepalive("srv-1", "A", "https://a.onrender.com")
        storage.set_keepalive("srv-2", "B", "https://b.onrender.com")
        _FakeClient.statuses = [200, 200]
        _FakeClient.calls = []
        with patch.object(render_keepalive.httpx, "AsyncClient", _FakeClient):
            await render_keepalive.ping_all_once()
        self.assertEqual(sorted(_FakeClient.calls), ["https://a.onrender.com", "https://b.onrender.com"])
        self.assertTrue(render_keepalive.get_last_ping("srv-1")["ok"])
        self.assertTrue(render_keepalive.get_last_ping("srv-2")["ok"])


class KeepaliveLoopTests(unittest.IsolatedAsyncioTestCase):
    """Fon tsikli: har servis o'z vazifasida, qat'iy jadval, o'chirilganlar tozalanadi."""

    def setUp(self):
        self._saved = dict(storage._data["keepalive"])
        storage._data["keepalive"] = {}
        self._patch = patch.object(storage, "_save", lambda: None)
        self._patch.start()
        render_keepalive._last_ping.clear()

    def tearDown(self):
        self._patch.stop()
        storage._data["keepalive"] = self._saved
        render_keepalive._last_ping.clear()
        render_keepalive.stop()

    def test_set_keepalive_rejects_bad_url(self):
        with self.assertRaises(ValueError):
            storage.set_keepalive("srv-x", "X", "javascript:alert(1)")
        with self.assertRaises(ValueError):
            storage.set_keepalive("srv-x", "X", "")

    async def test_slow_service_does_not_delay_others(self):
        storage.set_keepalive("slow", "Slow", "https://slow.example")
        storage.set_keepalive("fast", "Fast", "https://fast.example")
        done = []

        async def fake_ping(url):
            if "slow" in url:
                await asyncio.sleep(0.5)
            done.append(url)
            return {"ts": 0, "ok": True, "status": 200, "detail": "HTTP 200"}

        with patch.object(render_keepalive, "ping_url", fake_ping), \
                patch.object(render_keepalive, "_START_DELAY", 0), \
                patch.object(render_keepalive, "INTERVAL_SECONDS", 10):
            render_keepalive.start()
            await asyncio.sleep(0.15)
            self.assertEqual(done, ["https://fast.example"])   # tez servis sekinini kutmadi
            await asyncio.sleep(0.5)
            self.assertIn("https://slow.example", done)

    async def test_removed_service_result_is_pruned_and_no_overlap(self):
        storage.set_keepalive("a", "A", "https://a.example")
        calls = []

        async def fake_ping(url):
            calls.append(url)
            await asyncio.sleep(0.3)               # interval (0.1) dan uzun — ustma-ust ping bo'lmasligi kerak
            return {"ts": 0, "ok": True, "status": 200, "detail": "HTTP 200"}

        with patch.object(render_keepalive, "ping_url", fake_ping), \
                patch.object(render_keepalive, "_START_DELAY", 0), \
                patch.object(render_keepalive, "INTERVAL_SECONDS", 0.1):
            render_keepalive.start()
            await asyncio.sleep(0.25)
            self.assertEqual(len(calls), 1)         # oldingi ping tugamaguncha yangisi boshlanmadi
            storage.remove_keepalive("a")
            await asyncio.sleep(0.5)
            self.assertIsNone(render_keepalive.get_last_ping("a"))

    async def test_self_ping_is_opt_in(self):
        env = {"RENDER_EXTERNAL_URL": "https://bot.onrender.com"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("KEEPALIVE_SELF", None)
            self.assertEqual(render_keepalive._targets(), {})
            os.environ["KEEPALIVE_SELF"] = "1"
            try:
                self.assertEqual(render_keepalive._targets()["__self__"]["url"], "https://bot.onrender.com")
                storage.set_keepalive("srv-1", "A", "https://a.onrender.com")
                self.assertNotIn("__self__", render_keepalive._targets())   # qo'lda yoqilgan ustuvor
            finally:
                os.environ.pop("KEEPALIVE_SELF", None)


if __name__ == "__main__":
    unittest.main()
