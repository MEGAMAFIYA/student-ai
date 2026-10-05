import asyncio
import hashlib
import hmac
import io
import json
import time
import unittest
import urllib.parse
from unittest import mock

import config
import movie_watch
import telegram_mtproto

BOT = "123:TESTTOKEN"
MOVIES = {
    "m1": {"id": "m1", "title": "Film 1", "size": 1000},
    "m2": {"id": "m2", "title": "Film 2", "size": 2000},
    "m0": {"id": "m0", "title": "Hajmsiz", "size": 0},
}


class FakeStorage:
    @staticmethod
    def get_movie(movie_id):
        m = MOVIES.get(str(movie_id))
        return dict(m) if m else None

    @staticmethod
    def search_movies(q=""):
        return [dict(m) for m in MOVIES.values()]


def make_init_data(user_id=7, age=0, bot=BOT):
    user = json.dumps({"id": user_id, "first_name": "A"}, separators=(",", ":"))
    data = {"auth_date": str(int(time.time()) - age), "user": user}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(data)


class FakeHandler:
    def __init__(self, path, body=None, headers=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.path = path
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Length": str(len(raw)), **(headers or {})}
        self.status = None

    def send_response(self, status):
        self.status = status

    def send_header(self, *_):
        pass

    def end_headers(self):
        pass

    def json(self):
        return json.loads(self.wfile.getvalue().decode())


class KinoBase(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(movie_watch, "storage", FakeStorage),
            mock.patch.object(config, "TELEGRAM_TOKEN", BOT),
        ]
        for p in self._patches:
            p.start()
        movie_watch.ROOMS.clear()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        movie_watch.ROOMS.clear()


class ChangeMovieTest(KinoBase):
    def _room_with(self, *user_ids):
        rid = movie_watch.create_room("m1", user_ids[0])
        for uid in user_ids[1:]:
            movie_watch.join_room(rid, make_init_data(uid))
        return rid

    def test_route_exists_and_switches_movie_for_everyone(self):
        rid = self._room_with(7, 8)
        h = FakeHandler("/api/kino/change_movie", {"room": rid, "movie_id": "m2", "init_data": make_init_data(7)})
        movie_watch.handle_post(h)
        self.assertEqual(h.status, 200, h.json())
        data = h.json()["data"]
        self.assertEqual(data["movie"]["id"], "m2")
        self.assertFalse(data["playing"])
        self.assertEqual(data["position"], 0.0)
        # ikkinchi ishtirokchi keyingi /state da yangi kinoni ko'radi
        state, err = movie_watch.room_state(rid, make_init_data(8))
        self.assertIsNone(err)
        self.assertEqual(state["movie"]["id"], "m2")
        self.assertEqual(state["version"], data["version"])
        self.assertEqual(state["actor_id"], 7)

    def test_keeps_webrtc_signals(self):
        rid = self._room_with(7, 8)
        ok, err = movie_watch.put_signal(rid, make_init_data(7), 8, {"type": "offer", "sdp": "x"})
        self.assertTrue(ok, err)
        movie_watch.change_movie(rid, make_init_data(7), "m2")
        sigs, err = movie_watch.get_signals(rid, make_init_data(8))
        self.assertIsNone(err)
        self.assertEqual(len(sigs), 1)

    def test_same_movie_does_not_reset_playback(self):
        rid = self._room_with(7)
        movie_watch.room_state(rid, make_init_data(7), True, 1234.0)
        before, _ = movie_watch.room_state(rid, make_init_data(7))
        data, err = movie_watch.change_movie(rid, make_init_data(7), "m1")
        self.assertIsNone(err)
        self.assertEqual(data["version"], before["version"])
        self.assertEqual(data["position"], 1234.0)

    def test_rejects_stranger_unknown_and_sizeless_movie(self):
        rid = self._room_with(7)
        self.assertEqual(movie_watch.change_movie(rid, make_init_data(99), "m2")[1], movie_watch.ERR_NOT_MEMBER)
        self.assertEqual(movie_watch.change_movie(rid, make_init_data(7), "nope")[1], "Kino topilmadi.")
        self.assertIn("hajmi", movie_watch.change_movie(rid, make_init_data(7), "m0")[1])
        self.assertEqual(movie_watch.change_movie(rid, "garbage", "m2")[1], movie_watch.ERR_AUTH)


class SessionLifetimeTest(KinoBase):
    def test_init_data_older_than_one_hour_still_valid_for_kino(self):
        rid = movie_watch.create_room("m1", 7)
        two_hours = make_init_data(7, age=2 * 3600)
        state, err = movie_watch.room_state(rid, two_hours)
        self.assertIsNone(err)
        self.assertEqual(state["movie"]["id"], "m1")

    def test_init_data_beyond_kino_window_is_rejected_with_auth_code(self):
        rid = movie_watch.create_room("m1", 7)
        old = make_init_data(7, age=config.KINO_INIT_DATA_MAX_AGE_SEC + 60)
        h = FakeHandler(f"/api/kino/state?room={rid}", headers={"X-Telegram-Init-Data": old})
        movie_watch.handle_api(h)
        self.assertEqual(h.status, 401)
        self.assertEqual(h.json()["code"], "auth")

    def test_stream_token_is_valid_after_one_hour(self):
        token = movie_watch._make_stream_token("r", "m1", 7)
        real = time.time()
        with mock.patch.object(movie_watch.time, "time", return_value=real + 3 * 3600):
            self.assertEqual(movie_watch._verify_stream_token(token, "r", "m1"), 7)
        with mock.patch.object(movie_watch.time, "time", return_value=real + config.KINO_STREAM_TOKEN_TTL_SEC + 120):
            self.assertIsNone(movie_watch._verify_stream_token(token, "r", "m1"))

    def test_room_lifetime_is_activity_based(self):
        rid = movie_watch.create_room("m1", 7)
        now = time.time()
        # 5 soat oldin yaratilgan, lekin hozirgina faol — o'chmasligi kerak
        movie_watch.ROOMS[rid]["created_at"] = now - 5 * 3600
        movie_watch.ROOMS[rid]["last_active"] = now - 10
        self.assertIsNotNone(movie_watch._get_room(rid))
        # uzoq vaqt faol bo'lmagan — o'chadi
        movie_watch.ROOMS[rid]["last_active"] = now - config.KINO_ROOM_TTL_SEC - 5
        self.assertIsNone(movie_watch._get_room(rid))

    def test_room_absolute_max_age(self):
        rid = movie_watch.create_room("m1", 7)
        now = time.time()
        movie_watch.ROOMS[rid]["created_at"] = now - config.KINO_ROOM_MAX_AGE_SEC - 5
        movie_watch.ROOMS[rid]["last_active"] = now
        self.assertIsNone(movie_watch._get_room(rid))

    def test_room_missing_has_room_code(self):
        h = FakeHandler("/api/kino/state?room=nope", headers={"X-Telegram-Init-Data": make_init_data(7)})
        movie_watch.handle_api(h)
        self.assertEqual(h.status, 404)
        self.assertEqual(h.json()["code"], "room")


class FakeClient:
    def __init__(self, known=False, usernames=None):
        self.known = known
        self.dialogs_calls = 0
        self.usernames = usernames or {}

    async def get_input_entity(self, ref):
        if isinstance(ref, int):
            if self.known:
                return ("peer", ref)
            raise ValueError("Could not find the input entity")
        if ref in self.usernames:
            return ("peer-by-username", ref)
        raise ValueError("nope")

    async def get_dialogs(self):
        self.dialogs_calls += 1
        self.known = True


class MTProtoResolveTest(unittest.TestCase):
    def test_warms_entity_cache_from_dialogs(self):
        c = FakeClient()
        peer = asyncio.run(telegram_mtproto._resolve_input_chat(c, -1001))
        self.assertEqual(peer, ("peer", -1001))
        self.assertEqual(c.dialogs_calls, 1)

    def test_falls_back_to_public_username(self):
        c = FakeClient(usernames={"chan": 1})
        c.get_dialogs = mock.AsyncMock()   # kesh isimaydi
        with mock.patch.object(config, "KINO_STORAGE_CHANNEL_ID", -1001), \
             mock.patch.object(config, "KINO_STORAGE_CHANNEL_USERNAME", "chan"):
            peer = asyncio.run(telegram_mtproto._resolve_input_chat(c, -1001))
        self.assertEqual(peer, ("peer-by-username", "chan"))

    def test_clear_error_when_channel_unreachable_and_no_repeated_warmups(self):
        c = FakeClient()
        c.get_dialogs = mock.AsyncMock()
        with mock.patch.object(config, "KINO_STORAGE_CHANNEL_USERNAME", ""):
            for _ in range(3):
                with self.assertRaises(telegram_mtproto.MTProtoStorageError):
                    asyncio.run(telegram_mtproto._resolve_input_chat(c, -1001))
        self.assertEqual(c.get_dialogs.await_count, 1)   # har so'rovda dialoglar qayta yuklanmaydi


class FakeIter:
    def __init__(self, chunk_len, count):
        self.chunks = [b"x" * chunk_len for _ in range(count)]
        self.closed = False

    def __aiter__(self):
        async def gen():
            for c in self.chunks:
                yield c
        return gen()

    async def close(self):
        self.closed = True


class MTProtoRangeTest(unittest.TestCase):
    def test_range_is_trimmed_to_requested_length_and_iterator_closed(self):
        it = FakeIter(64 * 1024, 3)        # Telegram 64 KiB qaytaradi, brauzer 1024 bayt so'radi
        client = mock.Mock()
        client.iter_download = mock.Mock(return_value=it)
        with mock.patch.object(telegram_mtproto, "_stream_client", client):
            data = asyncio.run(telegram_mtproto._read_range(object(), 0, 1024))
        self.assertEqual(len(data), 1024)
        self.assertTrue(it.closed)

    def test_message_is_cached_between_chunks_and_refreshed_on_file_reference_error(self):
        telegram_mtproto._MSG_CACHE.clear()
        msg = mock.Mock(media="media")
        fetch = mock.AsyncMock(return_value=msg)
        calls = {"n": 0}

        async def read(media, offset, limit):
            calls["n"] += 1
            if calls["n"] == 2:
                raise type("FileReferenceExpiredError", (Exception,), {})()
            return b"ok"

        with mock.patch.object(telegram_mtproto, "_fetch_message", fetch), \
             mock.patch.object(telegram_mtproto, "_read_range", read):
            asyncio.run(telegram_mtproto._download_range_async(-1001, 5, 0, 10))      # fetch #1
            asyncio.run(telegram_mtproto._download_range_async(-1001, 5, 10, 10))     # kesh + xato -> fetch #2
        self.assertEqual(fetch.await_count, 2)
        telegram_mtproto._MSG_CACHE.clear()


class MobileBaseUrlTest(unittest.TestCase):
    def setUp(self):
        try:
            import mobile_api
        except ImportError as exc:          # og'ir bog'liqliklar yo'q muhit
            self.skipTest(f"mobile_api import bo'lmadi: {exc}")
        self.api = mobile_api

    def _h(self, **headers):
        return mock.Mock(headers=headers)

    def test_configured_url_wins_and_gets_scheme(self):
        with mock.patch.object(config, "PUBLIC_BASE_URL", "bot.example.com/"):
            self.assertEqual(self.api._public_base_url(self._h()), "https://bot.example.com")

    def test_render_env_then_request_headers(self):
        with mock.patch.object(config, "PUBLIC_BASE_URL", ""), \
             mock.patch.dict("os.environ", {"RENDER_EXTERNAL_URL": "https://x.onrender.com"}):
            self.assertEqual(self.api._public_base_url(self._h()), "https://x.onrender.com")
        with mock.patch.object(config, "PUBLIC_BASE_URL", ""), mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("RENDER_EXTERNAL_URL", None)
            h = self._h(**{"Host": "app.example.com", "X-Forwarded-Proto": "http"})
            self.assertEqual(self.api._public_base_url(h), "https://app.example.com")   # ochiq host -> https
            self.assertEqual(self.api._public_base_url(self._h(Host="10.0.2.2:8080")), "http://10.0.2.2:8080")
            self.assertEqual(self.api._public_base_url(self._h()), "")
            self.assertEqual(self.api._public_base_url(self._h(Host="evil.com/<x>")), "")


if __name__ == "__main__":
    unittest.main()
