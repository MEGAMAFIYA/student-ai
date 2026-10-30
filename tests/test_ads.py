import asyncio
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._stub_telegram import install_stubs
install_stubs()

import storage  # noqa: E402
from telegram import InlineKeyboardMarkup, InlineKeyboardButton  # noqa: E402
from telegram.error import BadRequest, ChatMigrated, Forbidden  # noqa: E402
from handlers import ads  # noqa: E402


class FakeBot:
    id = 777

    def __init__(self, chat_type="supergroup", status="administrator", send_errors=None):
        self.chat_type, self.status = chat_type, status
        self.sent = []
        self.send_errors = list(send_errors or [])

    async def get_chat(self, ref):
        return types.SimpleNamespace(id=ref if isinstance(ref, int) else -100999, type=self.chat_type,
                                     title="Guruh A", username=None)

    async def get_chat_member(self, chat_id, user_id):
        return types.SimpleNamespace(status=self.status)

    async def send_message(self, **kw):
        if self.send_errors:
            err = self.send_errors.pop(0)
            if err:
                raise err
        self.sent.append(kw)
        return types.SimpleNamespace(message_id=len(self.sent))


def fake_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Talaba AI", url="https://t.me/TestBot")]])


class AdsBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = dict(storage._data["ads"])
        storage._data["ads"] = {}
        self._patches = [
            patch.object(storage, "_save", lambda: None),
            patch.object(ads, "build_markup", fake_markup),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for t in list(ads._tasks.values()):
            t.cancel()
        ads._tasks.clear()
        for p in self._patches:
            p.stop()
        storage._data["ads"] = self._saved


class StorageAndParsing(AdsBase):
    def test_storage_crud(self):
        self.assertEqual(storage.get_ads(), [])
        ad = storage.add_ad(-1001, "G", "salom", 3, 1)
        self.assertEqual(storage.get_ad(ad["id"])["interval_min"], 3)
        self.assertEqual(len(storage.get_ads()), 1)
        self.assertTrue(storage.remove_ad(ad["id"]))
        self.assertFalse(storage.remove_ad(ad["id"]))

    def test_parse_chat_ref(self):
        self.assertEqual(ads.parse_chat_ref("-1001234567890"), -1001234567890)
        self.assertEqual(ads.parse_chat_ref("@my_group"), "@my_group")
        self.assertIsNone(ads.parse_chat_ref("salom"))
        self.assertIsNone(ads.parse_chat_ref("12"))

    def test_empty_menu(self):
        self.assertIn("faol reklama yo'q", ads.menu_text())
        callbacks = [b.callback_data for row in ads.menu_keyboard().inline_keyboard for b in row]
        self.assertIn("dev:adsadd", callbacks)

    def test_menu_lists_ads(self):
        ad = storage.add_ad(-1001, "Guruh <A>", "<b>Salom</b> dunyo", 2, 1)
        text = ads.menu_text()
        self.assertIn("Guruh &lt;A&gt;", text)
        self.assertIn("Salom dunyo", text)
        callbacks = [b.callback_data for row in ads.menu_keyboard().inline_keyboard for b in row]
        self.assertIn(f"dev:adsview:{ad['id']}", callbacks)

    def test_minutes_keyboard_1_to_10(self):
        nums = [b.callback_data for row in ads.minutes_keyboard().inline_keyboard for b in row
                if b.callback_data.startswith("dev:adsmin:")]
        self.assertEqual(nums, [f"dev:adsmin:{n}" for n in range(1, 11)])


class CheckGroup(AdsBase):
    async def test_ok(self):
        chat, err = await ads.check_group(FakeBot(), "-1001234567890")
        self.assertIsNone(err)
        self.assertEqual(chat.id, -1001234567890)

    async def test_private_rejected(self):
        chat, err = await ads.check_group(FakeBot(chat_type="private"), "123456789")
        self.assertIsNone(chat)
        self.assertIn("guruh emas", err)

    async def test_bot_not_member(self):
        chat, err = await ads.check_group(FakeBot(status="left"), "-1001234567890")
        self.assertIsNone(chat)
        self.assertIn("a'zo emas", err)

    async def test_channel_needs_admin(self):
        chat, err = await ads.check_group(FakeBot(chat_type="channel", status="member"), "-1001234567890")
        self.assertIsNone(chat)

    async def test_bad_format(self):
        chat, err = await ads.check_group(FakeBot(), "abc")
        self.assertIsNone(chat)


class Activation(AdsBase):
    def app(self, bot):
        return types.SimpleNamespace(bot=bot)

    async def test_activate_sends_first_message_with_button(self):
        bot = FakeBot()
        ad = await ads.activate(self.app(bot), chat_id=-1001, chat_title="G", text="<b>Reklama</b>",
                                interval_min=1, created_by=5)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.sent[0]["chat_id"], -1001)
        self.assertEqual(bot.sent[0]["parse_mode"], "HTML")
        self.assertEqual(bot.sent[0]["reply_markup"].inline_keyboard[0][0].url, "https://t.me/TestBot")
        self.assertEqual(len(storage.get_ads()), 1)
        self.assertIn(ad["id"], ads._tasks)

    async def test_failed_first_send_saves_nothing(self):
        bot = FakeBot(send_errors=[Forbidden("bot was kicked")])
        with self.assertRaises(Forbidden):
            await ads.activate(self.app(bot), chat_id=-1001, chat_title="G", text="x",
                               interval_min=1, created_by=5)
        self.assertEqual(storage.get_ads(), [])
        self.assertEqual(ads._tasks, {})

    async def test_html_parse_error_falls_back_to_plain(self):
        bot = FakeBot(send_errors=[BadRequest("Can't parse entities: bad")])
        await ads.send_ad(bot, {"chat_id": 1, "text": "<b>Salom</b>"})
        self.assertEqual(bot.sent[0]["text"], "Salom")
        self.assertNotIn("parse_mode", bot.sent[0])


class Loop(AdsBase):
    async def _run(self, bot, ad, until, timeout=3):
        real_sleep = asyncio.sleep

        async def fast_sleep(_):
            await real_sleep(0)

        with patch.object(ads.asyncio, "sleep", fast_sleep):
            ads.start_ad(types.SimpleNamespace(bot=bot), ad, first_delay=0)
            for _ in range(int(timeout * 100)):
                if until():
                    break
                await real_sleep(0.01)

    async def test_repeats_and_stops_when_deleted(self):
        bot = FakeBot()
        ad = storage.add_ad(-1001, "G", "salom", 1, 5)
        await self._run(bot, ad, lambda: len(bot.sent) >= 3)
        self.assertGreaterEqual(len(bot.sent), 3)
        storage.remove_ad(ad["id"])
        ads.stop_task(ad["id"])
        count = len(bot.sent)
        await asyncio.sleep(0.05)
        self.assertEqual(len(bot.sent), count)

    async def test_forbidden_stops_ad_and_notifies_admin(self):
        bot = FakeBot(send_errors=[Forbidden("kicked")])
        ad = storage.add_ad(-1001, "G", "salom", 1, 42)
        await self._run(bot, ad, lambda: storage.get_ads() == [])
        self.assertEqual(storage.get_ads(), [])
        self.assertTrue(any(m["chat_id"] == 42 and "to'xtatildi" in m["text"] for m in bot.sent))

    async def test_migrated_chat_updates_id_and_continues(self):
        bot = FakeBot(send_errors=[ChatMigrated(-100777)])
        ad = storage.add_ad(-1001, "G", "salom", 1, 5)
        await self._run(bot, ad, lambda: any(m["chat_id"] == -100777 for m in bot.sent))
        self.assertEqual(storage.get_ad(ad["id"])["chat_id"], -100777)
        self.assertTrue(any(m["chat_id"] == -100777 for m in bot.sent))

    async def test_repeated_transient_errors_stop_ad(self):
        bot = FakeBot(send_errors=[RuntimeError("net")] * ads.MAX_FAILURES)
        ad = storage.add_ad(-1001, "G", "salom", 1, 5)
        await self._run(bot, ad, lambda: storage.get_ads() == [])
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
        self._old = sys.modules.get("handlers.developer")
        sys.modules["handlers.developer"] = fake_dev
        import handlers
        self._old_attr = getattr(handlers, "developer", None)
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

    async def test_full_flow(self):
        bot = FakeBot()
        ctx = types.SimpleNamespace(user_data={}, bot=bot, application=types.SimpleNamespace(bot=bot))
        user = types.SimpleNamespace(id=9)

        r = await ads.handle_dev_callback("adsadd", ["dev", "adsadd"], types.SimpleNamespace(effective_user=user), ctx, None, "MENU", "TEXT")
        self.assertEqual((r, ctx.user_data["dev_action"]["type"]), ("TEXT", "ads_group"))

        msg = types.SimpleNamespace(text="-1001234567890", text_html="-1001234567890")
        r = await ads.handle_dev_text(ctx.user_data["dev_action"], types.SimpleNamespace(message=msg), ctx, "MENU", "TEXT")
        self.assertEqual((r, ctx.user_data["dev_action"]["type"]), ("TEXT", "ads_text"))

        msg = types.SimpleNamespace(text="Salom <dunyo>", text_html="<b>Salom</b> &lt;dunyo&gt;")
        r = await ads.handle_dev_text(ctx.user_data["dev_action"], types.SimpleNamespace(message=msg), ctx, "MENU", "TEXT")
        self.assertEqual(ctx.user_data["dev_action"]["type"], "ads_minutes")

        # noto'g'ri daqiqa
        msg = types.SimpleNamespace(text="11", text_html="11")
        r = await ads.handle_dev_text(ctx.user_data["dev_action"], types.SimpleNamespace(message=msg), ctx, "MENU", "TEXT")
        self.assertEqual((r, ctx.user_data["dev_action"]["type"]), ("TEXT", "ads_minutes"))

        # tugma orqali 5 daqiqa
        r = await ads.handle_dev_callback("adsmin", ["dev", "adsmin", "5"], types.SimpleNamespace(effective_user=user), ctx, None, "MENU", "TEXT")
        self.assertEqual(ctx.user_data["dev_action"]["interval_min"], 5)
        self.assertEqual(storage.get_ads(), [])  # tasdiqlanmaguncha saqlanmaydi

        r = await ads.handle_dev_callback("adsok", ["dev", "adsok"], types.SimpleNamespace(effective_user=user), ctx, None, "MENU", "TEXT")
        saved = storage.get_ads()
        self.assertEqual(len(saved), 1)
        self.assertEqual((saved[0]["interval_min"], saved[0]["chat_id"], saved[0]["created_by"]), (5, -1001234567890, 9))
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("<b>Salom</b>", bot.sent[0]["text"])

        # ikkinchi marta tasdiqlash bosilsa — dublikat yaratilmaydi
        await ads.handle_dev_callback("adsok", ["dev", "adsok"], types.SimpleNamespace(effective_user=user), ctx, None, "MENU", "TEXT")
        self.assertEqual(len(storage.get_ads()), 1)

        # o'chirish
        ad_id = saved[0]["id"]
        await ads.handle_dev_callback("adsdelyes", ["dev", "adsdelyes", ad_id], types.SimpleNamespace(effective_user=user), ctx, None, "MENU", "TEXT")
        self.assertEqual(storage.get_ads(), [])
        self.assertNotIn(ad_id, ads._tasks)


if __name__ == "__main__":
    unittest.main()
