import asyncio
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._stub_telegram import install_stubs
install_stubs()

import config  # noqa: E402
import storage  # noqa: E402
import telegram_mtproto  # noqa: E402
from telegram import InlineKeyboardMarkup, InlineKeyboardButton  # noqa: E402
from handlers import ads  # noqa: E402


# --- Telethon entity / xato klasslarining nomlari haqiqiysi bilan bir xil ---
class Channel:
    def __init__(self, id, title="Guruh A", username=None):
        self.id, self.title, self.username = id, title, username


class Chat(Channel):
    pass


class User:
    def __init__(self, id):
        self.id, self.username = id, None


class ChatWriteForbiddenError(Exception):
    pass


class FloodWaitError(Exception):
    seconds = 7


class FakeResult:
    def __init__(self, client):
        self.client = client

    async def click(self, entity):
        self.client.clicked.append(entity)


class FakeClient:
    """Telethon client o'rnini bosadi (faqat ads.py ishlatadigan metodlar)."""

    def __init__(self, entities=None, need_warm=False, send_errors=None, inline_empty=False):
        self.entities = entities or {}
        self.need_warm = need_warm
        self.warmed = False
        self.send_errors = list(send_errors or [])
        self.inline_empty = inline_empty
        self.queries, self.clicked = [], []

    async def get_me(self):
        return types.SimpleNamespace(id=555)

    async def get_dialogs(self, limit=None):
        self.warmed = True

    async def get_entity(self, ref):
        if self.need_warm and not self.warmed:
            raise ValueError("Could not find the input entity")
        if ref in self.entities:
            return self.entities[ref]
        raise ValueError("Could not find the input entity")

    async def inline_query(self, bot, query, entity=None):
        if self.send_errors:
            err = self.send_errors.pop(0)
            if err:
                raise err
        self.queries.append((bot, query, entity))
        return [] if self.inline_empty else [FakeResult(self)]


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kw):
        self.sent.append(kw)


def fake_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Talaba AI", url="https://t.me/TestBot")]])


class AdsBase(unittest.IsolatedAsyncioTestCase):
    client = None

    def setUp(self):
        self._saved = dict(storage._data["ads"])
        storage._data["ads"] = {}
        ads._session_user_id = None
        self._patches = [
            patch.object(storage, "_save", lambda: None),
            patch.object(ads, "build_markup", fake_markup),
            patch.object(telegram_mtproto, "is_configured", lambda: True),
            patch.object(telegram_mtproto, "get_client", self._get_client),
            patch("handlers.mention_dispatch.get_bot_username", lambda: "TestBot"),
        ]
        for p in self._patches:
            p.start()

    async def _get_client(self):
        return self.client

    def tearDown(self):
        for t in list(ads._tasks.values()):
            t.cancel()
        ads._tasks.clear()
        for p in self._patches:
            p.stop()
        storage._data["ads"] = self._saved
        ads._session_user_id = None

    def app(self):
        return types.SimpleNamespace(bot=FakeBot())


class StorageAndParsing(AdsBase):
    def test_storage_crud(self):
        self.assertEqual(storage.get_ads(), [])
        ad = storage.add_ad(-1001, "G", "salom", 3, 1)
        self.assertEqual(storage.get_ad(ad["id"])["interval_min"], 3)
        self.assertTrue(storage.remove_ad(ad["id"]))
        self.assertFalse(storage.remove_ad(ad["id"]))

    def test_parse_chat_ref(self):
        self.assertEqual(ads.parse_chat_ref("-1001234567890"), -1001234567890)
        self.assertEqual(ads.parse_chat_ref("1276198486"), 1276198486)
        self.assertEqual(ads.parse_chat_ref("@my_group"), "@my_group")
        self.assertIsNone(ads.parse_chat_ref("salom"))

    def test_positive_id_tries_supergroup_prefix_first(self):
        self.assertEqual(ads._candidates(1276198486), [-1001276198486, -1276198486, 1276198486])
        self.assertEqual(ads._candidates(-1001276198486), [-1001276198486])

    def test_menu(self):
        self.assertIn("faol reklama yo'q", ads.menu_text())
        ad = storage.add_ad(-1001, "Guruh <A>", "<b>Salom</b> dunyo", 2, 1)
        text = ads.menu_text()
        self.assertIn("Guruh &lt;A&gt;", text)
        self.assertIn("Salom dunyo", text)
        callbacks = [b.callback_data for row in ads.menu_keyboard().inline_keyboard for b in row]
        self.assertIn(f"dev:adsview:{ad['id']}", callbacks)
        self.assertIn("dev:adsadd", callbacks)

    def test_minutes_keyboard_1_to_10(self):
        nums = [b.callback_data for row in ads.minutes_keyboard().inline_keyboard for b in row
                if b.callback_data.startswith("dev:adsmin:")]
        self.assertEqual(nums, [f"dev:adsmin:{n}" for n in range(1, 11)])


class CheckGroup(AdsBase):
    async def test_positive_id_resolved_as_supergroup(self):
        # Foydalanuvchi aynan shunday ID yubordi: 1276198486 ('-100' siz)
        self.client = FakeClient({-1001276198486: Channel(1276198486, "Mening guruhim")})
        chat, err = await ads.check_group("1276198486")
        self.assertIsNone(err)
        self.assertEqual((chat.id, chat.title), (-1001276198486, "Mening guruhim"))

    async def test_username(self):
        self.client = FakeClient({"@my_group": Channel(42, "G")})
        chat, err = await ads.check_group("@my_group")
        self.assertEqual(chat.id, -10042)

    async def test_dialogs_are_warmed_when_entity_not_cached(self):
        self.client = FakeClient({-1001234567890: Channel(1234567890)}, need_warm=True)
        chat, err = await ads.check_group("-1001234567890")
        self.assertIsNone(err)
        self.assertTrue(self.client.warmed)

    async def test_user_id_is_rejected(self):
        self.client = FakeClient({123456789: User(123456789)})
        chat, err = await ads.check_group("123456789")
        self.assertIsNone(chat)
        self.assertIn("guruh emas", err)

    async def test_not_found(self):
        self.client = FakeClient({})
        chat, err = await ads.check_group("-1009999999999")
        self.assertIsNone(chat)
        self.assertIn("topa olmadi", err)

    async def test_bad_format(self):
        self.client = FakeClient({})
        chat, err = await ads.check_group("abc")
        self.assertIsNone(chat)

    async def test_not_configured(self):
        self.client = FakeClient({})
        with patch.object(telegram_mtproto, "is_configured", lambda: False):
            chat, err = await ads.check_group("-1001234567890")
            self.assertIn("ulanmagan", err)
            self.assertIn("ulanmagan", ads.menu_text())


class Activation(AdsBase):
    async def test_activate_sends_from_account_via_inline(self):
        self.client = FakeClient({-1001: Channel(1)})
        ad = await ads.activate(self.app(), chat_id=-1001, chat_title="G", text="<b>Reklama</b>",
                                interval_min=1, created_by=5)
        self.assertEqual(self.client.queries[0][:2], ("TestBot", f"ad:{ad['id']}"))
        self.assertEqual(len(self.client.clicked), 1)
        self.assertEqual(len(storage.get_ads()), 1)
        self.assertIn(ad["id"], ads._tasks)

    async def test_failed_first_send_removes_ad(self):
        self.client = FakeClient({-1001: Channel(1)}, send_errors=[ChatWriteForbiddenError("no")])
        with self.assertRaises(ChatWriteForbiddenError):
            await ads.activate(self.app(), chat_id=-1001, chat_title="G", text="x",
                               interval_min=1, created_by=5)
        self.assertEqual(storage.get_ads(), [])
        self.assertEqual(ads._tasks, {})

    async def test_bot_returns_no_result(self):
        self.client = FakeClient({-1001: Channel(1)}, inline_empty=True)
        with self.assertRaises(RuntimeError):
            await ads.activate(self.app(), chat_id=-1001, chat_title="G", text="x",
                               interval_min=1, created_by=5)
        self.assertEqual(storage.get_ads(), [])


class InlineHandler(AdsBase):
    def _update(self, query, user_id):
        answers = []

        async def answer(results, **kw):
            answers.append(results)

        inline = types.SimpleNamespace(query=query, from_user=types.SimpleNamespace(id=user_id), answer=answer)
        return types.SimpleNamespace(inline_query=inline), answers

    async def test_admin_gets_ad_with_bot_button(self):
        ad = storage.add_ad(-1001, "G", "<b>Salom</b>", 1, 5)
        with patch.object(config, "ADMIN_IDS", {99}):
            update, answers = self._update(f"ad:{ad['id']}", 99)
            await ads.on_inline_ad_query(update, None)
        result = answers[0][0]
        self.assertEqual(result.input_message_content.message_text, "<b>Salom</b>")
        self.assertEqual(result.input_message_content.parse_mode, "HTML")
        self.assertEqual(result.reply_markup.inline_keyboard[0][0].url, "https://t.me/TestBot")

    async def test_session_account_allowed_others_denied(self):
        ad = storage.add_ad(-1001, "G", "x", 1, 5)
        ads._session_user_id = 555
        with patch.object(config, "ADMIN_IDS", set()):
            update, answers = self._update(f"ad:{ad['id']}", 555)
            await ads.on_inline_ad_query(update, None)
            self.assertEqual(len(answers[0]), 1)
            update, answers = self._update(f"ad:{ad['id']}", 1)
            await ads.on_inline_ad_query(update, None)
            self.assertEqual(answers[0], [])

    async def test_unknown_ad_gets_nothing(self):
        with patch.object(config, "ADMIN_IDS", {99}):
            update, answers = self._update("ad:nope", 99)
            await ads.on_inline_ad_query(update, None)
        self.assertEqual(answers[0], [])


class Loop(AdsBase):
    async def _run(self, app, ad, until, timeout=3):
        real_sleep = asyncio.sleep

        async def fast_sleep(_):
            await real_sleep(0)

        with patch.object(ads.asyncio, "sleep", fast_sleep):
            ads.start_ad(app, ad, first_delay=0)
            for _ in range(int(timeout * 100)):
                if until():
                    break
                await real_sleep(0.01)

    async def test_repeats_and_stops_when_deleted(self):
        self.client = FakeClient({-1001: Channel(1)})
        ad = storage.add_ad(-1001, "G", "salom", 1, 5)
        await self._run(self.app(), ad, lambda: len(self.client.clicked) >= 3)
        self.assertGreaterEqual(len(self.client.clicked), 3)
        storage.remove_ad(ad["id"])
        ads.stop_task(ad["id"])
        count = len(self.client.clicked)
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.client.clicked), count)

    async def test_permanent_error_stops_ad_and_notifies_admin(self):
        self.client = FakeClient({-1001: Channel(1)}, send_errors=[ChatWriteForbiddenError("x")])
        app = self.app()
        ad = storage.add_ad(-1001, "G", "salom", 1, 42)
        await self._run(app, ad, lambda: storage.get_ads() == [] and app.bot.sent)
        self.assertEqual(storage.get_ads(), [])
        self.assertTrue(any(m["chat_id"] == 42 and "to'xtatildi" in m["text"] for m in app.bot.sent))

    async def test_floodwait_retries_not_stops(self):
        self.client = FakeClient({-1001: Channel(1)}, send_errors=[FloodWaitError("wait")])
        ad = storage.add_ad(-1001, "G", "salom", 1, 5)
        await self._run(self.app(), ad, lambda: len(self.client.clicked) >= 1)
        self.assertGreaterEqual(len(self.client.clicked), 1)
        self.assertEqual(len(storage.get_ads()), 1)

    async def test_repeated_transient_errors_stop_ad(self):
        self.client = FakeClient({-1001: Channel(1)}, send_errors=[RuntimeError("net")] * ads.MAX_FAILURES)
        ad = storage.add_ad(-1001, "G", "salom", 1, 5)
        await self._run(self.app(), ad, lambda: storage.get_ads() == [])
        self.assertEqual(storage.get_ads(), [])


class DevFlow(AdsBase):
    """handlers.developer o'rniga fake modul bilan butun oqim: guruh -> matn -> daqiqa -> tasdiq."""

    def setUp(self):
        super().setUp()
        self.screens = []

        async def safe_edit(query, text, reply_markup=None, parse_mode=None):
            self.screens.append(text)

        async def edit_menu(context, text, keyboard):
            self.screens.append(text)

        fake_dev = types.ModuleType("handlers.developer")
        fake_dev._safe_edit_query = safe_edit
        fake_dev._edit_menu = edit_menu
        import handlers
        self._old = sys.modules.get("handlers.developer")
        self._old_attr = getattr(handlers, "developer", None)
        sys.modules["handlers.developer"] = fake_dev
        handlers.developer = fake_dev

    def tearDown(self):
        import handlers
        if self._old is None:
            sys.modules.pop("handlers.developer", None)
        else:
            sys.modules["handlers.developer"] = self._old
        if self._old_attr is None:
            handlers.__dict__.pop("developer", None)
        else:
            handlers.developer = self._old_attr
        super().tearDown()

    async def test_wrong_id_shows_changing_error_then_flow_continues(self):
        self.client = FakeClient({-1001276198486: Channel(1276198486, "Mening guruhim")})
        ctx = types.SimpleNamespace(user_data={"dev_action": {"type": "ads_group"}})
        for bad in ("1111111111", "2222222222"):
            msg = types.SimpleNamespace(text=bad, text_html=bad)
            r = await ads.handle_dev_text(ctx.user_data["dev_action"], types.SimpleNamespace(message=msg), ctx, "MENU", "TEXT")
            self.assertEqual((r, ctx.user_data["dev_action"]["type"]), ("TEXT", "ads_group"))
        self.assertNotEqual(self.screens[-1], self.screens[-2])  # matn har safar o'zgaradi

        msg = types.SimpleNamespace(text="1276198486", text_html="1276198486")
        r = await ads.handle_dev_text(ctx.user_data["dev_action"], types.SimpleNamespace(message=msg), ctx, "MENU", "TEXT")
        self.assertEqual(ctx.user_data["dev_action"]["type"], "ads_text")
        self.assertEqual(ctx.user_data["dev_action"]["chat_id"], -1001276198486)

    async def test_full_flow(self):
        self.client = FakeClient({-1001234567890: Channel(1234567890, "G")})
        app = self.app()
        ctx = types.SimpleNamespace(user_data={}, bot=app.bot, application=app)
        upd = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=9))

        r = await ads.handle_dev_callback("adsadd", ["dev", "adsadd"], upd, ctx, None, "MENU", "TEXT")
        self.assertEqual((r, ctx.user_data["dev_action"]["type"]), ("TEXT", "ads_group"))

        async def say(text):
            msg = types.SimpleNamespace(text=text, text_html=text)
            return await ads.handle_dev_text(ctx.user_data["dev_action"], types.SimpleNamespace(message=msg), ctx, "MENU", "TEXT")

        await say("-1001234567890")
        self.assertEqual(ctx.user_data["dev_action"]["type"], "ads_text")
        msg = types.SimpleNamespace(text="Salom <dunyo>", text_html="<b>Salom</b> &lt;dunyo&gt;")
        await ads.handle_dev_text(ctx.user_data["dev_action"], types.SimpleNamespace(message=msg), ctx, "MENU", "TEXT")
        self.assertEqual(ctx.user_data["dev_action"]["type"], "ads_minutes")

        r = await say("11")  # noto'g'ri daqiqa
        self.assertEqual((r, ctx.user_data["dev_action"]["type"]), ("TEXT", "ads_minutes"))

        await ads.handle_dev_callback("adsmin", ["dev", "adsmin", "5"], upd, ctx, None, "MENU", "TEXT")
        self.assertEqual(ctx.user_data["dev_action"]["interval_min"], 5)
        self.assertEqual(storage.get_ads(), [])  # tasdiqlanmaguncha saqlanmaydi

        await ads.handle_dev_callback("adsok", ["dev", "adsok"], upd, ctx, None, "MENU", "TEXT")
        saved = storage.get_ads()
        self.assertEqual(len(saved), 1)
        self.assertEqual((saved[0]["interval_min"], saved[0]["chat_id"], saved[0]["created_by"]), (5, -1001234567890, 9))
        self.assertEqual(len(self.client.clicked), 1)
        self.assertEqual(saved[0]["text"], "<b>Salom</b> &lt;dunyo&gt;")

        await ads.handle_dev_callback("adsok", ["dev", "adsok"], upd, ctx, None, "MENU", "TEXT")  # dublikat yo'q
        self.assertEqual(len(storage.get_ads()), 1)

        ad_id = saved[0]["id"]
        await ads.handle_dev_callback("adsdelyes", ["dev", "adsdelyes", ad_id], upd, ctx, None, "MENU", "TEXT")
        self.assertEqual(storage.get_ads(), [])
        self.assertNotIn(ad_id, ads._tasks)

    async def test_activation_failure_shows_reason_and_saves_nothing(self):
        self.client = FakeClient({-1001: Channel(1)}, send_errors=[ChatWriteForbiddenError("x")])
        app = self.app()
        ctx = types.SimpleNamespace(user_data={"dev_action": {
            "type": "ads_confirm", "chat_id": -1001, "chat_title": "G", "text": "x", "interval_min": 1}},
            bot=app.bot, application=app)
        upd = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=9))
        await ads.handle_dev_callback("adsok", ["dev", "adsok"], upd, ctx, None, "MENU", "TEXT")
        self.assertEqual(storage.get_ads(), [])
        self.assertIn("faollashtirilmadi", self.screens[-1])
        self.assertIn("yozish huquqi", self.screens[-1])


if __name__ == "__main__":
    unittest.main()
