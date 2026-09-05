"""
🎬 KINO KATALOGI
- /kino: admin uchun kino boshqaruvi
- Mavjud kinolar: saqlangan katalogni ko'rsatadi
- Telegram video/document file_id saqlanadi; kino qayta yuklanmaydi.
"""

import logging
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

import config
import storage
import movie_watch

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
            "⚠️ Cloud Bot API bilan hozircha 45 MB gacha bo'lgan fayllar qabul qilinadi."
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
    file_id = None
    mime_type = "video/mp4"
    file_name = ""

    if msg.video:
        file_id = msg.video.file_id
        mime_type = msg.video.mime_type or "video/mp4"
        file_name = msg.video.file_name or ""
    elif msg.document and (msg.document.mime_type or "").lower().startswith("video/"):
        file_id = msg.document.file_id
        mime_type = msg.document.mime_type or "video/mp4"
        file_name = msg.document.file_name or ""

    if not file_id:
        await msg.reply_text("❌ Video yuboring. MP4/MKV kabi video faylni Telegramga fayl yoki video sifatida yuborishingiz mumkin.")
        return KINO_WAIT_VIDEO

    size = (msg.video.file_size if msg.video else msg.document.file_size) or 0
    max_bytes = config.KINO_MAX_UPLOAD_MB * 1024 * 1024
    if size and size > max_bytes:
        await msg.reply_text(
            f"❌ Fayl juda katta: {size / 1024 / 1024:.1f} MB.\n"
            f"Cloud Bot API uchun limit {config.KINO_MAX_UPLOAD_MB} MB qilib qo'yilgan."
        )
        return KINO_WAIT_VIDEO

    context.user_data["kino_pending"] = {
        "file_id": file_id,
        "mime_type": mime_type,
        "file_name": file_name,
        "size": size,
    }
    suggested = os.path.splitext(file_name)[0] if file_name else ""
    await msg.reply_text(
        ("📝 Kino nomini yuboring."
         + (f"\n\nMasalan: `{suggested}`" if suggested else ""))
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

    movie = storage.add_movie(
        title=title,
        file_id=pending["file_id"],
        mime_type=pending.get("mime_type") or "video/mp4",
        file_name=pending.get("file_name") or "",
        size=pending.get("size") or 0,
        uploaded_by=update.effective_user.id,
    )
    context.user_data.pop("kino_pending", None)

    await update.message.reply_text(
        f"✅ Kino katalogga qo'shildi!\n\n"
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
