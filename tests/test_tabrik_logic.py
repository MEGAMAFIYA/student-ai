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
    _ALLOWED = set("-_✓«»~+ \n🎁.🎉!:0123456789«»qwertyuiopasdfghjklzxcvbnmQWERTYUIOPASDFGHJKLZXCVBNM'\"")

    def _only_allowed_shape_chars(self, frame: str) -> bool:
        # Faqat SPETSIFIKATSIYADA ruxsat etilgan "shakl" belgilari:
        allowed_shape_chars = set("-_✓«»~+")
        # Freym ichidagi shaklni ifodalovchi qatorlarni ajratib olamiz
        # (sarlavha matn qatorlarini emas) — build_circle_frame 2-4-qatorlar shakl.
        shape_lines = frame.split("\n")[2:]
        shape_text = "".join(shape_lines)
        non_shape_chars = set(shape_text) - allowed_shape_chars - {" "}
        return len(non_shape_chars) == 0

    def test_all_frames_use_only_allowed_characters(self):
        for step in range(tabrik_logic.TOTAL_ROTATION_FRAMES):
            frame = tabrik_logic.build_circle_frame(step)
            self.assertTrue(
                self._only_allowed_shape_chars(frame),
                f"step={step} freymda ruxsat etilmagan belgi bor: {frame!r}",
            )

    def test_frames_are_distinct_across_a_rotation(self):
        frames = {tabrik_logic.build_circle_frame(s) for s in range(len(tabrik_logic._POSITIONS))}
        # 8 ta pozitsiyaning har biri boshqacha ko'rinishga ega bo'lishi kerak
        # (aks holda Telegram "message is not modified" xatosi beradi).
        self.assertEqual(len(frames), len(tabrik_logic._POSITIONS))

    def test_rotation_wraps_around(self):
        # step va step+8 (bitta to'liq aylanish) bir xil freymni berishi kerak.
        f0 = tabrik_logic.build_circle_frame(0)
        f8 = tabrik_logic.build_circle_frame(8)
        self.assertEqual(f0, f8)

    def test_countdown_frames_use_only_allowed_characters(self):
        allowed = set("-_✓«»~+ \n0123456789")
        for n in range(1, 6):
            frame = tabrik_logic.build_countdown_frame(n)
            extra = set(frame) - allowed - set("Tabrikoydansng.!ariqolganidTBRK'")
            # Harflar/so'zlar (lotin matni) bo'lishi tabiiy — biz faqat
            # "shakl chizig'i" qatoridagi belgilarni tekshiramiz.
            shape_line = frame.split("\n")[2]
            non_shape = set(shape_line) - set("-_✓«» ")
            self.assertEqual(non_shape, set(), f"n={n}: {shape_line!r}")

    def test_final_card_contains_greeting_text_verbatim(self):
        card = tabrik_logic.build_final_card("Mening tabrigim!")
        self.assertIn("Mening tabrigim!", card)


if __name__ == "__main__":
    unittest.main()
