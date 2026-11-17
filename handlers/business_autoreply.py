"""Telegram Business uchun egasi sozlaydigan shaxsiy avtomatik javob."""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat
from telegram.ext import filters

import business_storage

logger = logging.getLogger(__name__)
_WAITING_TEXT = "business_auto_reply_waiting_text"


async def ensure_command_visible(bot, user_id: int):
    """Business egasining bot chatidagi buyruqlar menyusiga /bossiyons qo'shadi."""
    try:
        scope = BotCommandScopeChat(chat_id=user_id)
        commands = await bot.get_my_commands(scope=scope)
        if not commands:
            commands = await bot.get_my_commands()
        if not any(c.command == "bossiyons" for c in commands):
            commands.append(BotCommand("bossiyons", "Avtomatik javob sozlash"))
            await bot.set_my_commands(commands, scope=scope)
    except Exception:
        logger.exception("Business Auto Reply command menu update failed: user_id=%s", user_id)


async def bossiyons_cmd(update, context):
    user = update.effective_user
    message = update.effective_message
    if not user or not message or message.chat.type != "private":
        return
    entry = business_storage.get_connection_for_user(user.id)
    usable, reason = business_storage.is_connection_usable(entry)
    if not usable:
        await message.reply_text(
            "Avval Telegram Business akkauntingizni botga ulang va unga xabarlarni o‘qish/javob berish ruxsatini bering."
        )
        return

    # Buyruq faqat aynan akkaunt egasining shaxsiy bot chatidagi menyusiga qo‘shiladi.
    await ensure_command_visible(context.bot, user.id)

    context.user_data[_WAITING_TEXT] = True
    await message.reply_text(
        "Bot nomingizdan sizga shaxsiy chatda yozganlarga nima deb javob bersin?\n\n"
        "Javob matnini yuboring. Bekor qilish uchun /cancel buyrug‘ini bosing."
    )


async def receive_reply_text(update, context):
    if not context.user_data.get(_WAITING_TEXT):
        return
    message = update.effective_message
    user = update.effective_user
    if not message or not user or message.chat.type != "private":
        return
    text = (message.text or "").strip()
    if not text:
        await message.reply_text("Iltimos, oddiy matn yuboring.")
        return
    if len(text) > 4096:
        await message.reply_text("Matn 4096 belgidan oshmasligi kerak. Qisqartirib qayta yuboring.")
        return
    context.user_data[_WAITING_TEXT] = False
    context.user_data["business_auto_reply_draft"] = text
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Yangi foydalanuvchilarga", callback_data="bar:set:new")],
        [InlineKeyboardButton("Hammaga", callback_data="bar:set:all")],
        [InlineKeyboardButton("Bekor qilish", callback_data="bar:cancel")],
    ])
    await message.reply_text("Kimlarga avtomatik javob yuborilsin?", reply_markup=keyboard)


async def choice_callback(update, context):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user = query.from_user
    data = query.data or ""
    if data == "bar:cancel":
        context.user_data.pop("business_auto_reply_draft", None)
        context.user_data.pop(_WAITING_TEXT, None)
        await query.edit_message_text("Avtomatik javob sozlash bekor qilindi.")
        return
    mode = data.removeprefix("bar:set:")
    draft = context.user_data.get("business_auto_reply_draft")
    if mode not in ("new", "all") or not draft:
        await query.edit_message_text("Sozlama muddati tugagan. Qayta /bossiyons buyrug‘ini yuboring.")
        return
    entry = business_storage.get_connection_for_user(user.id)
    usable, _ = business_storage.is_connection_usable(entry)
    if not usable:
        await query.edit_message_text("Telegram Business ulanishi faol emas. Avval akkauntni qayta ulang.")
        return
    business_storage.set_auto_reply(user.id, draft, mode)
    context.user_data.pop("business_auto_reply_draft", None)
    audience = "faqat sizga birinchi marta yozganlarga" if mode == "new" else "sizga yozgan barcha foydalanuvchilarga (har bir xabar kelganda)"
    await query.edit_message_text(
        "✅ Avtomatik javob yoqildi.\n\n"
        f"Javob matni: {draft}\n\n"
        f"Rejim: {audience}.\n"
        "Hech kimga oldindan yoki ommaviy xabar yuborilmaydi. Javob faqat yangi shaxsiy xabar kelganda yuboriladi."
    )


async def cancel_cmd(update, context):
    if context.user_data.pop(_WAITING_TEXT, None) or context.user_data.pop("business_auto_reply_draft", None):
        await update.effective_message.reply_text("Avtomatik javob sozlash bekor qilindi.")


async def process_business_message(message, context):
    """Incoming Business private chatga egasi nomidan javob qaytaradi."""
    if not message or not message.business_connection_id or not message.chat:
        return
    if message.chat.type != "private":
        return
    owner_id = business_storage.get_user_id_for_connection(message.business_connection_id)
    if not owner_id:
        return
    sender = getattr(message, "from_user", None)
    if not sender or sender.is_bot or sender.id == owner_id:
        return
    is_new_peer = business_storage.record_auto_reply_peer(owner_id, message.chat.id)
    settings = business_storage.get_auto_reply(owner_id)
    if not settings or not settings.get("text"):
        return
    if settings.get("mode") == "new" and not is_new_peer:
        return
    try:
        await context.bot.send_message(
            business_connection_id=message.business_connection_id,
            chat_id=message.chat.id,
            text=settings["text"],
        )
    except Exception:
        # Telegram huquq yoki 24 soatlik oynani rad etsa, update handlerini yiqitmaymiz.
        logger.exception(
            "Business auto reply failed: owner_id=%s chat_id=%s", owner_id, message.chat.id
        )
