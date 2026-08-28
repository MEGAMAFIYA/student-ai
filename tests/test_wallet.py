"""
💳 wallet.py (balans/to'lov yadrosi) uchun testlar.

Bu testlar Telegram/AI'ga UMUMAN bog'liq emas — faqat wallet.py'ning pul
bilan bog'liq XAVFSIZLIK kafolatlarini tekshiradi:
- Balans hech qachon manfiy bo'lmaydi.
- Bitta to'lov faqat BIR MARTA kredit qilinadi (webhook/admin necha marta
  chaqirilsa ham — idempotency).
- Duplicate to'lov (bir xil provider_transaction_id yoki chek) rad etiladi.
- Race condition (bir nechta parallel so'rov) balansni buzmaydi.

Ishga tushirish: `python3 -m unittest tests.test_wallet -v`
(loyihaning ILDIZ papkasidan, ya'ni bot.py bilan bir joydan).

MUHIM: config.py `httpx` kutubxonasini talab qiladi. Agar test muhitida
u o'rnatilmagan bo'lsa, quyida ENGIL bir "stub" (soxta) modul sifatida
ro'yxatdan o'tkaziladi — bu FAQAT import xatosining oldini olish uchun;
haqiqiy tarmoq so'rovi HECH QACHON yuborilmaydi, chunki testlarda
Upstash/Neon/GitHub konfiguratsiyasi yo'q (persist_read/write ham qo'shimcha
ravishda xotiradagi soxta saqlash bilan almashtiriladi).
"""

import sys
import threading
import types
import unittest
import asyncio

if "httpx" not in sys.modules:
    _httpx_stub = types.ModuleType("httpx")

    class _DummyClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            raise RuntimeError("Testlarda haqiqiy HTTP so'rovi yuborilmasligi kerak edi.")

        def put(self, *a, **k):
            raise RuntimeError("Testlarda haqiqiy HTTP so'rovi yuborilmasligi kerak edi.")

    _httpx_stub.Client = _DummyClient
    sys.modules["httpx"] = _httpx_stub

import config  # noqa: E402  (yuqoridagi httpx stub'idan KEYIN import qilinishi shart)

# Testlarda haqiqiy fayl/tarmoqqa yozilmasligi uchun persist_read/write'ni
# xotiradagi (in-memory) soxta saqlash bilan almashtiramiz.
_FAKE_STORE: dict[str, str] = {}


def _fake_persist_read(local_filename, upstash_key):
    return _FAKE_STORE.get(upstash_key), "fake-memory"


def _fake_persist_write(local_filename, upstash_key, raw, commit_message=""):
    _FAKE_STORE[upstash_key] = raw


config.persist_read = _fake_persist_read
config.persist_write = _fake_persist_write

import wallet  # noqa: E402
import payment_providers  # noqa: E402


def _reset_wallet_state():
    """Har bir testdan oldin wallet.py'ning ICHKI holatini toza standart
    holatga qaytaradi (testlar bir-biriga ta'sir qilmasligi uchun)."""
    wallet._data = {
        "wallets": {},
        "transactions": [],
        "payments": {},
        "provider_tx_index": {},
        "receipt_fingerprints": {},
        "features": {k: dict(v) for k, v in wallet._DEFAULT_FEATURES.items()},
        "audit_log": [],
    }
    _FAKE_STORE.clear()


class WalletBalanceTests(unittest.TestCase):
    def setUp(self):
        _reset_wallet_state()

    def test_initial_balance_is_zero(self):
        self.assertEqual(wallet.get_balance(1), 0)

    def test_credit_balance_increases_balance(self):
        wallet.credit_balance(1, 5000, "test kredit")
        self.assertEqual(wallet.get_balance(1), 5000)

    def test_invalid_amount_rejected(self):
        with self.assertRaises(wallet.WalletError):
            wallet.credit_balance(1, 0, "noto'g'ri")
        with self.assertRaises(wallet.WalletError):
            wallet.credit_balance(1, -100, "noto'g'ri")
        with self.assertRaises(wallet.WalletError):
            wallet.debit_balance(1, -5, "noto'g'ri")

    def test_debit_balance_decreases_balance(self):
        wallet.credit_balance(1, 10000, "boshlang'ich")
        wallet.debit_balance(1, 3000, "xarajat")
        self.assertEqual(wallet.get_balance(1), 7000)

    def test_debit_never_goes_negative(self):
        wallet.credit_balance(1, 1000, "boshlang'ich")
        with self.assertRaises(wallet.InsufficientBalanceError):
            wallet.debit_balance(1, 5000, "juda ko'p")
        # Balans o'zgarishsiz qolishi kerak (muvaffaqiyatsiz debit hech narsa qilmaydi)
        self.assertEqual(wallet.get_balance(1), 1000)

    def test_exact_balance_payment(self):
        wallet.credit_balance(1, 5000, "boshlang'ich")
        wallet.debit_balance(1, 5000, "to'liq sarflash")
        self.assertEqual(wallet.get_balance(1), 0)

    def test_transactions_recorded(self):
        wallet.credit_balance(1, 1000, "a")
        wallet.debit_balance(1, 400, "b")
        txs = wallet.get_transactions(1)
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0]["description"], "b")  # eng yangisi birinchi


class FeatureChargeTests(unittest.TestCase):
    def setUp(self):
        _reset_wallet_state()

    def test_free_feature_no_charge(self):
        result = wallet.charge_for_feature(1, "translate")  # standart narx 0
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "free")
        self.assertEqual(wallet.get_balance(1), 0)

    def test_paid_feature_charges_correct_amount(self):
        wallet.credit_balance(1, 20000, "boshlang'ich")
        result = wallet.charge_for_feature(1, "course_work")  # standart narx 10000
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "charged")
        self.assertEqual(wallet.get_balance(1), 10000)

    def test_insufficient_balance_blocks_feature(self):
        wallet.credit_balance(1, 5000, "boshlang'ich")
        result = wallet.charge_for_feature(1, "course_work")  # narx 10000
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "insufficient")
        self.assertEqual(result.price, 10000)
        self.assertEqual(result.balance, 5000)
        # Balans O'ZGARMAGAN bo'lishi SHART (muvaffaqiyatsiz urinish pul yechmaydi)
        self.assertEqual(wallet.get_balance(1), 5000)

    def test_disabled_feature_blocks_even_with_balance(self):
        wallet.credit_balance(1, 100000, "boshlang'ich")
        wallet.set_feature_enabled("course_work", False)
        result = wallet.charge_for_feature(1, "course_work")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "disabled")
        self.assertEqual(wallet.get_balance(1), 100000)

    def test_price_change_reflected_immediately(self):
        wallet.set_feature_price("essay", 12345)
        self.assertEqual(wallet.get_feature("essay")["price"], 12345)
        wallet.credit_balance(1, 12345, "boshlang'ich")
        result = wallet.charge_for_feature(1, "essay")
        self.assertTrue(result.ok)
        self.assertEqual(wallet.get_balance(1), 0)

    def test_zero_price_makes_feature_free(self):
        wallet.set_feature_price("course_work", 0)
        result = wallet.charge_for_feature(1, "course_work")
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "free")


class PaymentConfirmationTests(unittest.TestCase):
    def setUp(self):
        _reset_wallet_state()

    def test_successful_payment_credits_balance_once(self):
        payment = wallet.create_payment(1, 10000, provider="kapitalbank", method=wallet.METHOD_ECOMMERCE)
        ok = wallet.confirm_payment(payment["payment_id"], actor_id="webhook", source="webhook")
        self.assertTrue(ok)
        self.assertEqual(wallet.get_balance(1), 10000)
        self.assertEqual(wallet.get_payment(payment["payment_id"])["status"], wallet.STATUS_PAID)

    def test_duplicate_webhook_does_not_double_credit(self):
        payment = wallet.create_payment(1, 10000, provider="kapitalbank", method=wallet.METHOD_ECOMMERCE)
        first = wallet.confirm_payment(payment["payment_id"], source="webhook")
        second = wallet.confirm_payment(payment["payment_id"], source="webhook")  # xuddi shu webhook qayta keldi
        third = wallet.confirm_payment(payment["payment_id"], source="webhook")
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertFalse(third)
        self.assertEqual(wallet.get_balance(1), 10000)  # FAQAT bir marta kredit qilingan

    def test_failed_payment_no_credit(self):
        payment = wallet.create_payment(1, 10000, provider="kapitalbank", method=wallet.METHOD_ECOMMERCE)
        wallet.set_payment_status(payment["payment_id"], wallet.STATUS_FAILED)
        ok = wallet.confirm_payment(payment["payment_id"])
        self.assertFalse(ok)
        self.assertEqual(wallet.get_balance(1), 0)

    def test_cancelled_payment_no_credit(self):
        payment = wallet.create_payment(1, 10000, provider="kapitalbank", method=wallet.METHOD_ECOMMERCE)
        wallet.set_payment_status(payment["payment_id"], wallet.STATUS_CANCELLED)
        ok = wallet.confirm_payment(payment["payment_id"])
        self.assertFalse(ok)
        self.assertEqual(wallet.get_balance(1), 0)

    def test_rejected_payment_cannot_be_confirmed_later(self):
        payment = wallet.create_payment(1, 10000, provider="manual", method=wallet.METHOD_MANUAL_RECEIPT)
        wallet.reject_payment(payment["payment_id"], actor_id=999, reason="soxta chek")
        ok = wallet.confirm_payment(payment["payment_id"], actor_id=999)
        self.assertFalse(ok)
        self.assertEqual(wallet.get_balance(1), 0)

    def test_manual_approval_credits_balance(self):
        payment = wallet.create_payment(2, 20000, provider="manual", method=wallet.METHOD_MANUAL_RECEIPT)
        wallet.mark_manual_review(payment["payment_id"])
        ok = wallet.approve_manual_payment(payment["payment_id"], actor_id=999)
        self.assertTrue(ok)
        self.assertEqual(wallet.get_balance(2), 20000)

    def test_manual_rejection_no_credit(self):
        payment = wallet.create_payment(2, 20000, provider="manual", method=wallet.METHOD_MANUAL_RECEIPT)
        wallet.mark_manual_review(payment["payment_id"])
        ok = wallet.reject_payment(payment["payment_id"], actor_id=999, reason="mos kelmadi")
        self.assertTrue(ok)
        self.assertEqual(wallet.get_balance(2), 0)
        self.assertEqual(wallet.get_payment(payment["payment_id"])["status"], wallet.STATUS_REJECTED)

    def test_nonexistent_payment_confirm_returns_false(self):
        self.assertFalse(wallet.confirm_payment("pay_does_not_exist"))


class DuplicateProtectionTests(unittest.TestCase):
    def setUp(self):
        _reset_wallet_state()

    def test_duplicate_provider_transaction_id_rejected(self):
        p1 = wallet.create_payment(1, 10000, provider="kapitalbank", method=wallet.METHOD_ECOMMERCE)
        p2 = wallet.create_payment(2, 10000, provider="kapitalbank", method=wallet.METHOD_ECOMMERCE)

        wallet.register_provider_transaction(p1["payment_id"], "kapitalbank", "TXN123")
        with self.assertRaises(wallet.DuplicateTransactionError) as ctx:
            wallet.register_provider_transaction(p2["payment_id"], "kapitalbank", "TXN123")
        self.assertEqual(ctx.exception.existing_payment_id, p1["payment_id"])

    def test_duplicate_receipt_fingerprint_rejected(self):
        p1 = wallet.create_payment(1, 5000, provider="manual", method=wallet.METHOD_MANUAL_RECEIPT)
        p2 = wallet.create_payment(2, 5000, provider="manual", method=wallet.METHOD_MANUAL_RECEIPT)

        wallet.register_receipt_fingerprint(p1["payment_id"], "file:ABC123")
        with self.assertRaises(wallet.DuplicateReceiptError) as ctx:
            wallet.register_receipt_fingerprint(p2["payment_id"], "file:ABC123")
        self.assertEqual(ctx.exception.existing_payment_id, p1["payment_id"])

    def test_same_fingerprint_same_payment_is_allowed(self):
        p1 = wallet.create_payment(1, 5000, provider="manual", method=wallet.METHOD_MANUAL_RECEIPT)
        wallet.register_receipt_fingerprint(p1["payment_id"], "file:ABC123")
        # O'sha PAYMENT uchun qayta chaqirish (masalan qayta urinish) xato bermasligi kerak
        wallet.register_receipt_fingerprint(p1["payment_id"], "file:ABC123")


class ConcurrencyTests(unittest.TestCase):
    """Race condition himoyasi — bir nechta OS thread PARALLEL ravishda
    wallet.py funksiyalarini chaqiradi (xuddi Telegram'dan tez-tez tugma
    bosilganidek)."""

    def setUp(self):
        _reset_wallet_state()

    def test_concurrent_confirm_same_payment_credits_once(self):
        payment = wallet.create_payment(1, 10000, provider="kapitalbank", method=wallet.METHOD_ECOMMERCE)
        results = []

        def worker():
            results.append(wallet.confirm_payment(payment["payment_id"], source="webhook_retry"))

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for r in results if r), 1)  # FAQAT bittasi muvaffaqiyatli bo'lishi kerak
        self.assertEqual(wallet.get_balance(1), 10000)

    def test_concurrent_debits_never_go_negative(self):
        wallet.credit_balance(1, 10000, "boshlang'ich")
        successes = []
        lock = threading.Lock()

        def worker():
            try:
                wallet.debit_balance(1, 1000, "parallel xarajat")
                with lock:
                    successes.append(True)
            except wallet.InsufficientBalanceError:
                with lock:
                    successes.append(False)

        threads = [threading.Thread(target=worker) for _ in range(50)]  # 50 x 1000 = 50000, balans 10000
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ok_count = sum(1 for s in successes if s)
        self.assertEqual(ok_count, 10)  # faqat 10 marta 1000 dan yecha oladi (10000 / 1000)
        self.assertEqual(wallet.get_balance(1), 0)  # HECH QACHON manfiy bo'lmaydi

    def test_concurrent_feature_charges_respect_balance(self):
        wallet.credit_balance(1, 10000, "boshlang'ich")  # course_work narxi = 10000
        results = []
        lock = threading.Lock()

        def worker():
            r = wallet.charge_for_feature(1, "course_work")
            with lock:
                results.append(r.ok)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for r in results if r), 1)  # faqat BITTASI muvaffaqiyatli
        self.assertEqual(wallet.get_balance(1), 0)


class PaymentProviderTests(unittest.TestCase):
    """Kapitalbank adapterlari SOZLANMAGAN holatda xavfsiz ishlashini
    tekshiradi (real API mavjud bo'lmagani uchun har doim shunday bo'lishi
    kerak, hozircha credentials berilmagan)."""

    def test_ecommerce_provider_reports_not_configured(self):
        provider = payment_providers.KapitalbankPaymentProvider()
        self.assertFalse(provider.is_configured())

    def test_create_order_fails_gracefully_when_not_configured(self):
        provider = payment_providers.KapitalbankPaymentProvider()

        async def _run():
            return await provider.create_order("pay_test123", 10000)

        result = asyncio.run(_run())
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "not_configured")
        self.assertIsNotNone(result.user_message)

    def test_verifier_reports_not_configured(self):
        verifier = payment_providers.KapitalbankTransactionVerifier()

        async def _run():
            return await verifier.verify_transaction({"amount": 10000}, expected_amount=10000)

        result = asyncio.run(_run())
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "not_configured")

    def test_webhook_signature_rejected_without_secret(self):
        provider = payment_providers.KapitalbankPaymentProvider()
        self.assertFalse(provider.verify_webhook_signature({}, b"{}"))


if __name__ == "__main__":
    unittest.main()
