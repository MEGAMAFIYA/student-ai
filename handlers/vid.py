"""
🎬 /vid — foydalanuvchi bergan havoladan (YouTube, Instagram va yt-dlp
qo'llab-quvvatlaydigan boshqa manbalar) videoni yuklab, Telegram'ga VIDEO
sifatida yuboradi.

Ishlaydi:
  /vid URL
  /vid@Student_ai_uz_bot URL   (CommandHandler avtomatik ushlaydi)
  @Student_ai_uz_bot /vid URL  (handlers/mention_dispatch.py orqali)

Private, group va supergroup — barchasida bir xil ishlaydi (bu — oddiy
ASCII buyruq, Telegram uni Privacy Mode yoqilgan bo'lsa ham botga
yetkazadi).

Parallel foydalanuvchilar bir-biriga aralashmasligi uchun HAR BIR
so'rov o'zining ALOHIDA vaqtinchalik papkasida ishlaydi (tempfile.mkdtemp)
va tugagach albatta o'chiriladi (finally blokida).
"""

import asyncio
import logging
import re
import shutil
import tempfile

from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import config
import storage
import video_tools

logger = logging.getLogger(__name__)

_COMMAND_RE = re.compile(r"^/vid(?:@\w+)?\s*", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _extract_url(raw_text: str) -> str | None:
    text = _COMMAND_RE.sub("", raw_text or "", count=1).strip()
    m = _URL_RE.search(text)
    return m.group(0) if m else None


async def vid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, override_text: str | None = None):
    """`override_text` — handlers/mention_dispatch.py orqali ("@Bot /vid URL"
    xabaridan mention qismi olib tashlangandan keyin) chaqirilganda beriladi."""
    if not update.message:
        return
    raw_text = override_text if override_text is not None else (update.message.text or "")
    url = _extract_url(raw_text)

    if not url:
        await update.message.reply_text(
            "🎬 Video havolasini ham yozing, masalan:\n\n`/vid https://www.youtube.com/watch?v=...`",
            parse_mode="Markdown",
        )
        return

    chat_id = update.effective_chat.id
    status = await update.message.reply_text("⏳ Video yuklab olinmoqda, biroz kuting...")
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)

    # Har bir so'rov uchun ALOHIDA vaqtinchalik papka — parallel
    # foydalanuvchilarning yuklab olishlari fayl darajasida aralashmasligi
    # uchun.
    dest_dir = tempfile.mkdtemp(prefix="vid_")
    try:
        filepath = await asyncio.to_thread(
            video_tools.download_video, url, dest_dir, config.VID_MAX_MB, config.VID_DOWNLOAD_TIMEOUT_SEC,
        )
        with open(filepath, "rb") as f:
            await update.message.reply_video(
                video=InputFile(f),
                caption="✅ Video tayyor.",
                write_timeout=120,
                read_timeout=120,
            )
        storage.record_usage("vid", update.effective_user.id)
        logger.info(f"🎬 /vid muvaffaqiyatli: chat_id={chat_id}, url={url}.")
    except video_tools.DownloadError as e:
        await update.message.reply_text(str(e))
    except Exception as e:
        logger.error(f"🎬 /vid kutilmagan xato (chat_id={chat_id}, url={url}): {type(e).__name__}: {e}", exc_info=True)
        await update.message.reply_text("❌ Video yuborishda kutilmagan xatolik yuz berdi.")
    finally:
        # ⬇️ Vaqtinchalik fayllarni albatta tozalaymiz (diskni to'ldirmasligi
        # uchun) — muvaffaqiyatli yoki xato bo'lishidan qat'i nazar.
        shutil.rmtree(dest_dir, ignore_errors=True)
        try:
            await status.delete()
        except Exception:
            pass
