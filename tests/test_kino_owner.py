"""Kino xonasi: egalik, vaqtinchalik egalik, onlayn holat va video sifati."""
import time
import unittest
from unittest import mock

import movie_watch
from tests.test_kino_watch import KinoBase, FakeHandler, FakeStorage, MOVIES, make_init_data

ALI, HASAN, VALI = 7, 8, 9


class OwnerBase(KinoBase):
    def setUp(self):
        super().setUp()
        # sifat variantli kino
        self._movies_backup = {k: dict(v) for k, v in MOVIES.items()}
        MOVIES["m1"]["variants"] = {"480": {"telegram_chat_id": -100, "telegram_message_id": 5, "size": 400}}
        self.rid = movie_watch.create_room("m1", ALI)
        movie_watch.join_room(self.rid, make_init_data(HASAN))

    def tearDown(self):
        MOVIES.clear()
        MOVIES.update(self._movies_backup)
        super().tearDown()


class OwnershipTest(OwnerBase):
    def test_creator_is_owner_and_can_control(self):
        st, err = movie_watch.room_state(self.rid, make_init_data(ALI), True, 10.0)
        self.assertIsNone(err)
        self.assertEqual(st["owner_id"], ALI)
        self.assertTrue(st["can_control"])

    def test_guest_cannot_pause_seek_or_change_movie(self):
        for args in ((True, None), (False, None), (None, 55.0)):
            data, err = movie_watch.room_state(self.rid, make_init_data(HASAN), *args)
            self.assertEqual(err, movie_watch.ERR_NOT_OWNER)
        self.assertEqual(movie_watch.change_movie(self.rid, make_init_data(HASAN), "m2")[1], movie_watch.ERR_NOT_OWNER)
        # guest hali ham holatni o'qiy oladi
        st, err = movie_watch.room_state(self.rid, make_init_data(HASAN))
        self.assertIsNone(err)
        self.assertFalse(st["can_control"])
        self.assertEqual(st["version"], 0)

    def test_owner_error_http_code(self):
        h = FakeHandler("/api/kino/state", {"room": self.rid, "playing": True, "init_data": make_init_data(HASAN)})
        movie_watch.handle_post(h)
        self.assertEqual(h.status, 403)
        self.assertEqual(h.json()["code"], "owner")

    def test_request_approve_gives_temporary_control(self):
        d, err = movie_watch.request_owner(self.rid, make_init_data(HASAN))
        self.assertIsNone(err)
        self.assertEqual(d["owner_request"]["user_id"], HASAN)
        # ega ham so'rovni ko'radi
        st, _ = movie_watch.room_state(self.rid, make_init_data(ALI))
        self.assertEqual(st["owner_request"]["user_id"], HASAN)
        d, err = movie_watch.respond_owner(self.rid, make_init_data(ALI), True)
        self.assertIsNone(err)
        self.assertIsNone(d["owner_request"])
        st, err = movie_watch.room_state(self.rid, make_init_data(HASAN), True, 5.0)
        self.assertIsNone(err)
        self.assertTrue(st["can_control"])
        self.assertEqual(st["temp_owner"]["user_id"], HASAN)
        self.assertEqual(st["request_result"]["status"], "approved")

    def test_temporary_control_expires_after_one_minute(self):
        movie_watch.request_owner(self.rid, make_init_data(HASAN))
        movie_watch.respond_owner(self.rid, make_init_data(ALI), True)
        movie_watch.ROOMS[self.rid]["temp_owner"]["until"] = time.time() - 1
        _, err = movie_watch.room_state(self.rid, make_init_data(HASAN), False, None)
        self.assertEqual(err, movie_watch.ERR_NOT_OWNER)
        st, _ = movie_watch.room_state(self.rid, make_init_data(HASAN))
        self.assertIsNone(st["temp_owner"])

    def test_temp_duration_is_60_seconds(self):
        movie_watch.request_owner(self.rid, make_init_data(HASAN))
        before = time.time()
        d, _ = movie_watch.respond_owner(self.rid, make_init_data(ALI), True)
        self.assertAlmostEqual(d["temp_owner"]["until"] - before, 60, delta=2)

    def test_reject_cancels_request(self):
        movie_watch.request_owner(self.rid, make_init_data(HASAN))
        d, err = movie_watch.respond_owner(self.rid, make_init_data(ALI), False)
        self.assertIsNone(err)
        st, _ = movie_watch.room_state(self.rid, make_init_data(HASAN))
        self.assertEqual(st["request_result"]["status"], "rejected")
        self.assertIsNone(st["temp_owner"])
        self.assertEqual(movie_watch.room_state(self.rid, make_init_data(HASAN), True, None)[1], movie_watch.ERR_NOT_OWNER)

    def test_only_real_owner_can_answer(self):
        movie_watch.request_owner(self.rid, make_init_data(HASAN))
        _, err = movie_watch.respond_owner(self.rid, make_init_data(HASAN), True)   # o'ziga tasdiq
        self.assertIsNotNone(err)
        st, _ = movie_watch.room_state(self.rid, make_init_data(HASAN))
        self.assertIsNone(st["temp_owner"])
        self.assertIsNotNone(st["owner_request"])

    def test_temp_owner_cannot_answer_or_change_quality(self):
        movie_watch.request_owner(self.rid, make_init_data(HASAN))
        movie_watch.respond_owner(self.rid, make_init_data(ALI), True)
        self.assertIsNotNone(movie_watch.set_quality(self.rid, make_init_data(HASAN), "480")[1])
        self.assertIsNotNone(movie_watch.respond_owner(self.rid, make_init_data(HASAN), True)[1])

    def test_unanswered_request_expires(self):
        movie_watch.request_owner(self.rid, make_init_data(HASAN))
        movie_watch.ROOMS[self.rid]["request"]["ts"] = time.time() - 999
        st, _ = movie_watch.room_state(self.rid, make_init_data(ALI))
        self.assertIsNone(st["owner_request"])
        self.assertEqual(movie_watch.respond_owner(self.rid, make_init_data(ALI), True)[1], "So'rov topilmadi yoki muddati o'tgan.")
        st, _ = movie_watch.room_state(self.rid, make_init_data(HASAN))
        self.assertEqual(st["request_result"]["status"], "expired")

    def test_respond_requires_bool(self):
        movie_watch.request_owner(self.rid, make_init_data(HASAN))
        self.assertEqual(movie_watch.respond_owner(self.rid, make_init_data(ALI), "yes")[1], movie_watch.ERR_BAD_REQUEST)

    def test_stranger_cannot_request(self):
        self.assertEqual(movie_watch.request_owner(self.rid, make_init_data(VALI))[1], movie_watch.ERR_NOT_MEMBER)

    def test_owner_survives_restore_via_link_hint(self):
        movie_watch.ROOMS.clear()                      # server qayta ishga tushdi
        # mehmon birinchi bo'lib qaytadi, lekin havoladagi ega ID'si ALI
        d, err = movie_watch.join_room(self.rid, make_init_data(HASAN), "m1", str(ALI))
        self.assertIsNone(err)
        self.assertEqual(d["owner_id"], ALI)
        self.assertFalse(d["can_control"])
        d2, err = movie_watch.join_room(self.rid, make_init_data(ALI))
        self.assertIsNone(err)
        self.assertTrue(d2["can_control"])

    def test_share_link_carries_owner(self):
        url = movie_watch.room_url("m1", self.rid)
        self.assertIn(f"_m1_{ALI}", url)
        self.assertLessEqual(len(url.split("startapp=")[1].split("&")[0]), 64)


class PresenceTest(OwnerBase):
    def test_online_list_and_stale_guest_frees_slot(self):
        st, _ = movie_watch.room_state(self.rid, make_init_data(ALI))
        self.assertEqual(sorted(st["online"]), [ALI, HASAN])
        movie_watch.ROOMS[self.rid]["participants"][str(HASAN)]["last_seen"] = time.time() - 999
        st, _ = movie_watch.room_state(self.rid, make_init_data(ALI))
        self.assertEqual(st["online"], [ALI])
        self.assertFalse([p for p in st["people"] if p["id"] == HASAN][0]["online"])
        # to'la xona: uzilgan mehmon o'rniga yangi odam kira oladi, ega esa hech qachon chiqarilmaydi
        d, err = movie_watch.join_room(self.rid, make_init_data(VALI))
        self.assertIsNone(err)
        self.assertEqual(sorted(d["participants"]), [ALI, VALI])

    def test_full_room_with_online_guest_still_rejects(self):
        _, err = movie_watch.join_room(self.rid, make_init_data(VALI))
        self.assertIn("to'la", err)

    def test_owner_never_evicted(self):
        movie_watch.ROOMS[self.rid]["participants"][str(ALI)]["last_seen"] = time.time() - 999
        movie_watch.ROOMS[self.rid]["participants"][str(HASAN)]["last_seen"] = time.time() - 999
        d, err = movie_watch.join_room(self.rid, make_init_data(VALI))
        self.assertIsNone(err)
        self.assertIn(ALI, d["participants"])

    def test_drop_does_not_change_playback_state(self):
        movie_watch.room_state(self.rid, make_init_data(ALI), True, 100.0)
        before, _ = movie_watch.room_state(self.rid, make_init_data(HASAN))
        movie_watch.ROOMS[self.rid]["participants"][str(ALI)]["last_seen"] = time.time() - 999
        after, _ = movie_watch.room_state(self.rid, make_init_data(HASAN))
        self.assertEqual(before["version"], after["version"])
        self.assertTrue(after["playing"])


class QualityTest(OwnerBase):
    def test_owner_sets_quality_for_whole_room(self):
        st, _ = movie_watch.room_state(self.rid, make_init_data(HASAN))
        self.assertEqual(st["qualities"], ["original", "480"])
        self.assertNotIn("&q=", st["stream_path"])
        d, err = movie_watch.set_quality(self.rid, make_init_data(ALI), "480")
        self.assertIsNone(err)
        self.assertEqual(d["quality"], "480")
        st, _ = movie_watch.room_state(self.rid, make_init_data(HASAN))
        self.assertEqual(st["quality"], "480")
        self.assertTrue(st["stream_path"].endswith("&q=480"))

    def test_guest_cannot_set_quality(self):
        self.assertIsNotNone(movie_watch.set_quality(self.rid, make_init_data(HASAN), "480")[1])

    def test_unavailable_or_bad_quality_rejected(self):
        self.assertIn("720p", movie_watch.set_quality(self.rid, make_init_data(ALI), "720")[1])
        self.assertEqual(movie_watch.set_quality(self.rid, make_init_data(ALI), "4k")[1], movie_watch.ERR_BAD_REQUEST)

    def test_back_to_original_and_fallback_when_movie_has_no_variant(self):
        movie_watch.set_quality(self.rid, make_init_data(ALI), "480")
        d, _ = movie_watch.set_quality(self.rid, make_init_data(ALI), "original")
        self.assertEqual(d["quality"], "original")
        movie_watch.set_quality(self.rid, make_init_data(ALI), "480")
        st, _ = movie_watch.change_movie(self.rid, make_init_data(ALI), "m2")   # m2 da variant yo'q
        self.assertEqual(st["quality"], "original")
        self.assertNotIn("&q=", st["stream_path"])

    def test_stream_uses_variant_source_not_original_file_id(self):
        movie = {"id": "m1", "size": 1000, "file_id": "FID", "telegram_chat_id": 1, "telegram_message_id": 2,
                 "variants": {"480": {"telegram_chat_id": -100, "telegram_message_id": 5, "size": 400}}}
        h = FakeHandler("/api/kino/stream/r/m1?token=x&q=480")
        v = movie_watch._stream_variant(h, movie)
        self.assertEqual((v["telegram_message_id"], v["size"], v["file_id"]), (5, 400, ""))
        h2 = FakeHandler("/api/kino/stream/r/m1?token=x")
        self.assertIs(movie_watch._stream_variant(h2, movie), movie)
        h3 = FakeHandler("/api/kino/stream/r/m1?token=x&q=1080")      # mavjud emas -> asl
        self.assertIs(movie_watch._stream_variant(h3, movie), movie)


if __name__ == "__main__":
    unittest.main()
