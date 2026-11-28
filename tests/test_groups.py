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
        self.report_added, self.silent_drop, self.total, self.spam_text = False, set(), None, None
        self.by_entity, self.deleted, self.delete_error, self.messages = {}, [], None, []

    async def iter_participants_gen(self):
        for m in self.members:
            yield m

    def iter_participants(self, entity):
        members = self.by_entity.get(getattr(entity, "id", None), self.members)

        async def gen():
            for m in members:
                yield m
        return gen()

    async def delete_messages(self, entity, ids):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.extend(ids)

    def iter_messages(self, entity, limit=None):
        async def gen():
            for m in self.messages[:limit]:
                yield m
        return gen()

    async def get_participants(self, entity, limit=0):
        return types.SimpleNamespace(total=self.total)

    def conversation(self, name, timeout=20):
        client = self

        class Conv:
            async def __aenter__(self_):
                return self_

            async def __aexit__(self_, *a):
                return False

            async def send_message(self_, text):
                client.calls.append(("spambot", text))

            async def get_response(self_):
                if client.spam_text is None:
                    raise RuntimeError("timeout")
                return types.SimpleNamespace(raw_text=client.spam_text)
        return Conv()

    async def __call__(self, request):
        self.calls.append(request)
        if request[0] == "invite_many":
            for u in request[1]:
                if self.fail.get(u.id) is not None:
                    raise self.fail[u.id]
            ids = {u.id for u in request[1]}
            res = types.SimpleNamespace(missing_invitees=[types.SimpleNamespace(user_id=i) for i in self.missing if i in ids])
            if self.report_added:
                # Telegram haqiqiy qo'shilganlarni service xabarda qaytaradi
                real = [u.id for u in request[1] if u.id not in self.missing and u.id not in self.silent_drop]
                msg = types.SimpleNamespace(id=9000 + (real[0] if real else 0), action=types.SimpleNamespace(users=real))
                res.updates = types.SimpleNamespace(updates=[types.SimpleNamespace(message=msg)] if real else [])
            return res
        exc = self.fail.get(request[1].id)
        if isinstance(exc, list):
            exc = exc.pop(0) if exc else None
        if exc is not None:
            raise exc
        return True


async def _noop(force=False):
    pass


class _FakeQuery:
    message = types.SimpleNamespace(chat_id=1, message_id=2)


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

    # ---- haqiqiy qo'shilganlarni sanash ----
    def test_only_really_added_users_are_counted(self):
        c = FakeClient()
        c.report_added = True
        c.silent_drop = {11}  # Telegram "ok" dedi, lekin 11 ni qo'shmadi
        st = self.run_add(c, [U(10), U(11), U(12)], channel=True)
        self.assertEqual((st["ok"], st["failed"]), (2, 1))
        self.assertEqual(st["reasons"]["Telegram qo'shmadi"], 1)

    def test_added_ids_parsing(self):
        res = types.SimpleNamespace(updates=types.SimpleNamespace(updates=[
            types.SimpleNamespace(message=types.SimpleNamespace(action=types.SimpleNamespace(users=[1, 2]))),
            types.SimpleNamespace(message=None), types.SimpleNamespace()]))
        self.assertEqual(groups._added_ids(res), {1, 2})
        self.assertEqual(groups._added_ids(True), set())

    # ---- sozlamalar ----
    def test_default_batch_is_two_and_settings_change_it(self):
        import storage
        from unittest.mock import patch
        with patch.object(storage, "_save", lambda: None):
            old = dict(storage._data["group_settings"])
            storage._data["group_settings"] = {}
            try:
                groups.ADD_BATCH_SIZE = 2
                self.assertEqual(groups._batch_size(), 2)
                storage.set_group_setting("add_batch", 10)
                self.assertEqual(groups._batch_size(), 10)
                storage.set_group_setting("add_batch", 5000)
                self.assertEqual(groups._batch_size(), 100)
                cbs = [b.callback_data for r in groups.settings_keyboard().inline_keyboard for b in r]
                for n in groups.BATCH_CHOICES:
                    self.assertIn(f"dev:grpbatch:{n}", cbs)
                for n in groups.DELAY_CHOICES:
                    self.assertIn(f"dev:grpdelay:{n}", cbs)
                for a in ("grpset", "grpbatch", "grpdelay"):
                    self.assertIn(a, groups.ACTIONS)
            finally:
                storage._data["group_settings"] = old

    def test_settings_callback_saves_and_rejects_invalid(self):
        import storage
        from unittest.mock import patch
        screens = []

        async def safe_edit(query, text, reply_markup=None, parse_mode=None):
            screens.append(text)
        import handlers
        fake_dev = types.ModuleType("handlers.developer")
        fake_dev._safe_edit_query = safe_edit
        old, old_attr = sys.modules.get("handlers.developer"), getattr(handlers, "developer", None)
        sys.modules["handlers.developer"] = fake_dev
        handlers.developer = fake_dev
        ctx = types.SimpleNamespace(user_data={})
        saved = dict(storage._data["group_settings"])
        try:
            with patch.object(storage, "_save", lambda: None):
                storage._data["group_settings"] = {}
                asyncio.run(groups.handle_dev_callback("grpbatch", ["dev", "grpbatch", "3"], None, ctx, None, "M", "T"))
                self.assertEqual(storage.get_group_setting("add_batch"), 3)
                asyncio.run(groups.handle_dev_callback("grpbatch", ["dev", "grpbatch", "77"], None, ctx, None, "M", "T"))
                self.assertEqual(storage.get_group_setting("add_batch"), 3)
                asyncio.run(groups.handle_dev_callback("grpdelay", ["dev", "grpdelay", "8"], None, ctx, None, "M", "T"))
                self.assertEqual(storage.get_group_setting("add_delay"), 8)
                self.assertIn("Guruh sozlamalari", screens[-1])
        finally:
            storage._data["group_settings"] = saved
            if old is None:
                sys.modules.pop("handlers.developer", None)
            else:
                sys.modules["handlers.developer"] = old
            if old_attr is None:
                handlers.__dict__.pop("developer", None)
            else:
                handlers.developer = old_attr

    # ---- spam holati ----
    def test_needs_spam_check_cases(self):
        n = groups._needs_spam_check
        st = groups._new_state()
        self.assertFalse(n(st))
        st.update(ok=5)
        st.update(members_before=10, members_after=15)
        self.assertFalse(n(st))
        st.update(members_after=10)  # "qo'shildi" lekin son o'zgarmadi
        self.assertTrue(n(st))
        st2 = groups._new_state(); st2["error"] = "Telegram akkauntni spam deb cheklab qo'ydi (PeerFlood)."
        self.assertTrue(n(st2))
        st3 = groups._new_state(); st3.update(ok=0, failed=3)
        self.assertTrue(n(st3))

    def test_spambot_report_text_and_failure(self):
        c = FakeClient()
        c.spam_text = "Afsuski, akkauntingizga cheklov qo'yilgan. <b>1 dekabrgacha</b>"
        txt = asyncio.run(groups._spambot_report(c))
        self.assertIn("cheklov", txt)
        self.assertIn(("spambot", "/start"), c.calls)
        c2 = FakeClient()  # javob kelmasa — None, xato ko'tarilmaydi
        self.assertIsNone(asyncio.run(groups._spambot_report(c2)))

    def test_final_text_shows_members_and_spam_report(self):
        st = groups._new_state()
        st.update(ok=81, members_before=120, members_after=120, unconfirmed=81,
                  spam_report="Cheklov bor <x>")
        txt = groups._final_text("add", "G", st)
        self.assertIn("120 → 120", txt)
        self.assertIn("tasdiqlamadi", txt)
        self.assertIn("@SpamBot", txt)
        self.assertIn("Cheklov bor &lt;x&gt;", txt)

    # ---- 🧹 «qo'shdi» xabarlarini o'chirish ----
    def test_service_messages_deleted_after_batch_add(self):
        c = FakeClient()
        c.report_added = True
        st = self.run_add(c, [U(10), U(11)], channel=True)
        self.assertEqual(c.deleted, [9010])
        self.assertEqual((st["svc_deleted"], st["svc_failed"]), (1, 0))

    def test_service_message_deleted_after_single_add(self):
        c = FakeClient()

        class C2(FakeClient):
            async def __call__(self, request):
                await FakeClient.__call__(self, request)
                msg = types.SimpleNamespace(id=777, action=types.SimpleNamespace(users=[request[1].id]))
                return types.SimpleNamespace(updates=[types.SimpleNamespace(message=msg)])  # Updates.updates ro'yxati
        c = C2()
        st = self.run_add(c, [U(10)], channel=False)
        self.assertEqual(c.deleted, [777])
        self.assertEqual(st["svc_deleted"], 1)

    def test_delete_without_rights_is_reported_once_and_not_retried(self):
        c = FakeClient()
        c.report_added = True
        c.delete_error = _mk("ChatAdminRequiredError", {})
        st = self.run_add(c, [U(10), U(11), U(12), U(13)], channel=True)
        groups.ADD_BATCH_SIZE = 2
        self.assertEqual(st["svc_deleted"], 0)
        self.assertGreaterEqual(st["svc_failed"], 1)
        self.assertEqual(st["svc_error"], "ChatAdminRequiredError")
        self.assertIn("o'chirib bo'lmadi", groups._final_text("add", "G", st))

    def test_sweep_deletes_only_own_add_messages(self):
        class MessageActionChatAddUser:
            users = [1]

        class MessageActionChatJoinedByLink:
            users = None
        c = FakeClient()
        c.messages = [
            types.SimpleNamespace(id=1, action=MessageActionChatAddUser(), sender_id=7),
            types.SimpleNamespace(id=2, action=MessageActionChatAddUser(), sender_id=99),   # boshqaniki
            types.SimpleNamespace(id=3, action=MessageActionChatJoinedByLink(), sender_id=7),
            types.SimpleNamespace(id=4, action=None, sender_id=7),
        ]
        st = groups._new_state()
        st["ok"] = 1
        asyncio.run(groups._sweep_service_messages(c, object(), st, 7))
        self.assertEqual(c.deleted, [1])
        st2 = groups._new_state()
        st2["ok"] = 1
        asyncio.run(groups._sweep_service_messages(c, object(), st2, None))  # my_id noma'lum — tegmaydi
        self.assertEqual(c.deleted, [1])

    # ---- 💾 saqlash va davom ettirish ----
    def test_done_users_are_skipped_on_resume_collect_and_add(self):
        c = FakeClient([U(10), U(11), U(12)])
        st = groups._new_state()
        st["done"] = {10, 11}
        asyncio.run(groups.collect_contacts(c, object(), st, _noop, 1))
        self.assertEqual([r[1].id for r in c.calls], [12])
        self.assertEqual(st["ok"], 1)
        groups._is_channel = lambda e: False
        c2 = FakeClient()
        c2.members = []
        st2 = groups._new_state()
        st2["done"] = {10}
        asyncio.run(groups.add_contacts_to_group(c2, types.SimpleNamespace(id=5), [U(10), U(11)], st2, _noop, 1))
        self.assertEqual([r[1].id for r in c2.calls], [11])

    def test_fatal_error_user_is_not_marked_done(self):
        c = FakeClient([U(10), U(11), U(12)], fail={11: _mk("PeerFloodError", {})})
        st = self.run_collect(c)
        self.assertEqual(st["done"], {10})  # 11 bajarilmagan — davom ettirishda qayta uriniladi

    def test_record_roundtrip_and_storage(self):
        import storage
        from unittest.mock import patch
        st = groups._new_state(50)
        st.update(ok=3, skipped=2, failed=1, error="x")
        st["done"] = {5, 1, 9}
        st["reasons"]["maxfiylik sozlamasi"] = 1
        job = {"kind": "transfer", "title": "A → B", "group_chat_id": -100, "target_id": -200,
               "target_title": "B", "state": st, "created_ts": 1.0}
        rec = groups._record(job, "paused")
        import json
        json.dumps(rec)  # JSON bo'lishi shart
        back = groups._state_from_record(rec)
        self.assertEqual((back["ok"], back["skipped"], back["failed"], back["total"]), (3, 2, 1, 50))
        self.assertEqual(back["done"], {1, 5, 9})
        self.assertEqual(back["reasons"]["maxfiylik sozlamasi"], 1)
        with patch.object(storage, "_save", lambda: None):
            old = dict(storage._data["group_jobs"])
            storage._data["group_jobs"] = {}
            try:
                storage.save_group_job("transfer", rec)
                self.assertEqual(storage.get_group_job("transfer")["target_id"], -200)
                self.assertIn("Davom etish", str([b.text for r in groups.menu_keyboard().inline_keyboard for b in r]))
                cbs = [b.callback_data for r in groups.menu_keyboard().inline_keyboard for b in r]
                self.assertIn("dev:grpres:transfer", cbs)
                self.assertIn("dev:grpdrop:transfer", cbs)
                self.assertIn("dev:grpmov", cbs)
                self.assertIn("Saqlangan", groups.menu_text())
                self.assertTrue(storage.remove_group_job("transfer"))
                self.assertIsNone(storage.get_group_job("transfer"))
            finally:
                storage._data["group_jobs"] = old

    def _run_job(self, client, job, entities):
        import storage
        from unittest.mock import patch
        import handlers
        screens = []

        async def safe_edit_bot(bot, chat_id, message_id, text, reply_markup=None, parse_mode=None):
            screens.append((text, reply_markup))
        fake_dev = types.ModuleType("handlers.developer")
        fake_dev._safe_edit_bot = safe_edit_bot
        old, old_attr = sys.modules.get("handlers.developer"), getattr(handlers, "developer", None)
        sys.modules["handlers.developer"] = fake_dev
        handlers.developer = fake_dev

        async def acc():
            return client

        async def res(c, ref):
            return entities[ref]
        try:
            with patch.object(groups.ads, "_account_client", acc), \
                 patch.object(groups.ads, "_resolve_entity", res), \
                 patch.object(groups.ads, "_session_user_id", 1), \
                 patch.object(storage, "_save", lambda: None):
                asyncio.run(groups._run_job(object(), 1, 2, job))
        finally:
            if old is None:
                sys.modules.pop("handlers.developer", None)
            else:
                sys.modules["handlers.developer"] = old
            if old_attr is None:
                handlers.__dict__.pop("developer", None)
            else:
                handlers.developer = old_attr
        return screens

    def test_transfer_moves_members_and_pause_then_resume(self):
        import storage
        from unittest.mock import patch
        groups._is_channel = lambda e: False  # bittalab rejim
        src, dst = types.SimpleNamespace(id=100), types.SimpleNamespace(id=200)
        c = FakeClient()
        c.by_entity = {100: [U(10), U(11), U(12, bot=True), U(13)], 200: [U(99)]}
        c.fail = {11: _mk("PeerFloodError", {})}
        mk = lambda st: {"kind": "transfer", "title": "A → B", "group_chat_id": 100, "target_id": 200,
                         "target_title": "B", "state": st, "created_ts": 1.0}
        saved = dict(storage._data["group_jobs"])
        storage._data["group_jobs"] = {}
        try:
            with patch.object(storage, "_save", lambda: None):
                job = mk(groups._new_state())
                screens = self._run_job(c, job, {100: src, 200: dst})
                self.assertEqual([r[1].id for r in c.calls if r[0] == "invite"], [10, 11])  # 11 da PeerFlood
                self.assertEqual(job["state"]["ok"], 1)
                rec = storage.get_group_job("transfer")
                self.assertIsNotNone(rec)                       # saqlandi
                self.assertEqual(rec["status"], "paused")
                self.assertEqual(sorted(rec["done"]), [10, 12])  # 11 va 13 hali bajarilmagan
                text, markup = screens[-1]
                self.assertIn("saqlandi", text)
                cbs = [b.callback_data for r in markup.inline_keyboard for b in r]
                self.assertIn("dev:grpres:transfer", cbs)       # ▶️ Davom etish tugmasi
                # --- cheklov tugadi: davom etish ---
                c.fail = {}
                c.calls.clear()
                job2 = mk(groups._state_from_record(rec))
                screens2 = self._run_job(c, job2, {100: src, 200: dst})
                self.assertEqual([r[1].id for r in c.calls if r[0] == "invite"], [11, 13])  # faqat qolganlari
                self.assertEqual(job2["state"]["ok"], 3)        # 1 (oldingi) + 2
                self.assertIsNone(storage.get_group_job("transfer"))   # tugagach saqlangan yozuv o'chadi
                self.assertIn("Tugadi", screens2[-1][0])
        finally:
            storage._data["group_jobs"] = saved

    def test_transfer_flow_asks_two_ids_then_confirms(self):
        from unittest.mock import patch
        import handlers
        screens = []

        async def edit_menu(context, text, keyboard):
            screens.append(text)
        async def safe_edit(query, text, reply_markup=None, parse_mode=None):
            screens.append(text)
        fake_dev = types.ModuleType("handlers.developer")
        fake_dev._edit_menu = edit_menu
        fake_dev._safe_edit_query = safe_edit
        old, old_attr = sys.modules.get("handlers.developer"), getattr(handlers, "developer", None)
        sys.modules["handlers.developer"] = fake_dev
        handlers.developer = fake_dev
        chats = {"-1001": types.SimpleNamespace(id=-1001, title="Manba"),
                 "-1002": types.SimpleNamespace(id=-1002, title="Maqsad")}

        async def check_group(raw):
            return (chats[raw], None) if raw in chats else (None, "topilmadi")

        async def info(chat_id, kind):
            return 42
        ctx = types.SimpleNamespace(user_data={})

        def say(text):
            upd = types.SimpleNamespace(message=types.SimpleNamespace(text=text))
            return asyncio.run(groups.handle_dev_text(ctx.user_data["dev_action"], upd, ctx, "MENU", "TEXT"))
        try:
            with patch.object(groups.ads, "check_group", check_group), patch.object(groups, "_group_info", info):
                r = asyncio.run(groups.handle_dev_callback("grpmov", ["dev", "grpmov"], None, ctx, _FakeQuery(), "MENU", "TEXT"))
                self.assertEqual((r, ctx.user_data["dev_action"]["type"]), ("TEXT", "grp_move_src"))
                self.assertEqual(say("yo'q"), "TEXT")                       # noto'g'ri ID — qayta so'raydi
                self.assertEqual(ctx.user_data["dev_action"]["type"], "grp_move_src")
                self.assertEqual(say("-1001"), "TEXT")
                self.assertEqual(ctx.user_data["dev_action"]["type"], "grp_move_dst")
                self.assertIn("Manba", screens[-1])
                self.assertEqual(say("-1001"), "TEXT")                      # bir xil guruh — rad
                self.assertIn("o'zi", screens[-1])
                self.assertEqual(say("-1002"), "MENU")
                pend = ctx.user_data["dev_action"]
                self.assertEqual((pend["type"], pend["kind"], pend["chat_id"], pend["target_id"]),
                                 ("grp_confirm", "transfer", -1001, -1002))
                self.assertIn("Manba", screens[-1])
                self.assertIn("Maqsad", screens[-1])
                self.assertIn("Boshlaymizmi", screens[-1])
        finally:
            if old is None:
                sys.modules.pop("handlers.developer", None)
            else:
                sys.modules["handlers.developer"] = old
            if old_attr is None:
                handlers.__dict__.pop("developer", None)
            else:
                handlers.developer = old_attr

    def test_resume_and_drop_callbacks(self):
        import storage
        from unittest.mock import patch
        import handlers
        screens = []

        async def safe_edit(query, text, reply_markup=None, parse_mode=None):
            screens.append(text)
        fake_dev = types.ModuleType("handlers.developer")
        fake_dev._safe_edit_query = safe_edit
        old, old_attr = sys.modules.get("handlers.developer"), getattr(handlers, "developer", None)
        sys.modules["handlers.developer"] = fake_dev
        handlers.developer = fake_dev
        launched = []

        async def fake_launch(context, query, job, edit):
            launched.append(job)
        saved = dict(storage._data["group_jobs"])
        ctx = types.SimpleNamespace(user_data={})
        try:
            with patch.object(storage, "_save", lambda: None), patch.object(groups, "_launch", fake_launch):
                storage._data["group_jobs"] = {}
                asyncio.run(groups.handle_dev_callback("grpres", ["dev", "grpres", "add"], None, ctx, None, "M", "T"))
                self.assertIn("topilmadi", screens[-1])               # saqlangan ish yo'q
                storage.save_group_job("add", {"kind": "add", "title": "G", "chat_id": -5, "done": [1, 2],
                                               "ok": 2, "skipped": 0, "failed": 0, "total": 9})
                asyncio.run(groups.handle_dev_callback("grpres", ["dev", "grpres", "add"], None, ctx, None, "M", "T"))
                self.assertEqual(len(launched), 1)
                self.assertEqual((launched[0]["kind"], launched[0]["group_chat_id"]), ("add", -5))
                self.assertEqual(launched[0]["state"]["done"], {1, 2})
                asyncio.run(groups.handle_dev_callback("grpdrop", ["dev", "grpdrop", "add"], None, ctx, None, "M", "T"))
                self.assertIsNone(storage.get_group_job("add"))
                for a in ("grpmov", "grpres", "grpdrop"):
                    self.assertIn(a, groups.ACTIONS)
        finally:
            storage._data["group_jobs"] = saved
            if old is None:
                sys.modules.pop("handlers.developer", None)
            else:
                sys.modules["handlers.developer"] = old
            if old_attr is None:
                handlers.__dict__.pop("developer", None)
            else:
                handlers.developer = old_attr

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
