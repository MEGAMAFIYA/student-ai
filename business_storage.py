"""
📇 Telegram Business ulanishlari uchun DOIMIY (persistent) saqlash.

MUHIM ARXITEKTURA QARORI: bu fayl storage.py'ni O'ZGARTIRMAYDI — chunki
storage.py'ning _DATA_FILENAME/_UPSTASH_KEY va _DEFAULT_DATA sxemasi allaqachon
boshqa (fayllar/statistika/eslatmalar/guruhlar) domenlarga tegishli, ularni
business-connection state bilan aralashtirish keyinchalik migratsiyani
qiyinlashtiradi. Shu bilan birga MEXANIZM (config.persist_read/persist_write —
Upstash Redis bo'lsa o'sha yerda, aks holda mahalliy JSON fayl, Render'da esa
restart bilan yo'qolishi mumkin) storage.py bilan BIR XIL — ya'ni "umumiy
mantiq ikki joyga nusxalanmasin" talabiga rioya qilingan: yangi persistence
mexanizmi YARATILMAGAN, faqat config.py'dagi mavjudi qayta ishlatilgan.

Har bir `business_connection` update kelganda (bot Telegram Business orqali
ulanganda/uzilganda/sozlamalari o'zgarganda) yozib boriladi. Bitta foydalanuvchi
(business account egasi) faqat bitta faol connection_id'ga ega bo'ladi deb
hisoblanadi (Telegram'ning o'zi shunday ishlaydi — user botni qayta ulasa,
eski connection_id o'rniga yangisi keladi), shuning uchun user_id bo'yicha
indekslanadi va connection_id bo'yicha ham (tez qidiruv uchun) saqlanadi.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

_DATA_FILENAME = "business_connections.json"
_UPSTASH_KEY = "student_ai_business_connections"

_lock = threading.Lock()

_DEFAULT_DATA = {
    # "<business_user_id>" -> {connection_id, user_id, is_enabled, rights, updated_ts, ...}
    "by_user": {},
    # "<connection_id>" -> "<business_user_id>" — tez teskari qidiruv uchun
    "by_connection_id": {},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    raw, source = config.persist_read(_DATA_FILENAME, _UPSTASH_KEY)
    if not raw:
        logger.info("📇 Business connection ma'lumotlari topilmadi — bo'sh holatdan boshlanadi.")
        return {"by_user": {}, "by_connection_id": {}}
    try:
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"📇 business_connections JSON parse xato: {e} — bo'sh holatdan boshlanadi.")
        return {"by_user": {}, "by_connection_id": {}}
    data.setdefault("by_user", {})
    data.setdefault("by_connection_id", {})
    logger.info(f"📇 {source} dan business connection ma'lumotlari yuklandi ({len(data['by_user'])} ta).")
    return data


_data = _load()


def _save() -> None:
    raw = json.dumps(_data, ensure_ascii=False)
    config.persist_write(
        _DATA_FILENAME, _UPSTASH_KEY, raw,
        commit_message="📇 Business connection ma'lumotlari yangilandi",
    )


def _rights_to_dict(rights) -> dict:
    """`telegram.BusinessBotRights` obyektini (yoki None'ni) oddiy dict'ga
    aylantiradi — faqat bizga kerakli maydonlar, qolganini yo'qotmaymiz."""
    if rights is None:
        return {}
    out = {}
    for attr in ("can_reply", "can_read_messages", "can_delete_sent_messages", "can_delete_all_messages"):
        val = getattr(rights, attr, None)
        if val is not None:
            out[attr] = bool(val)
    return out


def save_connection(business_connection) -> None:
    """`business_connection` — python-telegram-bot'ning
    `telegram.BusinessConnection` obyekti (update.business_connection).
    Har safar business_connection update kelganda chaqiriladi — ulanish
    yangi bo'lsa ham, o'zgargan bo'lsa ham, o'chirilgan bo'lsa ham (Telegram
    uch holatda ham shu update turini yuboradi, `is_enabled` orqali farqlanadi).
    """
    with _lock:
        user_id = str(business_connection.user.id)
        connection_id = business_connection.id
        entry = {
            "connection_id": connection_id,
            "user_id": business_connection.user.id,
            "user_chat_id": getattr(business_connection, "user_chat_id", business_connection.user.id),
            "is_enabled": bool(business_connection.is_enabled),
            "rights": _rights_to_dict(getattr(business_connection, "rights", None)),
            "updated_ts": time.time(),
            "updated_iso": _now_iso(),
        }
        _data["by_user"][user_id] = entry
        _data["by_connection_id"][connection_id] = user_id
        _save()
    logger.info(
        f"📇 BUSINESS_CONNECTION_SAVED user_id={business_connection.user.id} "
        f"connection_id={connection_id} is_enabled={business_connection.is_enabled} "
        f"rights={entry['rights']}"
    )


def get_connection_for_user(business_user_id: int) -> dict | None:
    """Berilgan business-account egasi (masalan /tabrik yuboruvchi A) uchun
    saqlangan eng so'nggi connection yozuvini qaytaradi (topilmasa None)."""
    return _data["by_user"].get(str(business_user_id))


def get_user_id_for_connection(connection_id: str) -> int | None:
    uid = _data["by_connection_id"].get(connection_id)
    return int(uid) if uid is not None else None


def is_connection_usable(entry: dict | None) -> tuple[bool, str]:
    """(usable, reason_code) — reason_code loglash uchun aniq kod:
    'NO_CONNECTION' | 'DISABLED' | 'CAN_REPLY_FALSE' | 'OK'."""
    if entry is None:
        return False, "NO_CONNECTION"
    if not entry.get("is_enabled", False):
        return False, "DISABLED"
    rights = entry.get("rights") or {}
    # can_reply topilmasa (eski Bot API / rights kelmagan) — ehtiyotkorlik
    # bilan "False" deb hisoblamaymiz, chunki ba'zi holatlarda Telegram
    # rights maydonini umuman yubormasligi mumkin; faqat ANIQ False bo'lsa rad etamiz.
    if rights.get("can_reply") is False:
        return False, "CAN_REPLY_FALSE"
    return True, "OK"


def can_delete_sent_messages(entry: dict | None) -> bool:
    if not entry:
        return False
    rights = entry.get("rights") or {}
    # Aniq False bo'lmasa (ya'ni None/topilmagan holatda ham) True deb
    # "optimistik" harakat qilamiz — chunki delete muvaffaqiyatsiz bo'lsa ham
    # bu animatsiyani to'xtatadigan darajada jiddiy xato emas (faqat log +
    # xabar chatda qolib ketadi). Chaqiruvchi baribir har bir delete natijasini
    # alohida tekshiradi va DELETE_FAILED logini yozadi.
    return rights.get("can_delete_sent_messages") is not False
