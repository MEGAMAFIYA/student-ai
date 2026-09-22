"""
💳 handlers/payment_notify.py testlari — balans to'ldirilganda adminga
xabar ("@user hisobini X ga to'ldirdi, tasdiqlaysizmi?") va admin
tugmalari (✅ Tasdiqlash / ❌ Rad etish / ⚠️ Shubhali).

Ishga tushirish: python3 -m unittest tests.test_payment_notify -v
"""
import asyncio
import sys
import types
import unittest

from tests._stub_telegram import install_stubs

install_stubs()

from tests.test_wallet import _reset_wallet_state  # noqa: E402  (httpx stub + fake persist)

import config  # noqa: E402
import wallet  # noqa: E402

# handlers.wallet_ui og'ir bog'liqliklarga ega — faqat foydalanuvchiga xabar
# yuborish funksiyalarini soxta modul bilan almashtiramiz.
USER_NOTICES = []
REVOKE_NOTICES = []
AUTO_CONFIRM_NOTICES = []
_fake_ui = types.ModuleType("handlers.wallet_ui")


async def _fake_notify_user(bot, payment, approved, reason=""):
    USER_NOTICES.append((payment["user_id"], approved, reason))


async def _fake_notify_revoked(bot, payment, new_balance, reason=""):
    REVOKE_NOTICES.append((payment["user_id"], new_balance, reason))


async def _fake_notify_auto_confirmed(bot, payment):
    AUTO_CONFIRM_NOTICES.append(payment["user_id"])


_fake_ui.notify_user_payment_decision = _fake_notify_user
_fake_ui.notify_user_payment_revoked = _fake_notify_revoked
_fake_ui.notify_user_payment_auto_confirmed = _fake_notify_auto_confirmed
sys.modules["handlers.wallet_ui"] = _fake_ui

from handlers import payment_notify as pn  # noqa: E402


class FakeBot:
    def __init__(self, chats=None):
        self.sent = []          # (kind, chat_id, text_or_file, kwargs)
        self.chats = chats or {}

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(("message", chat_id, text, kw))

    async def send_photo(self, chat_id, file_id, **kw):
        self.sent.append(("photo", chat_id, file_id, kw))

    async def send_document(self, chat_id, file_id, **kw):
        self.sent.append(("document", chat_id, file_id, kw))

    async def get_chat(self, user_id):
        if user_id not in self.chats:
            raise RuntimeError("chat not found")
        return self.chats[user_id]


class U:
    def __init__(self, id=1, username=None, full_name="Ali Valiyev"):
        self.id, self.username, self.full_name = id, username, full_name


class FakeMessage:
    def __init__(self, text_html):
        self.text_html = text_html


class FakeQuery:
    def __init__(self, data, text_html="ASL XABAR"):
        self.data, self.message = data, FakeMessage(text_html)
        self.answers, self.edits = [], []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, parse_mode=None, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeUpdate:
    def __init__(self, query, user):
        self.callback_query, self.effective_user = query, user


class Ctx:
    def __init__(self, bot):
        self.bot = bot


def run(coro):
    return asyncio.run(coro)


def _payment(amount=10000, extracted=None, method=None):
    p = wallet.create_payment(555, amount, provider="manual", method=method or wallet.METHOD_MANUAL_RECEIPT)
    wallet.attach_receipt(p["payment_id"], "FILEID", "UNIQ", extracted=extracted or {}, confidence=None)
    wallet.mark_manual_review(p["payment_id"], reason="test")
    return wallet.get_payment(p["payment_id"])


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


class PaymentNotifyTests(unittest.TestCase):
    def setUp(self):
        _reset_wallet_state()
        USER_NOTICES.clear()
        REVOKE_NOTICES.clear()
        AUTO_CONFIRM_NOTICES.clear()
        self._old_admins = config.ADMIN_IDS
        config.ADMIN_IDS = {111, 222}

    def tearDown(self):
        config.ADMIN_IDS = self._old_admins

    # ---------- matn ----------
    def test_text_with_username(self):
        text = pn.build_admin_text(_payment(), "mittivoy", "Ali", awaiting_decision=True)
        self.assertIn("@mittivoy nomli foydalanuvchi hisobini <b>10 000 so'm</b> ga to'ldirdi. Tasdiqlaysizmi?", text)
        self.assertIn("555", text)

    def test_text_without_username_links_profile_and_escapes(self):
        text = pn.build_admin_text(_payment(), None, "A<b>li", awaiting_decision=True)
        self.assertIn('<a href="tg://user?id=555">A&lt;b&gt;li</a> ismli foydalanuvchi', text)

    def test_underscore_username_is_safe_in_html(self):
        text = pn.build_admin_text(_payment(), "user_name_x", "Ali", awaiting_decision=True)
        self.assertIn("@user_name_x nomli foydalanuvchi", text)

    def test_amount_mismatch_warning(self):
        p = _payment(10000, extracted={"amount": 5000, "transaction_id": "T1"})
        text = pn.build_admin_text(p, "u", "Ali", awaiting_decision=True)
        self.assertIn("FARQ qiladi", text)
        self.assertIn("transaction_id: T1", text)
        p2 = _payment(20000, extracted={"amount": "20 000"})
        self.assertNotIn("FARQ", pn.build_admin_text(p2, "u", "Ali", awaiting_decision=True))

    def test_callback_data_within_telegram_limit(self):
        p = _payment()
        for b in _buttons(pn.approval_keyboard(p["payment_id"])):
            self.assertLessEqual(len(b.callback_data.encode()), 64)

    # ---------- yuborish ----------
    def test_new_payment_goes_to_every_admin_with_buttons_and_receipt(self):
        bot = FakeBot()
        n = run(pn.notify_admins_new_payment(bot, _payment(), user=U(555, "mittivoy"), file_kind="photo"))
        self.assertEqual(n, 2)
        kinds = [(k, chat) for k, chat, *_ in bot.sent]
        for admin in (111, 222):
            self.assertIn(("photo", admin), kinds)
            self.assertIn(("message", admin), kinds)
        msg = next(x for x in bot.sent if x[0] == "message")
        self.assertEqual(msg[3]["parse_mode"], "HTML")
        self.assertEqual(
            [b.text for b in _buttons(msg[3]["reply_markup"])],
            ["✅ Tasdiqlash", "❌ Rad etish", "⚠️ Shubhali deb belgilash"],
        )

    def test_pdf_receipt_sent_as_document(self):
        bot = FakeBot()
        run(pn.notify_admins_new_payment(bot, _payment(), user=U(555, "x"), file_kind="document"))
        self.assertTrue(any(k == "document" for k, *_ in bot.sent))

    def test_no_admins_no_messages(self):
        config.ADMIN_IDS = set()
        bot = FakeBot()
        self.assertEqual(run(pn.notify_admins_new_payment(bot, _payment(), user=U(555))), 0)
        self.assertEqual(bot.sent, [])

    def test_confirmed_info_has_no_buttons_and_resolves_user_via_get_chat(self):
        p = _payment(method=wallet.METHOD_BANK_RECEIPT)
        wallet.confirm_payment(p["payment_id"], actor_id="webhook", source="webhook")
        bot = FakeBot(chats={555: U(555, "mittivoy")})
        run(pn.notify_admins_payment_confirmed(bot, wallet.get_payment(p["payment_id"]), "Kapitalbank onlayn to'lov"))
        _, _, text, kw = bot.sent[0]
        self.assertNotIn("reply_markup", kw)
        self.assertIn("@mittivoy nomli foydalanuvchi", text)
        self.assertIn("Balans to'ldirildi", text)
        self.assertIn("10 000 so'm", text)

    def test_one_admin_blocked_bot_does_not_stop_others(self):
        class Flaky(FakeBot):
            async def send_message(self, chat_id, text, **kw):
                if chat_id == 111:
                    raise RuntimeError("Forbidden: bot was blocked")
                await super().send_message(chat_id, text, **kw)
        bot = Flaky()
        self.assertEqual(run(pn.notify_admins_new_payment(bot, _payment(), user=U(555, "x"))), 1)

    # ---------- admin tugmalari ----------
    def _press(self, action, payment_id, admin):
        q = FakeQuery(f"payn:{action}:{payment_id}")
        bot = FakeBot()
        run(pn.admin_payment_decision_callback(FakeUpdate(q, admin), Ctx(bot)))
        return q

    def test_admin_approve_credits_once_and_notifies_user(self):
        p = _payment()
        q = self._press("approve", p["payment_id"], U(111, full_name="Admin 1"))
        self.assertEqual(wallet.get_balance(555), 10000)
        self.assertEqual(wallet.get_payment(p["payment_id"])["status"], wallet.STATUS_PAID)
        self.assertEqual(USER_NOTICES, [(555, True, "")])
        text, markup = q.edits[-1]
        self.assertIn("TASDIQLANDI", text)
        self.assertIsNone(markup)
        # Ikkinchi admin xuddi shu xabarni bossa — takror kredit BO'LMAYDI
        q2 = self._press("approve", p["payment_id"], U(222, full_name="Admin 2"))
        self.assertEqual(wallet.get_balance(555), 10000)
        self.assertTrue(q2.answers[-1][1])  # alert
        self.assertEqual(len(USER_NOTICES), 1)

    def test_admin_reject(self):
        p = _payment()
        q = self._press("reject", p["payment_id"], U(111))
        self.assertEqual(wallet.get_balance(555), 0)
        self.assertEqual(wallet.get_payment(p["payment_id"])["status"], wallet.STATUS_REJECTED)
        self.assertEqual(USER_NOTICES[0][:2], (555, False))
        self.assertIn("RAD ETILDI", q.edits[-1][0])

    def test_suspicious_keeps_approve_reject_buttons(self):
        p = _payment()
        q = self._press("susp", p["payment_id"], U(111))
        self.assertEqual(wallet.get_payment(p["payment_id"])["status"], wallet.STATUS_SUSPICIOUS)
        self.assertEqual(USER_NOTICES, [])
        labels = [b.text for b in _buttons(q.edits[-1][1])]
        self.assertEqual(labels, ["✅ Tasdiqlash", "❌ Rad etish"])
        # keyin tasdiqlash mumkin
        self._press("approve", p["payment_id"], U(111))
        self.assertEqual(wallet.get_balance(555), 10000)

    def test_non_admin_cannot_press(self):
        p = _payment()
        q = self._press("approve", p["payment_id"], U(999, full_name="Begona"))
        self.assertEqual(wallet.get_balance(555), 0)
        self.assertEqual(wallet.get_payment(p["payment_id"])["status"], wallet.STATUS_MANUAL_REVIEW)
        self.assertTrue(q.answers[-1][1])
        self.assertEqual(q.edits, [])

    def test_unknown_payment_and_bad_data(self):
        q = self._press("approve", "pay_yoq", U(111))
        self.assertTrue(q.answers[-1][1])
        q = FakeQuery("payn:hack:pay_x")
        run(pn.admin_payment_decision_callback(FakeUpdate(q, U(111)), Ctx(FakeBot())))
        self.assertTrue(q.answers[-1][1])

    # ---------- 🚫 "Soxta deb qaytarish" (revoke) ----------

    def test_revoke_button_debits_balance_and_notifies_user(self):
        p = _payment()
        wallet.confirm_payment(p["payment_id"], actor_id="bot_auto", source="test")
        self.assertEqual(wallet.get_balance(555), 10000)
        wallet.debit_balance(555, 8000, "test_spend")  # foydalanuvchi qisman ishlatib bo'lgan

        q = self._press("revoke", p["payment_id"], U(111, full_name="Admin 1"))

        self.assertEqual(wallet.get_payment(p["payment_id"])["status"], wallet.STATUS_REVOKED)
        self.assertEqual(wallet.get_balance(555), -8000)  # qarz
        self.assertIn("SOXTA DEB QAYTARILDI", q.edits[-1][0])
        self.assertEqual(REVOKE_NOTICES[-1][0], 555)
        self.assertEqual(REVOKE_NOTICES[-1][1], -8000)

    def test_revoke_button_refused_on_unpaid_payment(self):
        p = _payment()  # hali 'paid' emas (manual_review)
        q = self._press("revoke", p["payment_id"], U(111))
        self.assertTrue(q.answers[-1][1])  # alert — ruxsat berilmadi
        self.assertEqual(wallet.get_payment(p["payment_id"])["status"], wallet.STATUS_MANUAL_REVIEW)
        self.assertEqual(REVOKE_NOTICES, [])

    def test_revoke_button_idempotent(self):
        p = _payment()
        wallet.confirm_payment(p["payment_id"], actor_id="bot_auto", source="test")
        self._press("revoke", p["payment_id"], U(111))
        self.assertEqual(len(REVOKE_NOTICES), 1)
        q2 = self._press("revoke", p["payment_id"], U(222))
        self.assertTrue(q2.answers[-1][1])  # ikkinchi marta — alert, o'zgarish yo'q
        self.assertEqual(len(REVOKE_NOTICES), 1)

    def test_non_admin_cannot_revoke(self):
        p = _payment()
        wallet.confirm_payment(p["payment_id"], actor_id="bot_auto", source="test")
        q = self._press("revoke", p["payment_id"], U(999, full_name="Begona"))
        self.assertTrue(q.answers[-1][1])
        self.assertEqual(wallet.get_payment(p["payment_id"])["status"], wallet.STATUS_PAID)
        self.assertEqual(REVOKE_NOTICES, [])

    # ---------- 🤖 auto_hold -> bot avtomatik qabul qildi ----------

    def test_notify_admins_payment_auto_confirmed_has_revoke_button(self):
        p = _payment(method=wallet.METHOD_BANK_RECEIPT)
        wallet.confirm_payment(p["payment_id"], actor_id="bot_auto", source="auto_after_hold")
        payment = wallet.get_payment(p["payment_id"])
        bot = FakeBot()
        delivered = run(pn.notify_admins_payment_auto_confirmed(bot, payment, user=U(555, "x")))
        self.assertEqual(delivered, 2)
        kind, chat_id, text, kw = bot.sent[0]
        self.assertEqual(kind, "message")
        self.assertIn("avtomatik", text.lower())
        markup = kw["reply_markup"]
        labels = [b.text for b in _buttons(markup)]
        self.assertEqual(labels, ["🚫 Soxta deb qaytarish"])
        self.assertEqual(_buttons(markup)[0].callback_data, f"payn:revoke:{p['payment_id']}")


if __name__ == "__main__":
    unittest.main()
