"""
🎁 /tabrik — Telegram BUSINESS API orqali "aynan do'stning private chatida"
tabriknoma animatsiyasi.

MUHIM TARIXIY QAROR (batafsili suhbatda muhokama qilingan, quyida qisqacha):
  - Business orqali yuborilgan xabarlarga `callback_data` tugma QO'YIB
    BO'LMAYDI (python-telegram-bot rasmiy hujjati: "Not supported for
    messages sent on behalf of a Telegram Business account"). Shuning
    uchun HAR QANDAY tugma — boshlang'ich HAM, 120 soniyadan keyingi
    qayta chiqadigani HAM — faqat ASL inline xabarda (A yuborgan
    `@Bot /tabrik ...` natijasi) bo'ladi, `inline_message_id` orqali
    edit qilinadi. Business orqali yuboriladigan audio/emoji/final matn
    xabarlarida umuman tugma YO'Q.
  - Inline xabar ustidagi callback query'da `chat_id` YO'Q (faqat
    `inline_message_id`) — shuning uchun recipient (B)ning chat_id'i
    `query.from_user.id` orqali olinadi va Telegram Bot API'ning shaxsiy
    (1:1) chat modeliga ko'ra `chat_id == boshqa tomon user_id`
    tengligidan foydalaniladi. Bu rasmiy hujjatda so'zma-so'z tasdiqlangan
    IBORA emas — Bot API'ning barqaror xatti-harakati; shuning uchun har
    bir chaqiruvda ANIQ loglanadi va har qanday xato ochiq ko'rsatiladi
    (yashirilmaydi).

Bu modul faqat "sof" logikani emas, balki to'g'ridan-to'g'ri
`telegram.ext.ContextTypes.DEFAULT_TYPE`ning `context.bot`'i orqali haqiqiy
Bot API chaqiruvlarini ham o'z ichiga oladi (business_connection_id kerak
bo'lgani uchun buni tabrik_logic.py'dagi kabi "telegram'dan mustaqil" qilib
bo'lmaydi) — lekin testlarda `context.bot` osongina fake/mock bilan
almashtiriladi (qarang: tests/test_tabrik_business.py).
"""

import asyncio
import logging
import os
import time
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, RetryAfter

import business_storage
import tabrik_logic
import telegram_effects
import config

logger = logging.getLogger(__name__)

DEFAULT_EMOJIS = ["😍", "🥳", "🎉", "❤️", "✨"]
EMOJI_DISPLAY_DELAY_SEC = 2.0
REVERT_DELAY_SEC = 120

_DEFAULT_AUDIO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "tabrik", "tabrik_music.mp3")
TABRIK_AUDIO_PATH = os.getenv("TABRIK_AUDIO_PATH", _DEFAULT_AUDIO_PATH)

# ------------------------------------------------------------------
# short_id -> {sender_user_id, created_at, cycle} — tabrik_logic.py'dagi
# asosiy (matn/emoji) ombordan ALOHIDA, chunki u yerda sender_user_id
# saqlanmaydi (uni faqat Business oqimi talab qiladi, /pro va group
# /tabrik'da kerak emas — shu sabab tabrik_logic.py sxemasi buzilmadi).
# ------------------------------------------------------------------
_CELEBRATIONS: dict[str, dict] = {}
ENTRY_TTL_SECONDS = 60 * 60 * 24


def register_celebration(short_id: str, sender_user_id: int) -> None:
    _CELEBRATIONS[short_id] = {"sender_user_id": sender_user_id, "created_at": time.time(), "cycle": 0}
    _purge_expired_celebrations()


def _purge_expired_celebrations(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    expired = [k for k, v in _CELEBRATIONS.items() if now - v["created_at"] > ENTRY_TTL_SECONDS]
    for k in expired:
        del _CELEBRATIONS[k]


def _get_celebration(short_id: str) -> dict | None:
    return _CELEBRATIONS.get(short_id)


# ------------------------------------------------------------------
# Recipient bo'yicha lock (14-band: global lock ishlatilmaydi — har bir
# recipient o'z cycle'iga ega, bir-biriga xalaqit bermaydi).
# ------------------------------------------------------------------
_recipient_locks: dict[int, asyncio.Lock] = {}
_revert_tasks: dict[int, asyncio.Task] = {}


def _lock_for(recipient_user_id: int) -> asyncio.Lock:
    lock = _recipient_locks.get(recipient_user_id)
    if lock is None:
        lock = asyncio.Lock()
        _recipient_locks[recipient_user_id] = lock
    return lock


def _ready_markup(short_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎁 Tabriknomani qabul qilish", callback_data=f"itabrik:claim:{short_id}")
    ]])


def _make_logger(trace_id: str, short_id: str):
    prefix = f"[TABRIK][trace={trace_id}][short_id={short_id}]"

    def log(stage: str, **kwargs) -> None:
        suffix = " " + " ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
        logger.info(f"{prefix} {stage}{suffix}")

    def log_error(stage: str, error: Exception) -> None:
        logger.error(
            f"{prefix}[ERROR] stage={stage} error_type={type(error).__name__} error={error}",
            exc_info=True,
        )

    return log, log_error


_BUSINESS_ERROR_MESSAGES = {
    "NO_CONNECTION": "⚠️ Yuboruvchi hali Telegram Business botini ulamagan — tabrikni ko'rsatib bo'lmaydi.",
    "DISABLED": "⚠️ Yuboruvchining Business ulanishi hozir faol emas.",
    "CAN_REPLY_FALSE": "⚠️ Botga bu chatda javob berish huquqi berilmagan (can_reply=false).",
}


# ------------------------------------------------------------------
# ASOSIY KIRISH NUQTASI — handlers/inline_query.py'dagi
# `inline_tabrik_claim_callback` shuni chaqiradi.
# ------------------------------------------------------------------
async def handle_claim(update, context) -> None:
    query = update.callback_query
    inline_message_id = query.inline_message_id
    short_id = query.data.split(":", 2)[2]
    trace_id = uuid.uuid4().hex[:8]
    log, log_error = _make_logger(trace_id, short_id)

    if not inline_message_id:
        await query.answer("⚠️ Bu tugma faqat inline xabarlar uchun.", show_alert=True)
        return

    greeting = tabrik_logic.get_greeting(short_id)
    if not greeting:
        log("GREETING_EXPIRED")
        await query.answer("⚠️ Bu tabrikning muddati o'tgan.", show_alert=True)
        return
    emojis = config.get_tabrik_settings()["emojis"]

    celebration = _get_celebration(short_id)
    if not celebration:
        # Eski (Business qo'shilishidan oldingi) short_id yoki restart —
        # sender_user_id topilmadi, business ulanishini aniqlab bo'lmaydi.
        log("CELEBRATION_META_MISSING")
        await query.answer("⚠️ Bu tabrik ma'lumotlari topilmadi (eskirgan bo'lishi mumkin).", show_alert=True)
        return
    sender_user_id = celebration["sender_user_id"]

    log("ACCEPT_BUTTON_PRESSED", from_user_id=query.from_user.id)

    # --- 1) RECIPIENT RESOLVE ---
    log("RECIPIENT_RESOLVE_STARTED")
    recipient_user_id = query.from_user.id
    recipient_chat_id = recipient_user_id  # Bot API shaxsiy chat modeli — batafsili yuqoridagi docstring'da
    log("RECIPIENT_USER_ID", value=recipient_user_id)
    log("RECIPIENT_CHAT_ID", value=recipient_chat_id)

    if recipient_user_id == sender_user_id:
        log("RECIPIENT_IS_SENDER")
        await query.answer("⚠️ O'zingiz yuborgan tabrikni qabul qila olmaysiz.", show_alert=True)
        return

    # --- 2) BUSINESS CONNECTION RESOLVE + RIGHTS CHECK ---
    conn_entry = business_storage.get_connection_for_user(sender_user_id)
    usable, reason = business_storage.is_connection_usable(conn_entry)
    log("BUSINESS_RIGHTS_CHECK", reason=reason)
    if not usable:
        if reason == "CAN_REPLY_FALSE":
            log("BUSINESS_CAN_REPLY_FALSE")
        elif reason == "DISABLED":
            log("BUSINESS_CONNECTION_DISABLED")
        await query.answer(_BUSINESS_ERROR_MESSAGES.get(reason, "⚠️ Business ulanishi topilmadi."), show_alert=True)
        return

    business_connection_id = conn_entry["connection_id"]
    log("BUSINESS_CONNECTION_FOUND", connection_id=business_connection_id)
    if not business_storage.can_delete_sent_messages(conn_entry):
        log("BUSINESS_DELETE_RIGHT_FALSE")
        # Muhim emas — animatsiyani baribir boshlaymiz, faqat emoji
        # xabarlari o'chirilmay qolishi mumkin (har bir delete alohida
        # sinaladi va DELETE_FAILED logi bilan qayd etiladi).
    log("RECIPIENT_RESOLVE_SUCCESS")

    # --- 3) DUPLICATE CLICK / LOCK ---
    lock = _lock_for(recipient_user_id)
    if lock.locked():
        log("DUPLICATE_CLICK_IGNORED")
        await query.answer("⏳ Animatsiya allaqachon ketmoqda...", show_alert=False)
        return

    pending_revert = _revert_tasks.pop(recipient_user_id, None)
    if pending_revert:
        pending_revert.cancel()
        log("REVERT_TASK_CANCELLED")

    tabrik_logic.touch_greeting(short_id)
    celebration["cycle"] = celebration.get("cycle", 0) + 1
    await query.answer("🎁 Ochilmoqda...")

    async with lock:
        log("LOCK_ACQUIRED")
        try:
            await _run_cycle(context, business_connection_id, recipient_chat_id, greeting, emojis, log, log_error)
            log("CELEBRATION_COMPLETED")
        except Exception as e:
            log_error("CYCLE", e)
        finally:
            task = asyncio.create_task(
                _schedule_revert(context, inline_message_id, short_id, recipient_user_id, trace_id)
            )
            _revert_tasks[recipient_user_id] = task
            log("REVERT_SCHEDULED", seconds=REVERT_DELAY_SEC)


_NOT_ELIGIBLE_MARKERS = ("initiate conversation", "bot_access_forbidden", "business_connection_invalid")


def _looks_not_eligible(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _NOT_ELIGIBLE_MARKERS)


async def _send_with_retry(coro_factory, log, stage: str):
    """`coro_factory` — argumentsiz `async def` chaqiruvchi (masalan
    `lambda: bot.send_message(...)`) — RetryAfter kelsa BIR MARTA kutib
    qayta uriniladi, boshqa xatolarda None + xato qaytariladi (chaqiruvchi
    hal qiladi)."""
    try:
        return await coro_factory(), None
    except RetryAfter as e:
        log(f"{stage}_RETRY_AFTER", seconds=e.retry_after)
        await asyncio.sleep(e.retry_after + 0.1)
        try:
            return await coro_factory(), None
        except Exception as e2:
            return None, e2
    except Exception as e:
        return None, e


async def _run_cycle(context, business_connection_id, chat_id, greeting_text, emojis, log, log_error) -> None:
    bot = context.bot

    # --- AUDIO ---
    log("AUDIO_SEND_STARTED")
    audio_id = config.get_tabrik_settings().get("audio_file_id")
    if audio_id:
        try:
            sent = await bot.send_audio(
                business_connection_id=business_connection_id, chat_id=chat_id, audio=audio_id
            )
            log("AUDIO_SEND_SUCCESS", message_id=sent.message_id, source="developer_file_id")
            log("AUDIO_AUTOPLAY_NOT_CONTROLLED_BY_BOT")
        except (BadRequest, Forbidden) as e:
            if _looks_not_eligible(e):
                log("TABRIK_BUSINESS_CHAT_NOT_ELIGIBLE", at_stage="AUDIO", error=str(e))
                return
            log_error("AUDIO_SEND", e)
        except Exception as e:
            log_error("AUDIO_SEND", e)
    else:
        log("AUDIO_NOT_CONFIGURED")
        log("AUDIO_SKIPPED")

    # --- 5 EMOJI ---
    for idx, emoji in enumerate(emojis, start=1):
        effect_id = telegram_effects.get_effect_id(emoji)
        log(f"EMOJI_{idx}_SEND_STARTED", emoji=emoji, effect_id=effect_id)

        sent, err = await _send_with_retry(
            lambda: bot.send_message(
                business_connection_id=business_connection_id, chat_id=chat_id,
                text=emoji, message_effect_id=effect_id,
            ),
            log, f"EMOJI_{idx}_SEND",
        )

        if err is not None and effect_id is not None:
            # Effekt rad etilgan bo'lishi mumkin — effektsiz qayta urinamiz.
            log(f"EMOJI_{idx}_EFFECT_REJECTED", effect_id=effect_id, error=str(err))
            sent, err = await _send_with_retry(
                lambda: bot.send_message(business_connection_id=business_connection_id, chat_id=chat_id, text=emoji),
                log, f"EMOJI_{idx}_SEND_NO_EFFECT",
            )

        if err is not None:
            if _looks_not_eligible(err):
                log("TABRIK_BUSINESS_CHAT_NOT_ELIGIBLE", at_stage=f"EMOJI_{idx}", error=str(err))
                return
            log_error(f"EMOJI_{idx}_SEND", err)
            continue  # shu emoji o'tkazib yuboriladi, keyingisiga o'tamiz

        log(f"EMOJI_{idx}_SEND_SUCCESS", message_id=sent.message_id)
        log(f"EMOJI_{idx}_WAIT", seconds=EMOJI_DISPLAY_DELAY_SEC)
        await asyncio.sleep(config.get_tabrik_settings()["emoji_delay"])

        log(f"EMOJI_{idx}_DELETE_STARTED", message_id=sent.message_id)
        try:
            await bot.delete_business_messages(business_connection_id=business_connection_id, message_ids=[sent.message_id])
            log(f"EMOJI_{idx}_DELETE_SUCCESS")
        except Exception as e:
            log(f"EMOJI_{idx}_DELETE_FAILED", error_type=type(e).__name__, error=str(e))

    # --- FINAL TEXT ---
    log("FINAL_TEXT_SEND_STARTED")
    sent, err = await _send_with_retry(
        lambda: bot.send_message(business_connection_id=business_connection_id, chat_id=chat_id, text=greeting_text),
        log, "FINAL_TEXT_SEND",
    )
    if err is not None:
        if _looks_not_eligible(err):
            log("TABRIK_BUSINESS_CHAT_NOT_ELIGIBLE", at_stage="FINAL_TEXT", error=str(err))
            return
        log_error("FINAL_TEXT_SEND", err)
        return
    log("FINAL_TEXT_SEND_SUCCESS", message_id=sent.message_id)


async def _schedule_revert(context, inline_message_id: str, short_id: str, recipient_user_id: int, trace_id: str) -> None:
    log, log_error = _make_logger(trace_id, short_id)
    try:
        await asyncio.sleep(config.get_tabrik_settings()["revert_minutes"] * 60)
        await context.bot.edit_message_text(
            inline_message_id=inline_message_id,
            text=tabrik_logic.build_ready_card(),
            reply_markup=_ready_markup(short_id),
        )
        log("REVERT_SUCCESS")
    except asyncio.CancelledError:
        raise
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            log_error("REVERT", e)
    except Exception as e:
        log_error("REVERT", e)
    finally:
        _revert_tasks.pop(recipient_user_id, None)
