"""
🎙 Ovozli xabar — foydalanuvchi istalgan paytda (hech qanday tugma bosmasdan)
ovozli xabar yuborsa, Gemini'ning multimodal (audio) qobiliyati orqali
to'g'ridan-to'g'ri (alohida transkripsiya bosqichisiz) tinglaydi, avval
NIMA DEYILGANINI yozma ko'rinishda tasdiqlaydi, so'ng unga javob beradi —
xuddi ovozli xabarni matn qilib yozgandek.
"""

import logging
from io import BytesIO

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction, ParseMode

from config import VOICE_AI
from ai_clients import ask_gemini_multimodal
import storage

logger = logging.getLogger(__name__)

_SYSTEM = (
    "Sizga ovozli xabar (audio) beriladi. Vazifangiz:\n"
    "1. Avval audioda AYTILGAN gapni SO'ZMA-SO'Z yozib bering.\n"
    "2. Keyin o'sha gapga (savol bo'lsa savolga, so'rov bo'lsa so'rovga) qisqa "
    "va aniq javob bering.\n\n"
    "Javobingizni AYNAN quyidagi formatda bering, boshqa hech narsa qo'shmang:\n"
    "TRANSKRIPSIYA:\n(bu yerga eshitilgan gap)\n\nJAVOB:\n(bu yerga javobingiz)\n\n"
    "FAQAT o'zbek tilida javob bering (audio boshqa tilda bo'lsa ham, javobni "
    "o'zbekchada bering, lekin transkripsiyani audioning o'z tilida yozing)."
)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Telegram Business/inline/service update'larida update.message bo'lmasligi
    # mumkin. Voice handler bunday update'ni xato sifatida global error handler'ga
    # uzatmasligi kerak.
    message = update.effective_message
    voice = getattr(message, "voice", None)
    if message is None or voice is None:
        logger.debug("🎙 Voice handler o'tkazib yuborildi: voice message topilmadi.")
        return

    chat = update.effective_chat
    if chat is None:
        logger.debug("🎙 Voice handler o'tkazib yuborildi: effective_chat yo'q.")
        return

    chat_id = chat.id
    user_id = update.effective_user.id if update.effective_user else 0
    logger.info(f"🎙 Ovozli xabar qabul qilindi: chat_id={chat_id}, davomiyligi={voice.duration}s, hajmi={voice.file_size} bayt.")

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    status = await message.reply_text("⏳ Ovozli xabar tinglanmoqda...")

    try:
        file = await context.bot.get_file(voice.file_id)
        bio = BytesIO()
        await file.download_to_memory(out=bio)
        audio_bytes = bio.getvalue()
    except Exception as e:
        logger.error(f"🎙 Ovozli xabarni yuklab olishda xato: chat_id={chat_id}, {type(e).__name__}: {e}", exc_info=True)
        await status.edit_text(f"❌ Ovozli xabarni yuklab olib bo'lmadi.\n\nSabab: {type(e).__name__}: {e}")
        return

    result, status_code, detail = await ask_gemini_multimodal(VOICE_AI, _SYSTEM, audio_bytes, "audio/ogg", label="Ovozli xabar")

    if not result:
        logger.error(f"🎙 Ovozli xabar QAYTA ISHLANMADI: chat_id={chat_id}, status={status_code}, sabab={detail}.")
        if status_code == "quota":
            user_msg = f"❌ Ovozli xabarni tushunolmadim.\n\nSabab: {detail}"
        elif status_code == "invalid":
            user_msg = f"❌ Ovozli xabar xizmati sozlanmagan yoki kaliti yaroqsiz.\n\nSabab: {detail}"
        elif status_code == "paid":
            user_msg = f"❌ Ovozli xabar xizmati hozircha ishlamayapti.\n\nSabab: {detail}"
        else:
            user_msg = (
                f"❌ Ovozli xabarni tushunib bo'lmadi.\n\nSabab: {detail}\n\n"
                "Aniqroq gapirib qayta yuboring yoki matn qilib yozing."
            )
        await status.edit_text(user_msg)
        return

    transcript, answer = _split_response(result)
    logger.info(f"🎙 ✅ Ovozli xabar muvaffaqiyatli qayta ishlandi: chat_id={chat_id}, transkripsiya uzunligi={len(transcript)}, javob uzunligi={len(answer)}.")

    if user_id:
        storage.record_usage("voice", user_id)

    try:
        await status.delete()
    except Exception:
        pass

    text = f"🎙 _\"{transcript}\"_\n\n{answer}" if transcript else answer
    await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


def _split_response(raw: str) -> tuple[str, str]:
    upper = raw.upper()
    t_idx = upper.find("TRANSKRIPSIYA:")
    j_idx = upper.find("JAVOB:")
    if t_idx != -1 and j_idx != -1 and j_idx > t_idx:
        transcript = raw[t_idx + len("TRANSKRIPSIYA:"):j_idx].strip()
        answer = raw[j_idx + len("JAVOB:"):].strip()
        return transcript, answer
    return "", raw.strip()
