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
    ChatMemberHandler,
    filters,
)

import config
from config import TELEGRAM_TOKEN
import wallet
import payment_providers
from handlers import (
    menu, universal_chat, course_work, translate as translate_handler, images_to_pdf,
    edit_pdf, guide, inline_query, developer, pptx_gen, essay, quiz, solve, summarize,
    grammar, citation, my_files, reminders, voice, wallet_ui,
)
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

    # ⏰ Bot ishga tushganda (har qayta deployda ham) storage'da saqlangan
    # BARCHA eslatmalarni qayta rejalashtiramiz — shu orqali eslatmalar
    # deploy/restart paytida yo'qolmaydi (handlers/reminders.py'dagi izohga qarang).
    try:
        reminders.reschedule_all(application)
    except Exception as e:
        logger.error(f"⏰ Eslatmalarni qayta rejalashtirishda xato: {type(e).__name__}: {e}", exc_info=True)


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

    def do_POST(self):
        """💳 Kapitalbank (yoki kelajakda boshqa provider) TO'LOV WEBHOOK'i
        shu yerga keladi (PUBLIC_BASE_URL + '/webhook/kapitalbank').

        MUHIM: bu HTTP server ALOHIDA OS thread'da ishlaydi (asyncio event
        loop'dan tashqarida) — shuning uchun wallet.py/payment_providers.py
        FAQAT sinxron (threading.Lock asosidagi) funksiyalardan iborat
        qilib ATAYLAB shunday loyihalangan, shu orqali bu yerdan to'g'ridan
        to'g'ri, xavfsiz chaqirish mumkin.

        Haqiqiy imzo tekshiruvi va JSON maydon nomlari hali Kapitalbank
        rasmiy hujjatiga asoslanmagan (payment_providers.py'dagi TODO'larga
        qarang) — shuning uchun bu endpoint hozircha faqat "sozlanmagan"
        holatda ishlaydi va har doim xavfsiz tarzda rad javobi qaytaradi."""
        if self.path != "/webhook/kapitalbank":
            self.send_response(404)
            self.end_headers()
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length) if length > 0 else b""
            headers = {k: v for k, v in self.headers.items()}

            provider = payment_providers.get_ecommerce_provider()
            if not provider.verify_webhook_signature(headers, raw_body):
                logger.warning("💳 Kapitalbank webhook: imzo tekshiruvidan o'tmadi yoki sozlanmagan — rad etildi.")
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b'{"status":"invalid_signature"}')
                return

            event = provider.parse_webhook(raw_body)
            if not event.ok:
                logger.error(f"💳 Kapitalbank webhook: parse xato — {event.error}")
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"status":"parse_error"}')
                return

            if event.status == "success":
                if event.provider_transaction_id:
                    try:
                        wallet.register_provider_transaction(event.payment_id, provider.name, event.provider_transaction_id)
                    except wallet.DuplicateTransactionError as e:
                        logger.warning(f"💳 Kapitalbank webhook: DUPLICATE tranzaksiya — {e}")
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b'{"status":"already_used"}')
                        return
                wallet.confirm_payment(event.payment_id, actor_id="kapitalbank_webhook", source="webhook")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        except Exception as e:
            logger.error(f"💳 Kapitalbank webhook qayta ishlashda xato: {type(e).__name__}: {e}", exc_info=True)
            self.send_response(500)
            self.end_headers()


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
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("course_work")(course_work.entry), pattern="^menu:course_work$")],
        states={
            course_work.CW_PAGES: [MessageHandler(filters.TEXT & ~filters.COMMAND, course_work.receive_pages)],
            course_work.CW_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, course_work.receive_topic_and_generate)],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="course_work_conv",
    )


def build_translate_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("translate")(translate_handler.entry), pattern="^menu:translate$")],
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
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("images_pdf")(images_to_pdf.entry), pattern="^menu:images_pdf$")],
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
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("edit_pdf")(edit_pdf.entry), pattern="^menu:edit_pdf$")],
        states={
            edit_pdf.EP_WAIT_PDF: [MessageHandler(filters.Document.PDF, edit_pdf.receive_pdf)],
            edit_pdf.EP_WAIT_INSTRUCTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_pdf.receive_instruction)],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="edit_pdf_conv",
    )


def build_guide_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("guide")(guide.entry), pattern="^menu:guide$")],
        states={
            guide.GD_COLLECTING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, guide.receive_questions),
                CallbackQueryHandler(guide.finish, pattern="^guide:finish$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="guide_conv",
    )


def build_pptx_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("pptx")(pptx_gen.entry), pattern="^menu:pptx$")],
        states={
            pptx_gen.PX_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, pptx_gen.receive_topic)],
            pptx_gen.PX_COUNT: [CallbackQueryHandler(pptx_gen.receive_count, pattern="^pptx:count:")],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="pptx_conv",
    )


def build_essay_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("essay")(essay.entry), pattern="^menu:essay$")],
        states={
            essay.ES_TYPE: [CallbackQueryHandler(essay.receive_type, pattern="^essay:type:")],
            essay.ES_PAGES: [MessageHandler(filters.TEXT & ~filters.COMMAND, essay.receive_pages)],
            essay.ES_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, essay.receive_topic_and_generate)],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="essay_conv",
    )


def build_quiz_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("quiz")(quiz.entry), pattern="^menu:quiz$")],
        states={
            quiz.QZ_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz.receive_topic)],
            quiz.QZ_COUNT: [CallbackQueryHandler(quiz.receive_count, pattern="^quiz:count:")],
            quiz.QZ_ACTIVE: [
                CallbackQueryHandler(quiz.receive_answer, pattern="^quiz:ans:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="quiz_conv",
    )


def build_solve_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("solve")(solve.entry), pattern="^menu:solve$")],
        states={
            solve.SV_WAIT: [
                MessageHandler(filters.PHOTO, solve.receive_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, solve.receive_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="solve_conv",
    )


def build_summarize_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("summarize")(summarize.entry), pattern="^menu:summarize$")],
        states={
            summarize.SM_WAIT: [
                MessageHandler((filters.TEXT | filters.Document.PDF) & ~filters.COMMAND, summarize.receive_content),
            ],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="summarize_conv",
    )


def build_grammar_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("grammar")(grammar.entry), pattern="^menu:grammar$")],
        states={
            grammar.GR_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, grammar.receive_text)],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="grammar_conv",
    )


def build_citation_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wallet_ui.require_payment("citation")(citation.entry), pattern="^menu:citation$")],
        states={
            citation.CT_FORMAT: [CallbackQueryHandler(citation.receive_format, pattern="^cite:fmt:")],
            citation.CT_TYPE: [CallbackQueryHandler(citation.receive_type, pattern="^cite:type:")],
            citation.CT_DETAILS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, citation.receive_details),
                CallbackQueryHandler(citation.add_more, pattern="^cite:more$"),
                CallbackQueryHandler(citation.finish, pattern="^cite:finish$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="citation_conv",
    )


def build_reminders_conv():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(reminders.entry, pattern="^menu:remind$"),
            CallbackQueryHandler(reminders.back_to_menu, pattern="^remind:menu$"),
            CallbackQueryHandler(reminders.new_reminder, pattern="^remind:new$"),
        ],
        states={
            reminders.RM_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminders.receive_text)],
            reminders.RM_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminders.receive_time)],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="reminders_conv",
        allow_reentry=True,
    )


def build_wallet_topup_conv():
    """➕ Balansni to'ldirish — summa tanlash -> to'lov usuli -> (bank/manual
    uchun) chek qabul qilish. E-commerce usulida chek kutish shart emas —
    to'lov havolasi ko'rsatilgach conversation darhol tugaydi (haqiqiy
    to'lov Kapitalbank'ning o'z sahifasida, webhook orqali tasdiqlanadi)."""
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wallet_ui.entry_topup, pattern="^menu:wallet_topup$")],
        states={
            wallet_ui.WT_AMOUNT: [CallbackQueryHandler(wallet_ui.amount_chosen, pattern="^wallet:amt:")],
            wallet_ui.WT_CUSTOM_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_ui.custom_amount_received)],
            wallet_ui.WT_METHOD: [CallbackQueryHandler(wallet_ui.method_chosen, pattern="^wallet:method:")],
            wallet_ui.WT_RECEIPT: [MessageHandler(filters.PHOTO | filters.Document.ALL, wallet_ui.receive_receipt)],
        },
        fallbacks=[CommandHandler("cancel", menu.cancel_cmd)],
        name="wallet_topup_conv",
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
        # allow_reentry=True: agar admin /developer buyrug'ini istalgan
        # bosqichda (masalan "yangi kalit yuboring" kutib turganida) qayta
        # yuborsa, conversation qotib qolmasdan DARHOL qayta boshdan ochiladi.
        allow_reentry=True,
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
    # Bot biror guruhga QO'SHILGANDA avtomatik xush kelibsiz xabari va
    # standart FAOL holatni bildirish uchun (guruh holati doimiy
    # saqlanadi — batafsili storage.py > "Guruhlarda Universal chat holati").
    app.add_handler(ChatMemberHandler(menu.my_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))

    # Har bir funksiya uchun alohida conversation (bosqichma-bosqich so'rov-javob)
    app.add_handler(build_course_work_conv())
    app.add_handler(build_essay_conv())
    app.add_handler(build_translate_conv())
    app.add_handler(build_pptx_conv())
    app.add_handler(build_quiz_conv())
    app.add_handler(build_solve_conv())
    app.add_handler(build_summarize_conv())
    app.add_handler(build_grammar_conv())
    app.add_handler(build_citation_conv())
    app.add_handler(build_images_pdf_conv())
    app.add_handler(build_edit_pdf_conv())
    app.add_handler(build_guide_conv())
    app.add_handler(build_reminders_conv())
    app.add_handler(build_wallet_topup_conv())
    app.add_handler(build_developer_conv())

    app.add_handler(CallbackQueryHandler(menu.universal_selected, pattern="^menu:universal$"))
    app.add_handler(CallbackQueryHandler(menu.back_to_menu, pattern="^menu:back$"))

    # 💰 Balansim / 🧾 To'lovlar tarixi — conversation emas, oddiy callback
    app.add_handler(CallbackQueryHandler(wallet_ui.entry_balance, pattern="^menu:wallet_balance$"))
    app.add_handler(CallbackQueryHandler(wallet_ui.entry_history, pattern="^menu:wallet_history$"))

    # 🗂 Mening fayllarim — conversation emas, oddiy callback (ro'yxat + qayta yuborish)
    app.add_handler(CallbackQueryHandler(my_files.entry, pattern="^menu:myfiles$"))
    app.add_handler(CallbackQueryHandler(my_files.open_file, pattern="^myfiles:open:"))

    # 📋 Test natijasini PDF qilish — test tugagandan KEYIN (conversation allaqachon
    # tugagan holatda) bosiladigan tugma, shuning uchun ALOHIDA (conv tashqarisida) handler
    app.add_handler(CallbackQueryHandler(quiz.export_pdf, pattern="^quiz:pdf$"))

    # ⏰ Eslatmalar ro'yxati/o'chirish — reminders_conv tashqarisida ham ishlashi kerak
    app.add_handler(CallbackQueryHandler(reminders.list_reminders, pattern="^remind:list$"))
    app.add_handler(CallbackQueryHandler(reminders.delete_reminder, pattern="^remind:del:"))

    # 🎙 Ovozli xabar — istalgan paytda (hech qanday menyu tanlanmagan bo'lsa ham)
    app.add_handler(MessageHandler(filters.VOICE, voice.handle_voice))

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
