"""
🎨 /rasim Mini App uchun xavfsizlik: Telegram WebApp `initData`ni
tekshirish (rasmiy Telegram algoritmi bo'yicha) + bitta martalik
"so'rov token"lari ombori (qaysi chatga rasm qaytarilishi kerakligini
xavfsiz aniqlash uchun).

Bu modul ATAYLAB `telegram` kutubxonasiga BOG'LIQ EMAS — faqat
`hashlib`/`hmac`/`json`/`urllib` ishlatadi, shuning uchun `python-telegram-bot`
o'rnatilmagan muhitda ham to'liq unit-test qilinishi mumkin
(tests/test_webapp_security.py'ga qarang).

TELEGRAM INITDATA TEKSHIRUV ALGORITMI (rasmiy hujjatga muvofiq):
1. initData — URL query-string ko'rinishidagi satr (key=value&key=value...).
2. `hash` maydonini judo qilib olamiz, qolgan maydonlarni ALIFBO tartibida
   saralaymiz va "key=value" qatorlarini "\n" bilan qo'shib
   data_check_string hosil qilamiz.
3. secret_key = HMAC_SHA256(key=b"WebAppData", msg=bot_token).digest()
4. computed_hash = HMAC_SHA256(key=secret_key, msg=data_check_string).hexdigest()
5. computed_hash MUST == hash maydoni (doimiy vaqtli taqqoslash bilan).
6. auth_date juda eski bo'lmasligi kerak (replay hujumidan himoya).
"""

import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import parse_qsl

MAX_INIT_DATA_AGE_SECONDS = 3600  # 1 soat — bundan eskirgan initData rad etiladi


def verify_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = MAX_INIT_DATA_AGE_SECONDS) -> dict | None:
    """`init_data` haqiqiy va yetarlicha yangi bo'lsa — undagi `user` obyektini
    (dict, kamida `id` maydoni bilan) qaytaradi. Aks holda `None`.

    HECH QACHON initDataUnsafe (front-end tomonidan tekshirilmagan JS
    obyekti) ga ishonmang — faqat shu funksiya orqali server tomonida
    tasdiqlangan initData ishlatilishi kerak."""
    if not init_data or not bot_token:
        return None

    try:
        pairs = parse_qsl(init_data, strict_parsing=True, keep_blank_values=True)
    except ValueError:
        return None

    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date = data.get("auth_date")
    if auth_date:
        try:
            age = time.time() - int(auth_date)
        except ValueError:
            return None
        if age > max_age_seconds or age < -60:  # -60: soat farqiga kichik tolerantlik
            return None

    user_raw = data.get("user")
    if not user_raw:
        return None
    try:
        user = json.loads(user_raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(user, dict) or "id" not in user:
        return None
    # 🧩 "query_id" — FAQAT Mini App inline rejimda (do'st bilan shaxsiy
    # chatda "@Bot ..." orqali) ochilganda keladi. Bu Telegram'ning HAM
    # imzolangan (initData ichida, shuning uchun HMAC bilan tekshirilgan)
    # maydoni — keyinchalik `answer_web_app_query()` chaqirish uchun kerak
    # bo'ladi. Oddiy user maydoniga aralashtirmaslik uchun "_" prefiksi bilan
    # qo'shib qo'yamiz (haqiqiy Telegram user maydonlari orasida bunday nom
    # yo'q, shuning uchun to'qnashuv xavfi yo'q).
    user["_query_id"] = data.get("query_id")
    return user


# ------------------------------------------------------------------
# 🎫 Bitta martalik "so'rov token"lari — /rasim tugmasi bosilganda
# yaratiladi, Mini App URL'iga `?rid=...` sifatida qo'shiladi. Rasm
# yuklanganda shu rid orqali "qaysi chatga, qaysi foydalanuvchi nomidan
# qaytarish kerak" xavfsiz tarzda aniqlanadi — Mini App frontendidan
# kelayotgan chat_id/user_id'ga HECH QACHON to'g'ridan-to'g'ri ishonilmaydi.
# ------------------------------------------------------------------
_REQUESTS: dict[str, dict] = {}
REQUEST_TTL_SECONDS = 15 * 60   # 15 daqiqa — Mini App'ni ochib picture chizish uchun yetarli
MAX_REQUESTS = 5000


def _purge_expired_requests(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    expired = [k for k, v in _REQUESTS.items() if now - v["created_at"] > REQUEST_TTL_SECONDS]
    for k in expired:
        del _REQUESTS[k]


def create_request(chat_id: int, user_id: int) -> str:
    """/rasim bosilganda chaqiriladi — yangi bitta martalik token yaratadi."""
    rid = uuid.uuid4().hex
    _REQUESTS[rid] = {
        "chat_id": chat_id,
        "user_id": user_id,
        "created_at": time.time(),
        "used": False,
    }
    _purge_expired_requests()
    if len(_REQUESTS) > MAX_REQUESTS:
        oldest = sorted(_REQUESTS.items(), key=lambda kv: kv[1]["created_at"])
        for k, _ in oldest[: len(_REQUESTS) - MAX_REQUESTS]:
            del _REQUESTS[k]
    return rid


def consume_request(rid: str, verified_user_id: int) -> int | None:
    """Rasm yuklanganda chaqiriladi. `rid` mavjud, muddati o'tmagan,
    HALI ISHLATILMAGAN va `verified_user_id` (initData orqali
    TASDIQLANGAN foydalanuvchi) so'rovni yaratgan foydalanuvchi bilan
    BIR XIL bo'lsagina `chat_id`ni qaytaradi va tokenni "ishlatilgan"
    deb belgilaydi (bir xil rid ikkinchi marta ishlatilmaydi — masalan
    foydalanuvchi 'Uzatish'ni ikki marta bossa ham, ikkita rasm
    yuborilib ketmasligi uchun emas, balki ESKI/discarded so'rov
    orqali qayta rasm yuborib bo'lmasligi uchun)."""
    entry = _REQUESTS.get(rid)
    if not entry:
        return None
    if entry["used"]:
        return None
    if time.time() - entry["created_at"] > REQUEST_TTL_SECONDS:
        del _REQUESTS[rid]
        return None
    if int(entry["user_id"]) != int(verified_user_id):
        return None
    entry["used"] = True
    return entry["chat_id"]


# ------------------------------------------------------------------
# 🎫🔍 INLINE REJIM uchun so'rov tokenlari — do'st bilan shaxsiy chatda
# "@Bot /rasim" orqali ochilgan Mini App uchun. Bu yerda `chat_id`
# UMUMAN YO'Q — Telegram inline rejimda maxfiylik sababli qaysi chatga
# yuborilayotganini botga aytmaydi. Buning o'rniga, rasm tayyor bo'lgach
# Telegram'ning maxsus `answer_web_app_query` mexanizmi ishlatiladi (bot.py),
# u esa `query_id` orqali (initData ichida imzolangan) xabarni TO'G'RI
# joyga o'zi yetkazadi — bizga chat_id bilishimiz shart emas.
#
# rid'lar oddiy (chatga bog'langan) so'rovlardan "in_" prefiksi bilan
# FARQLANADI — shu orqali /miniapp/rasim/upload bitta rid qaysi turga
# tegishli ekanini (qaysi ombordan qidirishni) darhol biladi.
# ------------------------------------------------------------------
_INLINE_REQUESTS: dict[str, dict] = {}
INLINE_REQUEST_TTL_SECONDS = 15 * 60
MAX_INLINE_REQUESTS = 5000


def _purge_expired_inline_requests(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    expired = [k for k, v in _INLINE_REQUESTS.items() if now - v["created_at"] > INLINE_REQUEST_TTL_SECONDS]
    for k in expired:
        del _INLINE_REQUESTS[k]


def create_inline_request(user_id: int) -> str:
    """Inline `/rasim` uchun yangi bitta martalik token yaratadi (rid
    "in_" bilan boshlanadi — bot.py shu orqali inline turini aniqlaydi)."""
    rid = "in_" + uuid.uuid4().hex
    _INLINE_REQUESTS[rid] = {"user_id": user_id, "created_at": time.time(), "used": False}
    _purge_expired_inline_requests()
    if len(_INLINE_REQUESTS) > MAX_INLINE_REQUESTS:
        oldest = sorted(_INLINE_REQUESTS.items(), key=lambda kv: kv[1]["created_at"])
        for k, _ in oldest[: len(_INLINE_REQUESTS) - MAX_INLINE_REQUESTS]:
            del _INLINE_REQUESTS[k]
    return rid


def consume_inline_request(rid: str, verified_user_id: int) -> bool:
    """`consume_request`ga o'xshaydi, lekin chat_id emas, faqat
    muvaffaqiyat/muvaffaqiyatsizlikni (bool) qaytaradi — chunki bu yerda
    "qaysi chatga qaytarish" ma'lumoti umuman saqlanmaydi."""
    entry = _INLINE_REQUESTS.get(rid)
    if not entry:
        return False
    if entry["used"]:
        return False
    if time.time() - entry["created_at"] > INLINE_REQUEST_TTL_SECONDS:
        del _INLINE_REQUESTS[rid]
        return False
    if int(entry["user_id"]) != int(verified_user_id):
        return False
    entry["used"] = True
    return True
