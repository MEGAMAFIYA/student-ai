"""
📝 Testlar bazasi — DOIMIY (persistent) saqlash.

Loyihada allaqachon mavjud bo'lgan umumiy mexanizmdan (config.persist_read /
config.persist_write) foydalanadi — bu AI kalitlari va storage.py
(fayllar/statistika/eslatmalar) bilan BIR XIL ustuvorlikda ishlaydi:
Upstash Redis -> Neon (Postgres) -> GitHub repo -> mahalliy fayl.

Demak, agar .env'da UPSTASH_REDIS_REST_URL/TOKEN, DATABASE_URL yoki
GITHUB_TOKEN/GITHUB_REPO'dan biri sozlangan bo'lsa (bu loyihada DATABASE_URL
sozlangan), qo'shgan testlaringiz Render qayta deploy qilinganda ham
O'CHIB KETMAYDI.

MUHIM: quyidagi barcha funksiyalarning nomi/imzosi avvalgi (sqlite asosidagi)
versiya bilan bir xil qoldirilgan — shuning uchun handlers/managed_tests.py
faylida HECH QANDAY o'zgarish shart emas.
"""

import json
import logging
import threading

import config

logger = logging.getLogger(__name__)

_DATA_FILENAME = "tests_data.json"
_UPSTASH_KEY = "student_ai_tests_db"

_lock = threading.Lock()

_DEFAULT_DATA = {
    "topics": {},        # {"<topic_id>": {"name": str, "active": 0/1}}
    "questions": {},     # {"<question_id>": {"topic_id": int, "question": str, "options": [...], "correct": int, "active": 0/1}}
    "next_topic_id": 1,
    "next_question_id": 1,
}


def _load() -> dict:
    raw, source = config.persist_read(_DATA_FILENAME, _UPSTASH_KEY)
    if not raw:
        return json.loads(json.dumps(_DEFAULT_DATA))  # chuqur nusxa

    try:
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"Testlar bazasini JSON qilib o'qishda xato: {e} — bo'sh holatdan boshlanadi.")
        return json.loads(json.dumps(_DEFAULT_DATA))

    for k, v in _DEFAULT_DATA.items():
        data.setdefault(k, json.loads(json.dumps(v)))
    logger.info(f"{source} dan testlar bazasi yuklandi ({len(data.get('topics', {}))} mavzu, {len(data.get('questions', {}))} savol).")
    return data


_data = _load()


def _save() -> None:
    raw = json.dumps(_data, ensure_ascii=False)
    config.persist_write(_DATA_FILENAME, _UPSTASH_KEY, raw, commit_message="📝 Testlar bazasi yangilandi")


def init():
    """Eski (sqlite) versiyada jadval yaratish uchun chaqirilardi — bu
    versiyada shart emas, faqat moslik uchun bo'sh funksiya sifatida
    qoldirilgan."""
    return None


# ============================================================
# 📚 Mavzular
# ============================================================

def topics(active_only=False):
    rows = []
    for tid_s, t in _data["topics"].items():
        if active_only and not t.get("active"):
            continue
        rows.append((int(tid_s), t["name"], int(t.get("active", 0))))
    rows.sort(key=lambda r: r[0])
    return rows


def get_topic(tid):
    t = _data["topics"].get(str(tid))
    if not t:
        return None
    return (int(tid), t["name"], int(t.get("active", 0)))


def get_topic_by_name(name):
    for tid_s, t in _data["topics"].items():
        if t["name"] == name:
            return (int(tid_s), t["name"], int(t.get("active", 0)))
    return None


def add_topic(name):
    """Yangi mavzu qo'shadi va uni darhol FAOL qiladi. Agar shu nomli mavzu
    allaqachon mavjud bo'lsa, uning holatiga tegmaydi — shunchaki id'sini
    qaytaradi (savol qo'shishni davom ettirish uchun)."""
    with _lock:
        existing = get_topic_by_name(name)
        if existing:
            return existing[0]
        tid = _data["next_topic_id"]
        _data["next_topic_id"] = tid + 1
        _data["topics"][str(tid)] = {"name": name, "active": 1}
        _save()
        return tid


def toggle_topic(tid):
    with _lock:
        t = _data["topics"].get(str(tid))
        if t:
            t["active"] = 0 if t.get("active") else 1
            _save()


def delete_topic(tid):
    with _lock:
        _data["topics"].pop(str(tid), None)
        for qid_s in [k for k, v in _data["questions"].items() if v.get("topic_id") == tid]:
            _data["questions"].pop(qid_s, None)
        _save()


def count_questions(tid):
    return sum(1 for v in _data["questions"].values() if v.get("topic_id") == tid)


# ============================================================
# ❓ Savollar
# ============================================================

def add_question(tid, q, opts, correct):
    with _lock:
        qid = _data["next_question_id"]
        _data["next_question_id"] = qid + 1
        _data["questions"][str(qid)] = {
            "topic_id": tid,
            "question": q,
            "options": list(opts),
            "correct": int(correct),
            "active": 1,
        }
        _save()


def list_questions(tid):
    """Berilgan mavzudagi barcha savollarni (faol/nofaol farqisiz) qaytaradi —
    tahrirlash/o'chirish ro'yxati uchun."""
    rows = [
        (int(qid_s), v["question"], v["options"], int(v.get("correct", 0)), int(v.get("active", 1)))
        for qid_s, v in _data["questions"].items() if v.get("topic_id") == tid
    ]
    rows.sort(key=lambda r: r[0])
    return rows


def get_question(qid):
    v = _data["questions"].get(str(qid))
    if not v:
        return None
    return (int(qid), v["topic_id"], v["question"], v["options"], int(v.get("correct", 0)), int(v.get("active", 1)))


def update_question(qid, question=None, options=None, correct=None):
    with _lock:
        v = _data["questions"].get(str(qid))
        if not v:
            return
        if question is not None:
            v["question"] = question
        if options is not None:
            v["options"] = list(options)
        if correct is not None:
            v["correct"] = int(correct)
        _save()


def delete_question(qid):
    with _lock:
        _data["questions"].pop(str(qid), None)
        _save()


def get_questions(active_only=True):
    rows = []
    for qid_s, v in _data["questions"].items():
        if active_only and not v.get("active"):
            continue
        t = _data["topics"].get(str(v.get("topic_id")))
        if active_only and (not t or not t.get("active")):
            continue
        topic_name = t["name"] if t else "?"
        rows.append((int(qid_s), topic_name, v["question"], v["options"], int(v.get("correct", 0))))
    return rows
