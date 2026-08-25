"""
⏰ Eslatmalar — foydalanuvchi matn va vaqt beradi, belgilangan vaqtda
bot avtomatik shu chatga eslatma xabarini yuboradi.

Vaqt formatlari (barchasi O'zbekiston, Toshkent lokal vaqti — config.py dagi
REMINDER_TZ_OFFSET_HOURS orqali sozlanadi):
  - "01.09.2026 18:00"  (kun.oy.yil soat:daqiqa)
  - "01.09 18:00"       (joriy yil deb olinadi)
  - "bugun 20:30"
  - "ertaga 09:00"
  - "3 soatdan keyin"
  - "2 kundan keyin"
  - "45 daqiqadan keyin"

Rejalashtirish REAL VAQTDA jarayon ichida (asyncio.create_task orqali)
amalga oshiriladi — bot qayta ishga tushganda (deploy) barcha kelajakdagi
eslatmalar storage.py dan qayta o'qilib, qayta rejalashtiriladi
(reschedule_all() — bot.py post_init'da chaqiriladi), shuning uchun
eslatmalar deploy paytida yo'qolmaydi (agar storage doimiy saqlashda
bo'lsa — Upstash yoki GitHub, config.py ga qarang).

MUHIM: Render'ning BEPUL tarifi uzoq vaqt so'rov kelmasa jarayonni
"uxlatib qo'yishi" (spin down) mumkin — shu holatda uxlab turgan vaqtda
eslatma vaqti kelib o'tib ketishi mumkin. Bot uyg'onganda (keyingi so'rov
kelganda) reschedule_all() qayta ishga tushmaydi (faqat bot butunlay
qayta ishga tushganda ishlaydi), shuning uchun BEPUL tarifda eslatmalar
100% aniq vaqtda kelishi kafolatlanmaydi — buni foydalanuvchiga aytib
qo'yish tavsiya etiladi.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

import config
import storage

logger = logging.getLogger(__name__)

RM_TEXT, RM_TIME = range(2)

_TZ = timezone(timedelta(hours=config.REMINDER_TZ_OFFSET_HOURS))

_REL_RE = re.compile(r"^(\d+)\s*(daqiqa|soat|kun)dan?\s*keyin$", re.IGNORECASE)
_ABS_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s+(\d{1,2}):(\d{2})$")
_TODAY_RE = re.compile(r"^(bugun|ertaga)\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)

_active_tasks: dict[str, asyncio.Task] = {}  # reminder_id -> task (qayta o'chirish uchun)


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Yangi eslatma", callback_data="remind:new")],
        [InlineKeyboardButton("📋 Eslatmalarim", callback_data="remind:list")],
        [InlineKeyboardButton("🏠 Bosh menyu", callback_data="menu:back")],
    ])


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"⏰ 'Eslatmalar' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        await query.edit_message_text("⏰ *Eslatmalar*\n\nNima qilmoqchisiz?", parse_mode="Markdown", reply_markup=_menu_keyboard())
    except Exception as e:
        logger.error(f"⏰ Eslatmalar menyusini ochishda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise
    return ConversationHandler.END


async def new_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["flow"] = "reminder"
    await query.edit_message_text("✍️ Eslatma matnini yozing (nima haqida eslatilsin?):")
    return RM_TEXT


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❗️ Iltimos, matn yozing.")
        return RM_TEXT
    context.user_data["rm_text"] = text
    await update.message.reply_text(
        "🕒 Qachon eslatilsin?\n\n"
        "Masalan:\n"
        "• <code>01.09.2026 18:00</code>\n"
        "• <code>ertaga 09:00</code>\n"
        "• <code>3 soatdan keyin</code>\n"
        "• <code>2 kundan keyin</code>",
        parse_mode="HTML",
    )
    return RM_TIME


def _parse_due(text: str) -> float | None:
    """Foydalanuvchi kiritgan vaqt matnini UTC unix timestamp'ga o'giradi.
    Tushunarsiz format yoki o'tmishdagi vaqt bo'lsa None qaytaradi."""
    text = text.strip().lower()
    now_local = datetime.now(_TZ)

    m = _REL_RE.match(text)
    if m:
        amount, unit = int(m.group(1)), m.group(2)
        delta = {"daqiqa": timedelta(minutes=amount), "soat": timedelta(hours=amount), "kun": timedelta(days=amount)}[unit]
        return (now_local + delta).timestamp()

    m = _TODAY_RE.match(text)
    if m:
        day_word, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
        base = now_local if day_word == "bugun" else now_local + timedelta(days=1)
        due = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return due.timestamp()

    m = _ABS_RE.match(text)
    if m:
        day, month, year, hh, mm = m.groups()
        year = int(year) if year else now_local.year
        try:
            due = datetime(year, int(month), int(day), int(hh), int(mm), tzinfo=_TZ)
        except ValueError:
            return None
        return due.timestamp()

    return None


async def receive_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    due_ts = _parse_due(raw)
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0

    if due_ts is None:
        logger.warning(f"⏰ Vaqt formatini aniqlab bo'lmadi: chat_id={chat_id}, kiritilgan='{raw}'.")
        await update.message.reply_text(
            "❗️ Vaqt formatini tushunmadim. Masalan: <code>ertaga 09:00</code> yoki "
            "<code>2 soatdan keyin</code> deb yozing.",
            parse_mode="HTML",
        )
        return RM_TIME

    if due_ts <= time.time():
        await update.message.reply_text("❗️ Bu vaqt allaqachon o'tib ketgan. Kelajakdagi vaqt kiriting.")
        return RM_TIME

    text = context.user_data.get("rm_text", "")
    reminder = storage.add_reminder(user_id, chat_id, text, due_ts)
    schedule_reminder(context.application, reminder)

    due_str = datetime.fromtimestamp(due_ts, _TZ).strftime("%d.%m.%Y %H:%M")
    logger.info(f"⏰ Eslatma rejalashtirildi: chat_id={chat_id}, id={reminder['id']}, vaqt={due_str}.")
    await update.message.reply_text(f"✅ Eslatma o'rnatildi!\n🕒 {due_str}\n📝 {text}")

    context.user_data.clear()
    return ConversationHandler.END


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id if update.effective_user else 0
    reminders = sorted(storage.get_user_reminders(user_id), key=lambda r: r["due_ts"])

    if not reminders:
        await query.edit_message_text("📋 Sizda hozircha faol eslatmalar yo'q.", reply_markup=_menu_keyboard())
        return

    rows = []
    lines = ["📋 <b>Eslatmalaringiz:</b>\n"]
    for r in reminders:
        due_str = datetime.fromtimestamp(r["due_ts"], _TZ).strftime("%d.%m %H:%M")
        lines.append(f"🕒 {due_str} — {r['text'][:60]}")
        rows.append([InlineKeyboardButton(f"🗑 {due_str} — {r['text'][:25]}", callback_data=f"remind:del:{r['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="remind:menu")])

    await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    reminder_id = query.data.split(":")[-1]
    user_id = update.effective_user.id if update.effective_user else 0

    task = _active_tasks.pop(reminder_id, None)
    if task and not task.done():
        task.cancel()

    removed = storage.remove_reminder(reminder_id)
    logger.info(f"⏰ Eslatma o'chirildi: user_id={user_id}, id={reminder_id}, natija={'ok' if removed else 'topilmadi'}.")
    await query.answer("🗑 O'chirildi." if removed else "⚠️ Topilmadi.", show_alert=False)
    await list_reminders(update, context)


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏰ *Eslatmalar*\n\nNima qilmoqchisiz?", parse_mode="Markdown", reply_markup=_menu_keyboard())


# ============================================================
# Fon rejasi (scheduler) — asyncio.create_task orqali, JobQueue kerak emas.
# ============================================================

async def _wait_and_send(application, reminder: dict):
    delay = reminder["due_ts"] - time.time()
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        await application.bot.send_message(
            reminder["chat_id"], f"⏰ *Eslatma!*\n\n{reminder['text']}", parse_mode="Markdown",
        )
        logger.info(f"⏰ ✅ Eslatma yuborildi: id={reminder['id']}, chat_id={reminder['chat_id']}.")
    except asyncio.CancelledError:
        logger.info(f"⏰ Eslatma bekor qilindi (yuborilishidan oldin): id={reminder['id']}.")
        raise
    except Exception as e:
        logger.error(f"⏰ ❌ Eslatmani yuborishda xato: id={reminder['id']}, {type(e).__name__}: {e}", exc_info=True)
    finally:
        storage.remove_reminder(reminder["id"])
        _active_tasks.pop(reminder["id"], None)


def schedule_reminder(application, reminder: dict) -> None:
    task = asyncio.create_task(_wait_and_send(application, reminder))
    _active_tasks[reminder["id"]] = task


def reschedule_all(application) -> None:
    """Bot ishga tushganda (post_init) chaqiriladi — storage'dagi BARCHA
    eslatmalarni qayta task sifatida rejalashtiradi. Vaqti allaqachon
    o'tib ketganlar (bot o'chib turgan vaqtda) DARHOL, kechikkani haqida
    ogohlantirish bilan yuboriladi."""
    reminders = storage.get_all_reminders()
    now = time.time()
    overdue, upcoming = 0, 0
    for r in reminders:
        if r["due_ts"] <= now:
            overdue += 1
        else:
            upcoming += 1
        schedule_reminder(application, r)
    logger.info(f"⏰ Eslatmalar qayta rejalashtirildi: {upcoming} ta kelajakda, {overdue} ta muddati o'tgan (darhol yuboriladi).")
