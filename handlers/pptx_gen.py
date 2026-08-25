"""
📊 Taqdimot (PPTX) tayyorlash — foydalanuvchi mavzu va slaydlar sonini
beradi, AI mavzuni mantiqiy slaydlarga bo'lib (sarlavha + 3-5 bullet nuqta
har biriga) JSON ko'rinishida tuzadi, so'ng pptx_tools.py orqali chiroyli,
izchil dizaynli .pptx fayl quriladi va yuboriladi.
"""

import asyncio
import json
import logging
import re

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode, ChatAction

from config import PPTX_AI
from ai_clients import ask_ai
from pptx_tools import build_presentation
from handlers.menu import main_menu_keyboard
import storage

logger = logging.getLogger(__name__)

PX_TOPIC, PX_COUNT = range(2)

_COUNT_OPTIONS = [6, 8, 10, 12, 15]

_SYSTEM = (
    "Siz taqdimot (prezentatsiya) tuzuvchi mutaxassissiz. Berilgan mavzuni "
    "so'ralgan sondagi slaydga mantiqiy ketma-ketlikda bo'ling (kirish -> "
    "asosiy qismlar -> xulosa/tavsiyalar tarzida). FAQAT quyidagi JSON "
    "massiv formatida javob bering, boshqa hech qanday matn yozmang:\n"
    '[{"heading": "Slayd sarlavhasi", "bullets": ["qisqa fikr 1", "qisqa fikr 2", "qisqa fikr 3"]}, ...]\n'
    "Har bir 'bullets' elementi QISQA (5-12 so'z) va aniq bo'lsin — to'liq "
    "gap emas, taqdimot uchun mos ibora. Har slaydda 3-5 ta bullet bo'lsin. "
    "FAQAT o'zbek tilida yozing."
)


def _count_keyboard() -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(str(n), callback_data=f"pptx:count:{n}") for n in _COUNT_OPTIONS]
    return InlineKeyboardMarkup([row])


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"📊 'Taqdimot (PPTX)' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        context.user_data["flow"] = "pptx"
        await query.edit_message_text(
            "📊 *Taqdimot (PPTX) tayyorlash*\n\n"
            "Taqdimot qaysi mavzuda bo'lishi kerak? Mavzuni yozing:",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"📊 Taqdimot menyusini ochishda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise
    return PX_TOPIC


async def receive_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = update.message.text.strip()
    if not topic:
        await update.message.reply_text("❗️ Iltimos, mavzuni yozing.")
        return PX_TOPIC
    context.user_data["px_topic"] = topic
    await update.message.reply_text(
        f"✅ Mavzu: *{topic}*\n\nNecha slayddan iborat bo'lsin?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_count_keyboard(),
    )
    return PX_COUNT


async def receive_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    count = int(query.data.split(":")[-1])
    topic = context.user_data.get("px_topic", "")
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0

    logger.info(f"📊 Taqdimot so'rovi: chat_id={chat_id}, mavzu='{topic}', slaydlar soni={count}.")
    await query.edit_message_text(f"⏳ *{topic}* mavzusida {count} slaydlik taqdimot tayyorlanmoqda...", parse_mode=ParseMode.MARKDOWN)
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)

    slides = await _generate_slides(topic, count)
    if not slides:
        logger.error(f"📊 Taqdimot YARATILMADI: chat_id={chat_id}, mavzu='{topic}' — AI JSON javob bermadi (sababi yuqoridagi ai_clients loglarida).")
        await query.edit_message_text(
            "❌ Taqdimotni yaratib bo'lmadi — AI xizmati hozir javob bermayapti. Birozdan so'ng qayta urinib ko'ring."
        )
        context.user_data.clear()
        return ConversationHandler.END

    try:
        pptx_buf = await asyncio.to_thread(build_presentation, topic, slides, "Talaba AI")
    except Exception as e:
        logger.error(f"📊 PPTX faylini qurishda xato ('{topic}'): {type(e).__name__}: {e}", exc_info=True)
        await query.edit_message_text("❌ Taqdimot faylini qurishda xatolik yuz berdi.")
        context.user_data.clear()
        return ConversationHandler.END

    filename = f"{topic[:40]}.pptx"
    msg = await context.bot.send_document(
        chat_id,
        document=InputFile(pptx_buf, filename=filename),
        caption=f"📊 {topic}\n📎 {len(slides)} slayd.",
        reply_markup=main_menu_keyboard(),
    )
    logger.info(f"📊 Taqdimot muvaffaqiyatli yuborildi: chat_id={chat_id}, {len(slides)} slayd.")
    if user_id and msg.document:
        storage.record_file(user_id, "pptx", topic, msg.document.file_id)
        storage.record_usage("pptx", user_id)

    try:
        await query.delete_message()
    except Exception:
        pass

    context.user_data.clear()
    return ConversationHandler.END


async def _generate_slides(topic: str, count: int) -> list[dict] | None:
    prompt = f"Mavzu: '{topic}'\nSlaydlar soni: {count} ta (kirish va xulosa ham shu songa kiradi)."
    for attempt in range(1, 3):
        logger.info(f"📊 [SLAYDLAR] AI ga so'rov yuborilmoqda ({attempt}/2-urinish, provider={PPTX_AI.get('provider')})...")
        raw = await ask_ai(PPTX_AI, prompt, _SYSTEM)
        if not raw:
            logger.warning(f"📊 [SLAYDLAR] Bo'sh javob ({attempt}/2-urinish).")
            continue
        try:
            cleaned = re.sub(r"^```json\s*|^```\s*|```\s*$", "", raw.strip(), flags=re.MULTILINE).strip()
            data = json.loads(cleaned)
            if isinstance(data, list) and data:
                slides = []
                for item in data:
                    if isinstance(item, dict) and item.get("heading"):
                        bullets = [str(b).strip() for b in item.get("bullets", []) if str(b).strip()]
                        slides.append({"heading": str(item["heading"]).strip(), "bullets": bullets or ["—"]})
                if slides:
                    logger.info(f"📊 [SLAYDLAR] ✅ {len(slides)} ta slayd tuzildi.")
                    return slides
            logger.warning(f"📊 [SLAYDLAR] JSON bo'sh yoki noto'g'ri struktura ({attempt}/2-urinish).")
        except Exception as e:
            logger.warning(f"📊 [SLAYDLAR] JSON parse xato ({attempt}/2-urinish): {e} — xom javob: {raw[:300]!r}")
    logger.error(f"📊 [SLAYDLAR] ❌ 2 marta urinishdan keyin ham to'g'ri JSON olinmadi ('{topic}').")
    return None
