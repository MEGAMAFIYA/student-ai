"""
🤖 Talaba AI — ko'p funksiyali Telegram bot.
Har bir funksiya (Universal chat, Kurs ishi, Tarjima, Suratlarni PDF qilish,
PDF tahrirlash, Qo'llanma tayyorlash) o'ziga alohida sozlanadigan AI (Gemini /
Groq / bepul zaxira) bilan ishlaydi. Render Free Web Service uchun HTTP
health-check server ham ishga tushiriladi.
"""

import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from telegram import BotCommand, BotCommandScopeDefault, BotCommandScopeAllGroupChats
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from config import TELEGRAM_TOKEN
from handlers import menu, universal_chat, course_work, translate as translate_handler, images_to_pdf, edit_pdf, guide

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand("start", "Botni ishga tushirish / asosiy menyu"),
    BotCommand("yoqish", "Guruhda Universal chatni yoqish"),
    BotCommand("ochirish", "Guruhda Universal chatni o'chirish"),
    BotCommand("cancel", "Joriy amalni bekor qilish"),
]


async def _post_init(application):
    """Telegram '/' tugmasi bosilganda ko'rinadigan buyruqlar ro'yxatini o'rnatadi
    (shaxsiy chatlar va guruhlar uchun alohida-alohida)."""
    await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllGroupChats())


async def _error_handler(update, context):
    """Har qanday kutilmagan xatoni ushlaydi va logga yozadi — shu orqali
    foydalanuvchi hech qanday xabarsiz "osilib" qolmaydi, aksincha aniq
    xato xabarini oladi."""
    logger.error("Kutilmagan xato yuz berdi:", exc_info=context.error)
    try:
        if update and getattr(update, "effective_message", None):
            await update.effective_message.reply_text(
                "❌ Kutilmagan xatolik yuz berdi. Iltimos, qayta urinib ko'ring "
                "yoki /start bilan boshidan boshlang."
            )
    except Exception:
        pass


# ============================================================
# RENDER HTTP HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Talaba AI bot ishlamoqda!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():
    try:
        port = int(os.getenv("PORT", "10000"))
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        print(f"🌐 HTTP server ishga tushdi: 0.0.0.0:{port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"HTTP server xatosi: {e}", exc_info=True)


# ============================================================
# CONVERSATION HANDLERLAR
# ============================================================

def build_course_work_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(course_work.entry, pattern="^menu:course_work$")],
        states={
            course_work.CW_PAGES: [MessageHandler(filters.TEXT & ~filters.COMMAND, course_work.receive_pages)],
            course_work.CW_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, course_work.receive_topic_and_generate)],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="course_work_conv",
    )


def build_translate_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(translate_handler.entry, pattern="^menu:translate$")],
        states={
            translate_handler.TR_WAIT_CONTENT: [
                MessageHandler((filters.TEXT | filters.Document.PDF) & ~filters.COMMAND, translate_handler.receive_content)
            ],
            translate_handler.TR_WAIT_LANG: [
                CallbackQueryHandler(translate_handler.lang_chosen, pattern="^trlang:")
            ],
            translate_handler.TR_WAIT_CUSTOM_LANG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, translate_handler.custom_lang_chosen)
            ],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="translate_conv",
    )


def build_images_pdf_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(images_to_pdf.entry, pattern="^menu:images_pdf$")],
        states={
            images_to_pdf.IMG_COLLECTING: [
                MessageHandler(filters.PHOTO, images_to_pdf.receive_photo),
                CallbackQueryHandler(images_to_pdf.confirm, pattern="^imgpdf:confirm$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="images_pdf_conv",
    )


def build_edit_pdf_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_pdf.entry, pattern="^menu:edit_pdf$")],
        states={
            edit_pdf.EP_WAIT_PDF: [MessageHandler(filters.Document.PDF, edit_pdf.receive_pdf)],
            edit_pdf.EP_WAIT_INSTRUCTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_pdf.receive_instruction)],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="edit_pdf_conv",
    )


def build_guide_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(guide.entry, pattern="^menu:guide$")],
        states={
            guide.GD_COLLECTING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, guide.receive_questions),
                CallbackQueryHandler(guide.finish, pattern="^guide:finish$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="guide_conv",
    )


# ============================================================
# ISHGA TUSHIRISH
# ============================================================

def main():
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN o'rnatilmagan!")
        return

    print("🤖 Bot ishga tushmoqda...")

    Thread(target=start_health_server, daemon=True).start()

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(60)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(60)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", menu.start_cmd))
    app.add_handler(CommandHandler("cancel", menu.cancel_cmd))
    app.add_handler(CommandHandler("yoqish", menu.group_enable_cmd))
    app.add_handler(CommandHandler("ochirish", menu.group_disable_cmd))

    # Har bir funksiya uchun alohida conversation (bosqichma-bosqich so'rov-javob)
    app.add_handler(build_course_work_conv())
    app.add_handler(build_translate_conv())
    app.add_handler(build_images_pdf_conv())
    app.add_handler(build_edit_pdf_conv())
    app.add_handler(build_guide_conv())

    app.add_handler(CallbackQueryHandler(menu.universal_selected, pattern="^menu:universal$"))
    app.add_handler(CallbackQueryHandler(menu.back_to_menu, pattern="^menu:back$"))

    # UNIVERSAL CHAT — hech qanday conversation faol bo'lmaganda ishlaydi
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, universal_chat.handle_message))

    app.add_error_handler(_error_handler)

    print("✅ Bot tayyor! /start yuboring.")
    app.run_polling()


if __name__ == "__main__":
    main()
