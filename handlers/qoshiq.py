"""
🎵 /qo'shiq — foydalanuvchi artist yoki qo'shiq nomini yozadi, bot eng
mos natijalarni inline tugmalar bilan ko'rsatadi, tanlangani Telegram
AUDIO sifatida yuboriladi.

Ishlaydi:
  /qo'shiq Ozodbek Nazarbekov
  /qoshiq Ozodbek Nazarbekov       (apostrofsiz ASCII alias — CommandHandler)
  @Student_ai_uz_bot /qo'shiq ...  (handlers/mention_dispatch.py orqali)

MUHIM (Telegram platforma cheklovi — kodda tuzatib bo'lmaydi): "/qo'shiq"
tarkibida apostrof borligi uchun Telegram buni haqiqiy buyruq deb
belgilamaydi. Natijada:
  - Shaxsiy chatda muammosiz ishlaydi (barcha xabarlar botga yetadi).
  - Guruhda Privacy Mode YOQILGAN bo'lsa, mention'siz "/qo'shiq ..."
    xabari botga UMUMAN YETIB BORMAYDI. Guruhda kafolatlangan ishlashi
    uchun ikkita yo'l bor: (1) "@Bot /qo'shiq ..." (mention) yoki
    (2) apostrofsiz "/qoshiq ..." (haqiqiy buyruq) yoki (3) BotFather'da
    Privacy Mode'ni o'chirish (bot.py'dagi eslatmaga qarang).

SESSIYA XAVFSIZLIGI: har bir qidiruv natijasi (user_id, chat_id) juftligi
bilan bog'langan ALOHIDA, tasodifiy sessiya ID ostida saqlanadi. Callback
bosilganda: (1) sessiya muddati o'tmaganini, (2) bosgan odam AYNAN o'sha
so'rovni yuborgan foydalanuvchi ekanini tekshiradi — shu orqali bitta
guruhda bir nechta foydalanuvchi parallel qidirsa ham, ularning
natijalari/tanlovlari bir-biriga ARALASHMAYDI.

MANBALAR: qidiruv bir nechta ochiq/qonuniy platformadan (hozircha
YouTube va SoundCloud — video_tools.SEARCH_SOURCES) baravar olib
boriladi, natijalar tugmalarda manba belgisi (▶️/☁️) bilan ko'rsatiladi.
"""

import asyncio
import logging
import re
import shutil
import tempfile
import time
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import config
import storage
import video_tools

logger = logging.getLogger(__name__)

# Apostrofning barcha variantlari (handlers/mention_dispatch.py dagi
# _APOSTROPHE_VARIANTS bilan bir xil ro'yxat) — foydalanuvchi qaysi
# klaviatura belgisini ishlatgan bo'lishidan qat'i nazar mos keladi.
_COMMAND_RE = re.compile(r"^/(?:qo[`'\u00b4\u2018\u2019\u02bb\u02bc]shiq|qoshiq)(?:@\w+)?\s*", re.IGNORECASE)

# session_id -> {"user_id","chat_id","results":[...],"ts": float}
_SESSIONS: dict[str, dict] = {}


def _purge_expired_sessions() -> None:
    cutoff = time.time() - config.QOSHIQ_SESSION_TTL_SEC
    expired = [sid for sid, s in _SESSIONS.items() if s["ts"] < cutoff]
    for sid in expired:
        _SESSIONS.pop(sid, None)


def _format_duration(seconds) -> str:
    if not seconds:
        return ""
    seconds = int(seconds)
    return f" ({seconds // 60}:{seconds % 60:02d})"


async def qoshiq_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, override_text: str | None = None):
    """`override_text` — handlers/mention_dispatch.py orqali chaqirilganda
    beriladi (qarang: fayl boshidagi izoh)."""
    if not update.message:
        return
    raw_text = override_text if override_text is not None else (update.message.text or "")
    query = _COMMAND_RE.sub("", raw_text, count=1).strip()

    if not query:
        await update.message.reply_text(
            "🎵 Qidirmoqchi bo'lgan qo'shiq yoki ijrochi nomini ham yozing, masalan:\n\n"
            "`/qo'shiq Ozodbek Nazarbekov`",
            parse_mode="Markdown",
        )
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    status = await update.message.reply_text(f"🔎 \"{query}\" qidirilmoqda...")

    try:
        results = await asyncio.to_thread(video_tools.search_tracks, query, config.QOSHIQ_SEARCH_COUNT)
    except video_tools.DownloadError as e:
        await status.edit_text(str(e))
        return
    except Exception as e:
        logger.error(f"🎵 /qo'shiq qidiruvida kutilmagan xato ('{query}'): {type(e).__name__}: {e}", exc_info=True)
        await status.edit_text(f"❌ Qidiruvda kutilmagan xatolik yuz berdi.\n\nSabab: {type(e).__name__}: {e}")
        return

    _purge_expired_sessions()
    session_id = uuid.uuid4().hex[:12]
    _SESSIONS[session_id] = {
        "user_id": user_id, "chat_id": chat_id, "results": results, "ts": time.time(),
    }

    # Har bir tugmada MANBA ham ko'rsatiladi (masalan "▶️ YouTube",
    # "☁️ SoundCloud") — foydalanuvchi qaysi saytdan kelayotganini bilib
    # tanlasin.
    buttons = [
        [InlineKeyboardButton(
            f"{r['source_emoji']} {r['title'][:48]}{_format_duration(r['duration'])}",
            callback_data=f"song:{session_id}:{i}",
        )]
        for i, r in enumerate(results)
    ]
    await status.edit_text(
        f"🎵 \"{query}\" bo'yicha natijalar — birini tanlang:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    logger.info(f"🎵 /qo'shiq qidiruvi: chat_id={chat_id}, user_id={user_id}, query='{query}', session={session_id}.")


async def qoshiq_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_cb = update.callback_query
    parts = query_cb.data.split(":")
    if len(parts) != 3:
        await query_cb.answer("⚠️ Noto'g'ri so'rov.", show_alert=True)
        return
    _, session_id, idx_str = parts

    _purge_expired_sessions()
    session = _SESSIONS.get(session_id)
    if not session:
        await query_cb.answer("⚠️ Bu qidiruv natijasining muddati o'tgan. Qaytadan /qo'shiq deb qidiring.", show_alert=True)
        return

    # 🔒 FAQAT shu qidiruvni boshlagan foydalanuvchi tanlashi mumkin — shu
    # orqali B foydalanuvchi A ning natijalarini bosib, A ning holatini
    # buzmaydi (yoki B o'ziga tegishli bo'lmagan audio olmaydi).
    if update.effective_user.id != session["user_id"]:
        await query_cb.answer("⚠️ Bu qidiruv sizga tegishli emas.", show_alert=True)
        return

    try:
        idx = int(idx_str)
        track = session["results"][idx]
    except (ValueError, IndexError):
        await query_cb.answer("⚠️ Bu natija endi mavjud emas.", show_alert=True)
        return

    await query_cb.answer("⏳ Yuklab olinmoqda...")
    chat_id = session["chat_id"]
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VOICE)

    dest_dir = tempfile.mkdtemp(prefix="qoshiq_")
    try:
        filepath = await asyncio.to_thread(
            video_tools.download_audio, track["webpage_url"], dest_dir, config.QOSHIQ_MAX_MB, config.QOSHIQ_DOWNLOAD_TIMEOUT_SEC,
        )
        with open(filepath, "rb") as f:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=InputFile(f),
                title=track["title"][:64],
                performer=track.get("uploader") or None,
                write_timeout=120,
                read_timeout=120,
            )
        storage.record_usage("qoshiq", session["user_id"])
        logger.info(f"🎵 /qo'shiq yuborildi: chat_id={chat_id}, user_id={session['user_id']}, track='{track['title']}'.")
    except video_tools.DownloadError as e:
        await context.bot.send_message(chat_id, str(e))
    except Exception as e:
        logger.error(f"🎵 /qo'shiq yuborishda kutilmagan xato (chat_id={chat_id}): {type(e).__name__}: {e}", exc_info=True)
        await context.bot.send_message(chat_id, f"❌ Qo'shiqni yuborishda kutilmagan xatolik yuz berdi.\n\nSabab: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(dest_dir, ignore_errors=True)
        # Sessiyani ishlatilgandan keyin o'chiramiz — bir marta tanlangan
        # tugmalar qayta bosilsa, "muddati o'tgan" deb ko'rsatiladi (aniq va
        # kutilgan xatti-harakat, xotira ham cheksiz o'smaydi).
        _SESSIONS.pop(session_id, None)
