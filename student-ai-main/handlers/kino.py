"""
🎬 KINO KATALOGI
- /kino: admin uchun kino boshqaruvi
- Mavjud kinolar: saqlangan katalogni ko'rsatadi
- Telegram video/document file_id saqlanadi; kino qayta yuklanmaydi.
"""

import html
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


# Brauzer (Mini App) va Android VideoView faqat H.264/AAC MP4 ni ishonchli ijro etadi.
# MKV/AVI kabi formatlar katalogga qo'shilsa-yu, hech qaerda ochilmasdi.
_OK_EXT = {".mp4", ".m4v"}
_OK_MIME = {"video/mp4", "video/x-m4v"}
_RISKY_EXT = {".mov", ".webm", ".3gp"}
_RISKY_MIME = {"video/quicktime", "video/webm", "video/3gpp"}
_CONVERT_HINT = (
    "Kompyuterda o'tkazish uchun:\n"
    "ffmpeg -i kirish.mkv -c:v libx264 -c:a aac -movflags +faststart chiqish.mp4"
)


def _classify_video(media) -> str:
    """'ok' | 'risky' | 'bad' — fayl brauzer/Androidda ijro etilish ehtimoli bo'yicha."""
    ext = os.path.splitext((getattr(media, "file_name", "") or "").lower())[1]
    mime = (getattr(media, "mime_type", "") or "").lower()
    if ext in _OK_EXT or mime in _OK_MIME:
        return "ok"
    if ext in _RISKY_EXT or mime in _RISKY_MIME:
        return "risky"
    if not ext and not mime:
        return "ok"     # Telegram'ning o'zi siqqan video (mime yo'q) — odatda MP4
    return "bad"


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
            "1️⃣ Video yoki video faylni shu yerga yuboring (H.264/AAC MP4 tavsiya etiladi; MKV/AVI ochilmaydi).\n"
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
    quality = _classify_video(source_media)
    if quality == "bad":
        kind = source_media.mime_type or os.path.splitext(source_media.file_name or "")[1] or "noma'lum"
        await msg.reply_text(
            f"❌ Bu format ({kind}) brauzer va Android ilovasida ochilmaydi.\n"
            "Kino H.264 video + AAC audio bilan MP4 bo'lishi kerak.\n\n" + _CONVERT_HINT
        )
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
    text = "✅ Video kino kanaliga saqlandi.\n\n📝 Endi kino nomini yuboring."
    if suggested:
        text += f"\n\nMasalan: <code>{html.escape(suggested)}</code>"
    if quality == "risky":
        text += (
            "\n\n⚠️ Bu format (MOV/WebM/3GP) ba'zi qurilmalarda ochilmasligi mumkin. "
            "Ishonchli ijro uchun H.264/AAC MP4 tavsiya etiladi.\n" + _CONVERT_HINT
        )
    await msg.reply_text(text, parse_mode="HTML")
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

    # Kino nusxasi saqlash kanalida turibdi: stream uchun kerakli manba (chat_id +
    # message_id) bizga allaqachon ma'lum. MTProto esa faqat TASDIQLASH va metadata
    # uchun; u xato bersa ham manba identifikatorlari saqlanadi (aks holda kino
    # "MTProto ma'lumotsiz" yozilib, oqim umuman ishlamay qolardi).
    source_chat_id = int(pending["source_chat_id"])
    source_message_id = int(pending["source_message_id"])
    mtproto_meta = {}
    mtproto_error = ""
    try:
        mtproto_meta = await asyncio.wait_for(
            telegram_mtproto.resolve_message(chat_id=source_chat_id, message_id=source_message_id),
            timeout=90,
        )
        logger.info(
            "🎬 Kino MTProto metadata olindi: chat=%s message=%s document=%s",
            mtproto_meta.get("telegram_chat_id"),
            mtproto_meta.get("telegram_message_id"),
            mtproto_meta.get("telegram_document_id"),
        )
    except Exception as exc:
        mtproto_error = f"{type(exc).__name__}: {exc}"[:300]
        logger.warning("🎬 MTProto metadata olinmadi: %s", mtproto_error)

    size = int(mtproto_meta.get("size") or pending.get("size") or 0)
    movie = storage.add_movie(
        title=title,
        file_id=pending["file_id"],
        mime_type=mtproto_meta.get("mime_type") or pending.get("mime_type") or "video/mp4",
        file_name=mtproto_meta.get("file_name") or pending.get("file_name") or "",
        size=size,
        uploaded_by=update.effective_user.id,
        telegram_chat_id=mtproto_meta.get("telegram_chat_id") or source_chat_id,
        telegram_message_id=mtproto_meta.get("telegram_message_id") or source_message_id,
        telegram_document_id=mtproto_meta.get("telegram_document_id"),
        telegram_access_hash=mtproto_meta.get("telegram_access_hash"),
        telegram_file_reference=mtproto_meta.get("telegram_file_reference", ""),
        telegram_file_unique_id=mtproto_meta.get("telegram_file_unique_id") or pending.get("file_unique_id", ""),
    )

    context.user_data.pop("kino_pending", None)

    if not mtproto_error:
        status = "📡 Media: Telegram MTProto oqimi (kanal tekshirildi ✅, Render diskiga saqlanmaydi)."
    else:
        status = (
            "⚠️ MTProto tekshiruvi o'tmadi: " + html.escape(mtproto_error) + "\n"
            "Kino saqlandi va kanal xabariga bog'landi, lekin MTProto tuzalmaguncha oqim "
            "Bot API zaxirasiga tayanadi (u faqat ~20 MB gacha fayllarda ishlaydi). "
            "Sababni /kino_check bilan tekshiring."
        )
    if size <= 0:
        status += "\n⚠️ Fayl hajmi aniqlanmadi — hajmsiz kinoni oqimlab bo'lmaydi."

    bot_name = html.escape(str(config.BOT_USERNAME_FALLBACK))
    await update.message.reply_text(
        f"✅ Kino katalogga qo'shildi!\n{status}\n\n"
        f"🎬 {html.escape(movie['title'])}\n"
        f"🆔 {html.escape(str(movie['id']))}\n\n"
        f"Endi <code>@{bot_name} kino</code> yoki "
        f"<code>@{bot_name} kino {html.escape(movie['title'])}</code> orqali topiladi.",
        reply_markup=_menu_markup(),
        parse_mode="HTML",
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
    room_id = movie_watch.find_or_create_room(movie_id, q.from_user.id)
    if not room_id:
        await q.answer("Xona yaratib bo'lmadi (faol xonalar ko'p bo'lishi mumkin).", show_alert=True)
        return
    url = movie_watch.room_url(movie_id, room_id)
    await q.edit_message_text(
        f"🎬 {movie['title']}\n\n"
        "Kino oldindan katalogga yuklangan. Qayta yuklash shart emas.\n"
        "👇 Tomosha qilish uchun oching:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Kino ko'rish", url=url)]]),
    )


async def kino_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin uchun: MTProto session kino saqlash kanalini va kinolarni ocha oladimi?

    Foydalanish: /kino_check
    Secretlar va media baytlari ko'rsatilmaydi.
    """
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return
    if not _is_admin(user.id):
        await msg.reply_text("⛔ Faqat admin uchun.")
        return

    lines = ["🔎 Kino MTProto diagnostikasi", ""]
    if not telegram_mtproto.is_configured():
        lines.append("❌ MTProto sozlanmagan (TG_API_ID / TG_API_HASH / TG_SESSION).")
        lines.append("Oqim faqat Bot API zaxirasida ishlaydi (≤20 MB).")
        await msg.reply_text("\n".join(lines))
        return

    async def _check(chat_id, message_id=None):
        try:
            return await asyncio.wait_for(telegram_mtproto.check_storage(chat_id, message_id), timeout=90)
        except asyncio.TimeoutError:
            return False, "vaqt tugadi (90s)"

    ok, detail = await _check(config.KINO_STORAGE_CHANNEL_ID)
    lines.append(f"{'✅' if ok else '❌'} Saqlash kanali ({config.KINO_STORAGE_CHANNEL_ID}): {detail[:250]}")

    movies = storage.search_movies("")
    linked = [m for m in movies if m.get("telegram_chat_id") and m.get("telegram_message_id") and int(m.get("size") or 0) > 0]
    lines.append(f"🎬 Jami kino: {len(movies)}, MTProto oqimiga tayyor: {len(linked)}, "
                 f"eski/ulanmagan: {len(movies) - len(linked)}")
    for m in linked[:3]:
        ok_m, detail_m = await _check(int(m["telegram_chat_id"]), int(m["telegram_message_id"]))
        lines.append(f"{'✅' if ok_m else '❌'} {str(m.get('title'))[:40]}: {detail_m[:200]}")
    if len(movies) - len(linked):
        lines.append("")
        lines.append("Eski kinolarni ulash: /kino_migration MOVIE_ID CHAT_ID MESSAGE_ID")
    if not ok:
        lines.append("")
        lines.append("Yechim: session akkaunti saqlash kanaliga a'zo bo'lishi (yoki kanal public "
                     "username'ga ega bo'lishi) va KINO_STORAGE_CHANNEL_ID to'g'ri bo'lishi kerak.")
    await msg.reply_text("\n".join(lines))


_QUALITY_LIST = ("1080", "720", "480")


async def kino_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: kinoga boshqa sifatdagi (1080/720/480) variantni ulaydi.

    Foydalanish:
      /kino_quality                                    -> yordam va ulangan variantlar
      /kino_quality MOVIE_ID 480 CHAT_ID MESSAGE_ID    -> 480p variantni ulash
      /kino_quality MOVIE_ID 480 off                   -> variantni olib tashlash

    Server videoni o'zi qayta kodlamaydi (Render'da og'ir). Past sifatli fayl kompyuterda
    tayyorlanib, saqlash kanaliga yuklanadi, so'ng shu buyruq bilan kinoga ulanadi.
    """
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return
    if not _is_admin(user.id):
        await msg.reply_text("⛔ Faqat admin uchun.")
        return

    args = list(context.args or [])
    if not args:
        lines = [
            "🎞 Kino sifat variantlari",
            "",
            "Ulash: /kino_quality MOVIE_ID 480 CHAT_ID MESSAGE_ID",
            "Olib tashlash: /kino_quality MOVIE_ID 480 off",
            "",
            "480p tayyorlash (kompyuterda):",
            "ffmpeg -i kino.mp4 -vf scale=-2:480 -c:v libx264 -crf 26 -c:a aac -movflags +faststart kino_480.mp4",
            "Faylni saqlash kanaliga yuboring, xabar ID'sini oling va yuqoridagi buyruq bilan ulang.",
            "",
        ]
        for m in storage.search_movies("")[:30]:
            have = ", ".join(f"{q}p" for q in _QUALITY_LIST if (m.get("variants") or {}).get(q)) or "faqat asl"
            lines.append(f"🎬 {m['title'][:40]} — {m['id']} — {have}")
        await msg.reply_text("\n".join(lines))
        return

    if len(args) < 3:
        await msg.reply_text("❌ Format: /kino_quality MOVIE_ID 480 CHAT_ID MESSAGE_ID")
        return
    movie_id, quality = args[0], args[1].lower().rstrip("p")
    movie = storage.get_movie(movie_id)
    if not movie:
        await msg.reply_text("❌ Bunday kino ID topilmadi.")
        return
    if quality not in _QUALITY_LIST:
        await msg.reply_text("❌ Sifat 1080, 720 yoki 480 bo'lishi kerak.")
        return

    variants = dict(movie.get("variants") or {})
    if args[2].lower() in ("off", "o'chir", "ochir", "delete"):
        variants.pop(quality, None)
        storage.update_movie(movie_id, variants=variants)
        await msg.reply_text(f"✅ {quality}p varianti olib tashlandi.")
        return

    if len(args) != 4:
        await msg.reply_text("❌ Format: /kino_quality MOVIE_ID 480 CHAT_ID MESSAGE_ID")
        return
    try:
        chat_id, message_id = int(args[2]), int(args[3])
        if message_id <= 0:
            raise ValueError
    except ValueError:
        await msg.reply_text("❌ CHAT_ID va MESSAGE_ID raqam bo'lishi kerak.")
        return
    try:
        meta = await asyncio.wait_for(
            telegram_mtproto.resolve_message(chat_id=chat_id, message_id=message_id), timeout=90)
    except Exception as exc:
        logger.warning("🎬 kino_quality resolve xato: %s: %s", type(exc).__name__, exc)
        await msg.reply_text(f"❌ Telegram xabarini MTProto orqali olib bo'lmadi.\nSabab: {type(exc).__name__}: {exc}")
        return
    if not meta.get("telegram_document_id") or int(meta.get("size") or 0) <= 0:
        await msg.reply_text("❌ Bu xabarda oqimlanadigan video topilmadi yoki hajmi noma'lum.")
        return

    variants[quality] = {
        "telegram_chat_id": meta.get("telegram_chat_id") or chat_id,
        "telegram_message_id": meta.get("telegram_message_id") or message_id,
        "size": int(meta.get("size") or 0),
        "mime_type": meta.get("mime_type") or "video/mp4",
    }
    storage.update_movie(movie_id, variants=variants)
    await msg.reply_text(
        f"✅ {quality}p varianti ulandi.\n🎬 {movie['title']}\n📦 {variants[quality]['size']} bayt\n\n"
        "Endi xona egasi sozlamalardan shu sifatni tanlay oladi."
    )
