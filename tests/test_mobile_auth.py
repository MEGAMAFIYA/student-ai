import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import mobile_auth


class MobileAuthPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "sessions.sqlite3")
        self.path_patch = patch.object(mobile_auth, "_db_path", return_value=self.db_path)
        self.path_patch.start()
        mobile_auth._init_db()

    def tearDown(self):
        self.path_patch.stop()
        self.tempdir.cleanup()

    def test_login_poll_creates_persistent_session(self):
        start = mobile_auth.start_login()
        user = SimpleNamespace(id=12345, username="tester", first_name="Test")
        self.assertEqual(mobile_auth.poll(start.login_token)["status"], "pending")
        self.assertTrue(mobile_auth.complete_login(start.login_token, user))
        result = mobile_auth.poll(start.login_token)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(mobile_auth.get_user(result["session_token"])["id"], 12345)
        # Reopening through a fresh DB connection still sees the session.
        self.assertEqual(mobile_auth.get_user(result["session_token"])["username"], "tester")
        self.assertEqual(mobile_auth.poll(start.login_token)["status"], "expired")


    def test_code_login_creates_same_user_session_and_code_is_one_time(self):
        code = mobile_auth.create_code_challenge(12345)
        result = mobile_auth.verify_code(12345, code)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(mobile_auth.get_user(result["session_token"])["id"], 12345)
        self.assertIsNone(mobile_auth.verify_code(12345, code))

    def test_wrong_code_is_rejected(self):
        mobile_auth.create_code_challenge(555)
        self.assertIsNone(mobile_auth.verify_code(555, "000000"))

    def test_session_can_be_revoked(self):
        start = mobile_auth.start_login()
        self.assertTrue(mobile_auth.complete_login(start.login_token, SimpleNamespace(id=7, username=None, first_name="A")))
        token = mobile_auth.poll(start.login_token)["session_token"]
        self.assertTrue(mobile_auth.revoke_session(token))
        self.assertIsNone(mobile_auth.get_user(token))
        self.assertFalse(mobile_auth.revoke_session(token))

    def test_code_cooldown_blocks_rapid_resend_and_discard_resets(self):
        mobile_auth.create_code_challenge(900)
        with self.assertRaises(mobile_auth.CodeCooldown) as ctx:
            mobile_auth.create_code_challenge(900)
        self.assertGreater(ctx.exception.retry_after, 0)
        mobile_auth.discard_code_challenge(900)
        self.assertEqual(len(mobile_auth.create_code_challenge(900)), 6)

    def test_code_locked_after_max_attempts(self):
        code = mobile_auth.create_code_challenge(901)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.assertIsNone(mobile_auth.verify_code(901, wrong))
        self.assertIsNone(mobile_auth.verify_code(901, code))

    def test_verify_code_keeps_profile(self):
        code = mobile_auth.create_code_challenge(902)
        res = mobile_auth.verify_code(902, code, profile={"username": "u", "first_name": "Ali"})
        self.assertEqual(res["user"]["first_name"], "Ali")

    def test_unwritable_db_path_falls_back(self):
        with patch.dict(os.environ, {"MOBILE_AUTH_DB_PATH": "/proc/nope/x.sqlite3"}):
            self.path_patch.stop()
            try:
                path = mobile_auth._db_path()
                self.assertNotEqual(path, "/proc/nope/x.sqlite3")
            finally:
                self.path_patch.start()


if __name__ == "__main__":
    unittest.main()
