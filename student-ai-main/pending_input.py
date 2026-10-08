"""
⏳ Ikki bosqichli buyruq kiritish holati ("/qoshiq" va "/vid" argumentsiz
yuborilganda — bot "...yuboring" deb so'raydi, keyingi xabar shu yerda
saqlangan holat orqali to'g'ri handlerga yo'naltiriladi).

MUHIM (parallel foydalanuvchilar / guruh xavfsizligi): holat FAQAT
(chat_id, user_id) juftligi bilan bog'lanadi — shu orqali bitta guruhda
bir nechta foydalanuvchi bir vaqtda "/qoshiq" yoki "/vid" ishlatsa ham,
ularning "kutish" holatlari bir-biriga ARALASHMAYDI (A ning navbatdagi
xabari B ning so'roviga hech qachon yubormaydi).

Xotira faqat shu jarayon (process) davomida saqlanadi — bot qayta
ishga tushsa (Render qayta deploy/restart) tozalanadi, bu esa muammo
EMAS: eng yomon holatda foydalanuvchi shunchaki qayta "/qoshiq" yoki
"/vid" deb yozadi.
"""

import time

import config

# (chat_id, user_id) -> {"kind": "qoshiq" | "vid", "ts": float}
_PENDING: dict[tuple[int, int], dict] = {}


def _key(chat_id: int, user_id: int) -> tuple[int, int]:
    return (chat_id, user_id)


def set_pending(chat_id: int, user_id: int, kind: str) -> None:
    """Foydalanuvchi keyingi oddiy matn xabarini `kind` ("qoshiq"/"vid")
    uchun kutayotganini belgilaydi. Oldingi (agar bo'lsa, boshqa
    "kind"dagi) kutish holati AVTOMATIK almashtiriladi — shu orqali
    foydalanuvchi "/qoshiq" dan keyin fikridan qaytib "/vid" yuborsa,
    faqat OXIRGI buyruq kutiladi."""
    _PENDING[_key(chat_id, user_id)] = {"kind": kind, "ts": time.time()}


def pop_pending(chat_id: int, user_id: int) -> str | None:
    """Kutilayotgan "kind"ni QAYTARIB OLIB TASHLAYDI (bir marta
    ishlatiladi — xuddi shu xabar ikkinchi marta boshqa holatga
    tushib ketmasin). Muddati o'tgan bo'lsa `None` qaytaradi."""
    entry = _PENDING.pop(_key(chat_id, user_id), None)
    if not entry:
        return None
    if time.time() - entry["ts"] > config.PENDING_INPUT_TTL_SEC:
        return None
    return entry["kind"]


def clear_pending(chat_id: int, user_id: int) -> None:
    """Kutish holatini (mavjud bo'lsa) bekor qiladi — foydalanuvchi
    boshqa biror buyruq ("/start", "/help" va h.k.) yuborganda
    chaqiriladi (qarang: bot.py > `_clear_pending_on_any_command`)."""
    _PENDING.pop(_key(chat_id, user_id), None)