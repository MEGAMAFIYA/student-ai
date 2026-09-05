"""
Umumiy doimiy (persistent) saqlash — quyidagi yangi funksiyalar uchun:
  - 🗂 "Mening fayllarim" — foydalanuvchi oldin yaratgan fayllar tarixi
  - 📊 /developer statistikasi — qaysi funksiya necha marta ishlatilgani
  - ⏰ Eslatmalar — belgilangan vaqtda yuboriladigan xabarlar ro'yxati

config.py dagi runtime_ai_config.json bilan BIR XIL prinsipda ishlaydi:
Upstash Redis sozlangan bo'lsa (UPSTASH_REDIS_REST_URL/TOKEN) — o'sha yerda,
aks holda mahalliy app_data.json faylida saqlanadi (Render'da bu holda
har deployda yo'qoladi — Upstash tavsiya etiladi, xuddi AI sozlamalari kabi).

Barcha yozish amallari _lock bilan himoyalangan (bitta jarayon ichida bir
vaqtning o'zida ikkita yozish bir-birini bosib ketmasligi uchun) va HAR
BIR o'zgarishdan keyin darhol tashqi saqlashga yoziladi (write-through) —
shuning uchun bot kutilmaganda to'xtab qolsa ham oxirgi holat saqlanib qoladi.
"""

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

_DATA_FILENAME = "app_data.json"
_UPSTASH_KEY = "student_ai_app_data"

# Bitta jarayon ichidagi yozishlarni ketma-ket qilish uchun (masalan bir
# vaqtda ikkita foydalanuvchi fayl yaratib, ikkalasi ham storage'ga bir
# vaqtda yozmoqchi bo'lsa — thread-based, chunki config.py'dagi kabi bu
# funksiyalar ham sinxron (httpx.Client) ishlaydi va aiogram/PTB bir nechta
# haqiqiy thread'da chaqirilishi mumkin bo'lgan sinxron kod ichida ishga
# tushishi mumkin).
_lock = threading.Lock()

MAX_FILES_PER_USER = 30      # har bir foydalanuvchi uchun saqlanadigan fayllar tarixi chegarasi
MAX_USAGE_DATES = 60         # statistikada saqlanadigan kunlar soni (eskilari siqiladi)
MAX_INLINE_LOGS = 300         # 🔍 inline jurnalida saqlanadigan yozuvlar chegarasi (eskilari siqiladi)

_DEFAULT_DATA = {
    "files": {},      # {"<user_id>": [{"type","title","file_id","ts"}, ...]}
    "usage": {},       # {"<func>": {"total": int, "unique_users": [ids], "by_date": {date: int}}}
    "all_users": [],   # botdan umuman foydalangan barcha noyob user_id'lar
    "reminders": [],   # [{"id","user_id","chat_id","text","due_ts","created_ts"}]
    "groups": {},      # {"<chat_id>": {"active": bool}} — guruhda Universal chat holati
    "inline_logs": [],  # [{"ts","user_id","username","query","status","detail"}] — inline (@Bot ...) jurnali
    "movies": {},      # {"<movie_id>": {"id","title","file_id","mime_type","file_name","size","uploaded_by","created_ts"}} — kino katalogi
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load() -> dict:
    raw, source = config.persist_read(_DATA_FILENAME, _UPSTASH_KEY)

    if not raw:
        return {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in _DEFAULT_DATA.items()}

    try:
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"app_data JSON parse xato: {e} — bo'sh holatdan boshlanadi.")
        return {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in _DEFAULT_DATA.items()}

    for k, v in _DEFAULT_DATA.items():
        data.setdefault(k, dict(v) if isinstance(v, dict) else list(v))
    logger.info(f"{source} dan foydalanuvchi ma'lumotlari (fayllar/statistika/eslatmalar) yuklandi.")
    return data


_data = _load()


def _save() -> None:
    raw = json.dumps(_data, ensure_ascii=False)
    config.persist_write(_DATA_FILENAME, _UPSTASH_KEY, raw, commit_message="📊 Foydalanuvchi fayllari/statistika/eslatmalar yangilandi")


# ============================================================
# 🗂 Foydalanuvchi fayllari tarixi ("Mening fayllarim" uchun)
# ============================================================

def record_file(user_id: int, file_type: str, title: str, file_id: str) -> None:
    """Har safar foydalanuvchiga hujjat (PDF/PPTX) yuborilganda chaqiriladi —
    shu orqali "🗂 Mening fayllarim" bo'limida keyinchalik qayta ko'rsatiladi."""
    with _lock:
        key = str(user_id)
        lst = _data["files"].setdefault(key, [])
        lst.append({"type": file_type, "title": title[:120], "file_id": file_id, "ts": _now_iso()})
        if len(lst) > MAX_FILES_PER_USER:
            del lst[: len(lst) - MAX_FILES_PER_USER]
        _save()
    logger.info(f"🗂 Fayl tarixga qo'shildi: user_id={user_id}, turi={file_type}, sarlavha='{title[:60]}'.")


def get_user_files(user_id: int) -> list:
    """Eng yangisi birinchi bo'lgan tartibda qaytaradi."""
    lst = _data["files"].get(str(user_id), [])
    return list(reversed(lst))


# ============================================================
# 📊 Foydalanish statistikasi (/developer > 📊 Statistika uchun)
# ============================================================

_FUNCTION_LABELS_FOR_STATS = {
    "course_work": "📘 Kurs ishi",
    "essay": "🗒 Referat/Insho",
    "translate": "🌐 Tarjima",
    "images_pdf": "🖼 Suratlarni PDF qilish",
    "edit_pdf": "📝 PDF ni tahrirlash",
    "guide": "📖 Qo'llanma tayyorlash",
    "pptx": "📊 Taqdimot (PPTX)",
    "quiz": "📋 Test/Viktorina",
    "solve": "🧮 Masala yechish",
    "summarize": "📑 Konspekt qisqartirish",
    "grammar": "✅ Imlo/Grammatika tekshirish",
    "citation": "📚 Iqtibos generatori",
    "voice": "🎙 Ovozli xabar",
    "universal_chat": "💬 Universal chat",
}


def record_usage(function_key: str, user_id: int) -> None:
    """Bir funksiyadan MUVAFFAQIYATLI foydalanilganda (masalan PDF tayyor
    bo'lganda) chaqiriladi. Global (bot bo'yicha) va shu funksiya bo'yicha
    noyob foydalanuvchilarni, jami sonini va kunlik sonini yuritadi."""
    with _lock:
        uid = int(user_id)
        if uid not in _data["all_users"]:
            _data["all_users"].append(uid)

        entry = _data["usage"].setdefault(function_key, {"total": 0, "unique_users": [], "by_date": {}})
        entry["total"] = entry.get("total", 0) + 1
        if uid not in entry["unique_users"]:
            entry["unique_users"].append(uid)

        today = _today_str()
        by_date = entry.setdefault("by_date", {})
        by_date[today] = by_date.get(today, 0) + 1
        if len(by_date) > MAX_USAGE_DATES:
            for old_day in sorted(by_date.keys())[: len(by_date) - MAX_USAGE_DATES]:
                del by_date[old_day]

        _save()
    logger.info(f"📊 Foydalanish qayd etildi: funksiya='{function_key}', user_id={user_id}.")


def get_stats() -> dict:
    """/developer > 📊 Statistika uchun umumiy hisobot tayyorlaydi."""
    today = _today_str()
    total_events = sum(e.get("total", 0) for e in _data["usage"].values())
    per_function = []
    for key, label in _FUNCTION_LABELS_FOR_STATS.items():
        e = _data["usage"].get(key)
        if not e:
            per_function.append((label, 0, 0, 0))
            continue
        today_count = e.get("by_date", {}).get(today, 0)
        per_function.append((label, e.get("total", 0), len(e.get("unique_users", [])), today_count))
    per_function.sort(key=lambda x: x[1], reverse=True)
    return {
        "total_events": total_events,
        "total_users": len(_data["all_users"]),
        "per_function": per_function,
    }


# ============================================================
# 🔍 Inline rejim jurnali (/developer > 🔍 Inline jurnali uchun)
# ============================================================
#
# Foydalanuvchi botni GURUHGA A'ZO QILMASDAN yoki SHAXSIY chatda
# "@Student_ai_uz_bot <savol/buyruq>" deb yozib ishlatganda (Telegram
# inline rejimi, qarang: handlers/inline_query.py) shu yerga qayd
# etiladi — qaysi foydalanuvchi, qanday savol/buyruq yuborgani va u
# ISHLAGAN-ISHLAMAGANI (ishlamagan bo'lsa — aniq sababi) bilan birga.

def record_inline_log(user_id: int, username: str, query: str, status: str, detail: str = "") -> None:
    """Har bir inline (@Bot ...) so'rovi ISHLANGANDA (yakuniy natija —
    muvaffaqiyatli javob/fayl BERILGANDA yoki muvaffaqiyatsiz/yo'naltirilgan
    bo'lganda) chaqiriladi.

    status:
      'ok'          — foydalanuvchiga muvaffaqiyatli javob/fayl berildi.
      'error'       — ishlashga urinildi, lekin xatolik chiqdi (detail — sababi).
      'queued'      — inline natija Telegram'ga chiqarildi, og'ir ish tanlashdan keyin boshlanadi.
      'redirect'    — inline rejimda bajarib bo'lmadigan vazifa, shaxsiy
                       chatga yo'naltirildi (detail — sababi).
      'instruction' — buyruq to'liq emas edi (masalan argumentsiz), shu
                       sababli ko'rsatma ko'rsatildi (detail — sababi).
    """
    with _lock:
        lst = _data.setdefault("inline_logs", [])
        lst.append({
            "ts": _now_iso(),
            "user_id": int(user_id) if user_id else 0,
            "username": (username or "")[:64],
            "query": (query or "")[:300],
            "status": status,
            "detail": (detail or "")[:300],
        })
        if len(lst) > MAX_INLINE_LOGS:
            del lst[: len(lst) - MAX_INLINE_LOGS]
        _save()
    logger.info(
        f"🔍 Inline jurnaliga yozildi: user_id={user_id}, username='{username}', "
        f"status={status}, savol='{(query or '')[:80]}'"
        + (f", sabab='{detail[:120]}'" if detail else "") + "."
    )


def get_inline_logs(limit: int = 25, status_filter: str | None = None) -> list:
    """Eng yangisi birinchi bo'lgan tartibda so'nggi yozuvlarni qaytaradi.
    status_filter berilsa (masalan 'error'), faqat o'sha holatdagilar."""
    lst = list(reversed(_data.get("inline_logs", [])))
    if status_filter:
        lst = [e for e in lst if e.get("status") == status_filter]
    return lst[:limit]


# ============================================================
# ⏰ Eslatmalar
# ============================================================

def add_reminder(user_id: int, chat_id: int, text: str, due_ts: float) -> dict:
    with _lock:
        reminder = {
            "id": uuid.uuid4().hex[:12],
            "user_id": int(user_id),
            "chat_id": int(chat_id),
            "text": text[:500],
            "due_ts": due_ts,
            "created_ts": time.time(),
        }
        _data["reminders"].append(reminder)
        _save()
    logger.info(f"⏰ Yangi eslatma qo'shildi: user_id={user_id}, id={reminder['id']}, muddat={datetime.fromtimestamp(due_ts).strftime('%Y-%m-%d %H:%M')}.")
    return reminder


def get_user_reminders(user_id: int) -> list:
    return [r for r in _data["reminders"] if r["user_id"] == int(user_id)]


def get_all_reminders() -> list:
    return list(_data["reminders"])


def remove_reminder(reminder_id: str) -> bool:
    with _lock:
        before = len(_data["reminders"])
        _data["reminders"] = [r for r in _data["reminders"] if r["id"] != reminder_id]
        changed = len(_data["reminders"]) != before
        if changed:
            _save()
    if changed:
        logger.info(f"⏰ Eslatma o'chirildi: id={reminder_id}.")
    return changed


# ============================================================
# 👥 Guruhlarda Universal chat holati
# ============================================================
# STANDART HOLAT — FAOL (True). Bot yangi guruhga qo'shilganda yoki
# deploy/restart bo'lganda, agar o'sha guruh uchun ANIQ "o'chirilgan" yozuvi
# bo'lmasa, u FAOL hisoblanadi. Guruh /ochirish buyrug'i bilan buni ANIQ
# o'chirsa, bu holat shu yerda (Upstash/app_data.json) doimiy saqlanadi va
# keyingi har qanday deployda ham o'sha holicha ("o'chirilgan") qoladi —
# chunki context.chat_data (PTB xotirasi) har restartda tozalanadi, bu esa
# tozalanmaydi.

def is_group_active(chat_id: int) -> bool:
    entry = _data["groups"].get(str(chat_id))
    if entry is None:
        return True  # hali hech qanday sozlama saqlanmagan — standart holat: FAOL
    return bool(entry.get("active", True))


def set_group_active(chat_id: int, active: bool) -> None:
    with _lock:
        key = str(chat_id)
        entry = _data["groups"].setdefault(key, {})
        entry["active"] = bool(active)
        entry["updated_ts"] = _now_iso()
        _save()
    logger.info(f"👥 Guruh holati o'zgartirildi: chat_id={chat_id}, active={active} (doimiy saqlandi).")


# ============================================================
# 🎬 Kino katalogi
# ============================================================

MAX_MOVIES = 1000


def add_movie(title: str, file_id: str, mime_type: str = "video/mp4",
              file_name: str = "", size: int = 0, uploaded_by: int = 0) -> dict:
    """Telegram file_id asosida katalogga kino qo'shadi.
    Faylning o'zi server diskiga ko'chirilmaydi: Telegramdagi media
    saqlanib qoladi va keyinchalik Mini App stream endpointi shu file_id
    orqali foydalanadi."""
    with _lock:
        movie_id = uuid.uuid4().hex[:16]
        movie = {
            "id": movie_id,
            "title": title.strip()[:200],
            "file_id": str(file_id),
            "mime_type": mime_type or "video/mp4",
            "file_name": file_name[:200],
            "size": int(size or 0),
            "uploaded_by": int(uploaded_by or 0),
            "created_ts": time.time(),
        }
        _data.setdefault("movies", {})[movie_id] = movie
        if len(_data["movies"]) > MAX_MOVIES:
            oldest = sorted(_data["movies"].values(), key=lambda x: x.get("created_ts", 0))
            for old in oldest[:len(_data["movies"]) - MAX_MOVIES]:
                _data["movies"].pop(old["id"], None)
        _save()
    logger.info("🎬 Kino katalogga qo'shildi: id=%s title=%r size=%s", movie_id, movie["title"], movie["size"])
    return dict(movie)


def get_movie(movie_id: str) -> dict | None:
    movie = _data.get("movies", {}).get(str(movie_id))
    return dict(movie) if movie else None


def search_movies(query: str = "") -> list[dict]:
    query = (query or "").strip().casefold()
    movies = list(_data.get("movies", {}).values())
    if query:
        words = [w for w in re.split(r"\s+", query) if w]
        def score(m):
            title = m.get("title", "").casefold()
            if query == title:
                return 1000
            if query in title:
                return 800
            return sum(100 for w in words if w in title)
        movies = [m for m in movies if score(m) > 0]
        movies.sort(key=lambda m: (score(m), m.get("created_ts", 0)), reverse=True)
    else:
        movies.sort(key=lambda m: m.get("created_ts", 0), reverse=True)
    return [dict(m) for m in movies]


def count_movies() -> int:
    return len(_data.get("movies", {}))
