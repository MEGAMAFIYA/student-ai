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
    _next_id = 1000

    def __init__(self):
        _FakeMessage._next_id += 1
        self.message_id = _FakeMessage._next_id


class _FakeBot:
    """Haqiqiy `telegram.Bot`ning O'RNIGA emas — faqat `tabrik_business.py`
    chaqiradigan metodlar (send_message/send_audio/delete_business_messages/
    edit_message_text) shakliga mos, sozlanadigan xatti-harakatli fake."""

    def __init__(self):
        self.sent_messages = []
        self.deleted_calls = []
        self.edit_calls = []
        self.fail_effect_ids: set = set()
        self.fail_delete_message_ids: set = set()
        self.raise_not_eligible = False
        self.retry_after_once_for = None  # emoji matni uchun bir marta RetryAfter

    async def send_message(self, business_connection_id, chat_id, text, message_effect_id=None):
        from telegram.error import BadRequest, RetryAfter
        if self.raise_not_eligible:
            raise BadRequest("Bot can't initiate conversation with a user")
        if text == self.retry_after_once_for:
            self.retry_after_once_for = None  # faqat bir marta
            raise RetryAfter(retry_after=0.01)
        if message_effect_id is not None and message_effect_id in self.fail_effect_ids:
            raise BadRequest(f"MESSAGE_EFFECT_INVALID: {message_effect_id}")
        msg = _FakeMessage()
        self.sent_messages.append({"chat_id": chat_id, "text": text, "effect_id": message_effect_id, "message_id": msg.message_id})
        return msg

    async def send_audio(self, business_connection_id, chat_id, audio):
        msg = _FakeMessage()
        self.sent_messages.append({"chat_id": chat_id, "text": "<audio>", "message_id": msg.message_id})
        return msg

    async def delete_business_messages(self, business_connection_id, message_ids):
        self.deleted_calls.append(list(message_ids))
        for mid in message_ids:
            if mid in self.fail_delete_message_ids:
                raise RuntimeError(f"delete failed for {mid}")
        return True

    async def edit_message_text(self, inline_message_id, text, reply_markup=None):
        self.edit_calls.append({"inline_message_id": inline_message_id, "text": text})


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


class TabrikBusinessTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = _FakePersistBackend()
        self._orig_read = config.persist_read
        self._orig_write = config.persist_write
        config.persist_read = self.backend.read
        config.persist_write = self.backend.write

        import importlib
        import business_storage
        import tabrik_business
        import tabrik_logic
        importlib.reload(business_storage)
        importlib.reload(tabrik_business)
        self.business_storage = business_storage
        self.tabrik_business = tabrik_business
        self.tabrik_logic = tabrik_logic

        # Har bir test tezroq ishlashi uchun kutish vaqtlarini qisqartiramiz
        # (mantiq bir xil qoladi — faqat 2s/120s ni sekundning ulushigacha
        # tushiramiz, testlar daqiqalab kutmasin deb).
        self.tabrik_business.EMOJI_DISPLAY_DELAY_SEC = 0.001
        self.tabrik_business.REVERT_DELAY_SEC = 0.05

        self.bot = _FakeBot()
        self.context = _FakeContext(self.bot)

        # Sender uchun ishlaydigan business connection ulaymiz.
        self._connect(sender_user_id=1, connection_id="conn_1", can_reply=True, can_delete=True, enabled=True)

    async def asyncTearDown(self):
        config.persist_read = self._orig_read
        config.persist_write = self._orig_write

    def _connect(self, sender_user_id, connection_id, can_reply=True, can_delete=True, enabled=True):
        conn = types.SimpleNamespace(
            id=connection_id,
            user=types.SimpleNamespace(id=sender_user_id),
            user_chat_id=sender_user_id,
            is_enabled=enabled,
            rights=types.SimpleNamespace(can_reply=can_reply, can_delete_sent_messages=can_delete, can_read_messages=True, can_delete_all_messages=False),
        )
        self.business_storage.save_connection(conn)

    def _make_greeting(self, sender_user_id=1, text="Salom do'stim!", emojis=None):
        short_id = self.tabrik_logic.store_greeting(text, emojis=emojis)
        self.tabrik_business.register_celebration(short_id, sender_user_id=sender_user_id)
        return short_id


class RecipientResolveTests(TabrikBusinessTestBase):
    async def test_recipient_chat_id_equals_user_id(self):
        short_id = self._make_greeting()
        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        update = _FakeUpdate(query)
        await self.tabrik_business.handle_claim(update, self.context)
        self.assertTrue(any(m["chat_id"] == 42 for m in self.bot.sent_messages))

    async def test_sender_cannot_claim_own_greeting(self):
        short_id = self._make_greeting(sender_user_id=1)
        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=1)  # o'zi bosadi
        update = _FakeUpdate(query)
        await self.tabrik_business.handle_claim(update, self.context)
        self.assertEqual(self.bot.sent_messages, [])
        self.assertTrue(any("o'zingiz" in (a[0] or "").lower() for a in query.answers))

    async def test_missing_inline_message_id_rejected(self):
        short_id = self._make_greeting()
        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42, inline_message_id=None)
        update = _FakeUpdate(query)
        await self.tabrik_business.handle_claim(update, self.context)
        self.assertEqual(self.bot.sent_messages, [])

    async def test_expired_greeting_rejected(self):
        query = _FakeQuery("itabrik:claim:doesnotexist", from_user_id=42)
        update = _FakeUpdate(query)
        await self.tabrik_business.handle_claim(update, self.context)
        self.assertEqual(self.bot.sent_messages, [])


class BusinessRightsTests(TabrikBusinessTestBase):
    async def test_no_connection_blocks(self):
        short_id = self._make_greeting(sender_user_id=999)  # ulanmagan sender
        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        update = _FakeUpdate(query)
        await self.tabrik_business.handle_claim(update, self.context)
        self.assertEqual(self.bot.sent_messages, [])
        self.assertTrue(any("ulamagan" in (a[0] or "") for a in query.answers))

    async def test_disabled_connection_blocks(self):
        self._connect(sender_user_id=2, connection_id="conn_2", enabled=False)
        short_id = self._make_greeting(sender_user_id=2)
        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        await self.tabrik_business.handle_claim(_FakeUpdate(query), self.context)
        self.assertEqual(self.bot.sent_messages, [])

    async def test_can_reply_false_blocks(self):
        self._connect(sender_user_id=3, connection_id="conn_3", can_reply=False)
        short_id = self._make_greeting(sender_user_id=3)
        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        await self.tabrik_business.handle_claim(_FakeUpdate(query), self.context)
        self.assertEqual(self.bot.sent_messages, [])

    async def test_can_delete_false_still_runs_animation(self):
        # 15-band: delete huquqi yo'qligi ANIMATSIYANI TO'XTATMAYDI, faqat
        # o'chirish urinishlari muvaffaqiyatsiz bo'ladi (alohida log bilan).
        self._connect(sender_user_id=4, connection_id="conn_4", can_delete=False)
        short_id = self._make_greeting(sender_user_id=4, emojis=["😀"])
        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        await self.tabrik_business.handle_claim(_FakeUpdate(query), self.context)
        self.assertTrue(any(m["text"] == "😀" for m in self.bot.sent_messages))


class CycleContentTests(TabrikBusinessTestBase):
    async def test_full_cycle_sends_audio_emojis_and_final_text_then_deletes_emojis(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"fake-audio-bytes")
            audio_path = f.name
        old_audio_path = self.tabrik_business.TABRIK_AUDIO_PATH
        self.tabrik_business.TABRIK_AUDIO_PATH = audio_path
        try:
            short_id = self._make_greeting(text="Tabriklayman!", emojis=["😍", "🥳"])
            query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
            await self.tabrik_business.handle_claim(_FakeUpdate(query), self.context)

            texts = [m["text"] for m in self.bot.sent_messages]
            self.assertIn("<audio>", texts)
            self.assertIn("😍", texts)
            self.assertIn("🥳", texts)
            self.assertIn("Tabriklayman!", texts)
            # Final matn ketma-ketlikda ENG OXIRIDA bo'lishi kerak (5 ta emoji
            # tugagach yuboriladi — 7-band).
            self.assertEqual(texts[-1], "Tabriklayman!")
            # Har ikkala emoji xabari o'chirilgan bo'lishi kerak, final matn EMAS.
            deleted_ids = {mid for call in self.bot.deleted_calls for mid in call}
            emoji_msg_ids = {m["message_id"] for m in self.bot.sent_messages if m["text"] in ("😍", "🥳")}
            final_msg_id = next(m["message_id"] for m in self.bot.sent_messages if m["text"] == "Tabriklayman!")
            self.assertTrue(emoji_msg_ids.issubset(deleted_ids))
            self.assertNotIn(final_msg_id, deleted_ids)
        finally:
            self.tabrik_business.TABRIK_AUDIO_PATH = old_audio_path
            os.unlink(audio_path)

    async def test_effect_rejected_falls_back_to_plain_send(self):
        import telegram_effects
        effect_id = telegram_effects.get_effect_id("😍")
        self.assertIsNotNone(effect_id)
        self.bot.fail_effect_ids.add(effect_id)

        short_id = self._make_greeting(emojis=["😍"])
        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        await self.tabrik_business.handle_claim(_FakeUpdate(query), self.context)

        emoji_sends = [m for m in self.bot.sent_messages if m["text"] == "😍"]
        self.assertEqual(len(emoji_sends), 1)
        self.assertIsNone(emoji_sends[0]["effect_id"])  # effektsiz qayta yuborilgan

    async def test_audio_missing_does_not_crash_cycle(self):
        old = self.tabrik_business.TABRIK_AUDIO_PATH
        self.tabrik_business.TABRIK_AUDIO_PATH = "/no/such/file.mp3"
        try:
            short_id = self._make_greeting(emojis=["🎉"])
            query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
            await self.tabrik_business.handle_claim(_FakeUpdate(query), self.context)
            texts = [m["text"] for m in self.bot.sent_messages]
            self.assertNotIn("<audio>", texts)
            self.assertIn("🎉", texts)  # animatsiya davom etadi
        finally:
            self.tabrik_business.TABRIK_AUDIO_PATH = old

    async def test_delete_failure_does_not_abort_cycle(self):
        # emoji xabari o'chmasa ham keyingi bosqichlar (final matn) davom etishi kerak.
        short_id = self._make_greeting(emojis=["😍", "🥳"])

        async def send_message_then_mark_undeletable(**kwargs):
            msg = _FakeMessage()
            self.bot.sent_messages.append({"chat_id": kwargs["chat_id"], "text": kwargs["text"], "effect_id": kwargs.get("message_effect_id"), "message_id": msg.message_id})
            self.bot.fail_delete_message_ids.add(msg.message_id)
            return msg
        self.bot.send_message = send_message_then_mark_undeletable

        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        await self.tabrik_business.handle_claim(_FakeUpdate(query), self.context)
        texts = [m["text"] for m in self.bot.sent_messages]
        self.assertIn("Salom do'stim!", texts)  # final text baribir yuborildi

    async def test_business_chat_not_eligible_stops_gracefully(self):
        self.bot.raise_not_eligible = True
        short_id = self._make_greeting(emojis=["😍"])
        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        # Kutilmagan exception ko'tarilmasligi kerak — handler ichida ushlanadi.
        # (Bu test bir marta haqiqiy TypeError'ni ushlab oldi: log() chaqiruvida
        # `stage=` kwarg pozitsion `stage` argumenti bilan to'qnashardi —
        # tuzatildi, endi `at_stage=` ishlatiladi.)
        await self.tabrik_business.handle_claim(_FakeUpdate(query), self.context)
        self.assertEqual(self.bot.sent_messages, [])

    async def test_retry_after_is_retried_once(self):
        self.bot.retry_after_once_for = "😍"
        short_id = self._make_greeting(emojis=["😍"])
        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        await self.tabrik_business.handle_claim(_FakeUpdate(query), self.context)
        emoji_sends = [m for m in self.bot.sent_messages if m["text"] == "😍"]
        self.assertEqual(len(emoji_sends), 1)  # qayta urinishdan keyin muvaffaqiyatli


class DuplicateClickAndConcurrencyTests(TabrikBusinessTestBase):
    async def test_duplicate_click_ignored_while_cycle_running(self):
        short_id = self._make_greeting(emojis=["😍"])
        self.tabrik_business.EMOJI_DISPLAY_DELAY_SEC = 0.05  # birinchisi tugamasdan ikkinchisini yuborish uchun yetarli vaqt

        query1 = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        task1 = asyncio.create_task(self.tabrik_business.handle_claim(_FakeUpdate(query1), self.context))
        await asyncio.sleep(0.01)  # task1 lock'ni ushlab ulgurishi uchun

        query2 = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        await self.tabrik_business.handle_claim(_FakeUpdate(query2), self.context)
        self.assertTrue(any("allaqachon" in (a[0] or "") for a in query2.answers))

        await task1

    async def test_different_recipients_run_concurrently_no_global_lock(self):
        short_id_a = self._make_greeting(emojis=["😍"])
        short_id_b = self._make_greeting(emojis=["🥳"])
        self.tabrik_business.EMOJI_DISPLAY_DELAY_SEC = 0.02

        query_a = _FakeQuery(f"itabrik:claim:{short_id_a}", from_user_id=101)
        query_b = _FakeQuery(f"itabrik:claim:{short_id_b}", from_user_id=202)

        start = asyncio.get_event_loop().time()
        await asyncio.gather(
            self.tabrik_business.handle_claim(_FakeUpdate(query_a), self.context),
            self.tabrik_business.handle_claim(_FakeUpdate(query_b), self.context),
        )
        elapsed = asyncio.get_event_loop().time() - start
        # Agar global lock bo'lganida ikkalasi KETMA-KET ishlab, vaqt ikki
        # baravar bo'lardi — parallel ishlaganini taxminiy tekshiramiz.
        self.assertLess(elapsed, 0.08)


class RevertSchedulingTests(TabrikBusinessTestBase):
    async def test_revert_runs_after_delay_and_resets_button(self):
        short_id = self._make_greeting(emojis=[])
        query = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        await self.tabrik_business.handle_claim(_FakeUpdate(query), self.context)
        await asyncio.sleep(self.tabrik_business.REVERT_DELAY_SEC + 0.05)
        self.assertEqual(len(self.bot.edit_calls), 1)
        self.assertEqual(self.bot.edit_calls[0]["inline_message_id"], "imsg1")

    async def test_second_click_cancels_pending_revert(self):
        short_id = self._make_greeting(emojis=[])
        query1 = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        await self.tabrik_business.handle_claim(_FakeUpdate(query1), self.context)

        # Revert hali tugamasdan qayta bosamiz.
        await asyncio.sleep(0.01)
        query2 = _FakeQuery(f"itabrik:claim:{short_id}", from_user_id=42)
        await self.tabrik_business.handle_claim(_FakeUpdate(query2), self.context)

        await asyncio.sleep(self.tabrik_business.REVERT_DELAY_SEC + 0.05)
        # Faqat OXIRGI revert ishlagan bo'lishi kerak (ikkitasi emas).
        self.assertEqual(len(self.bot.edit_calls), 1)


if __name__ == "__main__":
    unittest.main()
