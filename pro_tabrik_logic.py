"""
💎 /pro — /tabrik'ning "Pro" versiyasi: xuddi shu countdown + naqsh
animatsiyasi (tabrik_logic.py'dan qayta ishlatiladi — DUPLICATE yo'q),
lekin animatsiya oxirida, tabrik matni ochilishidan OLDIN, foydalanuvchi
"👤 Mening kabinetim" (/my) orqali GitHub'ga yuklab qo'ygan RASMLARINING
slайд-shousi ko'rsatiladi (har biri 1 soniya, keyin keyingisi bilan
almashtiriladi).

Bu yerda FAQAT sof mantiq (matn parsing, xotiradagi ombor — rasmlar
RO'YXATI yuborilgan PAYTDA "muzlatib" saqlanadi, keyinchalik foydalanuvchi
GitHub'dagi rasmlarini o'zgartirsa ham, ALLAQACHON YUBORILGAN tabriknoma
o'sha vaqtdagi rasmlar bilan qoladi — bu ataylab shunday, aks holda bir xil
short_id bir necha marta bosilganda turli natija berishi mumkin edi).
Telegram bilan bog'liq kod handlers/pro_tabrik.py'da.
"""

import re
import time
import uuid

import tabrik_logic  # ASCII-art countdown/naqsh funksiyalari QAYTA ishlatiladi

_COMMAND_RE = re.compile(r"^/pro(?:@\w+)?\s*", re.IGNORECASE)


def parse_pro_text(raw_message_text: str) -> str | None:
    """Xuddi tabrik_logic.parse_tabrik_text kabi, lekin '/pro' buyrug'i
    uchun."""
    if not raw_message_text:
        return None
    text = _COMMAND_RE.sub("", raw_message_text, count=1).strip()
    return text or None


# ------------------------------------------------------------------
# Xotiradagi ombor — {"text","photos","user_id","created_at"}
# ------------------------------------------------------------------
_STORE: dict[str, dict] = {}
ENTRY_TTL_SECONDS = 60 * 60 * 24  # 1 kun — tabrik_logic bilan bir xil
MAX_ENTRIES = 2000


def _purge_expired(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    expired = [k for k, v in _STORE.items() if now - v["created_at"] > ENTRY_TTL_SECONDS]
    for k in expired:
        del _STORE[k]
    if len(_STORE) > MAX_ENTRIES:
        oldest = sorted(_STORE.items(), key=lambda kv: kv[1]["created_at"])
        for k, _ in oldest[: len(_STORE) - MAX_ENTRIES]:
            del _STORE[k]


def store_pro_greeting(text: str, user_id: int, photos: list[str]) -> str:
    """`photos` — yuborilgan PAYTDAGI GitHub rasm URL'lari ro'yxati
    (chaqiruvchi handlers/pro_tabrik.py buni github_storage orqali oldindan
    olib, shu yerga "muzlatilgan" holda beradi)."""
    short_id = uuid.uuid4().hex[:10]
    _STORE[short_id] = {
        "text": text, "photos": list(photos), "user_id": int(user_id), "created_at": time.time(),
    }
    _purge_expired()
    return short_id


def get_pro_greeting(short_id: str) -> dict | None:
    _purge_expired()
    return _STORE.get(short_id)


def touch_pro_greeting(short_id: str) -> None:
    entry = _STORE.get(short_id)
    if entry:
        entry["created_at"] = time.time()


def build_ready_card() -> str:
    """/tabrik bilan BIR XIL — tugma hali bosilmagan holatdagi minimal
    matn/izoh (rasm ostidagi caption sifatida ishlatiladi)."""
    return "💎 Sizga Pro tabriknoma bor!"
