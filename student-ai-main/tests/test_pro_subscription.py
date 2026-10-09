"""
💎 pro_subscription.py uchun unit testlar. config.persist_read/write ni
soxtalashtiramiz — Upstash/GitHub/Neon'ga HAQIQIY tarmoq so'rovi yubormaslik
uchun (bu modul faqat sof mantiqni sinaydi, tashqi saqlashni emas)."""

import time
import unittest
from unittest.mock import patch

import config
import pro_subscription as ps


class ProSubscriptionTests(unittest.TestCase):
    def setUp(self):
        # Har bir test o'zining toza xotirasidan boshlasin — real
        # persist_read/write chaqirilmasligi uchun ikkalasini ham
        # soxtalashtiramiz.
        self._patchers = [
            patch.object(config, "persist_read", return_value=(None, "test")),
            patch.object(config, "persist_write", return_value=None),
        ]
        for p in self._patchers:
            p.start()
        ps._data = {"requests": {}, "subscriptions": {}}

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_new_user_is_not_pro(self):
        self.assertFalse(ps.is_pro(111))

    def test_create_request_returns_pending(self):
        req_id = ps.create_request(111)
        req = ps.get_request(req_id)
        self.assertEqual(req["status"], "pending")
        self.assertEqual(req["user_id"], 111)

    def test_approve_makes_user_pro(self):
        req_id = ps.create_request(111)
        approved_user_id = ps.approve_request(req_id)
        self.assertEqual(approved_user_id, 111)
        self.assertTrue(ps.is_pro(111))

    def test_reject_does_not_make_user_pro(self):
        req_id = ps.create_request(111)
        rejected_user_id = ps.reject_request(req_id)
        self.assertEqual(rejected_user_id, 111)
        self.assertFalse(ps.is_pro(111))

    def test_cannot_approve_twice(self):
        req_id = ps.create_request(111)
        ps.approve_request(req_id)
        self.assertIsNone(ps.approve_request(req_id))

    def test_cannot_reject_already_approved(self):
        req_id = ps.create_request(111)
        ps.approve_request(req_id)
        self.assertIsNone(ps.reject_request(req_id))

    def test_second_request_cancels_first_pending(self):
        first = ps.create_request(111)
        second = ps.create_request(111)
        self.assertEqual(ps.get_request(first)["status"], "cancelled")
        self.assertEqual(ps.get_request(second)["status"], "pending")
        # Eski (bekor qilingan) so'rovni tasdiqlab bo'lmasligi kerak.
        self.assertIsNone(ps.approve_request(first))

    def test_pending_requests_lists_only_pending_newest_first(self):
        r1 = ps.create_request(111)
        time.sleep(0.01)
        r2 = ps.create_request(222)
        ps.approve_request(r1)
        pending = ps.get_pending_requests()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["req_id"], r2)

    def test_expired_subscription_is_not_pro(self):
        req_id = ps.create_request(111)
        ps.approve_request(req_id)
        # Muddatni sun'iy ravishda o'tgan qilib qo'yamiz.
        ps._data["subscriptions"]["111"]["expires_ts"] = time.time() - 10
        self.assertFalse(ps.is_pro(111))

    def test_get_subscription_returns_details(self):
        req_id = ps.create_request(111)
        ps.approve_request(req_id)
        sub = ps.get_subscription(111)
        self.assertIsNotNone(sub)
        self.assertEqual(sub["req_id"], req_id)
        self.assertGreater(sub["expires_ts"], time.time())

    def test_unknown_request_id_operations_return_none(self):
        self.assertIsNone(ps.approve_request("doesnotexist"))
        self.assertIsNone(ps.reject_request("doesnotexist"))
        self.assertIsNone(ps.get_request("doesnotexist"))


if __name__ == "__main__":
    unittest.main()
