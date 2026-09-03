"""
🤖 Talaba AI — ko'p funksiyali Telegram bot.
Har bir funksiya (Universal chat, Kurs ishi, Tarjima, Suratlarni PDF qilish,
PDF tahrirlash, Qo'llanma tayyorlash) o'ziga alohida sozlanadigan AI (Gemini /
Groq / bepul zaxira) bilan ishlaydi. Render Free Web Service uchun HTTP
health-check server ham ishga tushiriladi.
"""

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread, Timer

import telegram.error
from telegram import BotCommand, BotCommandScopeDefault, BotCommandScopeAllGroupChats, BotCommandScopeChat, InlineQueryResultPhoto
import business_storage
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    InlineQueryHandler,
    ChosenInlineResultHandler,
    ChatMemberHandler,
    BusinessConnectionHandler,
    filters,
)

import config
from config import TELEGRAM_TOKEN, PUBLIC_BASE_URL
import pending_input
import wallet
import payment_providers
import webapp_security
import inline_media
from handlers import (
    menu, universal_chat, course_work, translate as translate_handler, images_to_pdf,
    edit_pdf, guide, inline_query, developer, pptx_gen, essay, quiz, solve, summarize,
    grammar, citation, my_files, reminders, voice, wallet_ui, tabrik, rasim,
    vid, qoshiq, mention_dispatch, pro_tabrik, my_cabinet,
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
    BotCommand("tabrik", "🎁 Chiroyli tabrik xabari yuborish"),
    BotCommand("rasim", "🎨 Mini App orqali rasm chizish"),
    BotCommand("vid", "🎬 Video yuklab olish (havola bilan)"),
    # Eslatma: Telegram "/" buyruqlar ro'yxatida apostrofli nomlarga ruxsat
    # bermaydi, shuning uchun shu yerda ASCII "qoshiq" ko'rsatiladi — lekin
    # "/qo'shiq" (apostrof bilan) ham xuddi shunday ishlaydi (handlers/qoshiq.py).
    BotCommand("qoshiq", "🎵 Qo'shiq qidirish va yuborish"),
    BotCommand("pro", "💎 Shaxsiy rasmli tabriknoma (Pro)"),
    BotCommand("my", "👤 Mening kabinetim"),
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

    # 🧭 "@Bot /vid ..." kabi mention orqali kelgan buyruqlarni aniqlash
    # uchun HAQIQIY (Telegram tomonidan tasdiqlangan) bot username kerak —
    # config.BOT_USERNAME_FALLBACK faqat shu so'rov muvaffaqiyatsiz bo'lsa
    # ishlatiladi.
    try:
        me = await application.bot.get_me()
        if me and me.username:
            mention_dispatch.set_bot_username(me.username)
    except Exception as e:
        logger.warning(f"🧭 Bot username'ni get_me() orqali olishda xato (fallback ishlatiladi): {e}")

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


async def _on_business_connection(update, context):
    """📇 `business_connection` update — bot Telegram Business orqali
    ulanganda, uzilganda yoki huquqlari o'zgarganda keladi (uch holatni
    ham Telegram xuddi shu update turi orqali, `is_enabled` maydoni bilan
    farqlab yuboradi). Ma'lumot restart-safe saqlanadi (business_storage.py).
    """
    conn = update.business_connection
    if conn is None:
        return
    business_storage.save_connection(conn)
    if not conn.is_enabled:
        logger.info(f"📇 BUSINESS_CONNECTION_DISABLED user_id={conn.user.id} connection_id={conn.id}")


async def _clear_pending_on_other_command(update, context):
    """"/qo'shiq" yoki "/vid" argumentsiz yuborilib, bot navbatdagi
    xabarni kutayotgan bo'lsa-yu, foydalanuvchi shu o'rniga BOSHQA
    buyruq ("/start", "/vid", "/help" va h.k.) yuborsa — eski kutish
    holati shu yerda bekor qilinadi (guruh=-1: har doim eng BIRINCHI
    ishlaydi, keyin oddiy add_handler(group=0) navbati bilan tegishli
    buyruq handleri — masalan CommandHandler("vid", ...) — normal davom
    etadi). Agar aynan "/qoshiq" yoki "/vid" qayta yuborilgan bo'lsa, bu
    shunchaki eski holatni tozalaydi — tegishli handler o'zi kerak
    bo'lsa YANGI kutish holatini qayta o'rnatadi, hech qanday
    ziddiyat yo'q."""
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    pending_input.clear_pending(update.effective_chat.id, update.effective_user.id)


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

# ============================================================
# 💎 Inline "/pro" boshlang'ich audio xabari uchun (pro_tabrik_business.py)
# ============================================================
# InlineQueryResultAudio `audio_url` talab qiladi (fayl baytlarini
# to'g'ridan-to'g'ri biriktirib bo'lmaydi) — shuning uchun yuqoridagi
# placeholder PDF bilan BIR XIL naqsh: audio faylni shu health-server
# orqali ochiq (https) manzildan xizmat qilamiz. Fayl topilmasa (hali
# joylanmagan bo'lsa), bo'sh bayt bilan qoladi — HealthHandler.do_GET
# buni graceful 404'ga aylantiradi, handlers/inline_query.py esa
# pro_tabrik_business.audio_available()ni oldindan tekshirib, audio
# yo'q bo'lsa oddiy matn+tugma natijasiga qaytadi (7-band: audio
# ishlamasa ham oqim to'xtamasin).
_PRO_AUDIO_BYTES: bytes = b""
_PRO_AUDIO_PATH = "/pro_audio.mp3"


def _load_pro_audio_bytes() -> bytes:
    try:
        import pro_tabrik_business
        if os.path.exists(pro_tabrik_business.PRO_AUDIO_PATH):
            with open(pro_tabrik_business.PRO_AUDIO_PATH, "rb") as f:
                return f.read()
        logger.info(f"💎 /pro audio fayli topilmadi ({pro_tabrik_business.PRO_AUDIO_PATH}) — inline /pro audiosiz ishlaydi.")
        return b""
    except Exception as e:
        logger.error(f"💎 /pro audio faylini o'qishda xato: {type(e).__name__}: {e}", exc_info=True)
        return b""


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


# ============================================================
# 🎵 Inline "/qo'shiq" natijalari uchun MUSIQA ikonkasi (thumbnail_url)
# ============================================================
# Inline natijalar `InlineQueryResultDocument` turida bo'lishi SHART
# (qarang: handlers/inline_query.py boshidagi izoh — bu keyinroq audio
# bilan almashtirilishi uchun texnik talab), shu sabab Telegram natija
# ro'yxatida ularni generik "hujjat" belgisi bilan ko'rsatadi (talab #2:
# "oddiy fayl ko'rinishidagi 📄 belgisini ishlatma"). Buni to'liq
# tuzatib bo'lmaydi (Document turi shu tarzda ko'rinadi), lekin
# `thumbnail_url` orqali o'zimizning mayda musiqa ikonkamizni
# ko'rsatib, "chiroyli musiqa menyusi" taassurotiga yaqinlashtiramiz.
_MUSIC_ICON_PNG_BYTES: bytes = b""
_MUSIC_ICON_PATH = "/music_icon.png"


def _build_music_icon_png() -> bytes:
    try:
        from PIL import Image, ImageDraw
        size = 128
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Doira fon (Telegram musiqa pleyerlariga o'xshash binafsha rang).
        draw.ellipse([2, 2, size - 2, size - 2], fill=(108, 92, 231, 255))
        # Oddiy "eighth note" (bitta nota) shakli — ikkita doira + tayoqcha.
        draw.ellipse([34, 78, 58, 98], fill=(255, 255, 255, 255))
        draw.ellipse([64, 66, 88, 86], fill=(255, 255, 255, 255))
        draw.rectangle([56, 30, 62, 88], fill=(255, 255, 255, 255))
        draw.rectangle([86, 22, 92, 76], fill=(255, 255, 255, 255))
        draw.line([56, 30, 92, 22], fill=(255, 255, 255, 255), width=6)
        import io as _io
        out = _io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception as e:
        logger.warning(f"🎵 Musiqa ikonkasini yaratib bo'lmadi (ahamiyatsiz — thumbnail'siz davom etadi): {type(e).__name__}: {e}")
        return b""


# ============================================================
# 🎨 /rasim MINI APP — statik fayllarni xizmat qilish + rasm yuklash
# ============================================================
# MUHIM: bu HTTP server ALOHIDA OS thread'da ishlaydi (yuqoridagi
# Kapitalbank webhook izohiga qarang), lekin rasmni Telegram'ga
# YUBORISH uchun asyncio Bot obyekti (asosiy event loop) kerak — shuning
# uchun `_MAIN_LOOP`/`_BOT_INSTANCE` global o'zgaruvchilarga `main()`
# ichida `app`/`loop` tayyor bo'lgach yoziladi, bu yerdan esa
# `asyncio.run_coroutine_threadsafe()` orqali xavfsiz chaqiriladi.
_MAIN_LOOP = None
_BOT_INSTANCE = None

_WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp", "rasim")
_WEBAPP_STATIC_FILES = {
    "/miniapp/rasim/": "index.html",
    "/miniapp/rasim/index.html": "index.html",
    "/miniapp/rasim/style.css": "style.css",
    "/miniapp/rasim/app.js": "app.js",
}
_WEBAPP_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB — Mini App rasm yuklash chegarasi
_WEBAPP_UPLOAD_TMP_DIR = os.path.join(tempfile.gettempdir(), "rasim_uploads")

# 🔍 Inline rejim (/rasim do'st bilan chatda) uchun: Telegram'ning
# `answer_web_app_query`/`InlineQueryResultPhoto` mexanizmi rasmni FAYL
# sifatida emas, OCHIQ HTTPS URL orqali talab qiladi — shuning uchun
# yuklangan PNG shu papkaga saqlanadi va pastdagi GET route orqali qisqa
# muddat (bir necha daqiqa, Telegram uni yetib olib ulguradi) ochiq
# turadi, so'ng threading.Timer bilan avtomatik o'chiriladi.
_WEBAPP_GENERATED_DIR = os.path.join(tempfile.gettempdir(), "rasim_generated")
_WEBAPP_GENERATED_URL_PREFIX = "/miniapp/rasim/generated/"
_WEBAPP_GENERATED_TTL_SEC = 10 * 60  # 10 daqiqa


def _serve_webapp_static(handler: "HealthHandler", path: str) -> bool:
    """`/miniapp/rasim/...` ostidagi statik fayllarni xizmat qiladi.
    Mos fayl topilmasa False qaytaradi (chaqiruvchi 404 qaytarsin)."""
    filename = _WEBAPP_STATIC_FILES.get(path)
    if not filename:
        return False
    file_path = os.path.join(_WEBAPP_DIR, filename)
    try:
        with open(file_path, "rb") as f:
            body = f.read()
    except OSError:
        return False
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text") or filename.endswith(".js") else ""))
    handler.send_header("Content-Length", str(len(body)))
    # Mini App fayllari tez-tez o'zgarmaydi, lekin ishlab chiqish paytida
    # eskirgan versiyani ko'rsatib qolmasligi uchun keshni cheklaymiz.
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(body)
    return True


def _decode_data_url_image(data_url: str) -> bytes | None:
    """`data:image/png;base64,....` ko'rinishidagi satrni dekodlaydi va
    HAQIQIY PNG ekanini (magic bytes orqali) tekshiradi — front-end
    yuborgan Content-Type'ga (spoof qilinishi mumkin) ishonilmaydi."""
    try:
        header, b64data = data_url.split(",", 1)
    except (ValueError, AttributeError):
        return None
    if "image/png" not in header and "image/jpeg" not in header:
        return None
    try:
        raw = base64.b64decode(b64data, validate=True)
    except Exception:
        return None
    if len(raw) > _WEBAPP_MAX_UPLOAD_BYTES:
        return None
    is_png = raw.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpeg = raw.startswith(b"\xff\xd8\xff")
    if not (is_png or is_jpeg):
        return None
    return raw


def _handle_rasim_upload(handler: "HealthHandler") -> None:
    """POST /miniapp/rasim/upload — Mini App'dan chizilgan rasmni qabul
    qiladi, Telegram initData'ni tasdiqlaydi va rasmni TO'G'RI joyga
    yuboradi. `rid`ning turi (oddiy/"in_" prefiksli) qaysi yo'l
    tanlanishini aniqlaydi:

    - Oddiy rid (chatga bog'langan, /rasim guruh/shaxsiy chatda
      chaqirilganda) -> rasm to'g'ridan-to'g'ri fayl sifatida o'sha
      chatga yuboriladi (`send_photo`).
    - "in_" prefiksli rid (do'st bilan chatda "@Bot /rasim" — inline
      rejim) -> chat_id UMUMAN yo'q, shuning uchun rasm avval ochiq URL
      orqali xizmat qilinadi, so'ng Telegram'ning `answer_web_app_query`
      mexanizmi orqali (initData ichidagi `query_id`) TO'G'RI joyga
      o'zi yetkaziladi.

    Har bir qadam mustaqil ravishda rad etilishi mumkin — hech qanday
    bosqichda boshqa foydalanuvchining fayli/chatiga kirish imkoni yo'q."""
    def _json_error(status: int, message: str) -> None:
        body = json.dumps({"ok": False, "error": message}).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _json_ok() -> None:
        body = json.dumps({"ok": True}).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    try:
        length = int(handler.headers.get("Content-Length", 0))
    except ValueError:
        length = 0
    if length <= 0 or length > _WEBAPP_MAX_UPLOAD_BYTES * 2:  # base64 ~1.34x kattaroq
        _json_error(413, "Fayl juda katta.")
        return

    try:
        raw_body = handler.rfile.read(length)
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        _json_error(400, "Noto'g'ri so'rov.")
        return

    rid = payload.get("rid")
    init_data = payload.get("init_data")
    image_data_url = payload.get("image")
    if not rid or not init_data or not image_data_url:
        _json_error(400, "To'liq bo'lmagan so'rov.")
        return

    verified_user = webapp_security.verify_telegram_init_data(init_data, TELEGRAM_TOKEN)
    if not verified_user:
        logger.warning("🎨 /rasim upload: initData tasdiqlanmadi (imzo mos kelmadi yoki eskirgan).")
        _json_error(403, "Tasdiqlashda xatolik. Mini App'ni qayta oching.")
        return

    image_bytes = _decode_data_url_image(image_data_url)
    if image_bytes is None:
        _json_error(400, "Rasm formati noto'g'ri yoki juda katta.")
        return

    if _MAIN_LOOP is None or _BOT_INSTANCE is None:
        logger.error("🎨 /rasim upload: bot hali to'liq ishga tushmagan.")
        _json_error(503, "Server hali tayyor emas, birozdan so'ng urinib ko'ring.")
        return

    if rid.startswith("in_"):
        _handle_rasim_upload_inline(handler, rid, verified_user, image_bytes, _json_error, _json_ok)
    else:
        _handle_rasim_upload_classic(handler, rid, verified_user, image_bytes, _json_error, _json_ok)


def _handle_rasim_upload_classic(handler, rid, verified_user, image_bytes, _json_error, _json_ok) -> None:
    chat_id = webapp_security.consume_request(rid, verified_user_id=verified_user["id"])
    if chat_id is None:
        logger.warning(f"🎨 /rasim upload: rid yaroqsiz/eskirgan/ishlatilgan (user_id={verified_user['id']}).")
        _json_error(410, "So'rov muddati o'tgan. /rasim buyrug'ini qayta yuboring.")
        return

    os.makedirs(_WEBAPP_UPLOAD_TMP_DIR, exist_ok=True)
    tmp_path = os.path.join(_WEBAPP_UPLOAD_TMP_DIR, f"{uuid.uuid4().hex}.png")
    try:
        with open(tmp_path, "wb") as f:
            f.write(image_bytes)

        async def _send():
            with open(tmp_path, "rb") as fh:
                await _BOT_INSTANCE.send_photo(chat_id, photo=fh, caption="🎨 Mini App orqali chizilgan rasm.")

        future = asyncio.run_coroutine_threadsafe(_send(), _MAIN_LOOP)
        future.result(timeout=30)  # HTTP javobini foydalanuvchi kutayotgani uchun sinxron kutamiz
    except Exception as e:
        logger.error(f"🎨 /rasim rasmni yuborishda xato (chat_id={chat_id}): {type(e).__name__}: {e}", exc_info=True)
        _json_error(502, "Rasmni yuborishda xatolik yuz berdi.")
        return
    finally:
        # 🧹 Vaqtinchalik fayl albatta o'chiriladi — muvaffaqiyatli
        # yuborilgan yoki yubormaganida ham diskni ifloslantirmasligi kerak.
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    logger.info(f"🎨 Mini App rasmi yuborildi: chat_id={chat_id}, user_id={verified_user['id']}.")
    _json_ok()


def _handle_rasim_upload_inline(handler, rid, verified_user, image_bytes, _json_error, _json_ok) -> None:
    if not webapp_security.consume_inline_request(rid, verified_user_id=verified_user["id"]):
        logger.warning(f"🎨 /rasim inline upload: rid yaroqsiz/eskirgan/ishlatilgan (user_id={verified_user['id']}).")
        _json_error(410, "So'rov muddati o'tgan. Mini App'ni qayta oching.")
        return

    query_id = verified_user.get("_query_id")
    if not query_id:
        logger.warning("🎨 /rasim inline upload: initData'da query_id yo'q (inline sessiya emas?).")
        _json_error(400, "Bu so'rov inline sessiyaga tegishli emas.")
        return

    if not PUBLIC_BASE_URL:
        _json_error(503, "Server hozircha bu funksiyani qo'llab-quvvatlamaydi.")
        return

    os.makedirs(_WEBAPP_GENERATED_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    file_path = os.path.join(_WEBAPP_GENERATED_DIR, filename)
    try:
        with open(file_path, "wb") as f:
            f.write(image_bytes)
    except OSError as e:
        logger.error(f"🎨 /rasim inline: faylni saqlab bo'lmadi: {e}")
        _json_error(500, "Rasmni saqlashda xatolik yuz berdi.")
        return

    photo_url = f"{PUBLIC_BASE_URL}{_WEBAPP_GENERATED_URL_PREFIX}{filename}"

    async def _answer():
        result = InlineQueryResultPhoto(
            id=uuid.uuid4().hex,
            photo_url=photo_url,
            thumbnail_url=photo_url,
            caption="🎨 Mini App orqali chizilgan rasm.",
        )
        await _BOT_INSTANCE.answer_web_app_query(query_id, result)

    try:
        future = asyncio.run_coroutine_threadsafe(_answer(), _MAIN_LOOP)
        future.result(timeout=30)
    except Exception as e:
        logger.error(f"🎨 /rasim inline: answer_web_app_query xato (user_id={verified_user['id']}): {type(e).__name__}: {e}", exc_info=True)
        try:
            os.remove(file_path)
        except OSError:
            pass
        _json_error(502, "Rasmni yuborishda xatolik yuz berdi.")
        return

    # 🧹 Telegram fotoni yuklab olishi uchun bir necha daqiqa ochiq
    # turishi kerak — shuning uchun DARHOL emas, KECHIKTIRIB o'chiramiz.
    def _cleanup():
        try:
            os.remove(file_path)
        except OSError:
            pass
    Timer(_WEBAPP_GENERATED_TTL_SEC, _cleanup).start()

    logger.info(f"🎨 Mini App rasmi (inline) yuborildi: user_id={verified_user['id']}.")
    _json_ok()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == _PLACEHOLDER_PDF_PATH and _PLACEHOLDER_PDF_BYTES:
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(_PLACEHOLDER_PDF_BYTES)))
            self.end_headers()
            self.wfile.write(_PLACEHOLDER_PDF_BYTES)
            return

        if self.path == _MUSIC_ICON_PATH and _MUSIC_ICON_PNG_BYTES:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(_MUSIC_ICON_PNG_BYTES)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(_MUSIC_ICON_PNG_BYTES)
            return

        if self.path == _PRO_AUDIO_PATH and _PRO_AUDIO_BYTES:
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(_PRO_AUDIO_BYTES)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(_PRO_AUDIO_BYTES)
            return

        if self.path.startswith(inline_media.URL_PREFIX):
            self._serve_inline_media()
            return

        if self.path.startswith(_WEBAPP_GENERATED_URL_PREFIX):
            self._serve_generated_image()
            return

        if self.path.startswith("/miniapp/rasim"):
            from urllib.parse import urlsplit
            path = urlsplit(self.path).path
            if path == "/miniapp/rasim":
                path = "/miniapp/rasim/"
            if _serve_webapp_static(self, path):
                return
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Talaba AI bot ishlamoqda!")

    def _serve_inline_media(self) -> None:
        """GET /inline-media/<token> — /vid va /qoshiq uchun tayyor faylni
        Telegram serveriga vaqtincha ochiq URL orqali beradi."""
        token = self.path[len(inline_media.URL_PREFIX):].split("?", 1)[0]
        resolved = inline_media.resolve_path(token)
        if not resolved:
            logger.warning("🔴 Inline media GET 404/expired: token=%s", token)
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Inline media not found or expired")
            return
        file_path, content_type = resolved
        try:
            size = os.path.getsize(file_path)
            with open(file_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "public, max-age=300")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(body)
            logger.info("📤 Inline media GET 200: token=%s type=%s size=%d", token, content_type, size)
        except OSError as e:
            logger.error("🔴 Inline media file serve ERROR: token=%s type=%s detail=%s", token, type(e).__name__, e, exc_info=True)
            self.send_response(500)
            self.end_headers()

    def _serve_generated_image(self) -> None:
        """GET /miniapp/rasim/generated/<hex>.png — inline `/rasim` orqali
        chizilgan, hali Telegram tomonidan yuklab olinmagan rasmni bir
        martalik xizmat qiladi (qarang: _handle_rasim_upload_inline).
        Fayl nomi QAT'IY tekshiriladi (faqat 32 xonali hex + \".png\") —
        path traversal yoki boshqa papkalarga kirishning oldini olish uchun."""
        filename = self.path[len(_WEBAPP_GENERATED_URL_PREFIX):]
        if not re.fullmatch(r"[0-9a-f]{32}\.png", filename):
            self.send_response(404)
            self.end_headers()
            return
        file_path = os.path.join(_WEBAPP_GENERATED_DIR, filename)
        try:
            with open(file_path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        return

    def do_POST(self):
        """💳 Kapitalbank to'lov webhook'i VA 🎨 /rasim Mini App rasm
        yuklash so'rovi shu yerga keladi."""
        if self.path == "/miniapp/rasim/upload":
            _handle_rasim_upload(self)
            return

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
    global _PLACEHOLDER_PDF_BYTES, _MUSIC_ICON_PNG_BYTES, _PRO_AUDIO_BYTES
    _PLACEHOLDER_PDF_BYTES = _build_placeholder_pdf()
    _MUSIC_ICON_PNG_BYTES = _build_music_icon_png()
    _PRO_AUDIO_BYTES = _load_pro_audio_bytes()
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
            developer.DEV_WAIT_AUDIO: [
                MessageHandler(filters.AUDIO | filters.Document.ALL, developer.on_audio),
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

    # 🎨 /rasim Mini App'ining upload endpoint'i (alohida HTTP thread'da)
    # rasmni Telegram'ga yuborish uchun shu bot instansi/asosiy event
    # loop'ga muhtoj — yuqoridagi HealthHandler.do_POST'ga qarang.
    global _MAIN_LOOP, _BOT_INSTANCE
    _MAIN_LOOP = loop
    _BOT_INSTANCE = app.bot

    # ⏳ "/qoshiq"/"/vid" ikki bosqichli kiritishning kutish holatini
    # BOSHQA istalgan buyruq kelganda bekor qiladi — group=-1 bo'lgani
    # uchun BARCHA quyidagi (group=0) buyruq handlerlaridan OLDIN, lekin
    # ularni to'xtatmasdan (block qilmasdan) ishlaydi.
    app.add_handler(MessageHandler(filters.COMMAND, _clear_pending_on_other_command), group=-1)

    app.add_handler(CommandHandler("start", menu.start_cmd))
    app.add_handler(CommandHandler("cancel", menu.cancel_cmd))
    app.add_handler(CommandHandler("yoqish", menu.group_enable_cmd))
    app.add_handler(CommandHandler("ochirish", menu.group_disable_cmd))
    # 🎁 /tabrik — private va group ikkalasida ham (filtersiz CommandHandler
    # standart holatda BARCHA chat turlarida ishlaydi, shu jumladan
    # "/tabrik@Student_ai_uz_bot ..." formatida ham).
    app.add_handler(CommandHandler("tabrik", tabrik.tabrik_cmd))
    app.add_handler(CallbackQueryHandler(tabrik.tabrik_claim_callback, pattern="^tabrik:claim:"))
    # 🔍 Xuddi shu, lekin do'st bilan chatda inline rejimda kelgan /tabrik
    # uchun (inline_message_id bilan ishlaydi, oddiy Message emas).
    app.add_handler(CallbackQueryHandler(inline_query.inline_tabrik_claim_callback, pattern="^itabrik:claim:"))
    # 📇 Telegram Business — foydalanuvchi botni Settings → Telegram
    # Business → Chatbots orqali ulaganda/uzganda/sozlamasini
    # o'zgartirganda keladigan update. /tabrik'ning Business oqimi
    # (tabrik_business.py) shu saqlangan ma'lumotdan foydalanadi.
    app.add_handler(BusinessConnectionHandler(_on_business_connection))
    # 🎨 /rasim — Telegram Mini App orqali rasm chizish.
    app.add_handler(CommandHandler("rasim", rasim.rasim_cmd))
    # 🎬 /vid — video yuklab olish (ASCII buyruq, Privacy Mode'dan qat'i
    # nazar barcha chat turlarida ishlaydi).
    app.add_handler(CommandHandler("vid", vid.vid_cmd))
    # 🎵 /qoshiq — ASCII alias ("/qo'shiq" apostrof bilan HAM ishlaydi,
    # lekin apostrof borligi uchun Telegram uni haqiqiy buyruq deb
    # belgilamaydi — shuning uchun u universal_chat.handle_message orqali,
    # handlers/mention_dispatch.py yordamida qayta ishlanadi, qarang:
    # handlers/qoshiq.py boshidagi izoh).
    app.add_handler(CommandHandler("qoshiq", qoshiq.qoshiq_cmd))
    app.add_handler(CallbackQueryHandler(qoshiq.qoshiq_choice_callback, pattern="^song:"))
    app.add_handler(CallbackQueryHandler(qoshiq.qoshiq_page_callback, pattern="^songpage:"))

    # 💎 /pro — /tabrik'ning shaxsiy rasmli versiyasi.
    app.add_handler(CommandHandler("pro", pro_tabrik.pro_cmd))
    app.add_handler(CallbackQueryHandler(pro_tabrik.pro_claim_callback, pattern="^protabrik:claim:"))
    app.add_handler(CallbackQueryHandler(inline_query.inline_pro_claim_callback, pattern="^iprotabrik:claim:"))

    # 👤 /my — "Mening kabinetim" (rasim yuklash, Pro obuna).
    app.add_handler(CommandHandler("my", my_cabinet.my_cabinet_cmd))
    app.add_handler(CallbackQueryHandler(my_cabinet.my_cabinet_callback, pattern="^mycab:"))
    # 💎 Admin: Pro obunani tasdiqlash/rad etish — DM tugmasi VA
    # /developer > 💎 Pro obunalar bo'limi IKKALASI HAM shu bitta
    # handlerni chaqiradi (callback_data bir xil).
    app.add_handler(CallbackQueryHandler(my_cabinet.prosub_decision_callback, pattern="^prosub:"))
    # 🖼 Kabinetga rasm yuklash — ALOHIDA guruhda (group=1), shunda
    # boshqa (masalan "Suratlarni PDF qilish") conversation'lardagi rasm
    # handlerlariga (asosiy guruh — group=0) XALAQIT bermaydi; ikkalasi
    # mustaqil ishlaydi, on_kabinet_photo esa faqat foydalanuvchi "🖼 Rasim
    # yuklash"ni bosgan bo'lsagina (user_data bayrog'i orqali) amal qiladi.
    app.add_handler(MessageHandler(filters.PHOTO, my_cabinet.on_kabinet_photo), group=1)
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
    # drop_pending_updates=True: ishga tushganda (ayniqsa Render'da QAYTA
    # DEPLOY qilingandan keyin) eski/navbatdagi update'larni tashlab
    # yuboradi va getUpdates seansini "toza" boshlaydi — bu ba'zida
    # "Conflict: terminated by other getUpdates request" xatosining oldini
    # olishga yordam beradi, agar u eski jarayon hali TO'LIQ to'xtamagan
    # bir lahzali qisqa muddatli overlapdan kelib chiqqan bo'lsa.
    #
    # MUHIM: agar bu xato DOIMIY ravishda takrorlansa, sabab kodda EMAS —
    # bu HAR DOIM bir xil TELEGRAM_TOKEN bilan BIR VAQTNING O'ZIDA IKKITA
    # (yoki ko'proq) bot jarayoni ishlab turgani (masalan: Render'da bir
    # xil token bilan ikkita alohida servis/environment, yoki eski deploy
    # hali to'liq to'xtamagan, yoki kimdir shu tokenni mahalliy kompyuterda
    # ham ishga tushirgan). Bu holatda Render dashboard'ni tekshiring:
    # (1) shu TELEGRAM_TOKEN faqat BITTA servisda ishlatilayotganini,
    # (2) "Instance Count"/scaling 1 taga sozlanganini,
    # (3) eski deploylar/preview environment'lar to'xtatilganini
    # tasdiqlang. Bu fayldagi main()/run_polling() FAQAT BIR MARTA,
    # FAQAT `if __name__ == "__main__":` ostida chaqiriladi — kodda
    # ikkinchi instansiya yaratadigan joy yo'q.
    try:
        app.run_polling(drop_pending_updates=True)
    except telegram.error.Conflict as e:
        logger.critical(
            "🚫 Telegram Conflict: boshqa bir jarayon HAM shu TELEGRAM_TOKEN bilan "
            "getUpdates so'rovini yubormoqda — ikkita bot instansiyasi bir vaqtda "
            "ishlab turibdi. Render dashboard'da duplicate servis/deploy yoki "
            f"boshqa joyda ishlab turgan instansiyani tekshiring. Tafsilot: {e}"
        )
        raise


if __name__ == "__main__":
    main()
