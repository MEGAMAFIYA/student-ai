"""🧮 Hisobchi testlari: hook'lar tez, xavfsiz, birlashtirish, hajm chegarasi, AI iz (trace)."""
import asyncio
import sys
import time
import types
import unittest

if "config" not in sys.modules:
    try:
        import config  # noqa: F401
    except Exception:  # httpx yo'q muhitda soxta config
        stub = types.ModuleType("config")
        stub.USE_GITHUB = stub.USE_UPSTASH = stub.USE_NEON = False
        stub.PROVIDER_LABELS = {"gemini": "Gemini", "groq": "Groq"}
        stub.ADMIN_IDS = {1}
        stub.persist_read = lambda f, k: (None, "")
        stub.persist_write = lambda f, k, raw, commit_message="": None
        sys.modules["config"] = stub

import hisobchi


class HisobchiTests(unittest.TestCase):
    def setUp(self):
        hisobchi.reset_for_tests()

    def test_hooks_are_fast_and_never_raise(self):
        t0 = time.perf_counter()
        for i in range(5000):
            hisobchi.begin(i % 50)
            hisobchi.ai_call("gemini", 1, "m", True)
            hisobchi.on_usage("essay", i % 50)
        self.assertLess(time.perf_counter() - t0, 1.5)
        hisobchi.on_tx({"bad": 1})                 # noto'g'ri ma'lumot ham xato bermaydi
        hisobchi.ai_call(None, None, None, None)
        s = hisobchi.summary()
        self.assertEqual(s["today"]["jobs"], 5000)

    def test_job_merges_usage_and_paid_with_ai_and_topic(self):
        async def run():
            hisobchi.begin(7)
            hisobchi.ai_call("gemini", 2, "g-flash", True)
            hisobchi.ai_call("gemini", 2, "g-flash", True)
            hisobchi.ai_call("gemini", 1, "g-flash", False, "quota")
            hisobchi.ai_call("groq", 1, "gpt", True)
            hisobchi.set_detail(topic="Raqamli iqtisodiyot", pages=20)
            hisobchi.on_tx({"type": "feature_charge", "amount": -12000, "user_id": 7})
            hisobchi.on_paid_done(7, "course_work_20", 12000)
            hisobchi.on_usage("course_work", 7)     # ikkinchi hook — birlashishi kerak
        asyncio.run(run())
        evs, total = hisobchi.recent_events()
        self.assertEqual(total, 1)
        e = evs[0]
        self.assertEqual((e["f"], e["u"], e["topic"], e["pg"], e["price"]), ("course_work", 7, "Raqamli iqtisodiyot", 20, 12000))
        self.assertEqual(e["ai"], {"gemini#2": 2, "groq#1": 1})
        self.assertEqual((e["aif"], e["lim"]), (1, 1))
        s = hisobchi.summary()
        self.assertEqual(s["today"]["spent"], 12000)
        self.assertEqual(s["today"]["quota"], 1)
        self.assertEqual(hisobchi.fn_table()[0][1]["n"], 1)

    def test_trace_shared_with_child_tasks(self):
        async def run():
            hisobchi.begin(9)
            async def child():
                hisobchi.ai_call("groq", 3, "x", True)
            await asyncio.create_task(child())
            hisobchi.on_paid_done(9, "essay", 5000)
        asyncio.run(run())
        self.assertEqual(hisobchi.recent_events()[0][0]["ai"], {"groq#3": 1})

    def test_refund_adjusts_net_and_attaches(self):
        hisobchi.begin(5)
        hisobchi.on_tx({"type": "refund", "amount": 3000, "user_id": 5})   # event'dan oldin
        hisobchi.on_paid_done(5, "course_work_20", 10000)
        self.assertEqual(hisobchi.recent_events()[0][0]["rf"], 3000)
        hisobchi.on_tx({"type": "feature_charge", "amount": -10000, "user_id": 5})
        self.assertEqual(hisobchi.user_stats(5)["spent"], 7000)

    def test_failure_event_and_caps(self):
        hisobchi.on_job_fail(3, "essay", "AI javob bermadi")
        e = hisobchi.recent_events()[0][0]
        self.assertFalse(e["ok"])
        self.assertEqual(hisobchi.summary()["today"]["fail"], 1)
        for i in range(hisobchi.MAX_EVENTS + 200):
            hisobchi.on_usage("quiz", 1000 + i)
        self.assertLessEqual(len(hisobchi._state["events"]), hisobchi.MAX_EVENTS + 50)
        self.assertLessEqual(len(hisobchi._state["users"]), hisobchi.MAX_USERS + 100)

    def test_disabled_is_noop(self):
        old = hisobchi.ENABLED
        hisobchi.ENABLED = False
        try:
            hisobchi.on_usage("essay", 1)
            self.assertEqual(hisobchi.recent_events()[1], 0)
        finally:
            hisobchi.ENABLED = old

    def test_flush_writes_once_when_dirty(self):
        writes = []
        orig = hisobchi.config.persist_write
        hisobchi.config.persist_write = lambda f, k, raw, commit_message="": writes.append(raw)
        try:
            hisobchi.on_usage("essay", 1)
            self.assertTrue(hisobchi.flush())
            self.assertFalse(hisobchi.flush())       # o'zgarish yo'q — yozilmaydi
            self.assertEqual(len(writes), 1)
            self.assertNotIn("api_key", writes[0])
        finally:
            hisobchi.config.persist_write = orig


if __name__ == "__main__":
    unittest.main()
