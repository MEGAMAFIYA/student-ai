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
        self.sent_headers = {}
        self.close_connection = False

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers[key] = value

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

    def test_every_stream_token_verifies_even_when_signature_contains_a_dot_byte(self):
        # Imzo 32 tasodifiy bayt; ~12% hollarda 0x2E ("." ) bor. Avval bunday tokenlar rad etilardi.
        with_dot = 0
        for uid in range(1, 1500):
            token = movie_watch._make_stream_token("r" * 24, "m1", uid)
            pad = "=" * (-len(token) % 4)
            import base64
            if b"." in base64.urlsafe_b64decode(token + pad)[-32:]:
                with_dot += 1
            self.assertEqual(movie_watch._verify_stream_token(token, "r" * 24, "m1"), uid, uid)
        self.assertGreater(with_dot, 50)    # test haqiqatan ham shu holatni qamrab oldi

    def test_tampered_or_truncated_stream_tokens_are_rejected(self):
        token = movie_watch._make_stream_token("r", "m1", 7)
        self.assertIsNone(movie_watch._verify_stream_token(token[:-4], "r", "m1"))
        self.assertIsNone(movie_watch._verify_stream_token(token, "r", "m2"))
        self.assertIsNone(movie_watch._verify_stream_token(token, "other", "m1"))
        self.assertIsNone(movie_watch._verify_stream_token("", "r", "m1"))
        self.assertIsNone(movie_watch._verify_stream_token("AAAA", "r", "m1"))

    def test_room_lifetime_is_activity_based(self):
        rid = movie_watch.create_room("m1", 7)
        movie_watch.join_room(rid, make_init_data(7))      # haqiqiy xona: kimdir kirgan
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


class RangeTest(KinoBase):
    SIZE = 100 * 1024 * 1024

    def _h(self, rng=None):
        headers = {"Range": rng} if rng else {}
        return FakeHandler("/x", headers=headers)

    def test_open_ended_range_is_206_and_capped(self):
        start, end, status = movie_watch._effective_range(self._h("bytes=0-"), self.SIZE)
        self.assertEqual(status, 206)                     # iOS Safari 206 kutadi (avval 200 edi)
        cap = config.KINO_STREAM_MAX_RESPONSE_BYTES
        self.assertEqual((start, end), (0, cap - 1))      # butun film emas, faqat bir bo'lak

    def test_seek_range_is_capped_from_new_offset(self):
        start, end, status = movie_watch._effective_range(self._h("bytes=50000000-"), self.SIZE)
        self.assertEqual(start, 50000000)
        self.assertEqual(end - start + 1, config.KINO_STREAM_MAX_RESPONSE_BYTES)

    def test_small_and_tail_ranges_untouched(self):
        self.assertEqual(movie_watch._effective_range(self._h("bytes=0-1"), self.SIZE), (0, 1, 206))
        s, e, st = movie_watch._effective_range(self._h("bytes=-1024"), self.SIZE)
        self.assertEqual((e, st), (self.SIZE - 1, 206))
        self.assertEqual(e - s + 1, 1024)

    def test_no_range_is_full_200_and_bad_range_is_none(self):
        self.assertEqual(movie_watch._effective_range(self._h(), 1000), (0, 999, 200))
        self.assertIsNone(movie_watch._effective_range(self._h("bytes=5000-"), 1000))
        self.assertIsNone(movie_watch._effective_range(self._h("garbage"), 1000))

    def test_headers_have_content_range_of_truncated_part(self):
        h = FakeHandler("/x")
        start, end, status = movie_watch._effective_range(self._h("bytes=0-"), self.SIZE)
        movie_watch._send_stream_headers(h, status, "video/mp4", self.SIZE, start, end)
        self.assertEqual(h.status, 206)
        self.assertEqual(h.sent_headers["Content-Range"], f"bytes 0-{end}/{self.SIZE}")
        self.assertEqual(h.sent_headers["Content-Length"], str(end + 1))

    def test_client_disconnect_releases_slot_without_fallback(self):
        rid = movie_watch.create_room("m1", 7)
        token = movie_watch._make_stream_token(rid, "m1", 7)
        h = FakeHandler(f"/api/kino/stream/{rid}/m1?token={token}", headers={"Range": "bytes=0-"})
        movie = dict(MOVIES["m1"], size=5000)
        with mock.patch.object(FakeStorage, "get_movie", staticmethod(lambda i: dict(movie) if i == "m1" else None)), \
             mock.patch.object(movie_watch, "_serve_mtproto_range", side_effect=BrokenPipeError()), \
             mock.patch.object(movie_watch, "_bot_api_stream") as fallback:
            movie_watch.serve_movie(h, rid, "m1")
        fallback.assert_not_called()
        self.assertTrue(h.close_connection)
        # slot qaytdi: yana to'liq limit olinadi
        slots = [movie_watch.STREAM_SLOT.acquire(blocking=False) for _ in range(config.KINO_STREAM_MAX_CONCURRENT)]
        self.assertTrue(all(slots))
        for _ in slots:
            movie_watch.STREAM_SLOT.release()


class InputValidationTest(KinoBase):
    def setUp(self):
        super().setUp()
        self.rid = movie_watch.create_room("m1", 7)
        movie_watch.join_room(self.rid, make_init_data(8))
        self.init = make_init_data(7)

    def _post(self, path, body=None, raw=None, headers=None):
        h = FakeHandler(path, body, headers={"X-Telegram-Init-Data": self.init, **(headers or {})})
        if raw is not None:
            h.rfile = io.BytesIO(raw)
            h.headers["Content-Length"] = str(len(raw))
        movie_watch.handle_post(h)
        return h

    def test_malformed_fields_are_400_not_crashes(self):
        for path, body in [
            ("/api/kino/signal", {"room": self.rid, "target_user_id": "abc", "payload": {}}),
            ("/api/kino/signal", {"room": self.rid, "target_user_id": 8, "payload": "x"}),
            ("/api/kino/state", {"room": self.rid, "playing": True, "position": "abc"}),
            ("/api/kino/state", {"room": self.rid, "playing": "yes", "position": 1}),
            ("/api/kino/chat", {"room": self.rid, "text": 123}),
        ]:
            h = self._post(path, body)
            self.assertEqual(h.status, 400, (path, body, h.wfile.getvalue()))

    def test_json_list_and_nonfinite_numbers_are_rejected(self):
        self.assertEqual(self._post("/api/kino/state", raw=b"[1,2]").status, 400)
        before, _ = movie_watch.room_state(self.rid, self.init)
        for bad in (b"Infinity", b"-Infinity", b"NaN"):
            h = self._post("/api/kino/state", raw=b'{"room":"%s","playing":true,"position":%s}' % (self.rid.encode(), bad))
            self.assertEqual(h.status, 400)
        after, _ = movie_watch.room_state(self.rid, self.init)
        self.assertEqual(before["version"], after["version"])           # holat buzilmadi
        json.dumps(after, allow_nan=False)                              # barcha uchun yaroqli JSON

    def test_position_is_clamped(self):
        data, err = movie_watch.room_state(self.rid, self.init, True, 1e30)
        self.assertIsNone(err)
        self.assertEqual(data["position"], float(movie_watch.MAX_POSITION_SEC))
        data, _ = movie_watch.room_state(self.rid, self.init, True, -5)
        self.assertEqual(data["position"], 0.0)

    def test_huge_content_length_rejected_before_reading(self):
        h = FakeHandler("/api/kino/state", headers={"Content-Length": "9999999999", "X-Telegram-Init-Data": self.init})
        h.rfile = mock.Mock()
        movie_watch.handle_post(h)
        self.assertEqual(h.status, 413)
        h.rfile.read.assert_not_called()
        self.assertTrue(h.close_connection)

    def test_bad_content_length_header(self):
        h = FakeHandler("/api/kino/state", headers={"Content-Length": "abc"})
        movie_watch.handle_post(h)
        self.assertEqual(h.status, 400)

    def test_unexpected_error_becomes_json_500(self):
        with mock.patch.object(movie_watch, "room_state", side_effect=RuntimeError("boom")):
            h = self._post("/api/kino/state", {"room": self.rid, "playing": True, "position": 1})
        self.assertEqual(h.status, 500)
        self.assertEqual(h.json()["code"], "error")


class PayloadPrivacyTest(KinoBase):
    SECRET = {"file_id": "F", "telegram_access_hash": 99, "telegram_file_reference": "REF",
              "telegram_document_id": 5, "uploaded_by": 1, "telegram_chat_id": -100, "telegram_message_id": 3}

    def setUp(self):
        super().setUp()
        MOVIES["m1"].update(self.SECRET)

    def tearDown(self):
        for k in self.SECRET:
            MOVIES["m1"].pop(k, None)
        super().tearDown()

    def _assert_clean(self, movie):
        self.assertEqual(sorted(movie), ["id", "mime_type", "size", "title"])
        blob = json.dumps(movie)
        for secret in ("REF", "file_id", "access_hash", "uploaded_by"):
            self.assertNotIn(secret, blob)

    def test_join_state_and_change_never_leak_internal_fields(self):
        rid = movie_watch.create_room("m1", 7)
        data, _ = movie_watch.join_room(rid, make_init_data(7))
        self._assert_clean(data["movie"])
        state, _ = movie_watch.room_state(rid, make_init_data(7))
        self._assert_clean(state["movie"])
        changed, _ = movie_watch.change_movie(rid, make_init_data(7), "m2")
        self._assert_clean(changed["movie"])


class RoomRestoreAndLimitsTest(KinoBase):
    def test_room_is_restored_after_restart_from_link_hint(self):
        rid = "a" * 24
        self.assertEqual(movie_watch.join_room(rid, make_init_data(7))[1], movie_watch.ERR_ROOM)   # hint yo'q
        data, err = movie_watch.join_room(rid, make_init_data(7), "m1")
        self.assertIsNone(err)
        self.assertEqual(data["movie"]["id"], "m1")
        # ikkinchi odam ham shu ID bilan qo'shiladi (yangi xona yaratilmaydi)
        data2, err2 = movie_watch.join_room(rid, make_init_data(8), "m1")
        self.assertIsNone(err2)
        self.assertEqual(sorted(data2["participants"]), [7, 8])
        self.assertEqual(len(movie_watch.ROOMS), 1)

    def test_restore_rejects_bad_ids_and_unknown_movie(self):
        self.assertEqual(movie_watch.join_room("short", make_init_data(7), "m1")[1], movie_watch.ERR_ROOM)
        self.assertEqual(movie_watch.join_room("b" * 24, make_init_data(7), "nope")[1], movie_watch.ERR_ROOM)
        self.assertEqual(movie_watch.join_room("B" * 24, make_init_data(7), "m1")[1], movie_watch.ERR_ROOM)

    def test_non_member_of_existing_room_gets_member_code(self):
        rid = movie_watch.create_room("m1", 7)
        movie_watch.join_room(rid, make_init_data(8))
        h = FakeHandler(f"/api/kino/state?room={rid}", headers={"X-Telegram-Init-Data": make_init_data(9)})
        movie_watch.handle_api(h)
        self.assertEqual((h.status, h.json()["code"]), (403, "member"))

    def test_room_cap_and_unused_rooms_expire(self):
        with mock.patch.object(config, "KINO_MAX_ROOMS", 3):
            rids = [movie_watch.create_room("m1", 100 + i) for i in range(3)]
            self.assertTrue(all(rids))
            self.assertIsNone(movie_watch.create_room("m1", 999))
            # hech kim kirmagan xonalar tez o'chadi va joy bo'shaydi
            for rid in rids:
                movie_watch.ROOMS[rid]["created_at"] -= config.KINO_UNUSED_ROOM_TTL_SEC + 5
            self.assertIsNotNone(movie_watch.create_room("m1", 999))

    def test_used_room_survives_unused_ttl(self):
        rid = movie_watch.create_room("m1", 7)
        movie_watch.join_room(rid, make_init_data(7))
        movie_watch.ROOMS[rid]["created_at"] -= config.KINO_UNUSED_ROOM_TTL_SEC + 5
        self.assertIsNotNone(movie_watch._get_room(rid))

    def test_streaming_marks_room_used(self):
        # mobil ilova join_room'siz stream qiladi: xona 30 daqiqada o'chib ketmasligi kerak
        rid = movie_watch.create_room("m1", 7)
        token = movie_watch._make_stream_token(rid, "m1", 7)
        h = FakeHandler(f"/x?token={token}")
        uid, movie = movie_watch._authorize_stream(h, rid, "m1")
        self.assertEqual(uid, 7)
        movie_watch.ROOMS[rid]["created_at"] -= config.KINO_UNUSED_ROOM_TTL_SEC + 5
        self.assertIsNotNone(movie_watch._get_room(rid))

    def test_room_url_carries_movie_id(self):
        url = movie_watch.room_url("m1", "c" * 24)
        self.assertIn("startapp=room_" + "c" * 24 + "_m1", url)


class UploadFormatTest(unittest.TestCase):
    def setUp(self):
        try:
            from tests._stub_telegram import install_stubs
            install_stubs()
            from handlers import kino
        except Exception as exc:                 # og'ir bog'liqliklar yo'q muhit
            self.skipTest(f"handlers.kino import bo'lmadi: {exc}")
        self.kino = kino

    def _media(self, name="", mime=""):
        return mock.Mock(file_name=name, mime_type=mime)

    def test_classification(self):
        c = self.kino._classify_video
        self.assertEqual(c(self._media("film.mp4", "video/mp4")), "ok")
        self.assertEqual(c(self._media("", "video/mp4")), "ok")
        self.assertEqual(c(self._media("", "")), "ok")                        # Telegram siqqan video
        self.assertEqual(c(self._media("a.MOV", "video/quicktime")), "risky")
        self.assertEqual(c(self._media("a.webm", "video/webm")), "risky")
        self.assertEqual(c(self._media("a.mkv", "video/x-matroska")), "bad")
        self.assertEqual(c(self._media("a.avi", "video/x-msvideo")), "bad")


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
