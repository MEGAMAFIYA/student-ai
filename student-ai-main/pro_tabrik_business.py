"""
💎 /pro — Telegram BUSINESS API orqali "aynan do'stning private chatida"
BOSQICHMA-BOSQICH (har bosishda BITTA qadam) tabriknoma oqimi.

Bu modul `tabrik_business.py`ga juda o'xshaydi (bir xil Business API
cheklovlari, bir xil recipient-resolve/rights-check naqshi, bir xil
effect-fallback/retry mexanizmi — shu qismlar ATAYLAB QAYTA ISHLATILGAN,
DUPLICATE yo'q: pastga qarang), lekin ASOSIY farq bor:

  `/tabrik` (tabrik_business.handle_claim) — BITTA bosishda BUTUN
  animatsiya (audio + 5 emoji + final matn) avtomatik, ketma-ket, ORASIDA
  2 soniya kutib, keyin HAR BIR emoji xabarini O'CHIRIB ishlaydi, va 120
  soniyadan keyin AVTOMATIK asl holatga qaytadi.

  `/pro` (bu modul) — HAR BOSISH FAQAT BITTA BOSQICHNI bajaradi:
    bosish 1: 😍 (+ effect)          -> tugma "Keyingi"ga o'tadi
    bosish 2: 🥳 (+ effect)          -> tugma "Keyingi"
    bosish 3: 🎉 (+ effect)          -> tugma "Keyingi"
    bosish 4: ❤️ (+ effect)          -> tugma "Keyingi"
    bosish 5: ✨ (+ effect)          -> tugma "Tabrikni ko'rish"
    bosish 6: original /pro matni    -> tugma OLIB TASHLANADI (yakuniy holat)
  Emoji xabarlari O'CHIRILMAYDI (doimiy qoladi) va yakuniy matndan keyin
  HECH QANDAY avtomatik revert YO'Q — final holat DOIMIY.

Business API cheklovlari (batafsili: tabrik_business.py docstring'i) BU
YERDA HAM BIR XIL: Business orqali yuborilgan xabarlarga callback tugma
qo'yib bo'lmaydi, shuning uchun YAGONA tugma har doim ASL inline xabarda
(`inline_message_id` orqali `edit_message_reply_markup` bilan) yashaydi —
Business orqali yuboriladigan har bir emoji/final-matn xabarida tugma YO'Q.

Matn/emoji SAQLASH uchun ALOHIDA ombor YARATILMAGAN — `tabrik_logic.py`dagi
mavjud `store_greeting`/`get_greeting`/`get_greeting_emojis` qayta
ishlatiladi (kontent modeli AYNAN BIR XIL: matn + emoji ro'yxati, rasm
YO'Q — 27-band: /pro rasm slайд-shousiz). Bu yerda FAQAT yangi narsa —
recipient/business_connection/bosqich holatini saqlaydigan qo'shimcha
"celebration" ombori (`_PRO_STATE`), chunki /pro'da bitta greeting bo'yicha
BIR NECHTA ketma-ket callback keladi (tabrik_business'dagi kabi bitta
emas) va ular orasida recipient/connection ma'lumoti ESLAB QOLINISHI kerak.

ESKI (fayl ichidagi rasm-slайд-shou) `/pro` kodi (`pro_tabrik_logic.py`,
`handlers/pro_tabrik.py`) BU YERDA ISHLATILMAYDI VA O'ZGARTIRILMAYDI — ular
hali ham ODDIY (guruh/shaxsiy) chatdagi `/pro <matn>` buyrug'i uchun
ishlaydi (Business API kerak emas, shuning uchun butunlay boshqa oqim).
Faqat INLINE (`@Bot /pro ...` do'stning private chatida) oqimi shu yangi
modulga o'tkazildi — qarang: handlers/inline_query.py.
"""

import asyncio
import logging
import os
import time
import uuid

from telegram.error import BadRequest, Forbidden, RetryAfter

import business_storage
import tabrik_logic
import config
import telegram_effects

logger = logging.getLogger(__name__)

# tabrik_business.DEFAULT_EMOJIS BILAN ATAYLAB BIR XIL (buyruqning 4-bandi:
# "Effect IDlarni loyiha ichidagi mavjud ishonchli effect mappingdan ol" —
# shu ro'yxat allaqachon /tabrik'da real Telegram effect'lari bilan
# tasdiqlangan). Mustaqil nusxa sifatida saqlanadi (import qilib olinmaydi),
# chunki /pro va /tabrik kelajakda mustaqil o'zgarishi mumkin (21-band).
DEFAULT_EMOJIS = ["😍", "🥳", "🎉", "❤️", "✨"]
TOTAL_EMOJI_STAGES = len(DEFAULT_EMOJIS)  # 5
FINAL_STAGE = TOTAL_EMOJI_STAGES + 1  # 6

# 💎 /pro audio — standart bo'yicha /tabrik bilan BIR XIL audio fayl qayta
# ishlatiladi (loyihada alohida /pro audio fayli topilmadi — 21-band:
# yangi asset talab qilinmaydi). Kerak bo'lsa PRO_AUDIO_PATH env orqali
# alohida faylga ko'rsatish mumkin.
_DEFAULT_AUDIO_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets", "tabrik", "tabrik_music.mp3"
)
PRO_AUDIO_PATH = os.getenv("PRO_AUDIO_PATH", os.getenv("TABRIK_AUDIO_PATH", _DEFAULT_AUDIO_PATH))


def audio_available() -> bool:
    return os.path.exists(PRO_AUDIO_PATH)


def build_ready_card() -> str:
    return "💎 Sizga Pro tabriknoma bor!"


# ------------------------------------------------------------------
# short_id -> {sender_user_id, recipient_user_id, recipient_chat_id,
#              business_connection_id, current_stage, trace_id, created_at}
# tabrik_business._CELEBRATIONS bilan BIR XIL maqsad, lekin bosqichni ham
# saqlaydi (chunki bu yerda BIR NECHTA ketma-ket callback bor).
# ------------------------------------------------------------------
_PRO_STATE: dict[str, dict] = {}
_revert_tasks: dict[int, asyncio.Task] = {}
ENTRY_TTL_SECONDS = 60 * 60 * 24


def register_pro_celebration(short_id: str, sender_user_id: int) -> None:
    _PRO_STATE[short_id] = {
        "sender_user_id": sender_user_id,
        "recipient_user_id": None,
        "recipient_chat_id": None,
        "business_connection_id": None,
        "current_stage": 0,
        "trace_id": None,
        "created_at": time.time(),
    }
    _purge_expired()


def _purge_expired(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    expired = [k for k, v in _PRO_STATE.items() if now - v["created_at"] > ENTRY_TTL_SECONDS]
    for k in expired:
        del _PRO_STATE[k]


def _get_state(short_id: str) -> dict | None:
    return _PRO_STATE.get(short_id)


def get_current_stage(short_id: str) -> int | None:
    entry = _PRO_STATE.get(short_id)
    return entry["current_stage"] if entry else None


# ------------------------------------------------------------------
# Har bir greeting (== har bir recipient) uchun ALOHIDA lock — 14-band:
# global lock ishlatilmaydi, User A->B va User C->D bir vaqtda ishlaydi.
# ------------------------------------------------------------------
_greeting_locks: dict[str, asyncio.Lock] = {}


def _lock_for(short_id: str) -> asyncio.Lock:
    lock = _greeting_locks.get(short_id)
    if lock is None:
        lock = asyncio.Lock()
        _greeting_locks[short_id] = lock
    return lock


def _make_logger(trace_id: str, short_id: str):
    prefix = f"[TABRIK-PRO][trace={trace_id}][short_id={short_id}]"

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

_NOT_ELIGIBLE_MARKERS = ("initiate conversation", "bot_access_forbidden", "business_connection_invalid")


def _looks_not_eligible(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _NOT_ELIGIBLE_MARKERS)


async def _send_with_retry(coro_factory, log, stage: str, max_retries: int = 2):
    """18-band: cheksiz retry yo'q — RetryAfter kelsa eng ko'p `max_retries`
    marta (standart 2) qayta uriniladi, PRO_RETRY logi bilan."""
    attempt = 0
    while True:
        try:
            return await coro_factory(), None
        except RetryAfter as e:
            if attempt >= max_retries:
                return None, e
            attempt += 1
            log("PRO_RETRY", at_stage=stage, attempt=attempt, seconds=e.retry_after)
            await asyncio.sleep(e.retry_after + 0.1)
        except Exception as e:
            return None, e


def _button_label(stage: int) -> str | None:
    """`stage` — SHU BOSQICHGACHA bajarilgan bosqichlar soni (0 =
    hali hech narsa bosilmagan). None qaytsa — tugma umuman ko'rsatilmaydi
    (final holat, 8-band: avtomatik reset yo'q, tugma qaytarilmaydi)."""
    if stage <= 0:
        return "🎁 Tabriknomani qabul qilish"
    if stage < TOTAL_EMOJI_STAGES:  # 1..4 -> yana emoji bosqichlari bor
        return "Keyingi"
    if stage == TOTAL_EMOJI_STAGES:  # 5 -> keyingisi yakuniy matn
        return "Tabrikni ko'rish"
    return None  # stage == FINAL_STAGE (6) -> tugma yo'q


def build_markup(short_id: str, stage: int):
    """None qaytarilsa — chaqiruvchi reply_markup=None bilan tugmani olib
    tashlashi kerak (final holat)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    label = _button_label(stage)
    if label is None:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"iprotabrik:claim:{short_id}")]])


# ------------------------------------------------------------------
# ASOSIY KIRISH NUQTASI — handlers/inline_query.py'dagi
# `inline_pro_claim_callback` shuni chaqiradi (har bir bosish uchun).
# ------------------------------------------------------------------
async def handle_stage_click(update, context) -> None:
    query = update.callback_query
    inline_message_id = query.inline_message_id
    short_id = query.data.split(":", 2)[2]

    state = _get_state(short_id)
    trace_id = (state or {}).get("trace_id") or uuid.uuid4().hex[:8]
    log, log_error = _make_logger(trace_id, short_id)
    log("PRO_CALLBACK_RECEIVED", from_user_id=query.from_user.id)

    if not inline_message_id:
        await query.answer("⚠️ Bu tugma faqat inline xabarlar uchun.", show_alert=True)
        return

    greeting_text = tabrik_logic.get_greeting(short_id)
    if not greeting_text or not state:
        log("PRO_GREETING_EXPIRED")
        await query.answer("⚠️ Bu tabrikning muddati o'tgan.", show_alert=True)
        return
    emojis = config.get_tabrik_settings()["emojis"]
    state["trace_id"] = trace_id

    sender_user_id = state["sender_user_id"]

    # --- RECIPIENT RESOLVE (faqat BIRINCHI bosishda; keyingi bosishlarda
    # xotiradan olinadi, chunki bir necha ketma-ket callback keladi) ---
    if state["recipient_user_id"] is None:
        log("PRO_RECIPIENT_RESOLVE_STARTED")
        recipient_user_id = query.from_user.id
        if recipient_user_id == sender_user_id:
            log("PRO_RECIPIENT_IS_SENDER")
            await query.answer("⚠️ O'zingiz yuborgan tabrikni qabul qila olmaysiz.", show_alert=True)
            return
        state["recipient_user_id"] = recipient_user_id
        state["recipient_chat_id"] = recipient_user_id  # Bot API shaxsiy chat modeli
        log("PRO_RECIPIENT_USER_ID", value=recipient_user_id)
    elif state["recipient_user_id"] != query.from_user.id:
        # Boshqa birov bu inline xabarni forward/tanlab bosgan bo'lishi
        # mumkin — faqat ASL qabul qiluvchi davom ettira oladi.
        log("PRO_RECIPIENT_MISMATCH", expected=state["recipient_user_id"], got=query.from_user.id)
        await query.answer("⚠️ Bu tabrik boshqa foydalanuvchi uchun.", show_alert=True)
        return

    recipient_chat_id = state["recipient_chat_id"]

    # --- BUSINESS CONNECTION + RIGHTS CHECK (har bosishda qayta
    # tekshiriladi — arzon, chunki xotiradagi dict o'qish, va ulanish
    # bosqichlar orasida uzilib qolishi mumkin) ---
    log("PRO_BUSINESS_CONNECTION_LOOKUP_STARTED")
    conn_entry = business_storage.get_connection_for_user(sender_user_id)
    usable, reason = business_storage.is_connection_usable(conn_entry)
    if not usable:
        log("PRO_RIGHTS_CHECK_FAILED", reason=reason)
        await query.answer(_BUSINESS_ERROR_MESSAGES.get(reason, "⚠️ Business ulanishi topilmadi."), show_alert=True)
        return
    business_connection_id = conn_entry["connection_id"]
    state["business_connection_id"] = business_connection_id
    log("PRO_BUSINESS_CONNECTION_FOUND", connection_id=business_connection_id)
    log("PRO_RIGHTS_CHECK_SUCCESS")

    # --- DUPLICATE CLICK / LOCK (13-band) ---
    lock = _lock_for(short_id)
    if lock.locked():
        log("PRO_DUPLICATE_CLICK_IGNORED")
        await query.answer("⏳ Bosqich allaqachon bajarilmoqda...", show_alert=False)
        return

    async with lock:
        log("PRO_LOCK_ACQUIRED")
        current_stage = state["current_stage"]
        if current_stage >= FINAL_STAGE:
            log("PRO_ALREADY_FINAL")
            await query.answer("🎁 Tabrik allaqachon ko'rsatilgan.", show_alert=False)
            return

        next_stage = current_stage + 1
        await query.answer("💎 Ochilmoqda...")

        try:
            if next_stage <= TOTAL_EMOJI_STAGES:
                if next_stage == 1:
                    audio_id = config.get_tabrik_settings().get("audio_file_id")
                    if audio_id:
                        sent_audio, audio_err = await _send_with_retry(
                            lambda: context.bot.send_audio(
                                business_connection_id=business_connection_id,
                                chat_id=recipient_chat_id,
                                audio=audio_id,
                            ),
                            log, "PRO_AUDIO_SEND",
                        )
                        if audio_err:
                            log_error("PRO_AUDIO_SEND", audio_err)
                        else:
                            log("PRO_AUDIO_SEND_SUCCESS", message_id=sent_audio.message_id)
                emoji = emojis[next_stage - 1] if next_stage - 1 < len(emojis) else config.get_tabrik_settings()["emojis"][next_stage - 1]
                ok = await _send_stage_emoji(
                    context, business_connection_id, recipient_chat_id, next_stage, emoji, log, log_error,
                )
                if not ok:
                    return  # kritik xato (recipient not eligible) — bosqich ilgarilamaydi
            else:
                ok = await _send_final_text(
                    context, business_connection_id, recipient_chat_id, greeting_text, log, log_error,
                )
                if not ok:
                    return

            state["current_stage"] = next_stage
            log("PRO_STAGE_COMPLETED", at_stage=next_stage)

            new_markup = build_markup(short_id, next_stage)
            try:
                await context.bot.edit_message_reply_markup(
                    inline_message_id=inline_message_id, reply_markup=new_markup,
                )
            except BadRequest as e:
                if "message is not modified" not in str(e).lower():
                    log_error("PRO_BUTTON_EDIT", e)
            except Exception as e:
                log_error("PRO_BUTTON_EDIT", e)

            if next_stage == FINAL_STAGE:
                log("PRO_FINAL_STATE")
                old_task = _revert_tasks.pop(recipient_user_id, None)
                if old_task:
                    old_task.cancel()
                _revert_tasks[recipient_user_id] = asyncio.create_task(
                    _schedule_pro_revert(
                        context, inline_message_id, short_id, recipient_user_id, trace_id
                    )
                )
        except Exception as e:
            log_error("PRO_STAGE_UNEXPECTED", e)


async def _schedule_pro_revert(context, inline_message_id: str, short_id: str, recipient_user_id: int, trace_id: str):
    delay = config.get_tabrik_settings()["revert_minutes"] * 60
    try:
        await asyncio.sleep(delay)
        await context.bot.edit_message_reply_markup(
            inline_message_id=inline_message_id,
            reply_markup=build_markup(short_id, 0),
        )
        state = _get_state(short_id)
        if state:
            state["current_stage"] = 0
        logger.info(f"[PRO][trace={trace_id}] PRO_REVERT_SUCCESS minutes={delay/60:g}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"[PRO][trace={trace_id}] PRO_REVERT_FAILED error_type={type(e).__name__} error={e}")
    finally:
        _revert_tasks.pop(recipient_user_id, None)

async def _send_stage_emoji(context, business_connection_id, chat_id, stage: int, emoji: str, log, log_error) -> bool:
    """True — bosqich muvaffaqiyatli (yoki kechiktirib bo'lmaydigan darajada
    muhim bo'lmagan xato bilan) yakunlandi, chaqiruvchi bosqichni
    ILGARILATISHI mumkin. False — CRITICAL xato, bosqich ILGARILAMASLIGI
    kerak (foydalanuvchi qayta urinib ko'rishi mumkin)."""
    bot = context.bot
    log(f"PRO_STAGE_{stage}_STARTED")
    log(f"PRO_EMOJI_{stage}_SEND_STARTED", emoji=emoji)

    effect_id = telegram_effects.get_effect_id(emoji)
    log(f"PRO_EFFECT_{stage}_SEND_STARTED", effect_id=effect_id)

    sent, err = await _send_with_retry(
        lambda: bot.send_message(
            business_connection_id=business_connection_id, chat_id=chat_id,
            text=emoji, message_effect_id=effect_id,
        ),
        log, f"PRO_EMOJI_{stage}_SEND",
    )

    if err is not None and effect_id is not None:
        # 4-band: effekt rad etilgan bo'lishi mumkin (BadRequest / "Invalid
        # message effect" / "premium restriction" va h.k.) — effektsiz
        # qayta urinamiz, bosqichni TO'XTATMAYMIZ.
        log(
            f"PRO_EFFECT_{stage}_FAILED", effect_id=effect_id,
            error_type=type(err).__name__, error=str(err),
        )
        sent, err = await _send_with_retry(
            lambda: bot.send_message(business_connection_id=business_connection_id, chat_id=chat_id, text=emoji),
            log, f"PRO_EMOJI_{stage}_SEND_NO_EFFECT",
        )
        if err is None:
            log("PRO_EFFECT_FALLBACK_USED", at_stage=stage, emoji=emoji)
    elif err is None and effect_id is not None:
        log(f"PRO_EFFECT_{stage}_SEND_SUCCESS")

    if err is not None:
        if _looks_not_eligible(err):
            log("PRO_BUSINESS_CHAT_NOT_ELIGIBLE", at_stage=f"EMOJI_{stage}", error=str(err))
            return False
        log_error(f"PRO_EMOJI_{stage}_SEND", err)
        # 17-band: effect/temporary xatolar oqimni to'xtatmaydi — emoji
        # yuborilmagan bo'lsa ham bosqich "bajarildi" deb hisoblanadi
        # (foydalanuvchi "Keyingi"ni yana bosishi mumkin bo'lishi uchun).
        log(f"PRO_EMOJI_{stage}_SEND_FAILED_NON_CRITICAL")
        return True

    log(f"PRO_EMOJI_{stage}_SEND_SUCCESS", message_id=sent.message_id)
    delay = config.get_tabrik_settings()["emoji_delay"]
    log(f"PRO_EMOJI_{stage}_WAIT", seconds=delay)
    await asyncio.sleep(delay)
    try:
        await bot.delete_business_messages(
            business_connection_id=business_connection_id,
            message_ids=[sent.message_id],
        )
        log(f"PRO_EMOJI_{stage}_DELETE_SUCCESS")
    except Exception as e:
        log(f"PRO_EMOJI_{stage}_DELETE_FAILED", error_type=type(e).__name__, error=str(e))
    log(f"PRO_STAGE_{stage}_COMPLETED")
    return True


async def _send_final_text(context, business_connection_id, chat_id, text: str, log, log_error) -> bool:
    bot = context.bot
    log("PRO_STAGE_6_STARTED")
    log("PRO_FINAL_TEXT_SEND_STARTED")
    sent, err = await _send_with_retry(
        lambda: bot.send_message(business_connection_id=business_connection_id, chat_id=chat_id, text=text),
        log, "PRO_FINAL_TEXT_SEND",
    )
    if err is not None:
        if _looks_not_eligible(err):
            log("PRO_BUSINESS_CHAT_NOT_ELIGIBLE", at_stage="FINAL_TEXT", error=str(err))
            return False
        log_error("PRO_FINAL_TEXT_SEND", err)
        return False  # yakuniy matnni yubora olmaslik CRITICAL — 6-bosqich tugallanmagan hisoblanadi
    log("PRO_FINAL_TEXT_SEND_SUCCESS", message_id=sent.message_id)
    return True
