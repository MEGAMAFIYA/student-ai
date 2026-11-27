"""👥 Guruh: kontakt yig'ish / kontakt qo'shish mantig'i (soxta Telethon client bilan)."""
import asyncio
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._stub_telegram import install_stubs
install_stubs()

from handlers import groups  # noqa: E402


def _mk(name, attrs):
    exc = type(name, (Exception,), {})(name)
    for k, v in attrs.items():
        setattr(exc, k, v)
    return exc


def U(uid, first="Ali", last="", **kw):
    return types.SimpleNamespace(id=uid, first_name=first, last_name=last, username=kw.get("username"),
                                 bot=kw.get("bot", False), deleted=kw.get("deleted", False),
                                 contact=kw.get("contact", False), is_self=kw.get("is_self", False))


class FakeClient:
    def __init__(self, members=(), fail=None):
        self.members, self.fail, self.calls, self.missing = list(members), fail or {}, [], []

    async def iter_participants_gen(self):
        for m in self.members:
            yield m

    def iter_participants(self, entity):
        return self.iter_participants_gen()

    async def __call__(self, request):
        self.calls.append(request)
        if request[0] == "invite_many":
            for u in request[1]:
                if self.fail.get(u.id) is not None:
                    raise self.fail[u.id]
            return types.SimpleNamespace(missing_invitees=[types.SimpleNamespace(user_id=i) for i in self.missing if i in {u.id for u in request[1]}])
        exc = self.fail.get(request[1].id)
        if isinstance(exc, list):
            exc = exc.pop(0) if exc else None
        if exc is not None:
            raise exc
        return True


async def _noop(force=False):
    pass


class GroupsTest(unittest.TestCase):
    def setUp(self):
        groups.COLLECT_DELAY_SEC = groups.ADD_DELAY_SEC = 0
        groups._add_contact_request = lambda u: ("contact", u)
        groups._invite_request = lambda e, u: ("invite", u)
        groups._invite_many_request = lambda e, us: ("invite_many", list(us))
        groups.ADD_BATCH_SIZE = 100

    def run_collect(self, client, my_id=1):
        st = groups._new_state()
        asyncio.run(groups.collect_contacts(client, object(), st, _noop, my_id))
        return st

    def run_add(self, client, contacts, existing=(), my_id=1, channel=False):
        client.members = [U(i) for i in existing]
        st = groups._new_state(len(contacts))
        entity = types.SimpleNamespace(id=5)
        groups._is_channel = lambda e: channel
        asyncio.run(groups.add_contacts_to_group(client, entity, contacts, st, _noop, my_id))
        return st

    # ---- kontakt yig'ish ----
    def test_collect_saves_real_users_and_skips_rest(self):
        c = FakeClient([U(10), U(11, bot=True), U(12, deleted=True), U(13, contact=True), U(1), U(14)])
        st = self.run_collect(c)
        self.assertEqual([r[1].id for r in c.calls], [10, 14])
        self.assertEqual((st["ok"], st["skipped"], st["failed"]), (2, 4, 0))

    def test_contact_name_is_own_name(self):
        self.assertEqual(groups._contact_names(U(1, "Ali", "Valiyev")), ("Ali", "Valiyev"))
        self.assertEqual(groups._contact_names(U(1, "", "", username="ali_v")), ("@ali_v", ""))
        self.assertEqual(groups._contact_names(U(7, "", "")), ("User 7", ""))

    def test_collect_stops_on_peer_flood(self):
        c = FakeClient([U(10), U(11), U(12)], fail={11: _mk("PeerFloodError", {})})
        st = self.run_collect(c)
        self.assertEqual(st["ok"], 1)
        self.assertIn("PeerFlood", st["error"])
        self.assertEqual(len(c.calls), 2)

    def test_collect_stop_flag(self):
        c = FakeClient([U(10), U(11)])
        st = groups._new_state()
        st["stop"] = True
        asyncio.run(groups.collect_contacts(c, object(), st, _noop, 1))
        self.assertEqual(c.calls, [])

    def test_collect_short_floodwait_is_waited_and_retried(self):
        groups.MAX_FLOOD_WAIT_SEC = 120
        slept = []
        real = asyncio.sleep

        async def fake_sleep(sec):
            slept.append(sec)
        groups.asyncio.sleep = fake_sleep
        try:
            c = FakeClient([U(10)], fail={10: [_mk("FloodWaitError", {"seconds": 3})]})
            st = self.run_collect(c)
        finally:
            groups.asyncio.sleep = real
        self.assertEqual(st["ok"], 1)
        self.assertIn(4, slept)

    def test_collect_aborts_after_many_consecutive_failures(self):
        users = [U(100 + i) for i in range(20)]
        c = FakeClient(users, fail={u.id: _mk("RPCError", {}) for u in users})
        st = self.run_collect(c)
        self.assertEqual(st["failed"], groups.MAX_CONSECUTIVE_FAILS)
        self.assertIn("Ketma-ket", st["error"])

    # ---- kontakt qo'shish ----
    def test_add_skips_existing_bots_self(self):
        c = FakeClient()
        st = self.run_add(c, [U(10), U(11), U(12, bot=True), U(1)], existing=[11])
        self.assertEqual([r[1].id for r in c.calls], [10])
        self.assertEqual((st["ok"], st["skipped"]), (1, 3))

    def test_add_counts_privacy_and_already_member(self):
        c = FakeClient(fail={10: _mk("UserPrivacyRestrictedError", {}),
                             11: _mk("UserAlreadyParticipantError", {})})
        st = self.run_add(c, [U(10), U(11), U(12)])
        self.assertEqual((st["ok"], st["skipped"], st["failed"]), (1, 1, 1))
        self.assertEqual(st["reasons"]["maxfiylik sozlamasi"], 1)
        self.assertIsNone(st["error"])

    def test_add_stops_on_peer_flood(self):
        c = FakeClient(fail={11: _mk("PeerFloodError", {})})
        st = self.run_add(c, [U(10), U(11), U(12)])
        self.assertEqual(st["ok"], 1)
        self.assertTrue(st["error"])
        self.assertEqual(len(c.calls), 2)

    # ---- paketli qo'shish (supergruppa) ----
    def test_add_batches_all_in_one_request(self):
        c = FakeClient()
        st = self.run_add(c, [U(i) for i in range(10, 60)], channel=True)
        self.assertEqual(len(c.calls), 1)
        self.assertEqual(len(c.calls[0][1]), 50)
        self.assertEqual(st["ok"], 50)

    def test_add_batch_splits_by_size_and_counts_missing(self):
        groups.ADD_BATCH_SIZE = 20
        c = FakeClient()
        c.missing = [10]
        st = self.run_add(c, [U(i) for i in range(10, 50)], channel=True)
        self.assertEqual(len(c.calls), 2)
        self.assertEqual((st["ok"], st["failed"]), (39, 1))
        self.assertEqual(st["reasons"]["maxfiylik sozlamasi"], 1)

    def test_add_batch_falls_back_to_single_on_batch_error(self):
        c = FakeClient(fail={11: _mk("UserPrivacyRestrictedError", {})})
        st = self.run_add(c, [U(10), U(11), U(12)], channel=True)
        self.assertEqual((st["ok"], st["failed"]), (2, 1))
        self.assertEqual(len(c.calls), 4)  # 1 paket + 3 bittalab

    def test_add_batch_stops_on_peer_flood(self):
        groups.ADD_BATCH_SIZE = 2
        c = FakeClient(fail={12: _mk("PeerFloodError", {})})
        st = self.run_add(c, [U(10), U(11), U(12), U(13), U(14)], channel=True)
        self.assertEqual(st["ok"], 2)
        self.assertTrue(st["error"])
        self.assertEqual(len(c.calls), 2)

    # ---- matnlar ----
    def test_final_text_mentions_counts(self):
        st = groups._new_state()
        st.update(ok=3, skipped=2, failed=1)
        st["reasons"]["maxfiylik sozlamasi"] += 1
        txt = groups._final_text("add", "Guruh <1>", st)
        self.assertIn("Tugadi", txt)
        self.assertIn("Guruh &lt;1&gt;", txt)
        self.assertIn("Qo`shildi: 3", txt)


if __name__ == "__main__":
    unittest.main()
