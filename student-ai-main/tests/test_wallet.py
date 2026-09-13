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
        "reservations": {},
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


class ReservationBasicTests(unittest.TestCase):
    """💰 Reservation (hold) tizimining asosiy senariylari."""

    def setUp(self):
        _reset_wallet_state()

    def test_successful_reservation_then_completion_debits_once(self):
        wallet.credit_balance(1, 20000, "boshlang'ich")
        result = wallet.reserve_for_feature(1, "course_work")  # narx 10000
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "reserved")
        self.assertIsNotNone(result.reservation_id)

        # Reservation paytida BALANS o'zgarmaydi, faqat "available" kamayadi.
        self.assertEqual(wallet.get_balance(1), 20000)
        self.assertEqual(wallet.get_available_balance(1), 10000)
        self.assertEqual(wallet.get_reserved_amount(1), 10000)

        ok = wallet.complete_reservation(result.reservation_id)
        self.assertTrue(ok)
        self.assertEqual(wallet.get_balance(1), 10000)
        self.assertEqual(wallet.get_available_balance(1), 10000)
        self.assertEqual(wallet.get_reserved_amount(1), 0)
        self.assertEqual(wallet.get_reservation(result.reservation_id)["status"], wallet.RES_STATUS_COMPLETED)

    def test_failed_service_triggers_automatic_release(self):
        wallet.credit_balance(1, 20000, "boshlang'ich")
        result = wallet.reserve_for_feature(1, "course_work")
        self.assertTrue(result.ok)

        ok = wallet.release_reservation(result.reservation_id, reason="AI xato qildi")
        self.assertTrue(ok)
        # Balans BUTUNLAY tegilmagan (hech qachon yechilmagan edi) — "qaytarish" shart emas.
        self.assertEqual(wallet.get_balance(1), 20000)
        self.assertEqual(wallet.get_available_balance(1), 20000)
        self.assertEqual(wallet.get_reservation(result.reservation_id)["status"], wallet.RES_STATUS_RELEASED)

    def test_insufficient_available_balance_blocks_reservation(self):
        wallet.credit_balance(1, 5000, "boshlang'ich")
        result = wallet.reserve_for_feature(1, "course_work")  # narx 10000
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "insufficient")
        self.assertEqual(result.available, 5000)
        self.assertEqual(wallet.get_balance(1), 5000)  # tegilmagan

    def test_free_feature_does_not_create_reservation(self):
        result = wallet.reserve_for_feature(1, "translate")  # narx 0
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "free")
        self.assertIsNone(result.reservation_id)
        self.assertEqual(len(wallet.list_reservations(user_id=1)), 0)

    def test_expired_reservation_is_auto_released_and_frees_balance(self):
        wallet.credit_balance(1, 20000, "boshlang'ich")
        result = wallet.reserve_for_feature(1, "course_work")
        # Muddatini sun'iy ravishda o'tkazib yuboramiz (orphaned/crash simulyatsiyasi).
        wallet._data["reservations"][result.reservation_id]["expires_at"] = "2000-01-01T00:00:00+00:00"

        # get_available_balance chaqirilganda LAZY tarzda eskirgan reservation avtomatik tozalanadi.
        self.assertEqual(wallet.get_available_balance(1), 20000)
        self.assertEqual(wallet.get_reservation(result.reservation_id)["status"], wallet.RES_STATUS_EXPIRED)
        self.assertEqual(wallet.get_balance(1), 20000)  # balans hech qachon kamaymagan

    def test_duplicate_release_is_idempotent(self):
        wallet.credit_balance(1, 20000, "boshlang'ich")
        result = wallet.reserve_for_feature(1, "course_work")
        first = wallet.release_reservation(result.reservation_id, reason="xato")
        second = wallet.release_reservation(result.reservation_id, reason="qayta urinish")
        third = wallet.release_reservation(result.reservation_id, reason="yana")
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertFalse(third)
        self.assertEqual(wallet.get_balance(1), 20000)

    def test_duplicate_completion_does_not_double_debit(self):
        wallet.credit_balance(1, 20000, "boshlang'ich")
        result = wallet.reserve_for_feature(1, "course_work")
        first = wallet.complete_reservation(result.reservation_id)
        second = wallet.complete_reservation(result.reservation_id)
        third = wallet.complete_reservation(result.reservation_id)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertFalse(third)
        self.assertEqual(wallet.get_balance(1), 10000)  # FAQAT bir marta yechilgan

    def test_cannot_release_already_completed_reservation(self):
        wallet.credit_balance(1, 20000, "boshlang'ich")
        result = wallet.reserve_for_feature(1, "course_work")
        wallet.complete_reservation(result.reservation_id)
        ok = wallet.release_reservation(result.reservation_id, reason="kech qoldi")
        self.assertFalse(ok)
        self.assertEqual(wallet.get_balance(1), 10000)  # o'zgarmagan (ikkinchi marta qaytarilmagan)

    def test_cannot_complete_already_released_reservation(self):
        wallet.credit_balance(1, 20000, "boshlang'ich")
        result = wallet.reserve_for_feature(1, "course_work")
        wallet.release_reservation(result.reservation_id, reason="xato")
        ok = wallet.complete_reservation(result.reservation_id)
        self.assertFalse(ok)
        self.assertEqual(wallet.get_balance(1), 20000)  # yechilmagan


class ReservationConcurrencyTests(unittest.TestCase):
    """🔀 Reservation tizimidagi race condition/crash-recovery himoyasi."""

    def setUp(self):
        _reset_wallet_state()

    def test_concurrent_reservations_respect_available_balance(self):
        wallet.credit_balance(1, 10000, "boshlang'ich")  # course_work narxi = 10000
        results = []
        lock = threading.Lock()

        def worker():
            try:
                r = wallet.reserve_for_feature(1, "course_work")
                with lock:
                    results.append(r.ok)
            except wallet.WalletError:
                with lock:
                    results.append(False)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for r in results if r), 1)  # faqat BITTASI band qila oladi
        self.assertEqual(wallet.get_available_balance(1), 0)
        self.assertEqual(wallet.get_balance(1), 10000)  # hali HECH narsa yechilmagan (faqat band)

    def test_reservation_then_concurrent_debit_never_goes_negative(self):
        """Reservation band qilib turgan summani boshqa (parallel) debit_balance
        chaqiruvi "ko'rmaydi" (chunki debit_balance faqat balance'ga qaraydi) —
        LEKIN balans baribir hech qachon manfiy bo'lib qolmasligi kerak."""
        wallet.credit_balance(1, 10000, "boshlang'ich")
        result = wallet.reserve_for_feature(1, "course_work")  # 10000 band qilindi, available=0
        self.assertTrue(result.ok)

        # Parallel ravishda balansdan to'g'ridan-to'g'ri yechishga urinamiz.
        results = []
        lock = threading.Lock()

        def worker():
            try:
                wallet.debit_balance(1, 5000, "parallel debit")
                with lock:
                    results.append(True)
            except wallet.InsufficientBalanceError:
                with lock:
                    results.append(False)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertGreaterEqual(wallet.get_balance(1), 0)  # HECH QACHON manfiy emas
        # Endi reservation'ni completed qilishga harakat qilamiz — agar balans
        # reservation yaratilgandan keyin boshqa yo'l bilan kamaytirilgan bo'lsa,
        # complete_reservation faqat MAVJUD summani yechadi (manfiyga tushmaydi).
        wallet.complete_reservation(result.reservation_id)
        self.assertGreaterEqual(wallet.get_balance(1), 0)

    def test_crash_recovery_orphaned_reservation_does_not_lock_funds_forever(self):
        """Process reservation yaratgandan keyin 'crash' bo'lgan (complete/
        release HECH QACHON chaqirilmagan) holatni simulyatsiya qiladi —
        muddat o'tgach reservation avtomatik ozod bo'lishi SHART."""
        wallet.credit_balance(1, 10000, "boshlang'ich")
        result = wallet.reserve_for_feature(1, "course_work")
        self.assertEqual(wallet.get_available_balance(1), 0)

        # "Crash" simulyatsiyasi: process hech qachon complete/release chaqirmadi,
        # lekin vaqt o'tdi (expires_at allaqachon o'tgan holatga o'tkazamiz).
        wallet._data["reservations"][result.reservation_id]["expires_at"] = "2000-01-01T00:00:00+00:00"

        freed = wallet.expire_stale_reservations()
        self.assertEqual(freed, 1)
        self.assertEqual(wallet.get_available_balance(1), 10000)  # pul qaytadan ishlatish uchun ochildi
        self.assertEqual(wallet.get_balance(1), 10000)  # va hech qachon yo'qolmagan edi


class DuplicatePaymentApprovalRefundAndConcurrencyTests(unittest.TestCase):
    """💳 Admin tomonidan qo'lda tasdiqlash/refund dublikat himoyasi +
    reservation tizimidagi qo'shimcha race-condition stsenariylari (7-band)."""

    def setUp(self):
        _reset_wallet_state()

    def test_duplicate_manual_payment_approval_credits_once(self):
        payment = wallet.create_payment(5, 20000, provider="manual", method=wallet.METHOD_MANUAL_RECEIPT)
        wallet.mark_manual_review(payment["payment_id"])
        first = wallet.approve_manual_payment(payment["payment_id"], actor_id=999)
        second = wallet.approve_manual_payment(payment["payment_id"], actor_id=999)  # admin ikki marta bossa
        third = wallet.approve_manual_payment(payment["payment_id"], actor_id=1000)  # boshqa admin ham bossa
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertFalse(third)
        self.assertEqual(wallet.get_balance(5), 20000)  # FAQAT bir marta kredit qilingan

    def test_duplicate_refund_via_reservation_release_credits_once(self):
        """Reservation orqali refund/release ikki marta chaqirilsa ham,
        foydalanuvchiga faqat BIR MARTA 'pul qaytariladi' (aslida hech qachon
        yechilmagani uchun balans bir marta ham o'zgarmaydi, lekin muhimi —
        ikkinchi chaqiruv HECH NARSA qilmaydi)."""
        wallet.credit_balance(7, 20000, "boshlang'ich")
        result = wallet.reserve_for_feature(7, "course_work")
        balance_before = wallet.get_balance(7)
        first = wallet.release_reservation(result.reservation_id, reason="AI xato")
        second = wallet.release_reservation(result.reservation_id, reason="qayta urinish")
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(wallet.get_balance(7), balance_before)


    def test_two_concurrent_requests_one_balance_only_one_succeeds(self):
        """Aniq 7-band stsenariysi: balance=10000, narx=10000 — ikkita
        BARAVAR so'rov kelsa, FAQAT BITTASI band qila oladi, ikkinchisi
        'insufficient' oladi."""
        wallet.credit_balance(42, 10000, "boshlang'ich")
        outcomes = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()  # ikkalasi ham AYNAN bir vaqtda urinsin
            r = wallet.reserve_for_feature(42, "course_work")
            with lock:
                outcomes.append(r.reason)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(outcomes), ["insufficient", "reserved"])
        self.assertEqual(wallet.get_available_balance(42), 0)
        self.assertEqual(wallet.get_balance(42), 10000)  # hali yechilmagan

    def test_concurrent_reservation_and_payment_credit(self):
        """Reservation band qilib turgan paytda balansga PARALLEL kredit
        (masalan admin to'lovni tasdiqlashi) kelsa — ikkalasi ham xavfsiz
        birgalikda ishlashi kerak (balans hech qachon buzilmasin)."""
        wallet.credit_balance(50, 10000, "boshlang'ich")
        result = wallet.reserve_for_feature(50, "course_work")  # available=0
        self.assertTrue(result.ok)

        def credit_worker():
            wallet.credit_balance(50, 5000, "admin to'lov tasdiqladi")

        threads = [threading.Thread(target=credit_worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 3 marta +5000 = balans 25000 bo'lishi kerak, reservation hali 10000 band.
        self.assertEqual(wallet.get_balance(50), 25000)
        self.assertEqual(wallet.get_reserved_amount(50), 10000)
        self.assertEqual(wallet.get_available_balance(50), 15000)

        wallet.complete_reservation(result.reservation_id)
        self.assertEqual(wallet.get_balance(50), 15000)

    def test_concurrent_release_only_credits_once(self):
        """Bitta reservation'ni bir nechta thread PARALLEL ravishda release
        qilishga urinsa — faqat BITTASI muvaffaqiyatli bo'lishi kerak."""
        wallet.credit_balance(60, 10000, "boshlang'ich")
        result = wallet.reserve_for_feature(60, "course_work")
        outcomes = []
        lock = threading.Lock()
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()
            ok = wallet.release_reservation(result.reservation_id, reason="parallel release")
            with lock:
                outcomes.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for o in outcomes if o), 1)
        self.assertEqual(wallet.get_balance(60), 10000)

    def test_concurrent_double_finalization_only_debits_once(self):
        """Bitta reservation'ni bir nechta thread PARALLEL ravishda
        complete (finalize) qilishga urinsa — balans FAQAT BIR MARTA
        kamayishi kerak (double debit himoyasi race condition ostida ham)."""
        wallet.credit_balance(70, 10000, "boshlang'ich")
        result = wallet.reserve_for_feature(70, "course_work")
        outcomes = []
        lock = threading.Lock()
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()
            ok = wallet.complete_reservation(result.reservation_id)
            with lock:
                outcomes.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for o in outcomes if o), 1)
        self.assertEqual(wallet.get_balance(70), 0)  # FAQAT bir marta 10000 yechilgan


if __name__ == "__main__":
    unittest.main()
