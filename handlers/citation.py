"""
📚 Iqtibos (citation) generatori — foydalanuvchi manba ma'lumotlarini
(muallif, nom, yil va h.k.) erkin matn ko'rinishida beradi, AI ularni
tanlangan uslubda (GOST yoki APA) to'g'ri formatlangan iqtibosga aylantiradi.
Bir nechta manbani ketma-ket qo'shib, oxirida to'liq ro'yxat holida olish
mumkin.
"""

import asyncio
import logging

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from config import CITATION_AI, MAX_TELEGRAM_TEXT
from ai_clients import ask_ai
from pdf_tools import make_pdf
from handlers.menu import main_menu_keyboard
from handlers import wallet_ui
import storage

logger = logging.getLogger(__name__)

CT_FORMAT, CT_TYPE, CT_DETAILS = range(3)

_SOURCE_TYPES = {
    "book": "📕 Kitob (darslik/monografiya)",
    "article": "📰 Ilmiy maqola",
    "web": "🌐 Veb-sayt",
    "law": "📜 Qonun hujjati",
    "other": "✍️ Boshqa/erkin",
}

_DETAIL_HINTS = {
    "book": "Muallif(lar), kitob nomi, nashr shahri, nashriyot, yil, bet soni",
    "article": "Muallif(lar), maqola nomi, jurnal nomi, jild/son, yil, betlar",
    "web": "Muallif (agar bo'lsa), sahifa nomi, sayt nomi, havola (URL), ko'rilgan sana",
    "law": "Hujjat nomi, qabul qilingan sana, raqami, manba (Qonunchilik ma'lumotlar bazasi va h.k.)",
    "other": "Manba haqida bor bo'lgan barcha ma'lumotni yozing",
}

_SYSTEM_TEMPLATE = (
    "Siz ilmiy adabiyotlar ro'yxati (bibliografiya) bo'yicha mutaxassissiz. "
    "Foydalanuvchi bergan manba ma'lumotlaridan {style} uslubida TO'G'RI "
    "formatlangan BITTA iqtibos yozuvini tuzing. Faqat formatlangan yozuvning "
    "o'zini qaytaring, boshqa hech qanday izoh yozmang. Agar ba'zi ma'lumotlar "
    "yetishmasa, mavjud ma'lumotlar asosida iloji boricha to'g'ri formatlang, "
    "yetishmagan joyni [ma'lumot yo'q] deb qoldiring."
)


def _format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🇷🇺 GOST", callback_data="cite:fmt:GOST"),
        InlineKeyboardButton("🇺🇸 APA", callback_data="cite:fmt:APA"),
    ]])


def _type_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=f"cite:type:{key}")] for key, label in _SOURCE_TYPES.items()]
    return InlineKeyboardMarkup(rows)


def _after_add_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Yana manba qo'shish", callback_data="cite:more")],
        [InlineKeyboardButton("✅ Ro'yxatni yakunlash", callback_data="cite:finish")],
    ])


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"📚 'Iqtibos generatori' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        context.user_data["flow"] = "citation"
        context.user_data["cite_list"] = []
        await query.edit_message_text("📚 *Iqtibos generatori*\n\nQaysi uslubda kerak?", parse_mode=ParseMode.MARKDOWN, reply_markup=_format_keyboard())
    except Exception as e:
        logger.error(f"📚 Iqtibos menyusini ochishda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise
    return CT_FORMAT


async def receive_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["cite_format"] = query.data.split(":")[-1]
    await query.edit_message_text("Manba turini tanlang:", reply_markup=_type_keyboard())
    return CT_TYPE


async def receive_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    source_type = query.data.split(":")[-1]
    context.user_data["cite_type"] = source_type
    hint = _DETAIL_HINTS.get(source_type, "")
    await query.edit_message_text(
        f"{_SOURCE_TYPES.get(source_type, '')}\n\nMa'lumotlarni yozing (bitta xabarda, vergul bilan ajratib):\n<i>{hint}</i>",
        parse_mode=ParseMode.HTML,
    )
    return CT_DETAILS


async def receive_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    details = update.message.text.strip()
    style = context.user_data.get("cite_format", "GOST")
    source_type = context.user_data.get("cite_type", "other")
    chat_id = update.effective_chat.id
    logger.info(f"📚 Iqtibos so'rovi: chat_id={chat_id}, uslub={style}, turi={source_type}.")

    status = await update.message.reply_text("⏳ Iqtibos tuziladi...")

    system = _SYSTEM_TEMPLATE.format(style=style)
    prompt = f"Manba turi: {_SOURCE_TYPES.get(source_type, source_type)}\nMa'lumotlar: {details}"
    citation = await ask_ai(CITATION_AI, prompt, system)

    if not citation:
        logger.error(f"📚 Iqtibos YARATILMADI: chat_id={chat_id}, uslub={style} — sababi yuqoridagi ai_clients loglarida.")
        await status.edit_text("❌ Iqtibosni tuzib bo'lmadi. Qayta urinib ko'ring.")
        return CT_DETAILS

    context.user_data.setdefault("cite_list", []).append(citation.strip())
    logger.info(f"📚 Iqtibos muvaffaqiyatli tuzildi: chat_id={chat_id}, jami={len(context.user_data['cite_list'])} ta.")

    await status.edit_text(
        f"✅ *{style}*:\n\n{citation}\n\n📋 Jami ro'yxatda: {len(context.user_data['cite_list'])} ta.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_after_add_keyboard(),
    )
    return CT_DETAILS


async def add_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Manba turini tanlang:", reply_markup=_type_keyboard())
    return CT_TYPE


async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cite_list = context.user_data.get("cite_list", [])
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0

    if not cite_list:
        await query.edit_message_text("⚠️ Ro'yxat bo'sh.")
        # 💰 Hech qanday iqtibos yaratilmadi — xizmat aslida bajarilmagan,
        # shuning uchun band qilingan summa (agar bo'lsa) ozod qilinadi.
        await wallet_ui.finalize_failure(context, update=update, chat_id=chat_id, reason="citation_list_empty")
        context.user_data.clear()
        return ConversationHandler.END

    logger.info(f"📚 Iqtiboslar ro'yxati yakunlandi: chat_id={chat_id}, jami={len(cite_list)} ta.")
    if user_id:
        storage.record_usage("citation", user_id)

    full_text = "\n\n".join(f"{i + 1}. {c}" for i, c in enumerate(cite_list))
    if len(full_text) <= MAX_TELEGRAM_TEXT:
        await query.edit_message_text(f"📚 *Adabiyotlar ro'yxati:*\n\n{full_text}", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())
    else:
        pdf_buf = await asyncio.to_thread(make_pdf, "Adabiyotlar ro'yxati", full_text)
        msg = await context.bot.send_document(
            chat_id, document=InputFile(pdf_buf, filename="adabiyotlar_royxati.pdf"),
            caption="📚 Adabiyotlar ro'yxati.", reply_markup=main_menu_keyboard(),
        )
        if user_id and msg.document:
            storage.record_file(user_id, "citation", "Adabiyotlar ro'yxati", msg.document.file_id)
        try:
            await query.delete_message()
        except Exception:
            pass

    # 💰 Kamida bitta iqtibos muvaffaqiyatli tuzildi va foydalanuvchiga
    # yuborildi — xizmat MUVAFFAQIYATLI yakunlandi.
    await wallet_ui.finalize_success(context, update=update, chat_id=chat_id)
    context.user_data.clear()
    return ConversationHandler.END
