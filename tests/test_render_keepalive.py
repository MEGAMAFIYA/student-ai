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


if __name__ == "__main__":
    unittest.main()
