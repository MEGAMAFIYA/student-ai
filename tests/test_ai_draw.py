"""AI rasm chizdirish: narxlar, band qilish/yechish, universal chat aniqlash, kurs ishi surat joylash."""
import asyncio
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._stub_telegram import install_stubs
install_stubs()

import wallet  # noqa: E402
import ai_draw  # noqa: E402
import course_images as ci  # noqa: E402
from PIL import Image  # noqa: E402


def _load_ai_image():
    # telegram paketi testlarda yo'q — faqat regex mantiqini tekshirish uchun soxta modul.
    saved = {k: sys.modules.get(k) for k in ("telegram", "telegram.ext", "telegram.constants")}
    sys.modules["telegram"] = MagicMock()
    sys.modules["telegram.ext"] = MagicMock()
    sys.modules["telegram.constants"] = MagicMock()
    try:
        import importlib
        return importlib.import_module("handlers.ai_image")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class PricesAndHold(unittest.TestCase):
    def setUp(self):
        self.uid = 987001
        wallet.credit_balance(self.uid, 20000, "test")

    def test_default_prices(self):
        self.assertEqual(ai_draw.image_price(), 3000)
        for n in ai_draw.COURSE_PAGE_OPTIONS:
            self.assertIsNotNone(wallet.get_feature(ai_draw.course_feature_id(n)))
        self.assertIsNone(wallet.get_feature("course_work"))

    def test_total_three_images_is_9000_more(self):
        base = ai_draw.course_pages_price(20)
        self.assertEqual(ai_draw.course_total(20, 3), base + 9000)

    def test_admin_can_change_image_price(self):
        old = ai_draw.image_price()
        try:
            wallet.set_feature_price("ai_image", 5000)
            self.assertEqual(ai_draw.image_price(), 5000)
        finally:
            wallet.set_feature_price("ai_image", old)

    def test_reserve_commit_debits(self):
        before = wallet.get_balance(self.uid)
        hold = ai_draw.reserve(self.uid, "ai_image", 3000)
        self.assertEqual(hold.status, "reserved")
        self.assertEqual(wallet.get_balance(self.uid), before)  # hali yechilmagan
        ai_draw.commit(hold)
        self.assertEqual(wallet.get_balance(self.uid), before - 3000)

    def test_release_does_not_debit(self):
        before = wallet.get_balance(self.uid)
        hold = ai_draw.reserve(self.uid, "ai_image", 9000)
        ai_draw.release(hold)
        self.assertEqual(wallet.get_balance(self.uid), before)

    def test_insufficient(self):
        hold = ai_draw.reserve(self.uid, "ai_image", 10_000_000)
        self.assertEqual(hold.status, "insufficient")
        self.assertIn("yetarli emas", ai_draw.insufficient_text(hold))

    def test_disabled(self):
        wallet.set_feature_enabled("ai_image", False)
        try:
            self.assertEqual(ai_draw.reserve(self.uid, "ai_image", 3000).status, "disabled")
        finally:
            wallet.set_feature_enabled("ai_image", True)


class UniversalDetection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load_ai_image()

    def test_positive(self):
        for t in ["mushuk rasmini chizib ber", "rasm chiz: quyosh botishi", "menga bir surat yarat tog'lar",
                  "rasm quyosh botayotgan dengiz", "draw me a picture"[:0] + "kuchukcha suratini chizing"]:
            self.assertTrue(self.m.is_image_request(t), t)

    def test_negative(self):
        for t in ["salom qalaysan", "O'zbekiston poytaxti qayer", "fotosintez nima"]:
            self.assertFalse(self.m.is_image_request(t), t)

    def test_clean_prefix(self):
        self.assertEqual(self.m.clean_description("rasm: qizil olma"), "qizil olma")


class UploadPlacement(unittest.TestCase):
    def test_places_uploads_by_ai_and_fallback(self):
        wd = tempfile.mkdtemp()
        try:
            p = os.path.join(wd, "a.png")
            Image.new("RGB", (40, 30), "blue").save(p)
            words = " ".join(["so'z"] * 60)
            sections = {"bobs": [
                {"subsections": [{"heading": "1.1. Birinchi", "content": words}, {"heading": "1.2. Ikkinchi", "content": words}]},
                {"subsections": [{"heading": "2.1. Uchinchi", "content": words}]},
            ]}

            async def ask_ok(prompt, system, tag):
                return '{"placements": [{"image": 1, "section": "1.2", "caption": "Test izohi"}]}'

            async def ask_bad(prompt, system, tag):
                return None

            async def run(ask):
                with patch.object(ci, "describe_upload", return_value=asyncio.sleep(0, "tavsif")):
                    return await ci.place_uploads("Mavzu", sections, [{"path": p, "hint": "izoh"}], ask, wd)

            figs = asyncio.run(run(ask_ok))
            self.assertEqual((figs[0]["bob"], figs[0]["sub"], figs[0]["caption"]), (0, 1, "Test izohi"))
            figs = asyncio.run(run(ask_bad))  # AI ishlamasa ham joylanadi
            self.assertEqual(len(figs), 1)
            self.assertTrue(os.path.exists(figs[0]["path"]))
        finally:
            shutil.rmtree(wd, ignore_errors=True)

    def test_ai_only_plan_rejects_diagrams(self):
        index = {"1.1": (0, 0), "1.2": (0, 1)}
        raw = {"figures": [
            {"section": "1.1", "kind": "flowchart", "caption": "Sxema", "nodes": ["a", "b", "c"], "edges": [[0, 1], [1, 2]]},
            {"section": "1.2", "kind": "illustration", "caption": "Rasm", "prompt": "a detailed scene of a modern classroom with students"},
        ]}
        specs = ci.validate_plan(raw, index, {}, 3, True, ai_only=True)
        self.assertEqual([s.kind for s in specs], ["illustration"])


if __name__ == "__main__":
    unittest.main()
