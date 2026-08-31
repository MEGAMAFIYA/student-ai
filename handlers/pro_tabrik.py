"""
💎 /pro — /tabrik'ning "Pro" versiyasi. Farqi: animatsiya oxirida
foydalanuvchining "👤 Mening kabinetim" (/my) orqali GitHub'ga yuklab
qo'ygan shaxsiy RASMLARI slайд-shou sifatida ko'rsatiladi (har biri 1
soniya), so'ng yozilgan tabriknoma ochiladi.

Ishlaydi:
  /pro <matn>
  /pro@Student_ai_uz_bot <matn>   (CommandHandler avtomatik ushlaydi)
  @Student_ai_uz_bot /pro <matn>  (handlers/mention_dispatch.py orqali,
                                    guruh/shaxsiy chat)
  @Student_ai_uz_bot pro <matn>   (do'st bilan chatda, INLINE rejim —
                                    handlers/inline_query.py'ga qarang)

MUHIM ARXITEKTURA QARORI: handlers/tabrik.py'dagi xabar MATN sifatida
boshlanadi va oxirigacha matn bo'lib qoladi. /pro esa RASM SLAYD-SHOU
qilishi kerak bo'lgani uchun, claim-tugmali xabar BOSHIDANOQ RASM
sifatida yuboriladi (bosh placeholder — sovg'a qutisi rasmi) — Telegram
matn xabarini keyinchalik media'ga aylantirishga ruxsat BERMAYDI, lekin
bitta media turini (rasm) BOSHQA rasmga almashtirishga har doim ruxsat
beradi. Shu sabab countdown/emoji freym'lari ham matn EMAS, balki shu
placeholder rasmning IZOHI (caption) sifatida ko'rsatiladi.

Sanoqdan (5→1) keyin "aylanayotgan naqsh" o'rniga endi emoji animatsiyasi
ishlatiladi — bir nechta emoji ketma-ket, har biri alohida, qo'shimcha
matnsiz ko'rsatiladi. Foydalanuvchi /pro'dan keyin matndan OLDIN
emoji(lar) yozgan bo'lsa (masalan "/pro 😊🥰😳🙄 tabriklayman...")
o'shalar ishlatiladi, aks holda tabrik_logic.DEFAULT_EMOJIS.

Rasmlar ro'yxati /pro YUBORILGAN PAYTDA "muzlatiladi" (pro_tabrik_logic.py)
— shuning uchun sub'ekt keyinchalik rasmlarini o'zgartirsa ham, ALLAQACHON
yuborilgan tabriknoma o'sha vaqtdagi rasmlar bilan ishlaydi.
"""

import asyncio
import io
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

import github_storage
import pro_tabrik_logic
import tabrik_logic  # ASCII-art countdown/naqsh — /tabrik bilan BIR XIL

logger = logging.getLogger(__name__)

_ACTIVE: set[tuple[int, int]] = set()
_REVERT_TASKS: dict[tuple[int, int], asyncio.Task] = {}

COUNTDOWN_DELAY = 1.0
FRAME_DELAY = 0.45
SLIDESHOW_DELAY = 1.0     # so'ralganidek — har bir rasm 1 soniya turadi
REVERT_DELAY_SEC = 120

_GIFT_PLACEHOLDER_CACHE: bytes | None = None


def _build_gift_placeholder() -> bytes:
    """Sof vektor chizilgan "o'ralgan sovg'a qutisi" rasmi — tashqi rasm
    fayliga (tarmoq so'roviga) muhtoj emas, faqat Pillow bilan chiziladi.
    Bir marta generatsiya qilinib, xotirada keshlanadi (har chaqiriqda
    qayta chizmaslik uchun)."""
    from PIL import Image, ImageDraw

    size = 512
    bg = (30, 158, 144)       # loy-yashil — mavjud brend aksenti bilan bir xil
    ribbon = (251, 247, 240)  # issiq qog'oz rangi
    bow = (232, 140, 61)      # amber

    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)

    box_margin = 70
    draw.rectangle([box_margin, box_margin + 40, size - box_margin, size - box_margin], fill=(240, 250, 248))
    # Lenta (gorizontal + vertikal chiziqlar)
    ribbon_w = 46
    draw.rectangle([size // 2 - ribbon_w // 2, box_margin + 40, size // 2 + ribbon_w // 2, size - box_margin], fill=ribbon)
    draw.rectangle([box_margin, size // 2 - ribbon_w // 2, size - box_margin, size // 2 + ribbon_w // 2], fill=ribbon)
    # Kamon (ikkita oval halqa + markaziy tugun)
    cx, cy = size // 2, box_margin + 40
    draw.ellipse([cx - 85, cy - 48, cx - 15, cy + 12], fill=bow)
    draw.ellipse([cx + 15, cy - 48, cx + 85, cy + 12], fill=bow)
    draw.ellipse([cx - 24, cy - 22, cx + 24, cy + 22], fill=bow)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _gift_placeholder_bytes() -> bytes:
    global _GIFT_PLACEHOLDER_CACHE
    if _GIFT_PLACEHOLDER_CACHE is None:
        _GIFT_PLACEHOLDER_CACHE = _build_gift_placeholder()
    return _GIFT_PLACEHOLDER_CACHE


def _ready_markup(short_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎁 Tabriknomani qabul qilish", callback_data=f"protabrik:claim:{short_id}")
    ]])


async def pro_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, override_text: str | None = None):
    if not update.message:
        return
    raw_text = override_text if override_text is not None else (update.message.text or "")
    text = pro_tabrik_logic.parse_pro_text(raw_text)
    if not text:
        await update.message.reply_text(
            "💎 Tabrik matnini ham yozing, masalan:\n\n"
            "`/pro Salom mening aziz do'stim, seni tavallud kuning bilan tabriklayman!`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Matn boshida emoji(lar) yozilgan bo'lsa (masalan
    # "/pro 😊🥰😳🙄 tabriklayman...") — o'shalar animatsiyada ishlatiladi,
    # qolgan qism esa haqiqiy tabrik matni bo'ladi.
    emojis, text = tabrik_logic.extract_emojis(text)
    if not text:
        await update.message.reply_text(
            "💎 Tabrik matnini ham yozing, masalan:\n\n"
            "`/pro Salom mening aziz do'stim, seni tavallud kuning bilan tabriklayman!`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    sender_id = update.effective_user.id
    photos = github_storage.list_user_photos(sender_id) if github_storage.is_configured() else []
    short_id = pro_tabrik_logic.store_pro_greeting(text, sender_id, photos, emojis)

    await update.message.reply_photo(
        photo=InputFile(io.BytesIO(_gift_placeholder_bytes()), filename="gift.png"),
        caption=pro_tabrik_logic.build_ready_card(),
        reply_markup=_ready_markup(short_id),
    )
    logger.info(f"💎 /pro yuborildi: chat_id={update.effective_chat.id}, short_id={short_id}, rasmlar_soni={len(photos)}.")


async def _safe_edit_caption(msg, caption: str, reply_markup=None) -> None:
    try:
        await msg.edit_caption(caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except RetryAfter as e:
        logger.warning(f"💎 Telegram flood-control: {e.retry_after}s kutilmoqda.")
        await asyncio.sleep(e.retry_after + 0.1)
        try:
            await msg.edit_caption(caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        except Exception:
            pass
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"💎 Caption edit qilib bo'lmadi: {e}")
    except Exception as e:
        logger.warning(f"💎 Caption edit qilishda kutilmagan xato: {type(e).__name__}: {e}")


async def _run_slideshow(msg, photos: list[str]) -> None:
    """Har bir rasmni 1 soniyaga ko'rsatib, keyingisi bilan almashtiradi.
    Bitta rasm yuklab bo'lmasa (masalan GitHub'dan o'chirilgan bo'lsa),
    xatoni jimgina o'tkazib, keyingisiga o'tadi — bitta buzuq havola butun
    slайд-shouni to'xtatib qo'ymasligi kerak."""
    for i, url in enumerate(photos):
        try:
            await msg.edit_media(media=InputMediaPhoto(media=url, caption=f"📷 {i + 1}/{len(photos)}"))
        except Exception as e:
            logger.warning(f"💎 Slайд-shou: rasmni ko'rsatib bo'lmadi ({url}): {type(e).__name__}: {e}")
        await asyncio.sleep(SLIDESHOW_DELAY)


async def pro_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    msg = query.message
    if not msg:
        await query.answer("⚠️ Xabar topilmadi.", show_alert=True)
        return

    chat_id = update.effective_chat.id
    message_key = (chat_id, msg.message_id)

    short_id = query.data.split(":", 2)[2]
    entry = pro_tabrik_logic.get_pro_greeting(short_id)
    if not entry:
        await query.answer("⚠️ Bu tabrikning muddati o'tgan.", show_alert=True)
        return

    if message_key in _ACTIVE:
        await query.answer("⏳ Animatsiya allaqachon ketmoqda...", show_alert=False)
        return

    pending_revert = _REVERT_TASKS.pop(message_key, None)
    if pending_revert:
        pending_revert.cancel()

    pro_tabrik_logic.touch_pro_greeting(short_id)
    _ACTIVE.add(message_key)
    await query.answer("💎 Ochilmoqda...")

    try:
        for n in (5, 4, 3, 2, 1):
            await _safe_edit_caption(msg, tabrik_logic.build_countdown_frame(n))
            await asyncio.sleep(COUNTDOWN_DELAY)

        emojis = entry.get("emojis") or tabrik_logic.DEFAULT_EMOJIS
        for emoji in emojis:
            await _safe_edit_caption(msg, tabrik_logic.build_emoji_frame(emoji))
            await asyncio.sleep(FRAME_DELAY)

        if entry["photos"]:
            await _run_slideshow(msg, entry["photos"])

        escaped = escape_markdown(entry["text"], version=1)
        await _safe_edit_caption(msg, tabrik_logic.build_final_card(escaped))
        logger.info(f"💎 /pro animatsiyasi yakunlandi: chat_id={chat_id}, message_id={msg.message_id}.")

        task = asyncio.create_task(_schedule_revert(context, chat_id, msg.message_id, short_id, message_key))
        _REVERT_TASKS[message_key] = task
    except Exception as e:
        logger.error(f"💎 /pro animatsiyasida xato (chat_id={chat_id}, message_id={msg.message_id}): {type(e).__name__}: {e}", exc_info=True)
        try:
            escaped = escape_markdown(entry["text"], version=1)
            await _safe_edit_caption(msg, f"❌ Animatsiyada xatolik yuz berdi, lekin tabrigingiz shu yerda:\n\n{escaped}")
        except Exception:
            pass
    finally:
        _ACTIVE.discard(message_key)


async def _schedule_revert(context, chat_id: int, message_id: int, short_id: str, message_key: tuple[int, int]) -> None:
    try:
        await asyncio.sleep(REVERT_DELAY_SEC)
        await context.bot.edit_message_media(
            chat_id=chat_id,
            message_id=message_id,
            media=InputMediaPhoto(
                media=InputFile(io.BytesIO(_gift_placeholder_bytes()), filename="gift.png"),
                caption=pro_tabrik_logic.build_ready_card(),
            ),
            reply_markup=_ready_markup(short_id),
        )
        logger.info(f"💎 /pro xabari asl holatga qaytarildi: chat_id={chat_id}, message_id={message_id}.")
    except asyncio.CancelledError:
        raise
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"💎 /pro xabarini asl holatga qaytarishda xato: {e}")
    except Exception as e:
        logger.warning(f"💎 /pro xabarini asl holatga qaytarishda kutilmagan xato: {type(e).__name__}: {e}")
    finally:
        _REVERT_TASKS.pop(message_key, None)
