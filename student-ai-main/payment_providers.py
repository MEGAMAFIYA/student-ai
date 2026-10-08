"""
🏦 TO'LOV PROVIDER ABSTRAKSIYASI — Kapitalbank uchun ADAPTER/INTERFEYS.

JUDA MUHIM: bu faylda Kapitalbank'ning HECH QANDAY haqiqiy API endpoint'i,
so'rov/javob JSON formati, avtorizatsiya sxemasi yoki webhook imzo
algoritmi O'YLAB TOPILMAGAN. Har bir shunday joy aniq "# TODO: ..." deb
belgilangan va rasmiy Kapitalbank hujjatlari kelgandan keyin to'ldirilishi
kerak.

Hozircha bu adapterlar "sozlanmagan" (`is_configured() -> False`) holatda
ishlaydi — bu degani:
  - `KapitalbankPaymentProvider.create_order()` — foydalanuvchiga "hozircha
    bu usul mavjud emas" javobini qaytaradi (xato emas, kutilgan holat).
  - `KapitalbankTransactionVerifier.verify_transaction()` — har doim
    "tekshirib bo'lmadi" (not_configured) natijasini qaytaradi, shuning
    uchun chek har doim manual_review (admin qo'lda tekshirishi)ga tushadi.

Kelajakda haqiqiy credentials/hujjat kelganda FAQAT shu ikkita klass
ICHINI to'ldirish kifoya — qolgan butun tizim (wallet.py, handlers/) hech
qanday o'zgarishsiz ishlayveradi, chunki ular FAQAT shu abstrakt
interfeyslar bilan gaplashadi.
"""

import hashlib
import hmac
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import config

logger = logging.getLogger(__name__)


# ============================================================
# Umumiy natija turlari
# ============================================================

@dataclass
class OrderResult:
    ok: bool
    payment_url: str | None = None      # foydalanuvchi to'lov qilishi uchun havola
    provider_order_id: str | None = None
    error: str | None = None            # ok=False bo'lsa, sabab (foydalanuvchiga ko'rsatish uchun EMAS, faqat log uchun)
    user_message: str | None = None     # ok=False bo'lsa, foydalanuvchiga ko'rsatiladigan xabar


@dataclass
class WebhookEvent:
    ok: bool
    payment_id: str | None = None            # bizning ICHKI payment_id (merchant order reference orqali)
    provider_transaction_id: str | None = None
    status: str | None = None                # "success" | "failed" | "cancelled" | ...
    amount: int | None = None
    raw: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class VerificationResult:
    ok: bool                     # barcha tekshiruvlar muvaffaqiyatli o'tdimi
    reason: str = ""             # "not_configured" | "not_found" | "amount_mismatch" | "already_used" | "ok" | ...
    details: dict = field(default_factory=dict)


# ============================================================
# Abstrakt interfeyslar
# ============================================================

class PaymentProvider(ABC):
    """E-commerce (Telegram ichida to'lov) provayderlari uchun umumiy
    interfeys — kelajakda Kapitalbank'dan tashqari boshqa provayder
    (masalan Payme, Click) qo'shilsa ham shu interfeysga amal qiladi."""

    name: str = "unknown"

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @abstractmethod
    async def create_order(self, payment_id: str, amount: int, currency: str = "UZS") -> OrderResult:
        """Yangi to'lov sessiyasi/buyurtma yaratadi va foydalanuvchi
        to'lashi uchun havola/order ID qaytaradi. `payment_id` — bizning
        ICHKI wallet.py payment_id — provayderga "merchant order
        reference" sifatida yuborilishi kerak, shunda webhook kelganda
        qaysi to'lovga tegishli ekanini aniqlay olamiz."""
        ...

    @abstractmethod
    def verify_webhook_signature(self, headers: dict, raw_body: bytes) -> bool:
        """Webhook so'rovi HAQIQATAN Kapitalbank'dan kelganini tasdiqlaydi
        (imzo/HMAC tekshiruvi). Sozlanmagan holatda HAR DOIM False."""
        ...

    @abstractmethod
    def parse_webhook(self, raw_body: bytes) -> WebhookEvent:
        """Webhook body'sini bizning normallashtirilgan WebhookEvent
        formatiga o'giradi."""
        ...


class BankTransactionVerifier(ABC):
    """Chek orqali yuborilgan ma'lumotlarni bank API'si bilan ANIQLIK
    darajasida tekshirish uchun interfeys (2-usul: Bank/Paynet + chek)."""

    name: str = "unknown"

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @abstractmethod
    async def verify_transaction(self, extracted: dict, expected_amount: int) -> VerificationResult:
        """`extracted` — chekdan AI/OCR orqali ajratilgan ma'lumotlar
        (amount, transaction_id, date, time, sender, receiver, provider).
        Tekshiradi: transaction mavjudmi, summa mos keladimi, qabul
        qiluvchi mos keladimi, sana/vaqt oqilonami, bu tranzaksiya
        ALLAQACHON ishlatilmaganmi, status muvaffaqiyatlimi."""
        ...


# ============================================================
# Kapitalbank — E-COMMERCE adapter (hozircha MOCK/sozlanmagan)
# ============================================================

class KapitalbankPaymentProvider(PaymentProvider):
    name = "kapitalbank"

    def is_configured(self) -> bool:
        return bool(config.KAPITALBANK_API_BASE_URL and config.KAPITALBANK_API_KEY and config.KAPITALBANK_MERCHANT_ID)

    async def create_order(self, payment_id: str, amount: int, currency: str = "UZS") -> OrderResult:
        if not self.is_configured():
            logger.info(
                "🏦 Kapitalbank e-commerce SO'ZLANMAGAN — KAPITALBANK_API_BASE_URL/"
                "API_KEY/MERCHANT_ID kiritilmagan. create_order() chaqirildi, "
                f"lekin real so'rov yuborilmadi (payment_id={payment_id})."
            )
            return OrderResult(
                ok=False,
                error="not_configured",
                user_message=(
                    "🟢 Kapitalbank orqali to'lov hozircha ulanmagan.\n"
                    "Iltimos, boshqa usulni tanlang (🟡 Bank/Paynet orqali o'tkazma "
                    "yoki 🟠 admin qo'lda tekshiruvi)."
                ),
            )

        # TODO(Kapitalbank real API): Bu yerda HAQIQIY HTTP so'rovi bo'lishi
        # kerak — masalan (FAQAT NAMUNA, HAQIQIY EMAS):
        #   POST {KAPITALBANK_API_BASE_URL}/<rasmiy endpoint>
        #   headers: Authorization/imzo (rasmiy hujjatga qarab)
        #   body: {"merchant_id": ..., "amount": amount, "currency": currency,
        #          "merchant_trans_id": payment_id, "return_url": ...}
        # Javobdan `payment_url` (yoki order_id) olinadi.
        #
        # Rasmiy Kapitalbank hujjatlari/credentials kelmaguncha bu qism
        # ATAYLAB ishlamaydi — noto'g'ri/o'ylab topilgan endpoint bilan
        # productionga chiqib ketmasligi uchun.
        logger.error(
            "🏦 Kapitalbank create_order(): API sozlangan ko'rinadi, LEKIN haqiqiy "
            "HTTP integratsiyasi hali YOZILMAGAN (TODO). Rasmiy hujjat kelgach "
            "shu joyni to'ldiring: payment_providers.py > KapitalbankPaymentProvider.create_order()."
        )
        return OrderResult(
            ok=False,
            error="not_implemented",
            user_message="⚠️ Bu to'lov usuli hozircha texnik ishlanmoqda. Iltimos, boshqa usulni tanlang.",
        )

    def verify_webhook_signature(self, headers: dict, raw_body: bytes) -> bool:
        if not config.KAPITALBANK_WEBHOOK_SECRET:
            logger.warning("🏦 KAPITALBANK_WEBHOOK_SECRET sozlanmagan — webhook imzosi tekshirilmaydi (rad etiladi).")
            return False

        # TODO(Kapitalbank real API): haqiqiy imzo algoritmi (HMAC-SHA256
        # bo'lishi mumkin, lekin ANIQ sarlavha nomi/formatini rasmiy
        # hujjatdan tasdiqlash SHART). Quyidagi kod FAQAT NAMUNA sxema —
        # productionda ishlatishdan oldin rasmiy hujjat bilan solishtiring.
        signature = headers.get("X-Kapitalbank-Signature") or headers.get("x-kapitalbank-signature")
        if not signature:
            return False
        expected = hmac.new(
            config.KAPITALBANK_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    def parse_webhook(self, raw_body: bytes) -> WebhookEvent:
        # TODO(Kapitalbank real API): haqiqiy JSON maydon nomlarini rasmiy
        # hujjatga qarab moslashtiring (masalan "merchant_trans_id",
        # "status", "amount" nomlari hozircha TAXMIN — tasdiqlanmagan).
        import json
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            return WebhookEvent(ok=False, error=f"JSON parse xato: {e}")

        payment_id = body.get("merchant_trans_id")
        provider_transaction_id = body.get("transaction_id") or body.get("id")
        status = body.get("status")
        amount = body.get("amount")

        if not payment_id or not provider_transaction_id:
            return WebhookEvent(ok=False, raw=body, error="Majburiy maydonlar yo'q (merchant_trans_id/transaction_id).")

        return WebhookEvent(
            ok=True, payment_id=payment_id, provider_transaction_id=str(provider_transaction_id),
            status=status, amount=amount, raw=body,
        )


# ============================================================
# Kapitalbank — TRANSACTION VERIFIER (hozircha MOCK/sozlanmagan)
# ============================================================

class KapitalbankTransactionVerifier(BankTransactionVerifier):
    name = "kapitalbank"

    def is_configured(self) -> bool:
        return bool(config.KAPITALBANK_API_BASE_URL and config.KAPITALBANK_API_KEY)

    async def verify_transaction(self, extracted: dict, expected_amount: int) -> VerificationResult:
        if not self.is_configured():
            logger.info(
                "🏦 Kapitalbank transaction verifier SOZLANMAGAN — chek AVTOMATIK "
                "tasdiqlanmaydi, manual_review'ga yuboriladi."
            )
            return VerificationResult(ok=False, reason="not_configured")

        # TODO(Kapitalbank real API): bu yerda haqiqiy so'rov bo'lishi kerak
        # — masalan tranzaksiya ID/reference bo'yicha bank tizimidan
        # tasdiqlash so'rash. Rasmiy hujjat/credentials kelmaguncha bu
        # FUNKSIYA ATAYLAB "not_configured" qaytaradi (yuqoridagi shart
        # buni allaqachon qamrab oladi — bu qatorga hech qachon yetmaydi).
        logger.error(
            "🏦 Kapitalbank verify_transaction(): API sozlangan ko'rinadi, LEKIN "
            "haqiqiy tekshiruv integratsiyasi hali YOZILMAGAN (TODO)."
        )
        return VerificationResult(ok=False, reason="not_implemented")


# ============================================================
# Fabrika — handlerlar shu orqali provayderni oladi (kelajakda bir nechta
# provayder qo'shilsa, shu yerga qo'shiladi va handlerlar o'zgarmaydi)
# ============================================================

_kapitalbank_provider = KapitalbankPaymentProvider()
_kapitalbank_verifier = KapitalbankTransactionVerifier()


def get_ecommerce_provider() -> PaymentProvider:
    return _kapitalbank_provider


def get_bank_verifier() -> BankTransactionVerifier:
    return _kapitalbank_verifier
