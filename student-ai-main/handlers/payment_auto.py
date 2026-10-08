"""
🤖 To'lovni AVTOMATIK qabul qilish fon vazifasi.

Chek "🧾 Chek yuborish" usulida yuborilib, bot uni tekshirib (karta/ism/
summa) HAMMASI to'g'ri deb topsa, to'lov `wallet.STATUS_AUTO_HOLD` holatiga
o'tadi va shu modul orqali `config.PAYMENT_AUTO_CONFIRM_SECONDS` (standart
1 soat) dan keyin AVTOMATIK qabul qilinadi — agar admin undan OLDIN
tasdiqlamasa/rad etmasa/shubhali deb belgilamasa.

Rejalashtirish `handlers/reminders.py` bilan BIR XIL naqsh bo'yicha —
`asyncio.create_task` orqali, JobQueue kerak emas. Bot qayta ishga
tushganda (deploy/restart) `reschedule_all()` (bot.py post_init'da
chaqiriladi) storage'dagi BARCHA `auto_hold` to'lovlarni qayta
rejalashtiradi — shuning uchun muddat deploy paytida yo'qolmaydi;
muddati allaqachon o'tib ketgan bo'lsa, DARHOL (navbatdagi tsiklda)
avtomatik qabul qilinadi.
"""

import asyncio
import logging
from datetime import datetime, timezone

import config
import wallet
from handlers import payment_notify

logger = logging.getLogger(__name__)

_active_tasks: dict[str, asyncio.Task] = {}


def _seconds_until(due_iso: str) -> float:
    due = datetime.fromisoformat(due_iso)
    now = datetime.now(timezone.utc)
    return (due - now).total_seconds()


async def _wait_and_confirm(application, payment_id: str, due_iso: str) -> None:
    try:
        delay = _seconds_until(due_iso)
        if delay > 0:
            await asyncio.sleep(delay)

        ok = wallet.auto_confirm_payment(payment_id)
        if not ok:
            # Admin allaqachon hal qilgan (approve/reject/susp) yoki to'lov
            # boshqa sababga ko'ra endi auto_hold holatida emas — hech narsa
            # qilinmaydi (bu KUTILGAN holat, xato emas).
            logger.info(f"🤖 auto_confirm: payment_id={payment_id} — endi auto_hold holatida emas, o'tkazib yuborildi.")
            return

        payment = wallet.get_payment(payment_id)
        if not payment:
            return

        from handlers import wallet_ui  # kech import: aylanma import'dan qochish uchun
        try:
            await wallet_ui.notify_user_payment_auto_confirmed(application.bot, payment)
        except Exception as e:
            logger.warning(f"🤖 Foydalanuvchiga avtomatik tasdiq xabarini yuborib bo'lmadi: {e}")

        try:
            await payment_notify.notify_admins_payment_auto_confirmed(application.bot, payment)
        except Exception as e:
            logger.warning(f"🤖 Adminlarga avtomatik tasdiq xabarini yuborib bo'lmadi: {e}")

        logger.info(f"🤖✅ To'lov AVTOMATIK qabul qilindi (admin 1 soat ichida javob bermadi): payment_id={payment_id}.")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"🤖 auto_confirm fon vazifasida xato: payment_id={payment_id}, {type(e).__name__}: {e}", exc_info=True)
    finally:
        _active_tasks.pop(payment_id, None)


def schedule_auto_confirm(application, payment_id: str, due_iso: str) -> None:
    """Yangi (yoki qayta ishga tushirilgandagi) auto_hold to'lov uchun fon
    vazifasini rejalashtiradi. Xuddi shu payment_id uchun eski vazifa bo'lsa
    (masalan qayta chaqirilgan bo'lsa) — bekor qilinib, YANGISI qo'yiladi."""
    old = _active_tasks.get(payment_id)
    if old and not old.done():
        old.cancel()
    task = asyncio.create_task(_wait_and_confirm(application, payment_id, due_iso))
    _active_tasks[payment_id] = task


def reschedule_all(application) -> int:
    """Bot ishga tushganda (post_init) chaqiriladi — storage'dagi BARCHA
    `auto_hold` holatidagi to'lovlarni qayta rejalashtiradi (deploy/restart
    paytida yo'qolib qolmasligi uchun). Nechta to'lov rejalashtirilganini
    qaytaradi."""
    payments = wallet.list_payments(statuses=(wallet.STATUS_AUTO_HOLD,))
    count = 0
    for payment in payments:
        due_iso = payment.get("auto_confirm_at")
        if not due_iso:
            continue
        schedule_auto_confirm(application, payment["payment_id"], due_iso)
        count += 1
    if count:
        logger.info(f"🤖 {count} ta 'auto_hold' to'lov qayta rejalashtirildi (bot qayta ishga tushdi).")
    return count
