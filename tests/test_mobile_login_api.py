import io
import json
import os
import tempfile
import unittest
from unittest import mock

import mobile_auth


class FakeHandler:
    def __init__(self, path, body=None, headers=None, peer="10.0.0.1"):
        raw = json.dumps(body).encode() if body is not None else b""
        self.path = path
        self.headers = {"Content-Length": str(len(raw)), **(headers or {})}
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.client_address = (peer, 1234)
        self.status = None

    def send_response(self, code): self.status = code
    def send_header(self, *a): pass
    def end_headers(self): pass

    def json(self):
        return json.loads(self.wfile.getvalue().decode())


class CodeLoginApiTests(unittest.TestCase):
    def setUp(self):
        try:
            import mobile_api
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"mobile_api import bo'lmadi: {exc}")
        self.api = mobile_api
        self.api._RATE_LIMITS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(mobile_auth, "_db_path", return_value=os.path.join(self.tmp.name, "a.sqlite3"))
        self.p.start()
        mobile_auth._init_db()
        self.t = mock.patch.object(self.api.config, "TELEGRAM_TOKEN", "123:abc")
        self.t.start()

    def tearDown(self):
        self.t.stop(); self.p.stop(); self.tmp.cleanup()

    def _post(self, path, body, **kw):
        h = FakeHandler(path, body, **kw)
        self.assertTrue(self.api.handle_post(h))
        return h

    def test_request_sends_code_and_verify_logs_in(self):
        sent = {}
        def fake_send(chat_id, text):
            sent["chat_id"], sent["text"] = chat_id, text
            return "ok", ""
        with mock.patch.object(self.api, "_send_telegram_text", fake_send):
            h = self._post("/api/mobile/auth/code/request", {"telegram_id": 4242})
        self.assertEqual((h.status, h.json()["sent"]), (200, True))
        self.assertNotIn("\\n", sent["text"])  # literal backslash-n bo'lmasin
        code = sent["text"].split("kodi: ")[1][:6]
        h = self._post("/api/mobile/auth/code/verify", {"telegram_id": 4242, "code": code})
        self.assertEqual(h.status, 200)
        self.assertEqual(h.json()["user"]["id"], 4242)

    def test_unreachable_user_gets_400_and_can_retry_immediately(self):
        with mock.patch.object(self.api, "_send_telegram_text", return_value=("blocked", "chat not found")):
            h = self._post("/api/mobile/auth/code/request", {"telegram_id": 77})
        self.assertEqual((h.status, h.json()["error"]), (400, "cannot_message_user"))
        with mock.patch.object(self.api, "_send_telegram_text", return_value=("ok", "")):
            h = self._post("/api/mobile/auth/code/request", {"telegram_id": 77})
        self.assertEqual(h.status, 200)

    def test_second_request_is_429_and_bad_id_is_400(self):
        with mock.patch.object(self.api, "_send_telegram_text", return_value=("ok", "")):
            self._post("/api/mobile/auth/code/request", {"telegram_id": 5})
            h = self._post("/api/mobile/auth/code/request", {"telegram_id": 5})
        self.assertEqual(h.status, 429)
        h = self._post("/api/mobile/auth/code/request", {"telegram_id": "abc"})
        self.assertEqual(h.status, 400)

    def test_wrong_code_is_401(self):
        h = self._post("/api/mobile/auth/code/verify", {"telegram_id": 1, "code": "123456"})
        self.assertEqual(h.status, 401)

    def test_client_ip_uses_forwarded_for_behind_proxy_only(self):
        h = FakeHandler("/", headers={"X-Forwarded-For": "1.2.3.4, 9.9.9.9"}, peer="10.1.1.1")
        self.assertEqual(self.api._client_ip(h), "9.9.9.9")
        h = FakeHandler("/", headers={"X-Forwarded-For": "6.6.6.6"}, peer="8.8.8.8")
        self.assertEqual(self.api._client_ip(h), "8.8.8.8")  # spoof qilib bo'lmaydi


if __name__ == "__main__":
    unittest.main()
