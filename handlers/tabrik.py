"""
🎁 /tabrik — foydalanuvchi tabrik matnini yozadi, bot FAQAT
"🎁 Tabriknomani qabul qilish" tugmasi bilan xabar yuboradi (tabrik matni
HALI ko'rsatilmaydi). Tugma bosilganda, AYNAN SHU xabar (yangisi emas)
bosqichma-bosqich o'zgaradi:

  1) 5→1 countdown — har bir raqam "katta ASCII-art raqam" sifatida
     (5x7 nuqta-matritsa shrifti, faqat tabrik_logic.DECOR_CHARS
     palitrasidan foydalanib chizilgan — oddiy "5, 4, 3..." matn EMAS).
  2) "Aylanayotgan naqsh" animatsiyasi (bezak chizig'i + aylanuvchi
     halqa, yana shu palitra bilan).
  3) Yakuniy karta — foydalanuvchi yozgan tabrik matni shu YERDA birinchi
     marta ko'rinadi.
  4) 2 daqiqadan so'ng xabar avtomatik ravishda YANA faqat tugma
     holatiga qaytadi — qayta bosilsa, animatsiya BOSHIDAN takrorlanadi.

MUHIM ARXITEKTURA:
- Sof mantiq (matn parsing, ASCII-art freym generatsiyasi, xotiradagi
  ombor) tabrik_logic.py'da — bu yerda FAQAT Telegram bilan bog'liq kod.
- Endi bitta UMUMIY xabar (tugma joylashgan xabar) animatsiya qilinadi
  (avvalgidek har bosishda yangi xabar yaratilmaydi) — shuning uchun
  "faollik qulfi" endi (chat_id, user_id) emas, balki
  (chat_id, message_id) bo'yicha: bitta vaqtda faqat BITTA odam shu
  tugmani "band" qilishi mumkin (boshqasi bossa — "hozir band" javobi).
- 2 daqiqalik "asl holatga qaytarish" `asyncio.create_task` orqali fonda
  rejalashtiriladi; agar shu vaqt ichida tugma yana bosilsa, eski
  rejalashtirilgan vazifa BEKOR qilinadi (shu orqali ikki marta orqama-
  orqa qaytarib yuborish yoki animatsiya davomida qaytarib yuborish
  kabi holatlar oldini olinadi).
- Telegram flood-control (RetryAfter) va "message is not modified"
  xatolari xavfsiz tarzda ushlanadi — animatsiya Telegram limitlariga
  urilib bot butunlay yiqilib qolmaydi.
"""

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

import tabrik_logic

logger = logging.getLogger(__name__)

# (chat_id, message_id) -> True — hozir shu XABAR uchun animatsiya
# ketayotganini bildiradi (bir vaqtda faqat bitta animatsiya). Jarayon
# tugagach albatta o'chiriladi (finally blokida).
_ACTIVE: set[tuple[int, int]] = set()

# (chat_id, message_id) -> rejalashtirilgan "asl holatga qaytarish" vazifasi.
_REVERT_TASKS: dict[tuple[int, int], asyncio.Task] = {}

COUNTDOWN_DELAY = 1.0     # soniya — 5→1 orasidagi kutish
FRAME_DELAY = 0.45        # soniya — naqsh freym'lari orasidagi kutish
REVERT_DELAY_SEC = 120    # 2 daqiqa — yakuniy kartadan keyin tugmaga qaytish


def _ready_markup(short_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎁 Tabriknomani qabul qilish", callback_data=f"tabrik:claim:{short_id}")
    ]])


async def tabrik_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, override_text: str | None = None):
    """`override_text` — handlers/mention_dispatch.py orqali (masalan
    "@Bot /tabrik Salom..." xabaridan mention qismi olib tashlangandan
    keyin) chaqirilganda beriladi. Berilmasa (oddiy "/tabrik ..." buyrug'i
    CommandHandler orqali kelganda) xabar matnining o'zi ishlatiladi."""
    if not update.message:
        return
    raw_text = override_text if override_text is not None else (update.message.text or "")
    text = tabrik_logic.parse_tabrik_text(raw_text)
    if not text:
        await update.message.reply_text(
            "🎁 Tabrik matnini ham yozing, masalan:\n\n"
            "`/tabrik Salom mening qadrli insonim, sizni bugungi kun bilan "
            "tabriklayman. Hurmat bilan Davron`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    short_id = tabrik_logic.store_greeting(text)
    # MUHIM: bu yerda tabrik matni HECH QACHON ko'rsatilmaydi — faqat
    # tugma bosilgach, animatsiya oxirida ochiladi.
    await update.message.reply_text(
        tabrik_logic.build_ready_card(),
        reply_markup=_ready_markup(short_id),
    )
    logger.info(f"🎁 /tabrik yuborildi: chat_id={update.effective_chat.id}, short_id={short_id}.")


async def _safe_edit(msg, text: str, reply_markup=None) -> None:
    """Xabarni edit qilishda Telegram flood-control (RetryAfter) va
    'message is not modified' xatolarini xavfsiz tarzda o'tkazib yuboradi
    — animatsiya shu sabablarga ko'ra to'xtab qolmasligi kerak."""
    try:
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except RetryAfter as e:
        logger.warning(f"🎁 Telegram flood-control: {e.retry_after}s kutilmoqda.")
        await asyncio.sleep(e.retry_after + 0.1)
        try:
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        except Exception:
            pass
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"🎁 Animatsiya freymini edit qilib bo'lmadi: {e}")
    except Exception as e:
        logger.warning(f"🎁 Animatsiya freymini edit qilishda kutilmagan xato: {type(e).__name__}: {e}")


async def tabrik_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    msg = query.message
    if not msg:
        await query.answer("⚠️ Xabar topilmadi.", show_alert=True)
        return

    chat_id = update.effective_chat.id
    message_key = (chat_id, msg.message_id)

    short_id = query.data.split(":", 2)[2]
    greeting = tabrik_logic.get_greeting(short_id)
    if not greeting:
        await query.answer("⚠️ Bu tabrikning muddati o'tgan.", show_alert=True)
        return

    if message_key in _ACTIVE:
        await query.answer("⏳ Animatsiya allaqachon ketmoqda...", show_alert=False)
        return

    # Foydalanuvchi tugmani qayta bossa (yakuniy karta hali 2 daqiqalik
    # oynada turgan bo'lsa ham) — rejalashtirilgan "qaytarish"ni bekor
    # qilib, animatsiyani BOSHIDAN qayta ishga tushiramiz.
    pending_revert = _REVERT_TASKS.pop(message_key, None)
    if pending_revert:
        pending_revert.cancel()

    tabrik_logic.touch_greeting(short_id)  # tugma faol ishlatilyapti — TTL yangilanadi
    _ACTIVE.add(message_key)
    await query.answer("🎁 Ochilmoqda...")

    try:
        for n in (5, 4, 3, 2, 1):
            await _safe_edit(msg, tabrik_logic.build_countdown_frame(n))
            await asyncio.sleep(COUNTDOWN_DELAY)

        for step in range(tabrik_logic.TOTAL_ROTATION_FRAMES):
            await _safe_edit(msg, tabrik_logic.build_circle_frame(step))
            await asyncio.sleep(FRAME_DELAY)

        escaped = escape_markdown(greeting, version=1)
        await _safe_edit(msg, tabrik_logic.build_final_card(escaped))
        logger.info(f"🎁 Animatsiya muvaffaqiyatli yakunlandi: chat_id={chat_id}, message_id={msg.message_id}.")

        # 2 daqiqadan keyin xabarni yana "faqat tugma" holatiga qaytaramiz.
        task = asyncio.create_task(_schedule_revert(context, chat_id, msg.message_id, short_id, message_key))
        _REVERT_TASKS[message_key] = task
    except Exception as e:
        logger.error(f"🎁 /tabrik animatsiyasida xato (chat_id={chat_id}, message_id={msg.message_id}): {type(e).__name__}: {e}", exc_info=True)
        try:
            escaped = escape_markdown(greeting, version=1)
            await _safe_edit(msg, f"❌ Animatsiyada xatolik yuz berdi, lekin tabrigingiz shu yerda:\n\n{escaped}")
        except Exception:
            pass
    finally:
        _ACTIVE.discard(message_key)


async def _schedule_revert(context, chat_id: int, message_id: int, short_id: str, message_key: tuple[int, int]) -> None:
    """`REVERT_DELAY_SEC` soniyadan keyin xabarni yana "🎁 Tabrikni qabul
    qilish" tugmasi holatiga qaytaradi. Shu vaqt ichida tugma qayta
    bosilsa, `tabrik_claim_callback` bu vazifani bekor qiladi (yuqoriga
    qarang) — shuning uchun `CancelledError` kutilgan holat, xato emas."""
    try:
        await asyncio.sleep(REVERT_DELAY_SEC)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=tabrik_logic.build_ready_card(),
            reply_markup=_ready_markup(short_id),
        )
        logger.info(f"🎁 Xabar asl (tugma) holatiga qaytarildi: chat_id={chat_id}, message_id={message_id}.")
    except asyncio.CancelledError:
        raise
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"🎁 Xabarni asl holatiga qaytarishda xato: {e}")
    except Exception as e:
        logger.warning(f"🎁 Xabarni asl holatiga qaytarishda kutilmagan xato: {type(e).__name__}: {e}")
    finally:
        _REVERT_TASKS.pop(message_key, None)
