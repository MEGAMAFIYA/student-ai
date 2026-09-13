import asyncio
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._stub_telegram import install_stubs
install_stubs()

import config  # noqa: E402


class _FakePersistBackend:
    def __init__(self):
        self.store: dict[str, str] = {}

    def read(self, local_filename, upstash_key):
        return self.store.get(upstash_key), "fake-backend"

    def write(self, local_filename, upstash_key, raw, commit_message=""):
        self.store[upstash_key] = raw


class _FakeMessage:
    _next_id = 5000

    def __init__(self):
        _FakeMessage._next_id += 1
        self.message_id = _FakeMessage._next_id


class _FakeBot:
    """`pro_tabrik_business.py` chaqiradigan metodlar (send_message/
    edit_message_reply_markup) shakliga mos fake — tabrik_business.py
    testlaridagi _FakeBot bilan BIR XIL naqsh (DUPLICATE emas, chunki
    metod to'plami farq qiladi: delete/send_audio yo'q, reply_markup edit
    bor)."""

    def __init__(self):
        self.sent_messages = []
        self.markup_edits = []
        self.fail_effect_ids: set = set()
        self.raise_not_eligible = False
        self.retry_after_once_for = None
        self.fail_final_text = False

    async def send_message(self, business_connection_id, chat_id, text, message_effect_id=None):
        from telegram.error import BadRequest, RetryAfter
        if self.raise_not_eligible:
            raise BadRequest("Bot can't initiate conversation with a user")
        if self.fail_final_text and message_effect_id is None and text not in ("😍", "🥳", "🎉", "❤️", "✨"):
            # yakuniy matn yuborishda muvaffaqiyatsizlik simulyatsiyasi
            # (emoji'lar effektsiz ham shu shartga tushib qolmasligi uchun
            # ro'yxatdagi standart emojilarni istisno qilamiz).
            raise BadRequest("temporary failure")
        if text == self.retry_after_once_for:
            self.retry_after_once_for = None
            raise RetryAfter(retry_after=0.01)
        if message_effect_id is not None and message_effect_id in self.fail_effect_ids:
            raise BadRequest(f"MESSAGE_EFFECT_INVALID: {message_effect_id}")
        msg = _FakeMessage()
        self.sent_messages.append({"chat_id": chat_id, "text": text, "effect_id": message_effect_id, "message_id": msg.message_id})
        return msg

    async def edit_message_reply_markup(self, inline_message_id, reply_markup=None):
        self.markup_edits.append({"inline_message_id": inline_message_id, "reply_markup": reply_markup})


class _FakeQuery:
    def __init__(self, data, from_user_id, inline_message_id="imsg1"):
        self.data = data
        self.from_user = types.SimpleNamespace(id=from_user_id)
        self.inline_message_id = inline_message_id
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class _FakeUpdate:
    def __init__(self, query):
        self.callback_query = query


class _FakeContext:
    def __init__(self, bot):
        self.bot = bot


def _label(markup) -> str | None:
    if markup is None:
        return None
    return markup.inline_keyboard[0][0].text


class ProBusinessTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = _FakePersistBackend()
        self._orig_read = config.persist_read
        self._orig_write = config.persist_write
        config.persist_read = self.backend.read
        config.persist_write = self.backend.write

        import importlib
        import business_storage
        import pro_tabrik_business
        import tabrik_logic
        importlib.reload(business_storage)
        importlib.reload(pro_tabrik_business)
        self.business_storage = business_storage
        self.pro_business = pro_tabrik_business
        self.tabrik_logic = tabrik_logic

        self.bot = _FakeBot()
        self.context = _FakeContext(self.bot)

        self._connect(sender_user_id=1, connection_id="conn_1", can_reply=True, enabled=True)

    async def asyncTearDown(self):
        config.persist_read = self._orig_read
        config.persist_write = self._orig_write

    def _connect(self, sender_user_id, connection_id, can_reply=True, enabled=True):
        conn = types.SimpleNamespace(
            id=connection_id,
            user=types.SimpleNamespace(id=sender_user_id),
            user_chat_id=sender_user_id,
            is_enabled=enabled,
            rights=types.SimpleNamespace(can_reply=can_reply, can_delete_sent_messages=True, can_read_messages=True, can_delete_all_messages=False),
        )
        self.business_storage.save_connection(conn)

    def _make_greeting(self, sender_user_id=1, text="Tabriklayman!", emojis=None):
        short_id = self.tabrik_logic.store_greeting(text, emojis=emojis)
        self.pro_business.register_pro_celebration(short_id, sender_user_id=sender_user_id)
        return short_id

    async def _click(self, short_id, from_user_id=42, inline_message_id="imsg1"):
        query = _FakeQuery(f"iprotabrik:claim:{short_id}", from_user_id=from_user_id, inline_message_id=inline_message_id)
        await self.pro_business.handle_stage_click(_FakeUpdate(query), self.context)
        return query


class TextParsingAndButtonTests(unittest.TestCase):
    def test_pro_text_parsing(self):
        import pro_tabrik_logic
        self.assertEqual(pro_tabrik_logic.parse_pro_text("/pro Salom hammaga"), "Salom hammaga")

    def test_pro_text_parsing_with_bot_username(self):
        import pro_tabrik_logic
        self.assertEqual(pro_tabrik_logic.parse_pro_text("/pro@Student_ai_uz_bot Salom"), "Salom")

    def test_empty_text_returns_none(self):
        import pro_tabrik_logic
        self.assertIsNone(pro_tabrik_logic.parse_pro_text("/pro"))
        self.assertIsNone(pro_tabrik_logic.parse_pro_text("/pro   "))

    def test_button_label_progression(self):
        import pro_tabrik_business as pb
        self.assertEqual(pb._button_label(0), "🎁 Tabriknomani qabul qilish")
        self.assertEqual(pb._button_label(1), "Keyingi")
        self.assertEqual(pb._button_label(4), "Keyingi")
        self.assertEqual(pb._button_label(5), "Tabrikni ko'rish")
        self.assertIsNone(pb._button_label(6))


class RecipientResolutionTests(ProBusinessTestBase):
    async def test_recipient_resolution(self):
        short_id = self._make_greeting()
        await self._click(short_id, from_user_id=42)
        state = self.pro_business._get_state(short_id)
        self.assertEqual(state["recipient_user_id"], 42)
        self.assertEqual(state["recipient_chat_id"], 42)

    async def test_sender_cannot_claim_own_greeting(self):
        short_id = self._make_greeting(sender_user_id=1)
        query = await self._click(short_id, from_user_id=1)
        self.assertEqual(self.bot.sent_messages, [])
        self.assertTrue(any("o'zingiz" in (a[0] or "").lower() for a in query.answers))

    async def test_missing_inline_message_id_rejected(self):
        short_id = self._make_greeting()
        query = _FakeQuery(f"iprotabrik:claim:{short_id}", from_user_id=42, inline_message_id=None)
        await self.pro_business.handle_stage_click(_FakeUpdate(query), self.context)
        self.assertEqual(self.bot.sent_messages, [])

    async def test_second_user_cannot_hijack_in_progress_greeting(self):
        short_id = self._make_greeting()
        await self._click(short_id, from_user_id=42)  # stage 1 by user 42
        query = await self._click(short_id, from_user_id=999)  # different user tries stage 2
        self.assertTrue(any("boshqa" in (a[0] or "").lower() for a in query.answers))
        self.assertEqual(self.pro_business.get_current_stage(short_id), 1)


class BusinessConnectionLookupTests(ProBusinessTestBase):
    async def test_business_connection_lookup_success(self):
        short_id = self._make_greeting()
        await self._click(short_id)
        self.assertEqual(len(self.bot.sent_messages), 1)

    async def test_connection_missing_blocks(self):
        short_id = self._make_greeting(sender_user_id=555)  # ulanmagan
        query = await self._click(short_id)
        self.assertEqual(self.bot.sent_messages, [])
        self.assertTrue(any("ulamagan" in (a[0] or "") for a in query.answers))

    async def test_rights_check_can_reply_false_blocks(self):
        self._connect(sender_user_id=2, connection_id="conn_2", can_reply=False)
        short_id = self._make_greeting(sender_user_id=2)
        query = await self._click(short_id)
        self.assertEqual(self.bot.sent_messages, [])
        self.assertTrue(any("javob berish" in (a[0] or "") for a in query.answers))

    async def test_business_connection_missing_at_click_time(self):
        # Sender ulanishini butunlay o'chirib qo'ysa (masalan Business
        # ulanishini bekor qilsa) — CRITICAL, bosqich ILGARILAMASLIGI kerak.
        short_id = self._make_greeting(sender_user_id=1)
        self.business_storage._data["by_user"].pop("1", None)
        await self._click(short_id)
        self.assertEqual(self.pro_business.get_current_stage(short_id), 0)


class StageProgressionTests(ProBusinessTestBase):
    async def test_stage_1_sends_first_emoji_with_effect(self):
        short_id = self._make_greeting(emojis=["😍", "🥳", "🎉", "❤️", "✨"])
        await self._click(short_id)
        self.assertEqual(self.bot.sent_messages[0]["text"], "😍")
        self.assertIsNotNone(self.bot.sent_messages[0]["effect_id"])
        self.assertEqual(self.pro_business.get_current_stage(short_id), 1)

    async def test_stage_2_through_5_send_one_emoji_each_click(self):
        short_id = self._make_greeting(emojis=["😍", "🥳", "🎉", "❤️", "✨"])
        for expected_emoji in ["😍", "🥳", "🎉", "❤️", "✨"]:
            await self._click(short_id)
            self.assertEqual(self.bot.sent_messages[-1]["text"], expected_emoji)
        self.assertEqual(self.pro_business.get_current_stage(short_id), 5)
        texts = [m["text"] for m in self.bot.sent_messages]
        self.assertEqual(texts, ["😍", "🥳", "🎉", "❤️", "✨"])

    async def test_no_five_emojis_sent_in_a_single_click(self):
        short_id = self._make_greeting(emojis=["😍", "🥳", "🎉", "❤️", "✨"])
        await self._click(short_id)
        self.assertEqual(len(self.bot.sent_messages), 1)

    async def test_final_stage_sends_original_text(self):
        short_id = self._make_greeting(text="Chin qalbimdan tabriklayman!", emojis=["😍", "🥳", "🎉", "❤️", "✨"])
        for _ in range(6):
            await self._click(short_id)
        self.assertEqual(self.bot.sent_messages[-1]["text"], "Chin qalbimdan tabriklayman!")
        self.assertIsNone(self.bot.sent_messages[-1]["effect_id"])
        self.assertEqual(self.pro_business.get_current_stage(short_id), self.pro_business.FINAL_STAGE)

    async def test_seventh_click_is_noop(self):
        short_id = self._make_greeting(emojis=["😍", "🥳", "🎉", "❤️", "✨"])
        for _ in range(6):
            await self._click(short_id)
        count_before = len(self.bot.sent_messages)
        query = await self._click(short_id)
        self.assertEqual(len(self.bot.sent_messages), count_before)
        self.assertTrue(any("allaqachon" in (a[0] or "").lower() for a in query.answers))

    async def test_emoji_messages_are_never_deleted(self):
        # 6-band: emoji o'chirish shart emas — pro_tabrik_business.py'da
        # umuman delete chaqiruvi yo'q (_FakeBot'da delete metodi ham yo'q,
        # shuning uchun chaqirilsa AttributeError chiqarib testni yiqitadi).
        short_id = self._make_greeting(emojis=["😍"])
        await self._click(short_id)
        self.assertEqual(len(self.bot.sent_messages), 1)

    async def test_button_label_updates_after_each_click(self):
        short_id = self._make_greeting(emojis=["😍", "🥳", "🎉", "❤️", "✨"])
        expected_labels = ["Keyingi", "Keyingi", "Keyingi", "Keyingi", "Tabrikni ko'rish", None]
        for expected in expected_labels:
            await self._click(short_id)
            last_markup = self.bot.markup_edits[-1]["reply_markup"]
            self.assertEqual(_label(last_markup), expected)

    async def test_final_state_persists_no_reset(self):
        # 8-band: 120s reset yo'q — bu modulda umuman "revert"/schedule
        # funksiyasi mavjud emas, shuning uchun kutish vaqtidan keyin ham
        # holat o'zgarmasligini tekshiramiz.
        short_id = self._make_greeting(emojis=["😍"])
        for _ in range(6):
            await self._click(short_id)
        await asyncio.sleep(0.05)
        self.assertEqual(self.pro_business.get_current_stage(short_id), self.pro_business.FINAL_STAGE)
        self.assertFalse(hasattr(self.pro_business, "_schedule_revert"))


class EffectFallbackTests(ProBusinessTestBase):
    async def test_effect_success(self):
        short_id = self._make_greeting(emojis=["😍"])
        await self._click(short_id)
        self.assertIsNotNone(self.bot.sent_messages[0]["effect_id"])

    async def test_effect_failure_falls_back_to_plain_send(self):
        import telegram_effects
        effect_id = telegram_effects.get_effect_id("😍")
        self.assertIsNotNone(effect_id)
        self.bot.fail_effect_ids.add(effect_id)

        short_id = self._make_greeting(emojis=["😍"])
        await self._click(short_id)

        sends = [m for m in self.bot.sent_messages if m["text"] == "😍"]
        self.assertEqual(len(sends), 1)
        self.assertIsNone(sends[0]["effect_id"])
        self.assertEqual(self.pro_business.get_current_stage(short_id), 1)  # oqim to'xtamadi

    async def test_effect_failure_does_not_block_state_machine(self):
        import telegram_effects
        for emoji in ["😍", "🥳"]:
            eff = telegram_effects.get_effect_id(emoji)
            if eff:
                self.bot.fail_effect_ids.add(eff)
        short_id = self._make_greeting(emojis=["😍", "🥳", "🎉", "❤️", "✨"])
        await self._click(short_id)
        await self._click(short_id)
        self.assertEqual(self.pro_business.get_current_stage(short_id), 2)


class AudioAndTelegramErrorTests(ProBusinessTestBase):
    async def test_audio_success_flag(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"fake")
            path = f.name
        old = self.pro_business.PRO_AUDIO_PATH
        self.pro_business.PRO_AUDIO_PATH = path
        try:
            self.assertTrue(self.pro_business.audio_available())
        finally:
            self.pro_business.PRO_AUDIO_PATH = old
            os.unlink(path)

    async def test_audio_failure_when_file_missing(self):
        old = self.pro_business.PRO_AUDIO_PATH
        self.pro_business.PRO_AUDIO_PATH = "/no/such/pro_audio.mp3"
        try:
            self.assertFalse(self.pro_business.audio_available())
        finally:
            self.pro_business.PRO_AUDIO_PATH = old

    async def test_telegram_badrequest_on_emoji_is_non_critical(self):
        # Effektsiz ham xato bo'lsa (masalan vaqtinchalik BadRequest) —
        # 17-band: bu emoji darajasidagi xato, oqim TO'XTAMAYDI.
        async def always_fail(**kwargs):
            from telegram.error import BadRequest
            raise BadRequest("temporary glitch")
        self.bot.send_message = always_fail

        short_id = self._make_greeting(emojis=["😍"])
        await self._click(short_id)
        self.assertEqual(self.pro_business.get_current_stage(short_id), 1)

    async def test_retry_after_is_retried(self):
        self.bot.retry_after_once_for = "😍"
        short_id = self._make_greeting(emojis=["😍"])
        await self._click(short_id)
        sends = [m for m in self.bot.sent_messages if m["text"] == "😍"]
        self.assertEqual(len(sends), 1)

    async def test_business_connection_not_eligible_stops_current_stage(self):
        self.bot.raise_not_eligible = True
        short_id = self._make_greeting(emojis=["😍"])
        await self._click(short_id)
        self.assertEqual(self.bot.sent_messages, [])
        self.assertEqual(self.pro_business.get_current_stage(short_id), 0)

    async def test_expired_greeting_rejected(self):
        query = _FakeQuery("iprotabrik:claim:doesnotexist", from_user_id=42)
        await self.pro_business.handle_stage_click(_FakeUpdate(query), self.context)
        self.assertEqual(self.bot.sent_messages, [])


class DuplicateClickAndConcurrencyTests(ProBusinessTestBase):
    async def test_duplicate_click_ignored(self):
        short_id = self._make_greeting(emojis=["😍"])

        # Bot javobini sekinlashtirib, ikkinchi bosishni "ustidan" yuboramiz.
        original_send = self.bot.send_message

        async def slow_send(**kwargs):
            await asyncio.sleep(0.05)
            return await original_send(**kwargs)
        self.bot.send_message = slow_send

        query1 = _FakeQuery(f"iprotabrik:claim:{short_id}", from_user_id=42)
        task1 = asyncio.create_task(self.pro_business.handle_stage_click(_FakeUpdate(query1), self.context))
        await asyncio.sleep(0.01)

        query2 = _FakeQuery(f"iprotabrik:claim:{short_id}", from_user_id=42)
        await self.pro_business.handle_stage_click(_FakeUpdate(query2), self.context)
        self.assertTrue(any("allaqachon" in (a[0] or "").lower() for a in query2.answers))

        await task1
        self.assertEqual(self.pro_business.get_current_stage(short_id), 1)

    async def test_concurrent_recipients_no_global_lock(self):
        short_id_a = self._make_greeting(emojis=["😍"])
        short_id_b = self._make_greeting(emojis=["🥳"])

        original_send = self.bot.send_message

        async def slow_send(**kwargs):
            await asyncio.sleep(0.02)
            return await original_send(**kwargs)
        self.bot.send_message = slow_send

        start = asyncio.get_event_loop().time()
        await asyncio.gather(
            self._click(short_id_a, from_user_id=101),
            self._click(short_id_b, from_user_id=202),
        )
        elapsed = asyncio.get_event_loop().time() - start
        self.assertLess(elapsed, 0.06)


class StatePersistenceTests(ProBusinessTestBase):
    async def test_state_persists_across_clicks(self):
        short_id = self._make_greeting(emojis=["😍", "🥳"])
        await self._click(short_id, from_user_id=42)
        state_after_1 = self.pro_business._get_state(short_id)
        self.assertEqual(state_after_1["current_stage"], 1)
        await self._click(short_id, from_user_id=42)
        state_after_2 = self.pro_business._get_state(short_id)
        self.assertEqual(state_after_2["current_stage"], 2)
        # Recipient/connection identligi saqlangan bo'lishi kerak.
        self.assertEqual(state_after_1["recipient_user_id"], state_after_2["recipient_user_id"])

    async def test_final_state_persistence(self):
        short_id = self._make_greeting(emojis=["😍"])
        for _ in range(6):
            await self._click(short_id)
        self.assertEqual(self.pro_business._get_state(short_id)["current_stage"], self.pro_business.FINAL_STAGE)


class TabrikRegressionTests(unittest.TestCase):
    """19-band: /pro o'zgarishlari /tabrik'ga tegmasligini tasdiqlaydi —
    tabrik_logic.py va business_storage.py ommaviy interfeysi o'zgarmagan."""

    def test_tabrik_logic_store_get_roundtrip_unaffected(self):
        import tabrik_logic
        short_id = tabrik_logic.store_greeting("Tabrik testi", emojis=["🎉"])
        self.assertEqual(tabrik_logic.get_greeting(short_id), "Tabrik testi")
        self.assertEqual(tabrik_logic.get_greeting_emojis(short_id), ["🎉"])

    def test_tabrik_business_default_emojis_unaffected(self):
        import tabrik_business
        self.assertEqual(tabrik_business.DEFAULT_EMOJIS, ["😍", "🥳", "🎉", "❤️", "✨"])


if __name__ == "__main__":
    unittest.main()
