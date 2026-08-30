import re
import time
import unittest

import tabrik_logic


class ParseTabrikTextTests(unittest.TestCase):
    def test_plain_command(self):
        self.assertEqual(tabrik_logic.parse_tabrik_text("/tabrik Salom hammaga"), "Salom hammaga")

    def test_command_with_bot_username(self):
        self.assertEqual(
            tabrik_logic.parse_tabrik_text("/tabrik@Student_ai_uz_bot Salom hammaga"),
            "Salom hammaga",
        )

    def test_command_case_insensitive(self):
        self.assertEqual(tabrik_logic.parse_tabrik_text("/TABRIK Salom"), "Salom")

    def test_empty_after_command_returns_none(self):
        self.assertIsNone(tabrik_logic.parse_tabrik_text("/tabrik"))
        self.assertIsNone(tabrik_logic.parse_tabrik_text("/tabrik   "))
        self.assertIsNone(tabrik_logic.parse_tabrik_text("/tabrik@Student_ai_uz_bot"))

    def test_none_input(self):
        self.assertIsNone(tabrik_logic.parse_tabrik_text(None))
        self.assertIsNone(tabrik_logic.parse_tabrik_text(""))

    def test_multiline_text_preserved(self):
        raw = "/tabrik Salom\nQadrli insonim\nHurmat bilan"
        self.assertEqual(tabrik_logic.parse_tabrik_text(raw), "Salom\nQadrli insonim\nHurmat bilan")


class GreetingStoreTests(unittest.TestCase):
    def setUp(self):
        tabrik_logic._STORE.clear()

    def test_store_and_retrieve(self):
        sid = tabrik_logic.store_greeting("Salom dunyo")
        self.assertEqual(len(sid), 10)
        self.assertEqual(tabrik_logic.get_greeting(sid), "Salom dunyo")

    def test_unknown_id_returns_none(self):
        self.assertIsNone(tabrik_logic.get_greeting("doesnotexist"))

    def test_two_greetings_get_different_ids(self):
        sid1 = tabrik_logic.store_greeting("Birinchi")
        sid2 = tabrik_logic.store_greeting("Ikkinchi")
        self.assertNotEqual(sid1, sid2)
        self.assertEqual(tabrik_logic.get_greeting(sid1), "Birinchi")
        self.assertEqual(tabrik_logic.get_greeting(sid2), "Ikkinchi")

    def test_expired_entry_is_purged(self):
        sid = tabrik_logic.store_greeting("Eskiradigan")
        # Muddatni sun'iy ravishda o'tkazib yuboramiz.
        tabrik_logic._STORE[sid]["created_at"] = time.time() - tabrik_logic.ENTRY_TTL_SECONDS - 10
        self.assertIsNone(tabrik_logic.get_greeting(sid))

    def test_max_entries_evicts_oldest(self):
        tabrik_logic.MAX_ENTRIES, old_max = 5, tabrik_logic.MAX_ENTRIES
        try:
            ids = [tabrik_logic.store_greeting(f"matn {i}") for i in range(10)]
            self.assertLessEqual(len(tabrik_logic._STORE), 5)
            # Eng eski yozuvlar o'chirilgan, eng yangilari qolgan bo'lishi kerak.
            self.assertIsNone(tabrik_logic.get_greeting(ids[0]))
            self.assertIsNotNone(tabrik_logic.get_greeting(ids[-1]))
        finally:
            tabrik_logic.MAX_ENTRIES = old_max


class CircleAnimationTests(unittest.TestCase):
    def _art_block(self, frame: str) -> str:
        """Freym matnidan ```...``` kod bloki ichidagi "shakl" qismini
        ajratib oladi (sarlavha va Markdown belgilari hisobga olinmaydi)."""
        m = re.search(r"```\n(.*?)\n```", frame, re.DOTALL)
        self.assertIsNotNone(m, f"kod bloki topilmadi: {frame!r}")
        return m.group(1)

    def test_circle_frames_use_only_palette_characters(self):
        allowed = set(tabrik_logic.DECOR_CHARS) | {" ", "\n"}
        for step in range(tabrik_logic.TOTAL_ROTATION_FRAMES):
            art = self._art_block(tabrik_logic.build_circle_frame(step))
            extra = set(art) - allowed
            self.assertEqual(extra, set(), f"step={step}: ruxsat etilmagan belgi: {extra}")

    def test_circle_frames_are_distinct_across_a_rotation(self):
        frames = {tabrik_logic.build_circle_frame(s) for s in range(tabrik_logic._RING_POSITIONS)}
        self.assertEqual(len(frames), tabrik_logic._RING_POSITIONS)

    def test_circle_frames_vary_across_full_animation(self):
        # Bezak chizig'i offset sifatida to'liq `step`ni ishlatgani uchun
        # (faqat halqa pozitsiyasi emas), 16 freym HAMMASI bir-biridan farq
        # qilishi kerak — bu qat'iy 8-davrli qaytish emas, balki Telegram
        # "message is not modified" xatosiga tushib qolmaslik uchun ataylab
        # shunday: har bir freym oldingisidan farqli.
        frames = [tabrik_logic.build_circle_frame(s) for s in range(tabrik_logic.TOTAL_ROTATION_FRAMES)]
        self.assertEqual(len(set(frames)), tabrik_logic.TOTAL_ROTATION_FRAMES)

    def test_countdown_frames_use_only_palette_characters(self):
        allowed = set(tabrik_logic.DECOR_CHARS) | {" ", "\n"}
        for n in range(1, 6):
            art = self._art_block(tabrik_logic.build_countdown_frame(n))
            extra = set(art) - allowed
            self.assertEqual(extra, set(), f"n={n}: ruxsat etilmagan belgi: {extra}")

    def test_countdown_frames_are_distinct_per_digit(self):
        frames = {tabrik_logic.build_countdown_frame(n) for n in range(1, 6)}
        self.assertEqual(len(frames), 5)

    def test_final_card_contains_greeting_text_verbatim(self):
        card = tabrik_logic.build_final_card("Mening tabrigim!")
        self.assertIn("Mening tabrigim!", card)

    def test_ready_card_does_not_contain_greeting(self):
        # /tabrik yozilgan zahoti tabrik matni ko'rsatilmasligi kerak —
        # faqat tugma bosilgach ochiladi.
        ready = tabrik_logic.build_ready_card()
        self.assertNotIn("Mening tabrigim", ready)


class TouchGreetingTests(unittest.TestCase):
    def setUp(self):
        tabrik_logic._STORE.clear()

    def test_touch_refreshes_created_at(self):
        sid = tabrik_logic.store_greeting("Salom")
        tabrik_logic._STORE[sid]["created_at"] = time.time() - 1000
        tabrik_logic.touch_greeting(sid)
        self.assertGreater(tabrik_logic._STORE[sid]["created_at"], time.time() - 5)

    def test_touch_unknown_id_does_not_raise(self):
        tabrik_logic.touch_greeting("doesnotexist")  # xato ko'tarmasligi kerak


if __name__ == "__main__":
    unittest.main()
