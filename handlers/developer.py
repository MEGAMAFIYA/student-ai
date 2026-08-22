"""
👨‍💻 /developer — FAQAT adminlarga (config.ADMIN_IDS) ko'rinadigan buyruq.

Shu orqali barcha funksiyalar (Universal chat, Kurs ishi, Tarjima,
PDF tahrirlash, Qo'llanma, Vision) uchun AI PROVIDER / MODEL / API_KEY /
BASE_URL — .env faylni tahrirlamasdan, to'g'ridan-to'g'ri Telegram ichidan
o'zgartiriladi. O'zgarishlar config.py orqali runtime_ai_config.json fayliga
yoziladi, shuning uchun bot qayta ishga tushganda ham saqlanib qoladi.

Qo'shimcha "➕ Barcha modellar" bo'limi: bitta provider (masalan gemini)
tanlanadi, so'ng bitta model nomi kiritiladi — shu PROVIDER'ga ega BARCHA
funksiyalarning MODEL qiymati bir vaqtda shu nomga o'zgaradi.

Kelajakda yangi funksiya (masalan rasm generatsiyasi) qo'shilsa, uni ham
shu menyuga qo'shish uchun config.AI_FUNCTIONS / AI_FUNCTION_LABELS ga bitta
qator qo'shish kifoya — bu fayl o'zgarishsiz ishlayveradi.
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

import config

logger = logging.getLogger(__name__)

DEV_MENU, DEV_WAIT_TEXT, DEV_WAIT_BULK_MODEL = range(3)

_FIELD_LABELS = {
    "provider": "PROVIDER",
    "model": "MODEL",
    "api_key": "API_KEY",
    "base_url": "BASE_URL",
}


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in config.ADMIN_IDS)


def _mask_key(value: str) -> str:
    if not value:
        return "❌ o'rnatilmagan"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _main_menu_text() -> str:
    return (
        "🔧 *Developer — AI sozlamalari*\n\n"
        "Har bir funksiya uchun AI provider, model, API kalit va bazaviy "
        "URL manzilini shu yerdan boshqarishingiz mumkin.\n\n"
        "Bo'limni tanlang:"
    )


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    rows = []
    prefixes = list(config.AI_FUNCTION_LABELS.items())
    for i in range(0, len(prefixes), 2):
        row = [
            InlineKeyboardButton(label, callback_data=f"dev:func:{prefix}")
            for prefix, label in prefixes[i:i + 2]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("➕ Barcha modellar", callback_data="dev:bulk")])
    rows.append([InlineKeyboardButton("❌ Yopish", callback_data="dev:close")])
    return InlineKeyboardMarkup(rows)


def _func_menu_text(prefix: str) -> str:
    cfg = config.AI_FUNCTIONS[prefix]
    label = config.AI_FUNCTION_LABELS[prefix]
    base_url = cfg.get("base_url") or "(bo'sh — standart ishlatiladi)"
    return (
        f"{label} — AI sozlamalari\n\n"
        f"PROVIDER: `{cfg.get('provider') or '—'}`\n"
        f"MODEL: `{cfg.get('model') or '—'}`\n"
        f"API_KEY: `{_mask_key(cfg.get('api_key', ''))}`\n"
        f"BASE_URL: `{base_url}`\n\n"
        "O'zgartirish uchun maydonni tanlang:"
    )


def _func_menu_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ PROVIDER", callback_data=f"dev:editprov:{prefix}"),
            InlineKeyboardButton("✏️ MODEL", callback_data=f"dev:edit:{prefix}:model"),
        ],
        [
            InlineKeyboardButton("✏️ API_KEY", callback_data=f"dev:edit:{prefix}:api_key"),
            InlineKeyboardButton("✏️ BASE_URL", callback_data=f"dev:edit:{prefix}:base_url"),
        ],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")],
    ])


def _provider_choice_keyboard(back_callback: str, choose_prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(p.capitalize(), callback_data=f"{choose_prefix}:{p}")]
        for p in config.SUPPORTED_PROVIDERS
    ]
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data=back_callback)])
    return InlineKeyboardMarkup(rows)


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text("🚫 Bu buyruq faqat administratorlar uchun.")
        return ConversationHandler.END

    context.user_data.clear()
    msg = await update.message.reply_text(
        _main_menu_text(), reply_markup=_main_menu_keyboard(), parse_mode="Markdown"
    )
    context.user_data["dev_msg"] = (msg.chat_id, msg.message_id)
    return DEV_MENU


async def _edit_menu(context: ContextTypes.DEFAULT_TYPE, text: str, keyboard: InlineKeyboardMarkup):
    """Menyu xabarini (query orqali yoki saqlangan chat/message_id orqali) yangilaydi."""
    chat_id, message_id = context.user_data["dev_msg"]
    await context.bot.edit_message_text(
        chat_id=chat_id, message_id=message_id, text=text,
        reply_markup=keyboard, parse_mode="Markdown",
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_admin(update):
        await query.edit_message_text("🚫 Ruxsat yo'q.")
        return ConversationHandler.END

    data = query.data
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "close":
        await query.edit_message_text("✅ Yopildi.")
        context.user_data.clear()
        return ConversationHandler.END

    if action == "menu":
        await query.edit_message_text(
            _main_menu_text(), reply_markup=_main_menu_keyboard(), parse_mode="Markdown"
        )
        return DEV_MENU

    if action == "func":
        prefix = parts[2]
        await query.edit_message_text(
            _func_menu_text(prefix), reply_markup=_func_menu_keyboard(prefix), parse_mode="Markdown"
        )
        return DEV_MENU

    if action == "editprov":
        prefix = parts[2]
        await query.edit_message_text(
            f"{config.AI_FUNCTION_LABELS[prefix]} — PROVIDER tanlang:",
            reply_markup=_provider_choice_keyboard(f"dev:func:{prefix}", f"dev:setprov:{prefix}"),
        )
        return DEV_MENU

    if action == "setprov":
        prefix, provider = parts[2], parts[3]
        config.update_ai_field(prefix, "provider", provider)
        await query.edit_message_text(
            f"✅ PROVIDER — *{provider}* qilib o'rnatildi.\n\n" + _func_menu_text(prefix),
            reply_markup=_func_menu_keyboard(prefix), parse_mode="Markdown",
        )
        return DEV_MENU

    if action == "edit":
        prefix, field = parts[2], parts[3]
        context.user_data["dev_edit"] = (prefix, field)
        current = config.AI_FUNCTIONS[prefix].get(field, "")
        shown = _mask_key(current) if field == "api_key" else (current or "(bo'sh)")
        await query.edit_message_text(
            f"{config.AI_FUNCTION_LABELS[prefix]} — *{_FIELD_LABELS[field]}*\n"
            f"Joriy qiymat: `{shown}`\n\n"
            "Yangi qiymatni xabar qilib yuboring.\n"
            "Bo'sh qilish uchun `-` yuboring.",
            parse_mode="Markdown",
        )
        return DEV_WAIT_TEXT

    if action == "bulk":
        await query.edit_message_text(
            "➕ *Barcha modellar*\n\nAvval AI turini (provider) tanlang — shu "
            "turdagi BARCHA funksiyalarning modeli birdaniga o'zgaradi:",
            reply_markup=_provider_choice_keyboard("dev:menu", "dev:bulkprov"),
            parse_mode="Markdown",
        )
        return DEV_MENU

    if action == "bulkprov":
        provider = parts[2]
        context.user_data["dev_bulk_provider"] = provider
        affected = [
            config.AI_FUNCTION_LABELS[p] for p, c in config.AI_FUNCTIONS.items() if c.get("provider") == provider
        ]
        affected_txt = "\n".join(f"• {a}" for a in affected) or "(hozircha bu provider'da hech qaysi funksiya yo'q)"
        await query.edit_message_text(
            f"➕ *Barcha modellar — {provider}*\n\n"
            f"Ta'sir qiladigan funksiyalar:\n{affected_txt}\n\n"
            "Yangi model nomini xabar qilib yuboring:",
            parse_mode="Markdown",
        )
        return DEV_WAIT_BULK_MODEL

    return DEV_MENU


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return ConversationHandler.END

    prefix, field = context.user_data.get("dev_edit", (None, None))
    raw_value = update.message.text.strip()
    value = "" if raw_value in ("-", "bosh", "bo'sh") else raw_value

    # Xavfsizlik uchun: API kalit kabi maxfiy qiymat chatda uzoq turmasin.
    try:
        await update.message.delete()
    except Exception:
        pass

    if prefix is None or field is None:
        return DEV_MENU

    config.update_ai_field(prefix, field, value)
    await _edit_menu(
        context,
        f"✅ {_FIELD_LABELS[field]} yangilandi.\n\n" + _func_menu_text(prefix),
        _func_menu_keyboard(prefix),
    )
    context.user_data.pop("dev_edit", None)
    return DEV_MENU


async def on_bulk_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return ConversationHandler.END

    provider = context.user_data.get("dev_bulk_provider")
    model = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    if not provider or not model:
        return DEV_MENU

    updated = config.bulk_update_model_by_provider(provider, model)
    if updated:
        names = "\n".join(f"• {config.AI_FUNCTION_LABELS[p]}" for p in updated)
        text = f"✅ *{provider}* uchun model *{model}* ga o'zgartirildi:\n\n{names}"
    else:
        text = f"⚠️ *{provider}* provider'ida hech qaysi funksiya topilmadi — hech narsa o'zgarmadi."

    await _edit_menu(context, text + "\n\n" + _main_menu_text(), _main_menu_keyboard())
    context.user_data.pop("dev_bulk_provider", None)
    return DEV_MENU


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bekor qilindi.")
    context.user_data.clear()
    return ConversationHandler.END
