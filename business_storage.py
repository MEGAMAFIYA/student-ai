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

logger = logging.getLogger("business_autoreply")

_DATA_FILENAME = "business_connections.json"
_UPSTASH_KEY = "student_ai_business_connections"

_lock = threading.Lock()

_DEFAULT_DATA = {
    # "<business_user_id>" -> {connection_id, user_id, is_enabled, rights, updated_ts, ...}
    "by_user": {},
    # "<connection_id>" -> "<business_user_id>" — tez teskari qidiruv uchun
    "by_connection_id": {},
    # "<business_user_id>" -> {"<chat_id>": last_incoming_unix_ts}
    "recent_peers": {},
    "auto_replies": {},
    "auto_reply_seen": {},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    raw, source = config.persist_read(_DATA_FILENAME, _UPSTASH_KEY)
    if not raw:
        logger.info("📇 Business connection ma'lumotlari topilmadi — bo'sh holatdan boshlanadi.")
        return {"by_user": {}, "by_connection_id": {}, "recent_peers": {}, "auto_replies": {}, "auto_reply_seen": {}}
    try:
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"📇 business_connections JSON parse xato: {e} — bo'sh holatdan boshlanadi.")
        return {"by_user": {}, "by_connection_id": {}, "recent_peers": {}, "auto_replies": {}, "auto_reply_seen": {}}
    data.setdefault("by_user", {})
    data.setdefault("by_connection_id", {})
    data.setdefault("recent_peers", {})
    data.setdefault("auto_replies", {})
    data.setdefault("auto_reply_seen", {})
    logger.info(f"📇 {source} dan business connection ma'lumotlari yuklandi ({len(data['by_user'])} ta).")
    return data


_data = _load()


def _save() -> None:
    raw = json.dumps(_data, ensure_ascii=False)
    try:
        config.persist_write(
            _DATA_FILENAME, _UPSTASH_KEY, raw,
            commit_message="📇 Business connection ma'lumotlari yangilandi",
        )
        logger.info(
            "BUSINESS_STORAGE_SAVE_OK users=%s connections=%s autoreplies=%s",
            len(_data.get("by_user", {})),
            len(_data.get("by_connection_id", {})),
            len(_data.get("auto_replies", {})),
        )
    except Exception:
        logger.exception("BUSINESS_STORAGE_SAVE_FAILED")
        raise


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
            "first_name": getattr(business_connection.user, "first_name", "") or "",
            "last_name": getattr(business_connection.user, "last_name", "") or "",
            "username": getattr(business_connection.user, "username", "") or "",
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


def get_active_connections() -> list[dict]:
    """Restart paytida shaxsiy buyruqlar menyusini tiklash uchun faol ulanishlar."""
    active = [entry for entry in _data.get("by_user", {}).values() if entry.get("is_enabled")]
    logger.info("BUSINESS_ACTIVE_CONNECTIONS_READ count=%s", len(active))
    return active


def get_connection_for_user(business_user_id: int) -> dict | None:
    """Berilgan business-account egasi uchun saqlangan connection yozuvini qaytaradi."""
    entry = _data["by_user"].get(str(business_user_id))
    logger.info("BUSINESS_CONNECTION_USER_LOOKUP user_id=%s found=%s enabled=%s",
                business_user_id, bool(entry), entry.get("is_enabled") if entry else None)
    return entry


def get_user_id_for_connection(connection_id: str) -> int | None:
    uid = _data["by_connection_id"].get(connection_id)
    if uid is None:
        logger.error("BUSINESS_CONNECTION_LOOKUP_MISS connection_id=%s", connection_id)
        return None
    logger.info("BUSINESS_CONNECTION_LOOKUP_OK connection_id=%s user_id=%s", connection_id, uid)
    return int(uid)



def record_business_message(business_connection_id: str, chat_id: int, message_date=None) -> None:
    """Saqlangan Business connection uchun yaqinda incoming bo'lgan chatni qayd etadi.

    Telegram `can_reply` huquqini private chatdagi oxirgi incoming xabarning
    24 soatlik oynasi bilan bog'laydi. Shu sababli faqat connection mavjudligini
    tekshirishning o'zi yetarli emas; aynan qaysi peerga yuborish mumkinligini
    ham kuzatib boramiz.
    """
    if not business_connection_id or not chat_id:
        return
    user_id = _data.get("by_connection_id", {}).get(str(business_connection_id))
    if user_id is None:
        logger.warning(
            "📇 BUSINESS_MESSAGE_PEER_UNKNOWN connection_id=%s chat_id=%s",
            business_connection_id, chat_id,
        )
        return
    ts = time.time()
    if message_date is not None:
        try:
            ts = float(message_date.timestamp()) if hasattr(message_date, "timestamp") else float(message_date)
        except (TypeError, ValueError, OverflowError):
            ts = time.time()
    with _lock:
        peers = _data.setdefault("recent_peers", {}).setdefault(str(user_id), {})
        peers[str(int(chat_id))] = ts
        cutoff = time.time() - 24 * 60 * 60
        stale = [peer for peer, seen in peers.items() if float(seen) < cutoff]
        for peer in stale:
            peers.pop(peer, None)
        _save()
    logger.info(
        "📇 BUSINESS_PEER_RECORDED business_user_id=%s chat_id=%s age_sec=%.0f",
        user_id, chat_id, max(0.0, time.time() - ts),
    )


def is_recent_business_peer(business_user_id: int, chat_id: int, max_age_sec: int = 86400) -> bool:
    """True bo'lsa, shu peerga Business orqali reply qilish oynasi ochiq bo'lishi kutiladi."""
    peers = _data.get("recent_peers", {}).get(str(business_user_id), {})
    seen = peers.get(str(int(chat_id)))
    if seen is None:
        return False
    try:
        age = time.time() - float(seen)
    except (TypeError, ValueError):
        return False
    if age < 0:
        return True
    if age > max_age_sec:
        return False
    return True


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


def get_recent_business_peers(business_user_id: int, max_age_sec: int = 30 * 24 * 60 * 60) -> list[int]:
    """Business akkauntga oldin yozgan peerlar ro'yxati.

    `recent_peers` tarixiy storage sifatida emas, reply oynasini kuzatish uchun
    yaratilgan. Shuning uchun bu ro'yxat faqat shu storage'da saqlanib qolgan
    va ko'rsatilgan muddatdan oshmagan peerlarni qaytaradi.
    """
    uid = str(int(business_user_id))
    peers = _data.get("recent_peers", {}).get(uid, {})
    cutoff = time.time() - max_age_sec
    out = []
    for peer, seen in peers.items():
        try:
            if float(seen) >= cutoff:
                out.append(int(peer))
        except (TypeError, ValueError):
            continue
    return out


def get_auto_reply_seen_peers(business_user_id: int, limit: int = 20000) -> list[int]:
    """Old auto-reply tarixida ko'rilgan peerlar — yangi/eskini ajratishda yordam beradi."""
    raw = _data.get("auto_reply_seen", {}).get(str(int(business_user_id)), [])
    out = []
    for peer in raw[-max(1, int(limit)):]:
        try:
            out.append(int(peer))
        except (TypeError, ValueError):
            continue
    return out


# --- Business Auto Reply -------------------------------------------------
def set_auto_reply(business_user_id: int, text: str, mode: str) -> None:
    """Foydalanuvchining avtomatik javob matni va rejimini saqlaydi."""
    if mode not in ("new", "all"):
        raise ValueError("mode must be 'new' or 'all'")
    with _lock:
        uid = str(int(business_user_id))
        _data.setdefault("auto_replies", {})[uid] = {
            "text": str(text), "mode": mode, "updated_ts": time.time(),
        }
        # Mavjud storage'dagi oxirgi 24 soatda yozgan peerlar yangi hisoblanmasin.
        if mode == "new":
            known = _data.setdefault("auto_reply_seen", {}).setdefault(uid, [])
            for peer in _data.get("recent_peers", {}).get(uid, {}):
                if peer not in known:
                    known.append(peer)
        _save()

def get_auto_reply(business_user_id: int) -> dict | None:
    return _data.get("auto_replies", {}).get(str(int(business_user_id)))

def record_auto_reply_peer(business_user_id: int, chat_id: int) -> bool:
    """Peerning birinchi marta yozayotganini doimiy qayd etadi; yangi bo'lsa True."""
    uid, peer = str(int(business_user_id)), str(int(chat_id))
    with _lock:
        seen = _data.setdefault("auto_reply_seen", {}).setdefault(uid, [])
        if peer in seen:
            return False
        seen.append(peer)
        if len(seen) > 20000:
            del seen[:-20000]
        _save()
        return True


def should_send_auto_reply(business_user_id: int, chat_id: int) -> bool:
    """Moslama mavjud bo'lsa va yangi/all rejimiga ko'ra javob berish kerak bo'lsa True."""
    settings = get_auto_reply(business_user_id)
    if not settings:
        return False
    if settings.get("mode") == "all":
        return True
    # Eski chaqiruvchilar uchun xavfsiz fallback; asosiy oqim record_auto_reply_peer ishlatadi.
    return record_auto_reply_peer(business_user_id, chat_id)
