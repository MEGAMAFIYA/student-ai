import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._stub_telegram import install_stubs
install_stubs()

import config  # noqa: E402


class _FakePersistBackend:
    """config.persist_read/persist_write'ni haqiqiy Upstash/GitHub/fayl
    tizimiga tegmasdan, xotiradagi dict bilan almashtiradi — shu bilan
    birga business_storage.py'ning "restart-safe" bo'lishini, ya'ni
    modul qayta import qilinganda ham saqlangan ma'lumotni topa olishini
    haqiqatan tekshirish imkonini beradi (qarang: test_restart_safe_reload)."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def read(self, local_filename, upstash_key):
        return self.store.get(upstash_key), "fake-backend"

    def write(self, local_filename, upstash_key, raw, commit_message=""):
        self.store[upstash_key] = raw


class _FakeBusinessConnection:
    def __init__(self, conn_id, user_id, is_enabled=True, can_reply=True, can_delete=True):
        self.id = conn_id
        self.user = types.SimpleNamespace(id=user_id)
        self.user_chat_id = user_id
        self.is_enabled = is_enabled
        if can_reply is None and can_delete is None:
            self.rights = None
        else:
            self.rights = types.SimpleNamespace(
                can_reply=can_reply,
                can_delete_sent_messages=can_delete,
                can_read_messages=True,
                can_delete_all_messages=False,
            )


class BusinessStorageTests(unittest.TestCase):
    def setUp(self):
        self.backend = _FakePersistBackend()
        self._orig_read = config.persist_read
        self._orig_write = config.persist_write
        config.persist_read = self.backend.read
        config.persist_write = self.backend.write

        import business_storage
        import importlib
        importlib.reload(business_storage)
        self.business_storage = business_storage

    def tearDown(self):
        config.persist_read = self._orig_read
        config.persist_write = self._orig_write

    # 1) BusinessConnection storage
    def test_save_and_get_connection(self):
        conn = _FakeBusinessConnection("conn_abc", 111)
        self.business_storage.save_connection(conn)
        entry = self.business_storage.get_connection_for_user(111)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["connection_id"], "conn_abc")
        self.assertTrue(entry["is_enabled"])
        self.assertEqual(entry["rights"]["can_reply"], True)

    def test_get_user_id_for_connection(self):
        conn = _FakeBusinessConnection("conn_xyz", 222)
        self.business_storage.save_connection(conn)
        self.assertEqual(self.business_storage.get_user_id_for_connection("conn_xyz"), 222)

    def test_unknown_user_returns_none(self):
        self.assertIsNone(self.business_storage.get_connection_for_user(999999))

    # 2) connection disabled
    def test_connection_disabled(self):
        conn = _FakeBusinessConnection("conn_d", 333, is_enabled=False)
        self.business_storage.save_connection(conn)
        entry = self.business_storage.get_connection_for_user(333)
        usable, reason = self.business_storage.is_connection_usable(entry)
        self.assertFalse(usable)
        self.assertEqual(reason, "DISABLED")

    # 3) rights check
    def test_can_reply_false_blocks(self):
        conn = _FakeBusinessConnection("conn_r", 444, can_reply=False)
        self.business_storage.save_connection(conn)
        entry = self.business_storage.get_connection_for_user(444)
        usable, reason = self.business_storage.is_connection_usable(entry)
        self.assertFalse(usable)
        self.assertEqual(reason, "CAN_REPLY_FALSE")

    def test_missing_connection_reason(self):
        usable, reason = self.business_storage.is_connection_usable(None)
        self.assertFalse(usable)
        self.assertEqual(reason, "NO_CONNECTION")

    def test_rights_none_does_not_block_can_reply(self):
        # Rights obyekti umuman kelmagan holat (ba'zi eski Bot API javoblari) —
        # aniq False bo'lmagani uchun rad ETILMASLIGI kerak (business_storage.py
        # docstring'idagi "ehtiyotkorlik" qoidasi).
        conn = _FakeBusinessConnection("conn_none", 555, can_reply=None, can_delete=None)
        self.business_storage.save_connection(conn)
        entry = self.business_storage.get_connection_for_user(555)
        usable, reason = self.business_storage.is_connection_usable(entry)
        self.assertTrue(usable)

    def test_can_delete_sent_messages_false(self):
        conn = _FakeBusinessConnection("conn_del", 666, can_delete=False)
        self.business_storage.save_connection(conn)
        entry = self.business_storage.get_connection_for_user(666)
        self.assertFalse(self.business_storage.can_delete_sent_messages(entry))

    # 4) restart-safe: modul qayta yuklanganda (Render restart simulyatsiyasi)
    def test_restart_safe_reload(self):
        conn = _FakeBusinessConnection("conn_persist", 777)
        self.business_storage.save_connection(conn)

        import importlib
        reloaded = importlib.reload(self.business_storage)
        entry = reloaded.get_connection_for_user(777)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["connection_id"], "conn_persist")

    def test_reconnection_overwrites_old_entry(self):
        # Foydalanuvchi botni qayta ulasa (eski connection_id o'rniga yangisi).
        conn1 = _FakeBusinessConnection("conn_old", 888)
        self.business_storage.save_connection(conn1)
        conn2 = _FakeBusinessConnection("conn_new", 888)
        self.business_storage.save_connection(conn2)
        entry = self.business_storage.get_connection_for_user(888)
        self.assertEqual(entry["connection_id"], "conn_new")


if __name__ == "__main__":
    unittest.main()
