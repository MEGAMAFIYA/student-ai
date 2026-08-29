"""
🎨 /rasim — Telegram Mini App orqali rasm chizish. Foydalanuvchi tugmani
bosganda Mini App (webapp/rasim/index.html) ochiladi, u yerda chizib
"📤 Uzatish"ni bossa, rasm PNG sifatida backendga (bot.py > HealthHandler
> /miniapp/rasim/upload) yuboriladi va bot uni AYNAN shu buyruq
yuborilgan chatga rasm sifatida qaytaradi.

XAVFSIZLIK: qaysi chatga/kimning nomidan rasm qaytarilishi kerakligi
FAQAT server tomonida — webapp_security.py orqali — aniqlanadi (bitta
martalik "rid" tokeni + Telegram initData HMAC tasdiqlash), Mini App
frontendidan kelayotgan hech qanday chat_id/user_id'ga to'g'ridan-to'g'ri
ishonilmaydi (batafsili: webapp_security.py).
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

from config import PUBLIC_BASE_URL
import webapp_security

logger = logging.getLogger(__name__)


async def rasim_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, override_text: str | None = None):
    """`override_text` — /rasim uchun ishlatilmaydi (buyruqdan keyin
    matn kerak emas), lekin handlers/mention_dispatch.py BARCHA maxsus
    handlerlarni bir xil signatura bilan chaqirgani uchun shu yerda ham
    qabul qilinadi (e'tiborsiz qoldiriladi)."""
    if not update.message:
        return
    user = update.effective_user
    chat = update.effective_chat

    if not PUBLIC_BASE_URL:
        logger.error("🎨 /rasim: PUBLIC_BASE_URL sozlanmagan — Mini App uchun ochiq HTTPS manzil kerak.")
        await update.message.reply_text(
            "❌ Bu funksiya hozircha sozlanmagan (PUBLIC_BASE_URL o'rnatilmagan). Administratorga xabar bering."
        )
        return

    # 🎫 Bitta martalik so'rov tokeni — rasm yuborilganda AYNAN shu
    # chatga va AYNAN shu foydalanuvchi nomidan qaytarilishini kafolatlaydi.
    rid = webapp_security.create_request(chat_id=chat.id, user_id=user.id)
    webapp_url = f"{PUBLIC_BASE_URL}/miniapp/rasim/?rid={rid}"

    await update.message.reply_text(
        "🎨 Rasm chizish uchun quyidagi tugmani bosing:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎨 Rasm chizish", web_app=WebAppInfo(url=webapp_url))
        ]]),
    )
    logger.info(f"🎨 /rasim ochildi: chat_id={chat.id}, user_id={user.id}, rid={rid}.")
