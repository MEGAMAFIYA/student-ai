"""
🤖 Talaba AI — ko'p funksiyali Telegram bot.
Har bir funksiya (Universal chat, Kurs ishi, Tarjima, Suratlarni PDF qilish,
PDF tahrirlash, Qo'llanma tayyorlash) o'ziga alohida sozlanadigan AI (Gemini /
Groq / bepul zaxira) bilan ishlaydi. Render Free Web Service uchun HTTP
health-check server ham ishga tushiriladi.
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from telegram import BotCommand, BotCommandScopeDefault, BotCommandScopeAllGroupChats, BotCommandScopeChat
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    InlineQueryHandler,
    ChosenInlineResultHandler,
    filters,
)

import config
from config import TELEGRAM_TOKEN
from handlers import menu, universal_chat, course_work, translate as translate_handler, images_to_pdf, edit_pdf, guide, inline_query, developer
from pdf_tools import make_pdf

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# httpx/telegram kutubxonalari har 10 soniyada "getUpdates" so'rovini INFO
# darajasida yozib, logni to'ldirib yuboradi va botning O'Z harakatlari
# (kurs ishi bosqichlari, AI chaqiruvlari) shular orasida ko'zdan yo'qoladi.
# Shuning uchun bu kutubxonalarni WARNING darajasiga tushiramiz — faqat
# haqiqiy xatoliklar ko'rinadi, muvaffaqiyatli getUpdates spami yo'qoladi.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand("start", "Botni ishga tushirish / asosiy menyu"),
    BotCommand("yoqish", "Guruhda Universal chatni yoqish"),
    BotCommand("ochirish", "Guruhda Universal chatni o'chirish"),
    BotCommand("cancel", "Joriy amalni bekor qilish"),
]


async def _post_init(application):
    """Telegram '/' tugmasi bosilganda ko'rinadigan buyruqlar ro'yxatini o'rnatadi
    (shaxsiy chatlar va guruhlar uchun alohida-alohida). /developer buyrug'i
    BUNGA QO'SHILMAYDI — u faqat config.ADMIN_IDS ichidagi foydalanuvchilarning
    shaxsiy '/' menyusida (BotCommandScopeChat orqali) alohida ko'rsatiladi,
    boshqa hech kimga (oddiy foydalanuvchi yoki guruhlarga) ko'rinmaydi."""
    await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllGroupChats())

    if config.ADMIN_IDS:
        admin_commands = BOT_COMMANDS + [BotCommand("developer", "🔧 AI sozlamalari (faqat admin)")]
        for admin_id in config.ADMIN_IDS:
            try:
                await application.bot.set_my_commands(
                    admin_commands, scope=BotCommandScopeChat(chat_id=admin_id)
                )
            except Exception as e:
                logger.warning(f"/developer buyrug'ini admin ({admin_id}) uchun o'rnatishda xato: {e}")


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
# RENDER HTTP HEALTH SERVER + INLINE PLACEHOLDER PDF
# ============================================================
# Inline rejimda "kurs ishi" so'ralganda, Telegram'ga darhol (AI hali javob
# bermasdan turib) bitta "hujjat turidagi" natija ko'rsatishimiz kerak —
# aks holda keyinchalik uni haqiqiy PDF bilan almashtirib bo'lmaydi (Telegram
# matn xabarini hujjatga aylantirishga ruxsat bermaydi, lekin hujjatni
# boshqa hujjatga almashtirishga ruxsat beradi). Shuning uchun shu yerda
# kichik "⏳ tayyorlanmoqda..." PDF generatsiya qilib, health-server orqali
# ochiq (https) manzildan xizmat qilamiz — Telegram uni o'sha yerdan bir
# marta yuklab oladi.

_PLACEHOLDER_PDF_BYTES: bytes = b""
_PLACEHOLDER_PDF_PATH = "/placeholder.pdf"


def _build_placeholder_pdf() -> bytes:
    try:
        buf = make_pdf(
            "Talaba AI",
            "Hujjat tayyorlanmoqda...\n\nBiroz kuting, tez orada shu joyga tayyor "
            "kurs ishi (PDF) qo'yiladi. Agar bu matn hali ham ko'rinayotgan bo'lsa, "
            "generatsiya davom etmoqda yoki xatolik yuz bergan bo'lishi mumkin.",
        )
        return buf.getvalue()
    except Exception as e:
        logger.error(f"Placeholder PDF yaratishda xato: {e}", exc_info=True)
        return b""


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == _PLACEHOLDER_PDF_PATH and _PLACEHOLDER_PDF_BYTES:
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(_PLACEHOLDER_PDF_BYTES)))
            self.end_headers()
            self.wfile.write(_PLACEHOLDER_PDF_BYTES)
            return

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
    global _PLACEHOLDER_PDF_BYTES
    _PLACEHOLDER_PDF_BYTES = _build_placeholder_pdf()
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


def build_developer_conv():
    """👨‍💻 /developer — faqat adminlar uchun AI sozlamalari menyusi.
    Ruxsat tekshiruvi handlers/developer.py ichida (_is_admin) amalga oshadi —
    admin bo'lmagan foydalanuvchi buyruqni chaqirsa, darhol rad javobi olib
    conversation tugaydi."""
    return ConversationHandler(
        entry_points=[CommandHandler("developer", developer.entry)],
        states={
            developer.DEV_MENU: [
                CallbackQueryHandler(developer.on_callback, pattern="^dev:"),
            ],
            developer.DEV_WAIT_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, developer.on_text),
                CallbackQueryHandler(developer.on_callback, pattern="^dev:"),
            ],
            developer.DEV_WAIT_BULK_MODEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, developer.on_bulk_text),
                CallbackQueryHandler(developer.on_callback, pattern="^dev:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", developer.cancel)],
        name="developer_conv",
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

    # ============================================================
    # CONCURRENCY: bir nechta foydalanuvchi bir vaqtda ishlashi uchun
    # ============================================================
    # PTB'ning STANDART sozlamasi concurrent_updates=False — bu degani,
    # bot kiruvchi update'larni BIR-BIRIDAN KEYIN, TO'LIQ KUTIB ishlaydi
    # (hatto ular sof tarmoq so'rovi/AI javobi kutish bo'lsa ham). Aynan
    # shu sozlama "1-foydalanuvchi tugamaguncha 2-chi kuta beradi" degan
    # muammoning bevosita sababi edi.
    #
    # concurrent_updates(N) — PTB ICHIDA asyncio.Semaphore(N) orqali
    # ishlaydi: bir vaqtning o'zida ko'pi bilan N ta update parallel
    # qayta ishlanadi, N+1-chisi esa BOSHQA FOYDALANUVCHILARNI emas,
    # faqat navbatdagi update'ni bir oz kutadi. Bu — "cheksiz parallel
    # task" emas, balki aynan talab qilingan semaphore/worker-limit
    # yondashuvi (global navbat emas).
    #
    # N=32 qiymati: Python'ning standart ThreadPoolExecutor hajmi bilan
    # (pastga qarang) muvofiqlashtirilgan — 10-20 ta faol foydalanuvchi
    # bemalol sig'adi, shu bilan birga bitta jarayonni cheksiz sonli
    # oqim (thread)/vazifa bilan ortiqcha yuklab yubormaydi.
    CONCURRENT_UPDATES = 32

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(CONCURRENT_UPDATES)
        .connect_timeout(60)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(60)
        .post_init(_post_init)
        .build()
    )

    # PDF yaratish/o'qish kabi CPU-bog'liq vazifalar (pdf_tools.py) endi
    # asyncio.to_thread() orqali shu executor'da bajariladi (handlerlarga
    # qarang). Standart executor hajmi ba'zi hosting muhitlarida (masalan,
    # 1 vCPU'li Render Free) faqat 5 ga teng bo'lishi mumkin
    # (min(32, os.cpu_count()+4)) — bu bir nechta foydalanuvchi bir vaqtda
    # PDF generatsiya qilganda navbatga sabab bo'lishi mumkin, shuning
    # uchun uni CONCURRENT_UPDATES bilan mos hajmga kengaytiramiz.
    loop = asyncio.get_event_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=CONCURRENT_UPDATES))

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
    app.add_handler(build_developer_conv())

    app.add_handler(CallbackQueryHandler(menu.universal_selected, pattern="^menu:universal$"))
    app.add_handler(CallbackQueryHandler(menu.back_to_menu, pattern="^menu:back$"))

    # UNIVERSAL CHAT — hech qanday conversation faol bo'lmaganda ishlaydi
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, universal_chat.handle_message))

    # INLINE REJIM — "@Student_ai_uz_bot savol" deb istalgan chatda yozilganda
    # (bot o'sha chatga a'zo bo'lmasa ham) ishlaydi. BotFather'da /setinline va
    # /setinlinefeedback (Enabled) sozlangan bo'lishi SHART — inline_query.py
    # faylidagi izohga qarang.
    app.add_handler(InlineQueryHandler(inline_query.on_inline_query))
    app.add_handler(ChosenInlineResultHandler(inline_query.on_chosen_inline_result))

    app.add_error_handler(_error_handler)

    print("✅ Bot tayyor! /start yuboring.")
    app.run_polling()


if __name__ == "__main__":
    main()
