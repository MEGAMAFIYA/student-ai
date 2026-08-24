"""
/start menyusi, umumiy tugmalar va bosh menyuga qaytish.
"""

import logging

from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

MENU_CALLBACKS = {
    "course_work": "📘 Kurs ishi / loyiha",
    "translate": "🌐 Tarjima qilish",
    "images_pdf": "🖼 Suratlarni PDF qilish",
    "edit_pdf": "📝 PDF ni tahrirlash",
    "guide": "📖 Qo'llanma tayyorlash",
}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("💬 UNIVERSAL CHAT", callback_data="menu:universal")]]
    for key, label in MENU_CALLBACKS.items():
        rows.append([InlineKeyboardButton(label, callback_data=f"menu:{key}")])
    return InlineKeyboardMarkup(rows)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        logger.warning("/start chaqirildi, lekin update.message yo'q edi — javob yuborilmadi.")
        return
    user = update.effective_user
    logger.info(f"/start bosildi: user_id={user.id if user else '?'}.")
    context.user_data.clear()
    await update.message.reply_text(
        "🤖 *Talaba AI botiga xush kelibsiz!*\n\n"
        "💬 *UNIVERSAL CHAT* — istalgan savolingizga javob beradi va agar boshqa "
        "funksiya kerak bo'lsa (kurs ishi, tarjima va h.k.), o'zi shu funksiyaga "
        "murojat qilib, javobni sizga qaytaradi.\n\n"
        "Yoki quyidagi funksiyalardan birini tanlang:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(),
    )


async def universal_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"💬 'UNIVERSAL CHAT' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        await query.edit_message_text(
            "💬 *UNIVERSAL CHAT* faollashtirildi.\n\n"
            "Menga istalgan savolni yozing. Agar boshqa funksiya kerak bo'lsa "
            "(masalan: \"10 betlik sun'iy intellekt haqida kurs ishi yoz\"), "
            "buni ham shu yerga yozing — kerakli funksiyadan o'zim foydalanaman.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"💬 Universal chat tanlashda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Bekor qilindi.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


async def group_enable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text(
            "ℹ️ Bu buyruq faqat guruhlarda ishlatiladi. Shaxsiy chatda Universal chat "
            "har doim faol."
        )
        return
    context.chat_data["group_active"] = True
    await update.message.reply_text(
        "✅ *Universal chat* ushbu guruhda yoqildi.\n\n"
        "Menga murojaat qilish uchun xabaringizda *dase* so'zini ishlating "
        "(masalan: \"dase bugun qanaqa kun\") yoki mening xabarimga reply qiling.\n\n"
        "⚠️ Eslatma: guruhda barcha xabarlarni ko'rishim uchun @BotFather orqali "
        "\"Group Privacy\" sozlamasini o'chirib qo'yish kerak bo'lishi mumkin.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def group_disable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("ℹ️ Bu buyruq faqat guruhlarda ishlatiladi.")
        return
    context.chat_data["group_active"] = False
    await update.message.reply_text("❌ Universal chat ushbu guruhda o'chirildi.")


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("🏠 Bosh menyu:", reply_markup=main_menu_keyboard())
    return ConversationHandler.END
