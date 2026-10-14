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

    def test_session_can_be_revoked(self):
        start = mobile_auth.start_login()
        self.assertTrue(mobile_auth.complete_login(start.login_token, SimpleNamespace(id=7, username=None, first_name="A")))
        token = mobile_auth.poll(start.login_token)["session_token"]
        self.assertTrue(mobile_auth.revoke_session(token))
        self.assertIsNone(mobile_auth.get_user(token))
        self.assertFalse(mobile_auth.revoke_session(token))


if __name__ == "__main__":
    unittest.main()
