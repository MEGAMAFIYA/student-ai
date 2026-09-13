"""
🎬 KINO KATALOGI
- /kino: admin uchun kino boshqaruvi
- Mavjud kinolar: saqlangan katalogni ko'rsatadi
- Telegram video/document file_id saqlanadi; kino qayta yuklanmaydi.
"""

import logging
import os
import asyncio
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

import config
import storage
import movie_watch
import telegram_mtproto

logger = logging.getLogger(__name__)

KINO_MENU = 0
KINO_WAIT_VIDEO = 1
KINO_WAIT_TITLE = 2


def _is_admin(user_id: int) -> bool:
    return int(user_id) in config.ADMIN_IDS


def _menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Kino yuklash", callback_data="kino:upload")],
        [InlineKeyboardButton("📚 Mavjud kinolar", callback_data="kino:list")],
    ])


async def kino_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not _is_admin(user.id):
        await update.effective_message.reply_text("⛔ Kino yuklash va katalog boshqaruvi faqat admin uchun.")
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "🎬 Kino boshqaruvi\n\nKino yuklash yoki mavjud kinolarni ko'ring:",
        reply_markup=_menu_markup(),
    )
    return KINO_MENU


async def kino_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        await q.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return ConversationHandler.END
    await q.answer()

    if q.data == "kino:upload":
        context.user_data.pop("kino_pending", None)
        await q.edit_message_text(
            "🎬 Kino yuklash\n\n"
            "1️⃣ Video yoki video faylni shu yerga yuboring.\n"
            "2️⃣ Keyin kino nomini so'rayman.\n\n"
            "📡 Media Telegramdan MTProto orqali oqimlanadi; fayl Render/R2 ga saqlanmaydi."
        )
        return KINO_WAIT_VIDEO

    if q.data == "kino:list":
        text, markup = build_catalog_message()
        await q.edit_message_text(text, reply_markup=markup)
        return KINO_MENU

    return KINO_MENU


async def kino_receive_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or not _is_admin(update.effective_user.id):
        return ConversationHandler.END

    msg = update.message
    source_media = msg.video or msg.document
    if not source_media:
        await msg.reply_text("❌ Video yuboring. MP4/MKV kabi video faylni Telegramga fayl yoki video sifatida yuborishingiz mumkin.")
        return KINO_WAIT_VIDEO
    if msg.document and not (msg.document.mime_type or "").lower().startswith("video/"):
        await msg.reply_text("❌ Video fayl yuboring.")
        return KINO_WAIT_VIDEO

    # Kino avval maxsus kanalga ko'chiriladi. Keyingi katalog va MTProto
    # streaming aynan shu kanal xabarini yagona manba sifatida ishlatadi.
    try:
        copied = await context.bot.copy_message(
            chat_id=config.KINO_STORAGE_CHANNEL_ID,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
        )
    except Exception as exc:
        logger.exception("🎬 Kino kanalga ko'chirilmadi")
        await msg.reply_text(
            "❌ Kino saqlash kanaliga yuborilmadi. Bot kanalga admin qilinganini va "
            "KINO_STORAGE_CHANNEL_ID to'g'ri ekanini tekshiring.\n\n"
            f"Xato: {type(exc).__name__}: {exc}"
        )
        return KINO_WAIT_VIDEO

    context.user_data["kino_pending"] = {
        "file_id": source_media.file_id,
        "file_unique_id": source_media.file_unique_id or "",
        "source_chat_id": int(config.KINO_STORAGE_CHANNEL_ID),
        "source_message_id": int(copied.message_id),
        "mime_type": source_media.mime_type or "video/mp4",
        "file_name": source_media.file_name or "",
        "size": source_media.file_size or 0,
    }
    suggested = os.path.splitext(source_media.file_name or "")[0] if source_media.file_name else ""
    await msg.reply_text(
        "✅ Video kino kanaliga saqlandi.\n\n"
        + ("📝 Endi kino nomini yuboring." + (f"\n\nMasalan: `{suggested}`" if suggested else ""))
    )
    return KINO_WAIT_TITLE

async def kino_receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or not _is_admin(update.effective_user.id):
        return ConversationHandler.END
    title = (update.message.text or "").strip()
    if len(title) < 2:
        await update.message.reply_text("❌ Kino nomi juda qisqa. Qaytadan yuboring.")
        return KINO_WAIT_TITLE

    pending = context.user_data.get("kino_pending")
    if not pending:
        await update.message.reply_text("⚠️ Yuklash sessiyasi topilmadi. /kino buyrug'ini qayta bering.")
        return ConversationHandler.END

    # Stage 2: aynan shu Telegram message'ni MTProto orqali resolve qilamiz.
    # Media baytlari olinmaydi — faqat identifierlar DB'ga yoziladi.
    mtproto_meta = {}
    try:
        mtproto_meta = await telegram_mtproto.resolve_message(
            chat_id=int(pending["source_chat_id"]),
            message_id=int(pending["source_message_id"]),
        )
        logger.info(
            "🎬 Kino MTProto metadata olindi: chat=%s message=%s document=%s",
            mtproto_meta.get("telegram_chat_id"),
            mtproto_meta.get("telegram_message_id"),
            mtproto_meta.get("telegram_document_id"),
        )
    except Exception as exc:
        # Hozircha katalogni to'xtatmaymiz: eski file_id saqlanadi.
        # Keyingi migration bosqichida eski yozuvlarni ham MTProto bilan
        # to'ldirish uchun alohida resolver qo'shiladi.
        logger.warning("🎬 MTProto metadata olinmadi: %s: %s", type(exc).__name__, exc)

    movie = storage.add_movie(
        title=title,
        file_id=pending["file_id"],
        mime_type=mtproto_meta.get("mime_type") or pending.get("mime_type") or "video/mp4",
        file_name=mtproto_meta.get("file_name") or pending.get("file_name") or "",
        size=mtproto_meta.get("size") or pending.get("size") or 0,
        uploaded_by=update.effective_user.id,
        telegram_chat_id=mtproto_meta.get("telegram_chat_id"),
        telegram_message_id=mtproto_meta.get("telegram_message_id"),
        telegram_document_id=mtproto_meta.get("telegram_document_id"),
        telegram_access_hash=mtproto_meta.get("telegram_access_hash"),
        telegram_file_reference=mtproto_meta.get("telegram_file_reference", ""),
        telegram_file_unique_id=mtproto_meta.get("telegram_file_unique_id") or pending.get("file_unique_id", ""),
    )

    # Stage 3: kino baytlari R2/Render diskiga ko'chirilmaydi.
    # Telegram MTProto stream asosiy manba; Bot API proxy faqat avtomatik fallback.
    r2_note = "\n📡 Media: Telegram MTProto oqimi (Render disk/R2 ga saqlanmaydi)."

    context.user_data.pop("kino_pending", None)

    await update.message.reply_text(
        f"✅ Kino katalogga qo'shildi!{r2_note}\n\n"
        f"🎬 {movie['title']}\n"
        f"🆔 {movie['id']}\n\n"
        f"Endi `@{config.BOT_USERNAME_FALLBACK} kino` yoki "
        f"`@{config.BOT_USERNAME_FALLBACK} kino {movie['title']}` orqali topiladi.",
        reply_markup=_menu_markup(),
        parse_mode="Markdown",
    )
    return KINO_MENU


async def kino_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("kino_pending", None)
    if update.message:
        await update.message.reply_text("❌ Kino amali bekor qilindi.")
    return ConversationHandler.END



async def kino_migration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Eski kino yozuvlarini MTProto source metadata bilan bog'lash.

    Foydalanish:
      /kino_migration                  -> holat hisoboti
      /kino_migration MOVIE_ID CHAT_ID MESSAGE_ID

    Bu buyruq media baytlarini yuklamaydi. Ko'rsatilgan Telegram xabardan
    faqat MTProto metadata olinib, katalogdagi shu kino yozuviga yoziladi.
    """
    user = update.effective_user
    if not user or not _is_admin(user.id):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ Faqat admin uchun.")
        return

    args = list(context.args or [])
    movies = storage.search_movies("")
    total = len(movies)
    ready = sum(1 for m in movies if m.get("telegram_chat_id") and m.get("telegram_message_id") and m.get("telegram_document_id"))
    legacy = total - ready

    if not args:
        await update.effective_message.reply_text(
            "🎬 Kino MTProto migratsiya holati\n\n"
            f"Jami: {total}\n"
            f"MTProto tayyor: {ready}\n"
            f"Eski/fallback: {legacy}\n\n"
            "Eski kinoni ulash: \n"
            "/kino_migration MOVIE_ID CHAT_ID MESSAGE_ID\n\n"
            "CHAT_ID — video turgan Telegram chat/channel ID.\n"
            "MESSAGE_ID — aynan video xabarining ID'si."
        )
        return

    if len(args) != 3:
        await update.effective_message.reply_text(
            "❌ Format noto'g'ri.\n\n"
            "/kino_migration MOVIE_ID CHAT_ID MESSAGE_ID"
        )
        return

    movie_id, chat_raw, message_raw = args
    movie = storage.get_movie(movie_id)
    if not movie:
        await update.effective_message.reply_text("❌ Bunday kino ID topilmadi.")
        return
    try:
        chat_id = int(chat_raw)
        message_id = int(message_raw)
        if message_id <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ CHAT_ID va MESSAGE_ID raqam bo'lishi kerak.")
        return

    try:
        meta = await telegram_mtproto.resolve_message(chat_id=chat_id, message_id=message_id)
    except Exception as exc:
        logger.warning("🎬 Kino migration resolve xato: %s: %s", type(exc).__name__, exc)
        await update.effective_message.reply_text(
            "❌ Telegram source xabarini MTProto orqali olishning iloji bo'lmadi.\n"
            f"Sabab: {type(exc).__name__}: {exc}"
        )
        return

    # Xavfsizlik: ko'rsatilgan source xabarda haqiqiy media bo'lishi shart.
    if not meta.get("telegram_document_id") or not meta.get("telegram_file_reference"):
        await update.effective_message.reply_text("❌ Bu Telegram xabarida oqimlanadigan document/media topilmadi.")
        return

    updated = storage.update_movie(
        movie_id,
        telegram_chat_id=meta.get("telegram_chat_id"),
        telegram_message_id=meta.get("telegram_message_id"),
        telegram_document_id=meta.get("telegram_document_id"),
        telegram_access_hash=meta.get("telegram_access_hash"),
        telegram_file_reference=meta.get("telegram_file_reference", ""),
        telegram_file_unique_id=meta.get("telegram_file_unique_id", ""),
        mime_type=meta.get("mime_type") or movie.get("mime_type") or "video/mp4",
        file_name=meta.get("file_name") or movie.get("file_name") or "",
        size=meta.get("size") or movie.get("size") or 0,
    )
    if not updated:
        await update.effective_message.reply_text("❌ Kino yozuvini yangilab bo'lmadi.")
        return

    await update.effective_message.reply_text(
        "✅ Kino MTProto source bilan bog'landi!\n\n"
        f"🎬 {updated['title']}\n"
        f"🆔 {updated['id']}\n"
        f"💬 Chat: {updated.get('telegram_chat_id')}\n"
        f"📨 Message: {updated.get('telegram_message_id')}\n"
        f"📦 Size: {updated.get('size', 0)} bytes\n\n"
        "Endi kino oqimida MTProto primary ishlatiladi."
    )


def build_catalog_message(query: str = ""):
    movies = storage.search_movies(query)
    if not movies:
        text = "📚 Mavjud kinolar\n\nHech qanday kino topilmadi." if not query else f"🔎 «{query}» bo'yicha kino topilmadi."
        return text, _menu_markup()

    lines = ["📚 Mavjud kinolar"]
    if query:
        lines[0] += f"\n🔎 Qidiruv: {query}"
    rows = []
    for movie in movies[:20]:
        lines.append(f"\n🎬 {movie['title']}")
        rows.append([InlineKeyboardButton(f"🎬 {movie['title'][:45]}", callback_data=f"kino:open:{movie['id']}")])
    rows.append([InlineKeyboardButton("➕ Kino yuklash", callback_data="kino:upload")])
    rows.append([InlineKeyboardButton("🔄 Yangilash", callback_data="kino:list")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def kino_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    movie_id = q.data.split(":", 2)[-1]
    movie = storage.get_movie(movie_id)
    if not movie:
        await q.answer("Kino topilmadi yoki o'chirilgan.", show_alert=True)
        return
    await q.answer()
    # Admin katalogidan ham foydalanuvchi Mini App orqali ko'rishi mumkin.
    room_id = movie_watch.create_room(movie_id, q.from_user.id)
    if not room_id:
        await q.answer("Xona yaratib bo'lmadi.", show_alert=True)
        return
    url = movie_watch.room_url(movie_id, room_id)
    await q.edit_message_text(
        f"🎬 {movie['title']}\n\n"
        "Kino oldindan katalogga yuklangan. Qayta yuklash shart emas.\n"
        "👇 Tomosha qilish uchun oching:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Kino ko'rish", url=url)]]),
    )
