"""
❌ Markaziy BEKOR QILISH mexanizmi.

Muammo: avval "/cancel" faqat `context.user_data` ni tozalardi, lekin allaqachon
ishlab turgan vazifa (AI javobi, PDF yig'ish, video yuklash...) to'xtamasdi va
oxirida natijani baribir yuborardi.

Yechim: bot.py dagi `CancellableApplication` har bir kelgan update'ni qayta
ishlayotgan asyncio.Task'ni shu yerda (chat_id, user_id) kaliti bilan qayd etadi.
Foydalanuvchi "❌ Bekor qilish" tugmasini yoki /cancel ni bosganda
`cancel_user_jobs()` shu foydalanuvchining barcha ishlayotgan vazifalarini
`task.cancel()` bilan to'xtatadi (bekor qilayotgan update'ning o'zidan tashqari).

Eslatma: `asyncio.to_thread()` ichidagi oqimni (thread) majburan to'xtatib bo'lmaydi —
lekin kutib turgan coroutine bekor qilinadi, shuning uchun natija yuborilmaydi va
pul band qilinishi (reservation) ozod qilinadi.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

CANCEL_CALLBACK = "cancel:all"
CANCEL_LABEL = "❌ Bekor qilish"

_JOBS: dict[tuple[int, int], set] = {}


def _key(chat_id, user_id) -> tuple[int, int]:
    return (int(chat_id or 0), int(user_id))


def track(chat_id, user_id, task) -> None:
    if user_id is None or task is None:
        return
    _JOBS.setdefault(_key(chat_id, user_id), set()).add(task)


def untrack(chat_id, user_id, task) -> None:
    if user_id is None:
        return
    key = _key(chat_id, user_id)
    tasks = _JOBS.get(key)
    if not tasks:
        return
    tasks.discard(task)
    if not tasks:
        _JOBS.pop(key, None)


def cancel_user_jobs(chat_id, user_id, exclude=None) -> int:
    """Foydalanuvchining ishlayotgan barcha vazifalarini bekor qiladi. Soni qaytadi."""
    if user_id is None:
        return 0
    if exclude is None:
        try:
            exclude = asyncio.current_task()
        except RuntimeError:
            exclude = None
    cancelled = 0
    for task in list(_JOBS.get(_key(chat_id, user_id), ())):
        if task is exclude or task.done():
            continue
        task.cancel()
        cancelled += 1
    if cancelled:
        logger.info("❌ Bekor qilindi: chat=%s user=%s vazifalar=%d", chat_id, user_id, cancelled)
    return cancelled


def active_count(chat_id, user_id) -> int:
    return sum(1 for t in _JOBS.get(_key(chat_id, user_id), ()) if not t.done())


def cancel_markup():
    """Har bir jarayon xabari ostidagi umumiy '❌ Bekor qilish' tugmasi."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[InlineKeyboardButton(CANCEL_LABEL, callback_data=CANCEL_CALLBACK)]])


def with_cancel(markup=None):
    """Mavjud inline klaviaturaga (bo'lsa) pastiga '❌ Bekor qilish' qatorini qo'shadi."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = [list(r) for r in markup.inline_keyboard] if markup is not None else []
    for row in rows:
        if any(getattr(b, "callback_data", None) == CANCEL_CALLBACK for b in row):
            return markup
    rows.append([InlineKeyboardButton(CANCEL_LABEL, callback_data=CANCEL_CALLBACK)])
    return InlineKeyboardMarkup(rows)
