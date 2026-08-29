"""
💳 ICHKI BALANS VA TO'LOV TIZIMI — yadro moduli (domain logic).

Bu fayl hech qanday Telegram-ga bog'liq kod SAQLAMAYDI — faqat pul bilan
bog'liq BARCHA amallarning yagona, xavfsiz "manba haqiqati" (source of
truth) qismi. Telegram handlerlar (handlers/wallet_ui.py,
handlers/payment_admin.py) faqat shu modulning ochiq funksiyalarini chaqiradi.

ARXITEKTURA — storage.py bilan BIR XIL prinsip:
- config.persist_read/persist_write orqali saqlanadi (Upstash Redis ->
  Neon/Postgres -> GitHub -> mahalliy fayl — config.py'dagi ustuvorlik
  bo'yicha), shuning uchun Upstash/Neon sozlangan bo'lsa deploy/restart'da
  YO'QOLMAYDI.
- Har bir YOZISH amali `_lock` (threading.Lock) bilan himoyalangan va
  DARHOL tashqi saqlashga yoziladi (write-through).
- MUHIM: `_lock` — bu ASYNCIO emas, balki OS-DARAJASIDAGI thread lock.
  Buning ikkita sababi bor: (1) storage.py bilan bir xil pattern —
  ai bot handlerlari sinxron/async aralash chaqirilishi mumkin; (2) bu
  BUYUK ustunlik beradi: kelajakda HTTP webhook serveri (bot.py'dagi
  HealthHandler, ALOHIDA OS thread'da ishlaydi) ham SHU modulni to'g'ridan
  to'g'ri, xavfsiz chaqira oladi — asyncio.Lock buni qila olmas edi.
- Barcha pul miqdorlari — FAQAT INTEGER (so'm). Floating point HECH QACHON
  ishlatilmaydi (spetsifikatsiya talabi).

XAVFSIZLIK / IDEMPOTENCY:
- `confirm_payment()` — YAGONA joy, u orqali balansga pul qo'shiladi (bank
  to'lovlari uchun). U to'liq atomik: agar payment allaqachon "paid" bo'lsa,
  IKKINCHI marta chaqirilganda HECH NARSA qilmaydi (webhook qayta-qayta
  kelsa ham, admin ikki marta "Tasdiqlash" bossa ham xavfsiz).
- `debit_balance()` / `charge_for_feature()` — pullik funksiyadan
  foydalanishda balansni yechadi; lock ichida balans qayta tekshiriladi,
  shuning uchun bir necha tez-tez bosishda balans manfiy bo'lib
  ketolmaydi va ikki marta yechilmaydi (race condition himoyasi).
- Duplicate to'lov himoyasi UCH QATLAMDA: (1) provider_transaction_id
  indeksi (bir xil bank tranzaksiyasi ikki marta ishlatilmaydi), (2) chek
  fayl fingerprint (file_unique_id) indeksi, (3) chekdan chiqarilgan
  reference/transaction ID matn indeksi.
"""

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import config

logger = logging.getLogger(__name__)

_DATA_FILENAME = "wallet_data.json"
_UPSTASH_KEY = "student_ai_wallet_data"

_lock = threading.Lock()

# ------------------------------------------------------------------
# Statuslar / turlar (spetsifikatsiyaga mos)
# ------------------------------------------------------------------
STATUS_PENDING = "pending"
STATUS_PAID = "paid"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
STATUS_MANUAL_REVIEW = "manual_review"
STATUS_REJECTED = "rejected"
STATUS_SUSPICIOUS = "suspicious"

# Admin panelidagi 4 ta bo'lim shu statuslarni guruhlaydi:
STATUS_GROUP_UNCHECKED = (STATUS_PENDING, STATUS_MANUAL_REVIEW)
STATUS_GROUP_APPROVED = (STATUS_PAID,)
STATUS_GROUP_REJECTED = (STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_EXPIRED)
STATUS_GROUP_SUSPICIOUS = (STATUS_SUSPICIOUS,)

METHOD_ECOMMERCE = "kapitalbank_ecommerce"
METHOD_BANK_RECEIPT = "bank_receipt"          # 2-usul: chek + avtomatik tekshirish urinishi
METHOD_MANUAL_RECEIPT = "manual_receipt"      # 3-usul: to'g'ridan-to'g'ri admin qo'lda tekshiradi

TX_TOPUP = "topup"                # balans to'ldirish (kredit)
TX_FEATURE_CHARGE = "feature_charge"  # pullik funksiya (debet)
TX_REFUND = "refund"              # qaytarish (kredit)
TX_ADMIN_ADJUST = "admin_adjust"  # admin qo'lda tuzatishi (kredit yoki debet)

# ------------------------------------------------------------------
# 🔒 Reservation (hold) statuslari — pullik funksiya ISHGA TUSHIRILGANDA
# pul DARHOL yechilmaydi, buning o'rniga "band qilinadi" (reserved).
# Xizmat MUVAFFAQIYATLI tugasagina reservation -> COMPLETED (shu paytda
# balansdan HAQIQIY yechiladi). Xizmat xato qilsa yoki bekor qilinsa
# -> RELEASED. Foydalanuvchi/process javob bermay qolsa (masalan process
# crash bo'lsa) -> muddati o'tgach avtomatik EXPIRED (orphaned reservation
# BALANSGA umuman ta'sir qilmasdan turadi, chunki balans reservation
# yaratilganda emas, faqat COMPLETED bo'lganda kamayadi — shuning uchun
# "band qilib qo'yilgan pul yo'qolib qolishi" fizik jihatdan mumkin emas).
# ------------------------------------------------------------------
RES_STATUS_RESERVED = "reserved"
RES_STATUS_COMPLETED = "completed"
RES_STATUS_RELEASED = "released"
RES_STATUS_EXPIRED = "expired"

RESERVATION_ACTIVE_STATUSES = (RES_STATUS_RESERVED,)
RESERVATION_TERMINAL_STATUSES = (RES_STATUS_COMPLETED, RES_STATUS_RELEASED, RES_STATUS_EXPIRED)

# Reservation necha soniyadan keyin "orphaned" deb hisoblanib avtomatik
# bekor qilinadi (crash recovery). Oddiy funksiyalar bir necha daqiqada
# tugaydi — 20 daqiqa katta xavfsizlik zahirasi (uzoq davom etuvchi kurs
# ishi generatsiyasi ham shu ichiga sig'adi).
RESERVATION_TTL_SECONDS = 20 * 60


# ------------------------------------------------------------------
# Xatolar
# ------------------------------------------------------------------

class WalletError(Exception):
    pass


class InsufficientBalanceError(WalletError):
    def __init__(self, required: int, available: int):
        self.required = required
        self.available = available
        super().__init__(f"Balans yetarli emas: kerak={required}, mavjud={available}")


class DuplicateTransactionError(WalletError):
    """Bir xil provider_transaction_id boshqa to'lovda allaqachon ishlatilgan."""
    def __init__(self, existing_payment_id: str):
        self.existing_payment_id = existing_payment_id
        super().__init__(f"Bu tranzaksiya allaqachon ishlatilgan (payment_id={existing_payment_id}).")


class DuplicateReceiptError(WalletError):
    """Bir xil chek (fayl yoki reference raqami) allaqachon yuborilgan."""
    def __init__(self, existing_payment_id: str):
        self.existing_payment_id = existing_payment_id
        super().__init__(f"Bu chek allaqachon ishlatilgan (payment_id={existing_payment_id}).")


class ReservationError(WalletError):
    pass


# ------------------------------------------------------------------
# Standart pullik funksiyalar ro'yxati (birinchi ishga tushirilganda
# yaraladi — keyin /developer panelidan o'zgartiriladi va shu holicha
# doimiy saqlanadi, bu yerdagi qiymatlar faqat BOSHLANG'ICH standart).
# ------------------------------------------------------------------
_DEFAULT_FEATURES = {
    "course_work": {"name": "📘 Kurs ishi", "price": 10000, "enabled": True, "description": "Kurs ishi/loyiha generatsiyasi"},
    "essay": {"name": "🗒 Referat/Insho", "price": 5000, "enabled": True, "description": "Referat/Insho generatsiyasi"},
    "translate": {"name": "🌐 Tarjima", "price": 0, "enabled": True, "description": "Matn/hujjat tarjimasi"},
    "pptx": {"name": "📊 Taqdimot (PPTX)", "price": 7000, "enabled": True, "description": "PowerPoint taqdimot generatsiyasi"},
    "quiz": {"name": "📋 Test/Viktorina", "price": 0, "enabled": True, "description": "Test/viktorina generatsiyasi"},
    "solve": {"name": "🧮 Masala yechish", "price": 0, "enabled": True, "description": "Masala/misol yechish"},
    "summarize": {"name": "📑 Konspekt qisqartirish", "price": 0, "enabled": True, "description": "Matn/PDF qisqartirish"},
    "grammar": {"name": "✅ Imlo tekshirish", "price": 0, "enabled": True, "description": "Imlo/grammatika tekshiruvi"},
    "citation": {"name": "📚 Iqtibos generatori", "price": 0, "enabled": True, "description": "Iqtibos generatsiyasi"},
    "images_pdf": {"name": "🖼 Suratlarni PDF qilish", "price": 0, "enabled": True, "description": "Suratlardan PDF yasash"},
    "edit_pdf": {"name": "📝 PDF ni tahrirlash", "price": 0, "enabled": True, "description": "PDF hujjatni tahrirlash"},
    "guide": {"name": "📖 Qo'llanma tayyorlash", "price": 0, "enabled": True, "description": "Savol-javob qo'llanma generatsiyasi"},
}

_DEFAULT_DATA = {
    "wallets": {},              # {"<user_id>": {"balance": int}}
    "transactions": [],         # ko'rilsin: _new_tx()
    "payments": {},             # {"<payment_id>": {...}}
    "provider_tx_index": {},    # {"<provider>:<provider_transaction_id>": "<payment_id>"}
    "receipt_fingerprints": {}, # {"<fingerprint>": "<payment_id>"}
    "features": dict(_DEFAULT_FEATURES),
    "audit_log": [],
    "reservations": {},         # {"<reservation_id>": {...}} — ko'rilsin: reserve_balance()
}

MAX_TRANSACTIONS_PER_USER_SHOWN = 30   # "🧾 To'lovlar tarixi"da ko'rsatiladigan maksimal son
MAX_AUDIT_LOG_ENTRIES = 2000           # umumiy audit log chegarasi (eskilari siqiladi)
MAX_TRANSACTIONS_TOTAL = 5000          # umumiy transactions chegarasi (eskilari siqiladi)
MAX_RESERVATIONS_TOTAL = 5000          # umumiy reservations chegarasi (eskilari — faqat TERMINAL holatdagilar — siqiladi)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _iso_plus_seconds(iso_str: str, seconds: int) -> str:
    dt = datetime.fromisoformat(iso_str)
    return (dt + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _load() -> dict:
    raw, source = config.persist_read(_DATA_FILENAME, _UPSTASH_KEY)
    if not raw:
        data = {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in _DEFAULT_DATA.items()}
        data["features"] = {k: dict(v) for k, v in _DEFAULT_FEATURES.items()}
        return data

    try:
        import json
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"wallet_data JSON parse xato: {e} — bo'sh holatdan boshlanadi.")
        data = {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in _DEFAULT_DATA.items()}
        data["features"] = {k: dict(v) for k, v in _DEFAULT_FEATURES.items()}
        return data

    for k, v in _DEFAULT_DATA.items():
        data.setdefault(k, dict(v) if isinstance(v, dict) else list(v))
    # Yangi qo'shilgan standart feature'lar (kod yangilanganda) mavjud
    # ma'lumotlarga zarar YETKAZMASDAN qo'shiladi — admin oldin o'zgartirgan
    # narxlarga TEGILMAYDI.
    for fid, fdef in _DEFAULT_FEATURES.items():
        data["features"].setdefault(fid, dict(fdef))
    logger.info(f"{source} dan wallet (balans/to'lov) ma'lumotlari yuklandi.")
    return data


_data = _load()


def _save() -> None:
    import json
    raw = json.dumps(_data, ensure_ascii=False)
    config.persist_write(_DATA_FILENAME, _UPSTASH_KEY, raw, commit_message="💳 Wallet (balans/to'lov) ma'lumotlari yangilandi")


# ============================================================
# 📝 Audit log
# ============================================================

def _log_audit_locked(event: str, actor_id=None, payment_id: str | None = None,
                       user_id=None, amount: int | None = None, details: str = "") -> None:
    """_lock ALLAQACHON ushlangan holatda chaqirilishi kerak (ichki yordamchi)."""
    entry = {
        "id": _new_id("audit"),
        "event": event,
        "actor_id": actor_id,
        "payment_id": payment_id,
        "user_id": user_id,
        "amount": amount,
        "details": details[:500] if details else "",
        "created_at": _now_iso(),
    }
    _data["audit_log"].append(entry)
    if len(_data["audit_log"]) > MAX_AUDIT_LOG_ENTRIES:
        del _data["audit_log"][: len(_data["audit_log"]) - MAX_AUDIT_LOG_ENTRIES]


def log_audit(event: str, actor_id=None, payment_id: str | None = None,
              user_id=None, amount: int | None = None, details: str = "") -> None:
    with _lock:
        _log_audit_locked(event, actor_id, payment_id, user_id, amount, details)
        _save()


def get_audit_log(limit: int = 100) -> list:
    return list(reversed(_data["audit_log"]))[:limit]


# ============================================================
# 💰 Balans / wallet
# ============================================================

def get_balance(user_id: int) -> int:
    return int(_data["wallets"].get(str(user_id), {}).get("balance", 0))


def _new_tx_locked(user_id: int, tx_type: str, amount: int, balance_before: int,
                    balance_after: int, status: str = "completed", description: str = "",
                    related_payment_id: str | None = None) -> dict:
    tx = {
        "id": _new_id("tx"),
        "user_id": int(user_id),
        "type": tx_type,
        "amount": int(amount),
        "balance_before": int(balance_before),
        "balance_after": int(balance_after),
        "status": status,
        "description": description[:300],
        "related_payment_id": related_payment_id,
        "created_at": _now_iso(),
    }
    _data["transactions"].append(tx)
    if len(_data["transactions"]) > MAX_TRANSACTIONS_TOTAL:
        del _data["transactions"][: len(_data["transactions"]) - MAX_TRANSACTIONS_TOTAL]
    return tx


def credit_balance(user_id: int, amount: int, description: str,
                    tx_type: str = TX_TOPUP, related_payment_id: str | None = None,
                    actor_id=None) -> dict:
    """Balansga pul QO'SHADI (masalan tasdiqlangan to'lovdan keyin, yoki
    qaytarish/refund). amount albatta MUSBAT butun son bo'lishi kerak."""
    amount = int(amount)
    if amount <= 0:
        raise WalletError("Kredit summasi musbat butun son bo'lishi kerak.")

    with _lock:
        key = str(user_id)
        wallet = _data["wallets"].setdefault(key, {"balance": 0})
        before = int(wallet["balance"])
        after = before + amount
        wallet["balance"] = after
        tx = _new_tx_locked(user_id, tx_type, amount, before, after, description=description,
                             related_payment_id=related_payment_id)
        _log_audit_locked(
            "BALANCE_CREDIT", actor_id=actor_id, payment_id=related_payment_id,
            user_id=user_id, amount=amount, details=description,
        )
        _save()
    logger.info(f"💰 Balans OSHDI: user_id={user_id}, +{amount}, {before}->{after} ({description}).")
    return tx


def debit_balance(user_id: int, amount: int, description: str,
                   tx_type: str = TX_FEATURE_CHARGE) -> dict:
    """Balansdan pul YECHADI. Balans YETARLI bo'lmasa InsufficientBalanceError
    ko'taradi (balans HECH QACHON manfiy bo'lmaydi). Butun amal `_lock` ichida
    — shuning uchun bir foydalanuvchi tugmani tez-tez bossa ham (race
    condition) balans ikki marta yechilmaydi va manfiy bo'lib ketmaydi."""
    amount = int(amount)
    if amount <= 0:
        raise WalletError("Debet summasi musbat butun son bo'lishi kerak.")

    with _lock:
        key = str(user_id)
        wallet = _data["wallets"].setdefault(key, {"balance": 0})
        before = int(wallet["balance"])
        if before < amount:
            raise InsufficientBalanceError(required=amount, available=before)
        after = before - amount
        wallet["balance"] = after
        tx = _new_tx_locked(user_id, tx_type, -amount, before, after, description=description)
        _log_audit_locked("BALANCE_DEBIT", user_id=user_id, amount=amount, details=description)
        _save()
    logger.info(f"💰 Balans KAMAYDI: user_id={user_id}, -{amount}, {before}->{after} ({description}).")
    return tx


def get_transactions(user_id: int, limit: int = MAX_TRANSACTIONS_PER_USER_SHOWN) -> list:
    """Eng yangisi birinchi bo'lgan tartibda, faqat shu user_id uchun."""
    uid = int(user_id)
    rows = [t for t in _data["transactions"] if t["user_id"] == uid]
    return list(reversed(rows))[:limit]


def list_wallets(limit: int = 25) -> list[tuple[str, int]]:
    """Admin panelidagi '💰 Balanslar' bo'limi uchun — eng yuqori balansli
    foydalanuvchilar ro'yxati (user_id, balance) juftliklari."""
    rows = [(uid, w.get("balance", 0)) for uid, w in _data["wallets"].items()]
    rows.sort(key=lambda kv: kv[1], reverse=True)
    return rows[:limit]


# ============================================================
# ⚙️ Pullik funksiyalar (feature pricing)
# ============================================================

def get_feature(feature_id: str) -> dict | None:
    f = _data["features"].get(feature_id)
    return dict(f) if f else None


def list_features() -> list[tuple[str, dict]]:
    return [(fid, dict(f)) for fid, f in _data["features"].items()]


def ensure_feature(feature_id: str, name: str, default_price: int = 0, description: str = "") -> None:
    """Agar feature hali ro'yxatda bo'lmasa, standart qiymat bilan qo'shadi
    (mavjud bo'lsa TEGILMAYDI — admin narxini saqlab qoladi)."""
    with _lock:
        if feature_id not in _data["features"]:
            _data["features"][feature_id] = {
                "name": name, "price": int(default_price), "enabled": True, "description": description,
            }
            _save()


def set_feature_price(feature_id: str, price: int, actor_id=None) -> bool:
    price = int(price)
    if price < 0:
        raise WalletError("Narx manfiy bo'lishi mumkin emas.")
    with _lock:
        f = _data["features"].get(feature_id)
        if not f:
            return False
        old_price = f.get("price")
        f["price"] = price
        _log_audit_locked("PRICE_CHANGED", actor_id=actor_id, details=f"{feature_id}: {old_price} -> {price}")
        _save()
    logger.info(f"⚙️ Funksiya narxi o'zgartirildi: {feature_id}: {old_price} -> {price} (admin={actor_id}).")
    return True


def set_feature_enabled(feature_id: str, enabled: bool, actor_id=None) -> bool:
    with _lock:
        f = _data["features"].get(feature_id)
        if not f:
            return False
        f["enabled"] = bool(enabled)
        _log_audit_locked(
            "FEATURE_ENABLED" if enabled else "FEATURE_DISABLED",
            actor_id=actor_id, details=feature_id,
        )
        _save()
    return True


class ChargeResult:
    """charge_for_feature() natijasi — handler shu orqali foydalanuvchiga
    aniq xabar chiqaradi (balans yetmasa "kerak/mavjud" summalarini ham)."""
    def __init__(self, ok: bool, reason: str, price: int = 0, balance: int = 0, tx: dict | None = None):
        self.ok = ok
        self.reason = reason  # "free" | "charged" | "insufficient" | "disabled" | "unknown_feature"
        self.price = price
        self.balance = balance
        self.tx = tx


def charge_for_feature(user_id: int, feature_id: str) -> ChargeResult:
    """Pullik funksiya ISHGA TUSHIRILGANDA (menyu tugmasi bosilganda)
    chaqiriladigan YAGONA kirish nuqtasi. Butun tekshiruv+yechish `_lock`
    ichida ATOMIK bajariladi (debit_balance orqali) — shuning uchun bitta
    tugmani tez-tez bosish orqali bir necha marta pul yechilib ketmaydi."""
    feature = _data["features"].get(feature_id)
    if not feature:
        logger.warning(f"⚙️ Noma'lum feature_id uchun to'lov so'raldi: '{feature_id}' — bepul deb hisoblanadi.")
        return ChargeResult(ok=True, reason="unknown_feature", price=0, balance=get_balance(user_id))

    if not feature.get("enabled", True):
        return ChargeResult(ok=False, reason="disabled", price=feature.get("price", 0), balance=get_balance(user_id))

    price = int(feature.get("price", 0))
    if price <= 0:
        return ChargeResult(ok=True, reason="free", price=0, balance=get_balance(user_id))

    try:
        tx = debit_balance(user_id, price, description=f"Funksiya: {feature.get('name', feature_id)}")
    except InsufficientBalanceError as e:
        return ChargeResult(ok=False, reason="insufficient", price=price, balance=e.available)

    return ChargeResult(ok=True, reason="charged", price=price, balance=tx["balance_after"], tx=tx)


def refund(user_id: int, amount: int, description: str, related_payment_id: str | None = None, actor_id=None) -> dict:
    """Foydalanuvchiga pulni qaytaradi (masalan xizmat muvaffaqiyatsiz
    yakunlansa, admin panelidan qo'lda, yoki noto'g'ri yechilgan holatda).

    MUHIM: agar to'lov ALLAQACHON reservation (hold) orqali amalga
    oshirilgan bo'lsa (ya'ni pul HALI balansdan yechilmagan holatda —
    status='reserved'), bu funksiya EMAS, balki release_reservation()
    ishlatilishi kerak (chunki reserved pul balansdan умуман
    kamaytirilmagan — uni "qaytarish" DUBLIKAT kredit bo'lib qoladi).
    refund() faqat HAQIQATAN balansdan yechilgan (completed reservation
    yoki eski to'g'ridan-to'g'ri debit_balance) pul uchun ishlatiladi."""
    tx = credit_balance(user_id, amount, description, tx_type=TX_REFUND,
                         related_payment_id=related_payment_id, actor_id=actor_id)
    log_audit("PAYMENT_REFUNDED" if related_payment_id else "BALANCE_CREDIT",
              actor_id=actor_id, payment_id=related_payment_id, user_id=user_id,
              amount=amount, details=description)
    return tx


# ============================================================
# 🔒 Reservation (hold) tizimi — pullik funksiya ISHGA TUSHIRILGANDA
# ============================================================
# ARXITEKTURA: `debit_balance()`/`charge_for_feature()` YUQORIDA hamon
# mavjud (eski, orqaga-mos/test uchun saqlangan) — LEKIN endi
# `handlers/wallet_ui.py`dagi "to'lov devori" (`require_payment`) ULARNI
# EMAS, balki quyidagi `reserve_for_feature()` funksiyasini chaqiradi.
#
# G'OYA: balans (`wallet["balance"]`) reservation yaratilganda UMUMAN
# o'zgarmaydi — faqat "band qilingan" (reserved) summa alohida
# hisoblanadi (barcha 'reserved' holatidagi reservation'lar yig'indisi).
# "Mavjud (available) balans" = balance - band_qilingan. Shu tufayli:
#   - reservation orphan (crash) bo'lib qolsa ham, balans HECH QACHON
#     yo'qolmaydi/buzilmaydi — u faqat vaqtinchalik "band" edi.
#   - reservation COMPLETED bo'lgandagina balans HAQIQATAN kamayadi
#     (debit_balance bilan bir xil atomik/manfiy-bo'lmaslik kafolati).
#   - reservation RELEASED/EXPIRED bo'lsa — balansga hech narsa
#     "qaytarilmaydi", chunki u hech qachon olinmagan edi.


class ReservationResult:
    """reserve_for_feature() natijasi — handler shu orqali foydalanuvchiga
    aniq xabar chiqaradi. `reason`: "free" | "reserved" | "insufficient" |
    "disabled" | "unknown_feature"."""
    def __init__(self, ok: bool, reason: str, price: int = 0, balance: int = 0,
                 available: int = 0, reservation_id: str | None = None, feature_id: str = ""):
        self.ok = ok
        self.reason = reason
        self.price = price
        self.balance = balance
        self.available = available
        self.reservation_id = reservation_id
        self.feature_id = feature_id


def _expire_stale_reservations_locked() -> int:
    """`_lock` ALLAQACHON ushlangan holatda chaqirilishi kerak. Muddati
    o'tgan ('reserved' holatida turgan, lekin expires_at o'tib ketgan)
    barcha reservation'larni 'expired'ga o'tkazadi (CRASH RECOVERY —
    process qayerdadir to'xtab qolgan/crash bo'lgan taqdirda ham,
    orphaned reservation abadiy "band" bo'lib qolmaydi). Nechta yozuv
    o'zgartirilganini qaytaradi (0 bo'lsa chaqiruvchi _save() qilmasligi
    mumkin — keraksiz yozishning oldini olish uchun)."""
    now = _now_iso()
    changed = 0
    for r in _data["reservations"].values():
        if r["status"] == RES_STATUS_RESERVED and r.get("expires_at") and r["expires_at"] < now:
            r["status"] = RES_STATUS_EXPIRED
            r["completed_at"] = now
            _log_audit_locked(
                "RESERVATION_EXPIRED", user_id=r["user_id"], amount=r["amount"],
                details=f"reservation_id={r['reservation_id']}, feature_id={r['feature_id']} "
                        f"(orphaned/crash — avtomatik bekor qilindi, balansga ta'sir qilmadi)",
            )
            changed += 1
    if changed and len(_data["reservations"]) > MAX_RESERVATIONS_TOTAL:
        # Faqat TERMINAL holatdagi (endi kerak bo'lmaydigan) eng eski
        # yozuvlarni siqamiz — hech qachon 'reserved' (faol) yozuvni EMAS.
        terminal_ids_sorted = sorted(
            (rid for rid, r in _data["reservations"].items() if r["status"] in RESERVATION_TERMINAL_STATUSES),
            key=lambda rid: _data["reservations"][rid]["created_at"],
        )
        overflow = len(_data["reservations"]) - MAX_RESERVATIONS_TOTAL
        for rid in terminal_ids_sorted[:max(overflow, 0)]:
            del _data["reservations"][rid]
    return changed


def expire_stale_reservations() -> int:
    """Tashqi (masalan developer panelidan qo'lda, yoki davriy job orqali)
    chaqirilishi mumkin bo'lgan ochiq funksiya — orphaned reservation'larni
    tozalaydi. Odatiy holatda buni qo'lda chaqirish SHART emas, chunki
    barcha o'qish funksiyalari (get_available_balance, list_reservations,
    reserve_balance...) buni ICHKARIDA avtomatik (lazy) bajaradi."""
    with _lock:
        changed = _expire_stale_reservations_locked()
        if changed:
            _save()
    return changed


def get_reserved_amount(user_id: int) -> int:
    """Foydalanuvchining HOZIR 'reserved' (band) holatidagi barcha
    reservation'lari yig'indisi."""
    uid = int(user_id)
    with _lock:
        if _expire_stale_reservations_locked():
            _save()
        return sum(
            r["amount"] for r in _data["reservations"].values()
            if r["user_id"] == uid and r["status"] == RES_STATUS_RESERVED
        )


def get_available_balance(user_id: int) -> int:
    """Foydalanuvchi HOZIR ishlata oladigan balans (umumiy balans MINUS
    band qilingan summalar). Yangi reservation shu summadan oshib
    ketolmaydi — bu 'available balance = 10000' misolidagi aniq
    tushuncha."""
    return get_balance(user_id) - get_reserved_amount(user_id)


def get_reservation(reservation_id: str) -> dict | None:
    r = _data["reservations"].get(reservation_id)
    return dict(r) if r else None


def list_reservations(status: str | tuple | None = None, user_id: int | None = None) -> list[dict]:
    with _lock:
        if _expire_stale_reservations_locked():
            _save()
        rows = list(_data["reservations"].values())
    if status:
        statuses = (status,) if isinstance(status, str) else tuple(status)
        rows = [r for r in rows if r["status"] in statuses]
    if user_id is not None:
        rows = [r for r in rows if r["user_id"] == int(user_id)]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return [dict(r) for r in rows]


def reserve_balance(user_id: int, feature_id: str, amount: int,
                     ttl_seconds: int = RESERVATION_TTL_SECONDS) -> dict:
    """Foydalanuvchi balansidan `amount`ni ATOMIK ravishda "band qiladi"
    (hali yechmaydi). Balans YETARLI (available >= amount) bo'lmasa
    InsufficientBalanceError ko'taradi — bu tekshiruv va yozish BITTA
    `_lock` ichida bo'lgani uchun, bir nechta parallel so'rov kelsa ham
    (race condition) umumiy band qilingan summa hech qachon balansdan
    oshib ketmaydi (ya'ni "available" hech qachon manfiy bo'lmaydi)."""
    amount = int(amount)
    if amount <= 0:
        raise WalletError("Reservation summasi musbat butun son bo'lishi kerak.")

    with _lock:
        _expire_stale_reservations_locked()  # har doim quyidagi hisobdan OLDIN — eskirganlar hisobga kirmasin

        uid = int(user_id)
        balance = int(_data["wallets"].get(str(uid), {}).get("balance", 0))
        reserved = sum(
            r["amount"] for r in _data["reservations"].values()
            if r["user_id"] == uid and r["status"] == RES_STATUS_RESERVED
        )
        available = balance - reserved
        if available < amount:
            _save()  # yuqoridagi expire natijasi bo'lsa ham saqlanib qolsin
            raise InsufficientBalanceError(required=amount, available=available)

        reservation_id = _new_id("res")
        now = _now_iso()
        reservation = {
            "reservation_id": reservation_id,
            "user_id": uid,
            "feature_id": feature_id,
            "amount": amount,
            "status": RES_STATUS_RESERVED,
            "created_at": now,
            "expires_at": _iso_plus_seconds(now, ttl_seconds),
            "completed_at": None,
        }
        _data["reservations"][reservation_id] = reservation
        _log_audit_locked(
            "RESERVATION_CREATED", user_id=uid, amount=amount,
            details=f"reservation_id={reservation_id}, feature_id={feature_id}",
        )
        _save()
    logger.info(f"🔒 Reservation yaratildi: reservation_id={reservation_id}, user_id={uid}, "
                f"feature={feature_id}, amount={amount}.")
    return dict(reservation)


def complete_reservation(reservation_id: str, actor_id=None) -> bool:
    """💚 Xizmat MUVAFFAQIYATLI yakunlanganda chaqiriladi — band qilingan
    summa endi HAQIQATAN balansdan yechiladi (bir marta, atomik).
    TO'LIQ IDEMPOTENT: reservation allaqachon 'completed' bo'lsa —
    hech narsa qilmaydi (ikkinchi marta debit QILINMAYDI), False qaytaradi.
    'released'/'expired' (o'lik) reservationni ham completed qilib
    bo'lmaydi (False)."""
    with _lock:
        r = _data["reservations"].get(reservation_id)
        if not r:
            logger.warning(f"🔒 complete_reservation: topilmadi, reservation_id={reservation_id}.")
            return False

        if r["status"] == RES_STATUS_COMPLETED:
            logger.info(f"🔒 complete_reservation: ALLAQACHON completed (idempotent no-op), reservation_id={reservation_id}.")
            _log_audit_locked(
                "RESERVATION_COMPLETE_DUPLICATE_IGNORED", actor_id=actor_id,
                user_id=r["user_id"], amount=r["amount"], details=f"reservation_id={reservation_id}",
            )
            _save()
            return False

        if r["status"] in (RES_STATUS_RELEASED, RES_STATUS_EXPIRED):
            logger.warning(
                f"🔒 complete_reservation: o'lik holatdagi ({r['status']}) reservationni "
                f"tugatib bo'lmaydi, reservation_id={reservation_id}."
            )
            return False

        user_id = r["user_id"]
        amount = r["amount"]
        wallet_row = _data["wallets"].setdefault(str(user_id), {"balance": 0})
        before = int(wallet_row["balance"])

        if before < amount:
            # ANOMALIYA: nazariy jihatdan bo'lmasligi kerak (chunki
            # reserve_balance() available'ni oldindan tekshirgan edi) —
            # lekin masalan admin reservation yaratilgandan KEYIN balansni
            # qo'lda kamaytirgan bo'lishi mumkin. Balans HECH QACHON
            # manfiy bo'lmasligi (spetsifikatsiya talabi) ustuvor bo'lgani
            # uchun faqat MAVJUD summa yechiladi va bu holat AUDITga aniq
            # yoziladi (moliyaviy nomuvofiqlikni keyin ko'rish uchun).
            logger.error(
                f"🔒⚠️ ANOMALIYA: reservation_id={reservation_id} uchun band qilingan summa "
                f"({amount}) joriy balansdan ({before}) katta — faqat mavjud qism yechiladi. "
                f"Buning sababi tashqi/qo'lda balans o'zgarishi bo'lishi mumkin."
            )
            actual_charge = before
            after = 0
            _log_audit_locked(
                "RESERVATION_COMPLETE_BALANCE_ANOMALY", actor_id=actor_id, user_id=user_id,
                amount=amount, details=f"reservation_id={reservation_id}: kerak={amount}, mavjud={before}",
            )
        else:
            actual_charge = amount
            after = before - amount

        wallet_row["balance"] = after
        r["status"] = RES_STATUS_COMPLETED
        r["completed_at"] = _now_iso()
        feature = _data["features"].get(r["feature_id"])
        feature_name = feature["name"] if feature else r["feature_id"]
        _new_tx_locked(
            user_id, TX_FEATURE_CHARGE, -actual_charge, before, after,
            description=f"Funksiya: {feature_name}", related_payment_id=None,
        )
        _log_audit_locked(
            "RESERVATION_COMPLETED", actor_id=actor_id, user_id=user_id, amount=actual_charge,
            details=f"reservation_id={reservation_id}, feature_id={r['feature_id']}",
        )
        _save()
    logger.info(f"🔒✅ Reservation COMPLETED: reservation_id={reservation_id}, user_id={user_id}, "
                f"amount={amount}, yangi balans={after}.")
    return True


def release_reservation(reservation_id: str, reason: str = "", actor_id=None) -> bool:
    """❌ Xizmat MUVAFFAQIYATSIZ bo'lganda (yoki bekor qilinganda) chaqiriladi
    — band qilingan summa foydalanuvchiga "qaytariladi" (aslida balansga
    HECH QACHON tegilmagani uchun, shunchaki bandlik OLIB TASHLANADI).
    TO'LIQ IDEMPOTENT: allaqachon released/expired bo'lsa — hech narsa
    qilmaydi (ikkinchi marta "qaytarish" YO'Q — duplicate release himoyasi),
    False qaytaradi. Allaqachon 'completed' (pul allaqachon yechilgan)
    reservationni "release" qilib bo'lmaydi — bunday holatda `refund()`
    ishlatilishi kerak."""
    with _lock:
        r = _data["reservations"].get(reservation_id)
        if not r:
            logger.warning(f"🔒 release_reservation: topilmadi, reservation_id={reservation_id}.")
            return False

        if r["status"] in (RES_STATUS_RELEASED, RES_STATUS_EXPIRED):
            logger.info(f"🔒 release_reservation: ALLAQACHON {r['status']} (idempotent no-op), reservation_id={reservation_id}.")
            _log_audit_locked(
                "RESERVATION_RELEASE_DUPLICATE_IGNORED", actor_id=actor_id,
                user_id=r["user_id"], amount=r["amount"], details=f"reservation_id={reservation_id}",
            )
            _save()
            return False

        if r["status"] == RES_STATUS_COMPLETED:
            logger.warning(
                f"🔒 release_reservation: allaqachon COMPLETED (pul yechilgan) reservationni "
                f"release qilib bo'lmaydi — refund() kerak. reservation_id={reservation_id}."
            )
            return False

        r["status"] = RES_STATUS_RELEASED
        r["completed_at"] = _now_iso()
        _log_audit_locked(
            "RESERVATION_RELEASED", actor_id=actor_id, user_id=r["user_id"], amount=r["amount"],
            details=f"reservation_id={reservation_id}, feature_id={r['feature_id']}. {reason}",
        )
        _save()
    logger.info(f"🔒↩️ Reservation RELEASED: reservation_id={reservation_id}, user_id={r['user_id']}, "
                f"amount={r['amount']}, sabab={reason}.")
    return True


def reserve_for_feature(user_id: int, feature_id: str) -> ReservationResult:
    """Pullik funksiya ISHGA TUSHIRILGANDA (menyu tugmasi bosilganda)
    chaqiriladigan YAGONA kirish nuqtasi (`charge_for_feature()`ning
    reservation-asosidagi vorisi — `handlers/wallet_ui.py > require_payment`
    endi shuni chaqiradi). Bepul funksiya (price<=0) uchun reservation
    UMUMAN yaratilmaydi (spetsifikatsiya: 6-band)."""
    feature = _data["features"].get(feature_id)
    if not feature:
        logger.warning(f"⚙️ Noma'lum feature_id uchun reservation so'raldi: '{feature_id}' — bepul deb hisoblanadi.")
        bal = get_balance(user_id)
        return ReservationResult(ok=True, reason="unknown_feature", price=0, balance=bal, available=bal, feature_id=feature_id)

    if not feature.get("enabled", True):
        bal = get_balance(user_id)
        return ReservationResult(ok=False, reason="disabled", price=feature.get("price", 0),
                                  balance=bal, available=get_available_balance(user_id), feature_id=feature_id)

    price = int(feature.get("price", 0))
    if price <= 0:
        bal = get_balance(user_id)
        return ReservationResult(ok=True, reason="free", price=0, balance=bal, available=bal, feature_id=feature_id)

    try:
        reservation = reserve_balance(user_id, feature_id, price)
    except InsufficientBalanceError as e:
        return ReservationResult(ok=False, reason="insufficient", price=price,
                                  balance=get_balance(user_id), available=e.available, feature_id=feature_id)

    return ReservationResult(
        ok=True, reason="reserved", price=price, balance=get_balance(user_id),
        available=get_available_balance(user_id), reservation_id=reservation["reservation_id"],
        feature_id=feature_id,
    )


# ============================================================
# 📊 Admin panel uchun agregatsiya (developer.py > 📊/💳 bo'limlari)
# ============================================================

def get_admin_financial_stats() -> dict:
    """Developer panelida ko'rsatiladigan umumiy moliyaviy ko'rsatkichlar:
    - total_balance: barcha foydalanuvchilar balansi yig'indisi
    - total_deposits: barcha tasdiqlangan to'lovlar (topup) yig'indisi
    - total_spending: barcha haqiqiy yechilgan (feature_charge) summa
    - total_refunded: barcha qaytarilgan (refund) summa
    - reserved_balance: hozir 'reserved' holatidagi umumiy summa
    - pending_payments: hali ko'rilmagan to'lovlar soni (pending+manual_review)
    - manual_reviews: faqat manual_review holatidagi to'lovlar soni
    - failed_refunded_ops: released/expired reservation + refund tx soni
    """
    with _lock:
        if _expire_stale_reservations_locked():
            _save()
        total_balance = sum(int(w.get("balance", 0)) for w in _data["wallets"].values())
        total_deposits = sum(t["amount"] for t in _data["transactions"] if t["type"] == TX_TOPUP)
        total_spending = sum(-t["amount"] for t in _data["transactions"] if t["type"] == TX_FEATURE_CHARGE)
        total_refunded = sum(t["amount"] for t in _data["transactions"] if t["type"] == TX_REFUND)
        reserved_balance = sum(r["amount"] for r in _data["reservations"].values() if r["status"] == RES_STATUS_RESERVED)
        pending_payments = sum(1 for p in _data["payments"].values() if p["status"] in STATUS_GROUP_UNCHECKED)
        manual_reviews = sum(1 for p in _data["payments"].values() if p["status"] == STATUS_MANUAL_REVIEW)
        refund_tx_count = sum(1 for t in _data["transactions"] if t["type"] == TX_REFUND)
        released_or_expired = sum(
            1 for r in _data["reservations"].values() if r["status"] in (RES_STATUS_RELEASED, RES_STATUS_EXPIRED)
        )
    return {
        "total_balance": total_balance,
        "total_deposits": total_deposits,
        "total_spending": total_spending,
        "total_refunded": total_refunded,
        "reserved_balance": reserved_balance,
        "pending_payments": pending_payments,
        "manual_reviews": manual_reviews,
        "failed_refunded_ops": refund_tx_count + released_or_expired,
    }


# ============================================================
# 💳 To'lovlar (payments)
# ============================================================

def create_payment(user_id: int, amount: int, provider: str, method: str,
                    currency: str = "UZS", extra: dict | None = None) -> dict:
    """Yangi to'lov yozuvi yaratadi (status=pending). `method` — METHOD_*
    konstantalaridan biri (e-commerce / bank chek / qo'lda tekshirish)."""
    amount = int(amount)
    if amount <= 0:
        raise WalletError("To'lov summasi musbat butun son bo'lishi kerak.")

    payment_id = _new_id("pay")
    payment = {
        "payment_id": payment_id,
        "user_id": int(user_id),
        "amount": amount,
        "currency": currency,
        "provider": provider,
        "method": method,
        "provider_transaction_id": None,
        "status": STATUS_PENDING,
        "receipt": None,          # {"file_id","file_unique_id","extracted","confidence","uploaded_at"}
        "extra": extra or {},
        "created_at": _now_iso(),
        "confirmed_at": None,
        "confirmed_by": None,
        "rejected_at": None,
        "rejected_by": None,
        "reject_reason": None,
    }
    with _lock:
        _data["payments"][payment_id] = payment
        _log_audit_locked("PAYMENT_CREATED", user_id=user_id, payment_id=payment_id,
                           amount=amount, details=f"provider={provider}, method={method}")
        _save()
    logger.info(f"💳 Yangi to'lov yaratildi: payment_id={payment_id}, user_id={user_id}, amount={amount}, method={method}.")
    return dict(payment)


def get_payment(payment_id: str) -> dict | None:
    p = _data["payments"].get(payment_id)
    return dict(p) if p else None


def list_payments(statuses: tuple | None = None, user_id: int | None = None) -> list[dict]:
    rows = list(_data["payments"].values())
    if statuses:
        rows = [p for p in rows if p["status"] in statuses]
    if user_id is not None:
        rows = [p for p in rows if p["user_id"] == int(user_id)]
    rows.sort(key=lambda p: p["created_at"], reverse=True)
    return [dict(p) for p in rows]


def find_payment_by_provider_tx(provider: str, provider_transaction_id: str) -> dict | None:
    idx_key = f"{provider}:{provider_transaction_id}"
    payment_id = _data["provider_tx_index"].get(idx_key)
    if not payment_id:
        return None
    return get_payment(payment_id)


def register_provider_transaction(payment_id: str, provider: str, provider_transaction_id: str) -> None:
    """provider_transaction_id'ni shu payment_id bilan bog'laydi.
    Agar bu tranzaksiya ALLAQACHON BOSHQA payment_id bilan bog'langan bo'lsa
    — DuplicateTransactionError (bitta bank tranzaksiyasi ikki marta
    ishlatilmasin degan himoya, aynan shu yerda amalga oshadi)."""
    idx_key = f"{provider}:{provider_transaction_id}"
    with _lock:
        existing = _data["provider_tx_index"].get(idx_key)
        if existing and existing != payment_id:
            raise DuplicateTransactionError(existing_payment_id=existing)
        _data["provider_tx_index"][idx_key] = payment_id
        payment = _data["payments"].get(payment_id)
        if payment:
            payment["provider_transaction_id"] = provider_transaction_id
        _save()


def register_receipt_fingerprint(payment_id: str, fingerprint: str) -> None:
    """Chek fayl/matn fingerprintini ro'yxatga oladi. Bir xil fingerprint
    BOSHQA payment_id bilan allaqachon bog'liq bo'lsa — DuplicateReceiptError."""
    if not fingerprint:
        return
    with _lock:
        existing = _data["receipt_fingerprints"].get(fingerprint)
        if existing and existing != payment_id:
            raise DuplicateReceiptError(existing_payment_id=existing)
        _data["receipt_fingerprints"][fingerprint] = payment_id
        _save()


def attach_receipt(payment_id: str, file_id: str, file_unique_id: str,
                    extracted: dict | None = None, confidence: float | None = None) -> bool:
    with _lock:
        payment = _data["payments"].get(payment_id)
        if not payment:
            return False
        payment["receipt"] = {
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "extracted": extracted or {},
            "confidence": confidence,
            "uploaded_at": _now_iso(),
        }
        _save()
    return True


def set_payment_status(payment_id: str, status: str, actor_id=None, reason: str = "") -> bool:
    """Oraliq statuslar uchun (masalan manual_review, suspicious) — pul
    HARAKATI qilmaydi, faqat statusni belgilaydi. Yakuniy kredit faqat
    confirm_payment() orqali amalga oshadi."""
    with _lock:
        payment = _data["payments"].get(payment_id)
        if not payment:
            return False
        if payment["status"] == STATUS_PAID:
            logger.warning(f"💳 Allaqachon TASDIQLANGAN to'lov statusini o'zgartirishga urinish: payment_id={payment_id}.")
            return False
        old_status = payment["status"]
        payment["status"] = status
        if status == STATUS_REJECTED:
            payment["rejected_at"] = _now_iso()
            payment["rejected_by"] = actor_id
            payment["reject_reason"] = reason
        event = {
            STATUS_REJECTED: "PAYMENT_REJECTED",
            STATUS_SUSPICIOUS: "PAYMENT_MARKED_SUSPICIOUS",
            STATUS_MANUAL_REVIEW: "PAYMENT_RECEIVED",
        }.get(status, "PAYMENT_STATUS_CHANGED")
        _log_audit_locked(event, actor_id=actor_id, payment_id=payment_id,
                           user_id=payment["user_id"], amount=payment["amount"],
                           details=f"{old_status} -> {status}. {reason}")
        _save()
    logger.info(f"💳 To'lov statusi o'zgardi: payment_id={payment_id}, {old_status} -> {status} (actor={actor_id}).")
    return True


def confirm_payment(payment_id: str, actor_id=None, source: str = "manual") -> bool:
    """💚 YAGONA joy — bu orqali to'lov TASDIQLANADI va foydalanuvchi
    balansiga pul QO'SHILADI. TO'LIQ ATOMIK va IDEMPOTENT:
    - Agar payment topilmasa -> False.
    - Agar payment ALLAQACHON 'paid' bo'lsa -> HECH NARSA qilmaydi, False
      qaytaradi (webhook necha marta kelsa ham, admin necha marta tugma
      bossa ham — foydalanuvchi IKKINCHI marta kredit qilinmaydi).
    - Agar payment 'rejected'/'failed'/'cancelled'/'expired' bo'lsa ->
      False (o'lik holatdagi to'lovni qayta tasdiqlab bo'lmaydi).
    - Aks holda: status='paid', balans += amount, transaction+audit yoziladi.
    """
    with _lock:
        payment = _data["payments"].get(payment_id)
        if not payment:
            logger.warning(f"💳 confirm_payment: topilmadi, payment_id={payment_id}.")
            return False

        if payment["status"] == STATUS_PAID:
            logger.info(f"💳 confirm_payment: ALLAQACHON tasdiqlangan (idempotent no-op), payment_id={payment_id}, source={source}.")
            _log_audit_locked("PAYMENT_CONFIRM_DUPLICATE_IGNORED", actor_id=actor_id, payment_id=payment_id,
                               user_id=payment["user_id"], amount=payment["amount"], details=f"source={source}")
            _save()
            return False

        if payment["status"] in (STATUS_REJECTED, STATUS_FAILED, STATUS_CANCELLED, STATUS_EXPIRED):
            logger.warning(f"💳 confirm_payment: o'lik holatdagi to'lovni tasdiqlab bo'lmaydi, payment_id={payment_id}, status={payment['status']}.")
            return False

        user_id = payment["user_id"]
        amount = payment["amount"]

        payment["status"] = STATUS_PAID
        payment["confirmed_at"] = _now_iso()
        payment["confirmed_by"] = actor_id

        wallet = _data["wallets"].setdefault(str(user_id), {"balance": 0})
        before = int(wallet["balance"])
        after = before + amount
        wallet["balance"] = after
        _new_tx_locked(user_id, TX_TOPUP, amount, before, after,
                       description=f"Balansni to'ldirish ({payment['method']})",
                       related_payment_id=payment_id)
        _log_audit_locked("PAYMENT_CONFIRMED", actor_id=actor_id, payment_id=payment_id,
                           user_id=user_id, amount=amount, details=f"source={source}")
        _save()

    logger.info(f"💳✅ To'lov TASDIQLANDI: payment_id={payment_id}, user_id={user_id}, amount={amount}, source={source}, actor={actor_id}.")
    return True


def reject_payment(payment_id: str, actor_id=None, reason: str = "") -> bool:
    ok = set_payment_status(payment_id, STATUS_REJECTED, actor_id=actor_id, reason=reason)
    if ok:
        log_audit("MANUAL_PAYMENT_REJECTED" if actor_id else "PAYMENT_REJECTED",
                  actor_id=actor_id, payment_id=payment_id, details=reason)
    return ok


def mark_suspicious(payment_id: str, actor_id=None, reason: str = "") -> bool:
    return set_payment_status(payment_id, STATUS_SUSPICIOUS, actor_id=actor_id, reason=reason)


def mark_manual_review(payment_id: str, reason: str = "") -> bool:
    return set_payment_status(payment_id, STATUS_MANUAL_REVIEW, reason=reason)


def approve_manual_payment(payment_id: str, actor_id) -> bool:
    """Admin panelidan '✅ Tasdiqlash' bosilganda chaqiriladi."""
    ok = confirm_payment(payment_id, actor_id=actor_id, source="manual_admin")
    if ok:
        log_audit("MANUAL_PAYMENT_APPROVED", actor_id=actor_id, payment_id=payment_id)
    return ok
