"""
💎 Pro obuna holati — storage.py bilan BIR XIL printsipda doimiy saqlanadi
(Upstash Redis / Neon / GitHub / mahalliy fayl — config.persist_read/write
orqali, ustuvorlik tartibi o'sha yerda belgilangan).

Ikki asosiy tushuncha:
  - So'ROV (request) — foydalanuvchi "✅ To'ladim" tugmasini bosganda
    yaratiladi, admin tasdiqlashini/rad etishini kutadi ("pending" holati).
  - OBUNA (subscription) — so'rov TASDIQLANGANDAN keyin yoziladi, muddati
    (PRO_SUBSCRIPTION_DAYS) bilan. `is_pro()` shu muddatga qarab True/False
    qaytaradi.

Bitta foydalanuvchida bir vaqtning o'zida faqat BITTA "kutilayotgan" so'rov
bo'lishi mumkin (ketma-ket ikki marta "To'ladim" bossa, ikkinchisi eskisini
yangilaydi, ikkalasi uchun ham admin urinib qolmasin uchun).
"""

import logging
import time
import uuid
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

_DATA_FILENAME = "pro_subscriptions.json"
_UPSTASH_KEY = "student_ai_pro_subscriptions"

_DEFAULT_DATA = {
    "requests": {},       # {"<req_id>": {"user_id","status","created_ts","decided_ts"}}
    "subscriptions": {},  # {"<user_id>": {"expires_ts","approved_ts","req_id"}}
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    raw, source = config.persist_read(_DATA_FILENAME, _UPSTASH_KEY)
    if not raw:
        return {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in _DEFAULT_DATA.items()}
    try:
        import json
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"pro_subscriptions JSON parse xato: {e} — bo'sh holatdan boshlanadi.")
        return {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in _DEFAULT_DATA.items()}
    for k, v in _DEFAULT_DATA.items():
        data.setdefault(k, dict(v) if isinstance(v, dict) else list(v))
    logger.info(f"{source} dan Pro obuna ma'lumotlari yuklandi.")
    return data


_data = _load()


def _save(commit_message: str) -> None:
    import json
    raw = json.dumps(_data, ensure_ascii=False)
    config.persist_write(_DATA_FILENAME, _UPSTASH_KEY, raw, commit_message=commit_message)


# ============================================================
# So'ROVLAR
# ============================================================

def create_request(user_id: int) -> str:
    """Foydalanuvchi "✅ To'ladim" tugmasini bosganda chaqiriladi. Agar
    shu foydalanuvchining allaqachon "pending" so'rovi bo'lsa, ESKISINI
    "cancelled" deb belgilab, yangi so'rov yaratadi (bitta odam uchun
    faqat bitta faol so'rov bo'lishi uchun — admin ikki marta bir xil
    odamni tasdiqlashga urinib qolmasligi kerak)."""
    uid = str(user_id)
    for req_id, req in _data["requests"].items():
        if req["user_id"] == int(user_id) and req["status"] == "pending":
            req["status"] = "cancelled"
            req["decided_ts"] = time.time()

    req_id = uuid.uuid4().hex[:10]
    _data["requests"][req_id] = {
        "user_id": int(user_id),
        "status": "pending",
        "created_ts": time.time(),
        "decided_ts": None,
    }
    _save(f"💎 Yangi Pro obuna so'rovi: user_id={user_id}")
    logger.info(f"💎 Pro obuna so'rovi yaratildi: user_id={user_id}, req_id={req_id}.")
    return req_id


def get_request(req_id: str) -> dict | None:
    return _data["requests"].get(req_id)


def get_pending_requests() -> list[dict]:
    """/developer > 💎 Pro obunalar bo'limi uchun — eng yangisi birinchi."""
    pending = [
        {**req, "req_id": req_id}
        for req_id, req in _data["requests"].items()
        if req["status"] == "pending"
    ]
    pending.sort(key=lambda r: r["created_ts"], reverse=True)
    return pending


def approve_request(req_id: str) -> int | None:
    """So'rovni tasdiqlaydi, obunani PRO_SUBSCRIPTION_DAYS kunga
    faollashtiradi (agar foydalanuvchida FAOL obuna bo'lsa, muddat
    UNING USTIGA emas, HOZIRGI vaqtdan qayta hisoblanadi — soddalik
    uchun; ketma-ket ikki marta to'lash "muddatni ikki barobar
    uzaytirish" emas). Muvaffaqiyatli bo'lsa user_id qaytaradi (adminga
    xabar yuborish uchun kimga aytish kerakligini bilish uchun), aks
    holda None."""
    req = _data["requests"].get(req_id)
    if not req or req["status"] != "pending":
        return None
    req["status"] = "approved"
    req["decided_ts"] = time.time()
    user_id = req["user_id"]
    expires_ts = time.time() + config.PRO_SUBSCRIPTION_DAYS * 86400
    _data["subscriptions"][str(user_id)] = {
        "expires_ts": expires_ts,
        "approved_ts": time.time(),
        "req_id": req_id,
    }
    _save(f"💎 Pro obuna tasdiqlandi: user_id={user_id}")
    logger.info(f"💎 Pro obuna TASDIQLANDI: user_id={user_id}, req_id={req_id}, muddati={datetime.fromtimestamp(expires_ts).strftime('%Y-%m-%d')}.")
    return user_id


def reject_request(req_id: str) -> int | None:
    req = _data["requests"].get(req_id)
    if not req or req["status"] != "pending":
        return None
    req["status"] = "rejected"
    req["decided_ts"] = time.time()
    _save(f"💎 Pro obuna rad etildi: user_id={req['user_id']}")
    logger.info(f"💎 Pro obuna RAD ETILDI: user_id={req['user_id']}, req_id={req_id}.")
    return req["user_id"]


# ============================================================
# OBUNA HOLATI
# ============================================================

def is_pro(user_id: int) -> bool:
    sub = _data["subscriptions"].get(str(user_id))
    if not sub:
        return False
    return time.time() < sub["expires_ts"]


def get_subscription(user_id: int) -> dict | None:
    return _data["subscriptions"].get(str(user_id))
