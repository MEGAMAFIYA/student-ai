"""
🧮 HISOBCHI — botda bo'layotgan hamma narsani admin uchun yig'ib boradigan hisobchi.

Admin /hisobchi buyrug'ini berganda (handlers/hisobchi_cmd.py) shu yerda to'plangan
ma'lumotlar ko'rsatiladi: qaysi funksiya ishlatildi (kurs ishi, referat, taqdimot ...),
mavzu, foydalanuvchi, nechta va QAYSI raqamdagi AI kaliti ishladi (gemini#2 ×14),
nechta xato/limit bo'ldi va qancha pul (limit) sarflandi.

⚡ ASOSIY QOIDA: BU MODUL BOTNI HECH QACHON SEKINLASHTIRMASLIGI VA QOTIRMASLIGI KERAK.
  • Barcha "ilgak" (hook) funksiyalar FAQAT xotiradagi kichik lug'atlarga yozadi —
    tarmoq, disk, await, sleep YO'Q (mikrosekundlar).
  • Har bir hook try/except ichida: hisobchi xato qilsa ham asosiy funksiya
    ishlayveradi (xato faqat logga, minutiga 1 martadan ko'p emas).
  • Saqlash — alohida fon thread'da, har HISOBCHI_SAVE_SEC soniyada (standart 60),
    faqat o'zgarish bo'lsa; event loop va boshqa yozuvlar (storage/wallet) bilan
    bir navbatda turmaydi.
  • Hajm chegaralangan (MAX_EVENTS / MAX_USERS / KEEP_DAYS) — xotira va Upstash
    yozuv hajmi o'smaydi.
  • .env da HISOBCHI_ENABLED=0 qo'yilsa — butun modul o'chadi (barcha hook no-op).

Maxfiylik: faqat provider#raqam va model nomi saqlanadi — API kalitning o'zi
HECH QACHON saqlanmaydi. Mavzu 80 belgigacha qisqartiriladi.
"""

import atexit
import contextvars
import functools
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import config

logger = logging.getLogger(__name__)

ENABLED = os.getenv("HISOBCHI_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")

MAX_EVENTS = int(os.getenv("HISOBCHI_MAX_EVENTS", "400") or 400)
MAX_USERS = int(os.getenv("HISOBCHI_MAX_USERS", "1500") or 1500)
KEEP_DAYS = 45

_FILENAME = "hisobchi_data.json"
_UPSTASH_KEY = "student_ai_hisobchi"

# Faqat GitHub (commit) orqali saqlanayotgan bo'lsa tez-tez yozish repo'ni commit'larga to'ldirib
# yuboradi — shuning uchun u holda oraliq uzoqroq.
_github_only = bool(getattr(config, "USE_GITHUB", False)) and not getattr(config, "USE_UPSTASH", False) \
    and not getattr(config, "USE_NEON", False)
SAVE_SEC = float(os.getenv("HISOBCHI_SAVE_SEC", "300" if _github_only else "60") or 60)

# O'zbekiston vaqti (UTC+5, yozgi vaqt yo'q) — "bugun" shu bo'yicha hisoblanadi.
TZ = timezone(timedelta(hours=5))

MERGE_WINDOW_SEC = 180   # bir ishning ikki xil hook'dan kelgan yozuvi (record_usage + to'lov) birlashadi
REFUND_WINDOW_SEC = 600

_lock = threading.Lock()
_dirty = False
_writer = None
_last_err_log = 0.0

# Har bir Telegram update (so'rov) uchun alohida "iz" (trace): shu so'rov ichida
# ishlagan AI chaqiruvlari yig'iladi. Lug'at o'zgaruvchan (mutable) bo'lgani uchun
# asyncio.create_task() bilan ochilgan ichki vazifalar ham xuddi shu lug'atga yozadi.
_trace_var: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar("hisobchi_trace", default=None)

_open: dict = {}           # (uid, feature) -> {"ev": event, "src": set}
_last_by_user: dict = {}   # uid -> event (so'nggi) — keyin kelgan qaytarish (refund) shunga yoziladi
_pending_refund: dict = {} # uid -> [amount, ts] — hali event yaratilmagan paytdagi qaytarishlar
_last_title: dict = {}     # uid -> (title, ts) — trace bo'lmagan joyda (mobil API) mavzuni topish uchun


def _new_state() -> dict:
    return {"v": 1, "since": datetime.now(TZ).isoformat(timespec="seconds"),
            "events": [], "ai": {}, "fn": {}, "users": {}, "days": {}}


def _load() -> dict:
    if not ENABLED:
        return _new_state()
    try:
        raw, source = config.persist_read(_FILENAME, _UPSTASH_KEY)
        if not raw:
            return _new_state()
        data = json.loads(raw)
        base = _new_state()
        for k, v in base.items():
            data.setdefault(k, v)
        logger.info(f"🧮 Hisobchi ma'lumotlari {source} dan yuklandi ({len(data['events'])} ta hodisa).")
        return data
    except Exception as e:
        logger.error(f"🧮 Hisobchi ma'lumotlarini yuklashda xato ({type(e).__name__}: {e}) — bo'sh holatdan boshlanadi.")
        return _new_state()


_state = _load()


# ============================================================
# Yordamchilar
# ============================================================

def _day(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), TZ).strftime("%Y-%m-%d")


def today_str() -> str:
    return _day()


def norm_feature(feature_id: str) -> str:
    """'course_work_20' -> 'course_work' (wallet'dagi bet-bo'yicha narx variantlari bitta funksiya)."""
    return re.sub(r"_\d+$", "", str(feature_id or "")) or "unknown"


def _log_err(where: str, exc: Exception) -> None:
    global _last_err_log
    now = time.monotonic()
    if now - _last_err_log > 60:
        _last_err_log = now
        logger.warning(f"🧮 Hisobchi ({where}) xatosi e'tiborsiz qoldirildi: {type(exc).__name__}: {exc}")


def _safe(fn):
    """Hook hech qachon istisno ko'tarmasligi va ENABLED=0 bo'lsa umuman ishlamasligi uchun."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not ENABLED:
            return None
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — hisobchi asosiy ishga xalaqit bermasligi shart
            _log_err(fn.__name__, e)
            return None
    return wrapper


def _day_row(day: str) -> dict:
    rows = _state["days"]
    row = rows.get(day)
    if row is None:
        row = rows[day] = {"jobs": 0, "fail": 0, "ai_ok": 0, "ai_fail": 0, "quota": 0,
                           "spent": 0, "refund": 0, "topup": 0}
        if len(rows) > KEEP_DAYS:
            for old in sorted(rows)[: len(rows) - KEEP_DAYS]:
                del rows[old]
    return row


def _user_row(uid: int, ts: float) -> dict:
    users = _state["users"]
    row = users.get(str(uid))
    if row is None:
        row = users[str(uid)] = {"n": 0, "fail": 0, "spent": 0, "topup": 0, "ai": 0, "last": ts, "fn": {}}
        if len(users) > MAX_USERS + 100:
            keep = sorted(users.items(), key=lambda kv: kv[1].get("last", 0), reverse=True)[:MAX_USERS]
            _state["users"] = dict(keep)
            row = _state["users"].setdefault(str(uid), row)
    row["last"] = max(row.get("last", 0), ts)
    return row


def _mark_dirty() -> None:
    global _dirty
    _dirty = True
    _ensure_writer()


def _clip(text, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ============================================================
# Saqlash (alohida fon thread — asosiy oqimga tegmaydi)
# ============================================================

def _ensure_writer() -> None:
    global _writer
    if _writer is not None and _writer.is_alive():
        return
    _writer = threading.Thread(target=_writer_loop, name="hisobchi-writer", daemon=True)
    _writer.start()


def _writer_loop() -> None:
    while True:
        time.sleep(SAVE_SEC)
        try:
            flush()
        except Exception as e:  # noqa: BLE001
            _log_err("writer", e)


def flush() -> bool:
    """O'zgarish bo'lsa, holatni tashqi saqlashga yozadi. Fon thread yoki chiqishda chaqiriladi."""
    global _dirty
    if not ENABLED:
        return False
    with _lock:
        if not _dirty:
            return False
        _dirty = False
        raw = json.dumps(_state, ensure_ascii=False, separators=(",", ":"))
    try:
        config.persist_write(_FILENAME, _UPSTASH_KEY, raw, commit_message="🧮 Hisobchi hisoboti yangilandi")
        return True
    except Exception as e:  # noqa: BLE001
        _dirty = True  # keyingi safar qayta uriniladi
        _log_err("flush", e)
        return False


atexit.register(flush)


# ============================================================
# Iz (trace) — bitta so'rov ichidagi AI chaqiruvlarini yig'ish
# ============================================================

@_safe
def begin(user_id) -> None:
    """Har bir Telegram update boshida (bot.py > group=-3) chaqiriladi."""
    _trace_var.set({"uid": int(user_id) if user_id else 0, "ai": {}, "aif": 0, "lim": 0,
                    "topic": "", "pages": None})


@_safe
def set_detail(topic: str | None = None, pages: int | None = None) -> None:
    """Handler ishning mavzusini/hajmini aytib qo'yadi (masalan kurs ishi mavzusi)."""
    trace = _trace_var.get()
    if trace is None:
        return
    if topic:
        trace["topic"] = _clip(topic, 80)
    if pages:
        trace["pages"] = int(pages)


@_safe
def ai_call(provider: str, idx, model: str, ok: bool, status: str = "ok", source: str = "bot") -> None:
    """ai_clients.py'dagi HAR BIR AI urinishidan keyin chaqiriladi (muvaffaqiyatli yoki yo'q).
    idx — kalit to'plamidagi tartib raqami (gemini #2 -> 2); source: 'bot' | 'user'."""
    provider = str(provider or "?")
    if source == "user":
        key = f"user:{provider}"          # foydalanuvchilarning shaxsiy kalitlari — provayder bo'yicha umumlashtiriladi
    else:
        key = f"{provider}#{idx}" if idx not in (None, "", 0) else provider
    now = time.time()
    day = _day(now)
    trace = _trace_var.get()
    with _lock:
        rec = _state["ai"].get(key)
        if rec is None:
            rec = _state["ai"][key] = {"p": provider, "m": "", "ok": 0, "fail": 0, "quota": 0, "paid": 0,
                                       "invalid": 0, "error": 0, "last": 0, "days": {}}
        if model:
            rec["m"] = _clip(model, 60)
        rec["last"] = now
        d = rec["days"].setdefault(day, [0, 0])
        row = _day_row(day)
        if ok:
            rec["ok"] += 1
            d[0] += 1
            row["ai_ok"] += 1
        else:
            rec["fail"] += 1
            d[1] += 1
            row["ai_fail"] += 1
            st = status if status in ("quota", "paid", "invalid", "error") else "error"
            rec[st] += 1
            if st == "quota":
                row["quota"] += 1
        if len(rec["days"]) > KEEP_DAYS:
            for old in sorted(rec["days"])[: len(rec["days"]) - KEEP_DAYS]:
                del rec["days"][old]
        if trace is not None:
            if ok:
                trace["ai"][key] = trace["ai"].get(key, 0) + 1
            else:
                trace["aif"] += 1
                if status == "quota":
                    trace["lim"] += 1
        if ok:
            uid = trace["uid"] if trace is not None else 0
            if uid:
                _user_row(uid, now)["ai"] += 1
    _mark_dirty()


# ============================================================
# Ishlar (event) — kurs ishi tayyorlandi, referat, test ...
# ============================================================

def _take_trace(uid_hint: int) -> tuple[dict, int, int, str, int | None, int]:
    """Iz ichida yig'ilgan AI ma'lumotini oladi va NOLGA tushiradi (keyingi ish ikki marta sanamasligi uchun)."""
    trace = _trace_var.get()
    if trace is None:
        return {}, 0, 0, "", None, uid_hint
    ai, aif, lim = dict(trace["ai"]), trace["aif"], trace["lim"]
    topic, pages = trace.get("topic", ""), trace.get("pages")
    trace["ai"], trace["aif"], trace["lim"] = {}, 0, 0
    return ai, aif, lim, topic, pages, (uid_hint or trace.get("uid") or 0)


def _record_job(source: str, uid, feature: str, ok: bool, price: int = 0, why: str = "") -> None:
    now = time.time()
    feature = norm_feature(feature)
    ai, aif, lim, topic, pages, uid = _take_trace(int(uid) if uid else 0)
    if not topic and uid:
        t = _last_title.get(uid)
        if t and now - t[1] < 180:
            topic = t[0]
    day = _day(now)
    with _lock:
        open_ = _open.get((uid, feature))
        if ok and open_ and open_["ev"].get("ok") and now - open_["ev"]["t"] < MERGE_WINDOW_SEC \
                and source not in open_["src"]:
            ev = open_["ev"]                       # xuddi shu ishning ikkinchi hook'i — birlashtiramiz
            open_["src"].add(source)
            if price and not ev.get("price"):
                ev["price"] = int(price)
                _state["fn"].setdefault(feature, {"n": 0, "fail": 0, "price": 0, "ai": 0, "days": {}})["price"] += int(price)
            if topic and not ev.get("topic"):
                ev["topic"] = topic
            if pages and not ev.get("pg"):
                ev["pg"] = pages
            for k, n in ai.items():
                ev.setdefault("ai", {})[k] = ev.get("ai", {}).get(k, 0) + n
            ev["aif"] = ev.get("aif", 0) + aif
            ev["lim"] = ev.get("lim", 0) + lim
        else:
            ev = {"t": now, "u": uid, "f": feature, "ok": bool(ok)}
            if topic:
                ev["topic"] = topic
            if pages:
                ev["pg"] = pages
            if price:
                ev["price"] = int(price)
            if ai:
                ev["ai"] = ai
            if aif:
                ev["aif"] = aif
            if lim:
                ev["lim"] = lim
            if why:
                ev["why"] = _clip(why, 120)
            pr = _pending_refund.pop(uid, None)
            if pr and now - pr[1] < REFUND_WINDOW_SEC:
                ev["rf"] = pr[0]
            events = _state["events"]
            events.append(ev)
            if len(events) > MAX_EVENTS + 50:
                del events[: len(events) - MAX_EVENTS]
            if ok:
                _open[(uid, feature)] = {"ev": ev, "src": {source}}
            if len(_open) > 300:
                for k in [k for k, v in _open.items() if now - v["ev"]["t"] > MERGE_WINDOW_SEC]:
                    del _open[k]
            _last_by_user[uid] = ev
            if len(_last_by_user) > 2000:
                _last_by_user.clear()

            fn = _state["fn"].setdefault(feature, {"n": 0, "fail": 0, "price": 0, "ai": 0, "days": {}})
            fn["n" if ok else "fail"] += 1
            if ok and price:
                fn["price"] += int(price)
            fn["ai"] += sum(ai.values())
            fn["days"][day] = fn["days"].get(day, 0) + 1
            if len(fn["days"]) > KEEP_DAYS:
                for old in sorted(fn["days"])[: len(fn["days"]) - KEEP_DAYS]:
                    del fn["days"][old]
            row = _day_row(day)
            row["jobs" if ok else "fail"] += 1
            if uid:
                u = _user_row(uid, now)
                u["n" if ok else "fail"] += 1
                u["fn"][feature] = u["fn"].get(feature, 0) + 1
    _mark_dirty()


@_safe
def on_usage(function_key: str, user_id) -> None:
    """storage.record_usage() ichidan: funksiya MUVAFFAQIYATLI yakunlandi."""
    _record_job("usage", user_id, function_key, True)


@_safe
def on_file(user_id, file_type: str, title: str) -> None:
    """storage.record_file() ichidan: tayyor fayl sarlavhasi (odatda mavzu) — ishga biriktiriladi."""
    title = _clip(title, 80)
    if not title:
        return
    trace = _trace_var.get()
    if trace is not None and not trace.get("topic"):
        trace["topic"] = title
    if user_id:
        _last_title[int(user_id)] = (title, time.time())
        if len(_last_title) > 500:
            _last_title.clear()


@_safe
def on_paid_done(user_id, feature_id: str, price: int = 0) -> None:
    """wallet_ui.finalize_success() ichidan: pullik funksiya muvaffaqiyatli tugadi, summa yechildi."""
    _record_job("paid", user_id, feature_id, True, price=int(price or 0))


@_safe
def on_job_fail(user_id, feature_id: str, reason: str = "") -> None:
    """wallet_ui.finalize_failure() ichidan: xizmat muvaffaqiyatsiz tugadi (pul yechilmaydi)."""
    _record_job("fail", user_id, feature_id, False, why=reason)


@_safe
def on_tx(tx: dict) -> None:
    """wallet._new_tx_locked() ichidan (har bir balans harakati): sarflangan limit/qaytarish/to'ldirish."""
    t, amount, uid = tx.get("type"), int(tx.get("amount", 0)), int(tx.get("user_id", 0))
    now = time.time()
    day = _day(now)
    with _lock:
        row = _day_row(day)
        u = _user_row(uid, now) if uid else None
        if t == "feature_charge" and amount < 0:
            row["spent"] += -amount
            if u is not None:
                u["spent"] += -amount
        elif t == "refund" and amount > 0:
            row["refund"] += amount
            if u is not None:
                u["spent"] -= amount
            ev = _last_by_user.get(uid)
            if ev is not None and now - ev["t"] < MERGE_WINDOW_SEC:
                ev["rf"] = ev.get("rf", 0) + amount
            else:
                pr = _pending_refund.get(uid)
                _pending_refund[uid] = [(pr[0] if pr and now - pr[1] < REFUND_WINDOW_SEC else 0) + amount, now]
                if len(_pending_refund) > 500:
                    _pending_refund.clear()
        elif t == "topup" and amount > 0:
            row["topup"] += amount
            if u is not None:
                u["topup"] += amount
        else:
            return
    _mark_dirty()


# ============================================================
# O'qish (faqat /hisobchi ko'rsatishi uchun) — nusxalar qaytaradi
# ============================================================

def summary(day: str | None = None) -> dict:
    """Bir kun (None -> bugun) uchun qisqa hisobot + jami."""
    day = day or today_str()
    with _lock:
        row = dict(_state["days"].get(day) or {})
        total = {"jobs": 0, "fail": 0, "ai_ok": 0, "ai_fail": 0, "quota": 0, "spent": 0, "refund": 0, "topup": 0}
        for r in _state["days"].values():
            for k in total:
                total[k] += r.get(k, 0)
        users = _state["users"]
        active_today = sum(1 for u in users.values() if _day(u.get("last", 0)) == day)
        return {"day": day, "today": {k: row.get(k, 0) for k in total}, "total": total,
                "users": len(users), "active_today": active_today, "since": _state.get("since", "")}


def recent_events(limit: int = 8, offset: int = 0, user_id: int | None = None) -> tuple[list, int]:
    """Eng yangisi birinchi. (sahifadagi hodisalar, jami soni)."""
    with _lock:
        evs = _state["events"]
        if user_id is not None:
            evs = [e for e in evs if e.get("u") == user_id]
        total = len(evs)
        page = list(reversed(evs))[offset: offset + limit]
        return [dict(e) for e in page], total


def ai_table() -> list[tuple[str, dict]]:
    with _lock:
        return [(k, {**v, "days": dict(v.get("days", {}))}) for k, v in sorted(_state["ai"].items())]


def fn_table() -> list[tuple[str, dict]]:
    with _lock:
        return [(k, dict(v)) for k, v in sorted(_state["fn"].items(), key=lambda kv: kv[1].get("n", 0), reverse=True)]


def top_users(sort: str = "a", limit: int = 5, offset: int = 0) -> tuple[list[tuple[int, dict]], int]:
    """sort: 'a' — oxirgi faollik bo'yicha, 's' — sarflangan summa bo'yicha."""
    with _lock:
        items = [(int(uid), dict(u)) for uid, u in _state["users"].items()]
    keyf = (lambda kv: kv[1].get("spent", 0)) if sort == "s" else (lambda kv: kv[1].get("last", 0))
    items.sort(key=keyf, reverse=True)
    return items[offset: offset + limit], len(items)


def user_stats(uid: int) -> dict | None:
    with _lock:
        u = _state["users"].get(str(int(uid)))
        return {**u, "fn": dict(u.get("fn", {}))} if u else None


def find_user_by_username(username: str) -> int | None:
    """storage'dagi profillar bo'yicha @username -> user_id (faqat admin buyrug'ida, tez)."""
    try:
        import storage
        uname = username.lstrip("@").lower()
        for uid, prof in storage._data.get("user_profiles", {}).items():
            if (prof.get("username") or "").lower() == uname:
                return int(uid)
    except Exception as e:  # noqa: BLE001
        _log_err("find_user", e)
    return None


def reset_for_tests() -> None:
    """Faqat testlar uchun."""
    global _state, _dirty
    with _lock:
        _state = _new_state()
        _dirty = False
        _open.clear()
        _last_by_user.clear()
        _pending_refund.clear()
        _last_title.clear()
