"""Telegram Business uchun egasi sozlaydigan shaxsiy avtomatik javob.

Barcha amallar `business_autoreply` loggeriga yoziladi. Xabar matnining o'zi
maxfiylik uchun logga yozilmaydi; faqat uzunligi va texnik identifikatorlar yoziladi.
"""
import logging

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat,
)
import business_storage

logger = logging.getLogger("business_autoreply")
_WAITING_TEXT = "business_auto_reply_waiting_text"
_DRAFT_KEY = "business_auto_reply_draft"


async def ensure_command_visible(bot, user_id: int) -> bool:
    """Business egasining shaxsiy chatiga Business commandlarini o'rnatadi."""
    user_id = int(user_id)
    logger.info("COMMAND_MENU_START user_id=%s", user_id)
    try:
        scope = BotCommandScopeChat(chat_id=user_id)
        existing = await bot.get_my_commands(scope=scope)
        logger.info(
            "COMMAND_MENU_SCOPE_READ user_id=%s commands=%s",
            user_id, [c.command for c in existing],
        )
        commands = list(existing)
        if not commands:
            defaults = await bot.get_my_commands()
            commands = list(defaults)
            logger.info(
                "COMMAND_MENU_DEFAULT_FALLBACK user_id=%s commands=%s",
                user_id, [c.command for c in commands],
            )
        if not any(c.command == "bossiyons" for c in commands):
            commands.append(BotCommand("bossiyons", "Avtomatik javob sozlash"))
        if not any(c.command == "al_yordamchi" for c in commands):
            commands.append(BotCommand("al_yordamchi", "Shaxsiy AI yordamchi"))
        await bot.set_my_commands(commands, scope=scope)
        logger.info(
            "COMMAND_MENU_SET_OK user_id=%s command_count=%s commands=%s",
            user_id, len(commands), [c.command for c in commands],
        )

        # Telegram'ga qayta so'rov yuborib, buyruq haqiqatan scope'da saqlanganini tekshiramiz.
        verified = await bot.get_my_commands(scope=scope)
        visible_bossiyons = any(c.command == "bossiyons" for c in verified)
        visible_al = any(c.command == "al_yordamchi" for c in verified)
        visible = visible_bossiyons and visible_al
        if visible:
            logger.info(
                "COMMAND_MENU_VERIFY_OK user_id=%s commands=%s bossiyons=%s al_yordamchi=%s",
                user_id, [c.command for c in verified], visible_bossiyons, visible_al,
            )
            return True
        logger.error(
            "COMMAND_MENU_VERIFY_FAILED user_id=%s commands=%s",
            user_id, [c.command for c in verified],
        )
        return False
    except Exception as exc:
        logger.exception(
            "COMMAND_MENU_FAILED user_id=%s error_type=%s error=%s",
            user_id, type(exc).__name__, exc,
        )
        return False


async def bossiyons_cmd(update, context):
    user = update.effective_user
    message = update.effective_message
    logger.info(
        "BOSSIYONS_COMMAND_RECEIVED update_id=%s user_id=%s chat_id=%s chat_type=%s",
        getattr(update, "update_id", None), getattr(user, "id", None),
        getattr(getattr(message, "chat", None), "id", None),
        getattr(getattr(message, "chat", None), "type", None),
    )
    if not user or not message or message.chat.type != "private":
        logger.warning("BOSSIYONS_COMMAND_IGNORED reason=NOT_PRIVATE_OR_MISSING_USER")
        return
    entry = business_storage.get_connection_for_user(user.id)
    usable, reason = business_storage.is_connection_usable(entry)
    logger.info(
        "BOSSIYONS_CONNECTION_CHECK user_id=%s usable=%s reason=%s has_entry=%s",
        user.id, usable, reason, bool(entry),
    )
    if not usable:
        await message.reply_text(
            "Avval Telegram Business akkauntingizni botga ulang va unga xabarlarni o‘qish/javob berish ruxsatini bering."
        )
        logger.warning("BOSSIYONS_COMMAND_REJECTED user_id=%s reason=%s", user.id, reason)
        return

    menu_ok = await ensure_command_visible(context.bot, user.id)
    context.user_data[_WAITING_TEXT] = True
    await message.reply_text(
        "Bot nomingizdan sizga shaxsiy chatda yozganlarga nima deb javob bersin?\n\n"
        "Javob matnini yuboring. Bekor qilish uchun /cancel buyrug‘ini bosing."
    )
    logger.info(
        "BOSSIYONS_PROMPT_SENT user_id=%s command_menu_verified=%s",
        user.id, menu_ok,
    )


async def receive_reply_text(update, context):
    if not context.user_data.get(_WAITING_TEXT):
        return False  # bu xabar bizniki emas — keyingi handlerlarga o'tsin
    message = update.effective_message
    user = update.effective_user
    if not message or not user or message.chat.type != "private":
        logger.warning(
            "AUTOREPLY_TEXT_IGNORED reason=NOT_PRIVATE_OR_MISSING user_id=%s",
            getattr(user, "id", None),
        )
        return False
    text = (message.text or "").strip()
    if not text:
        logger.warning("AUTOREPLY_TEXT_REJECTED user_id=%s reason=EMPTY", user.id)
        await message.reply_text("Iltimos, oddiy matn yuboring.")
        return True
    if len(text) > 4096:
        logger.warning(
            "AUTOREPLY_TEXT_REJECTED user_id=%s reason=TOO_LONG length=%s",
            user.id, len(text),
        )
        await message.reply_text("Matn 4096 belgidan oshmasligi kerak. Qisqartirib qayta yuboring.")
        return True
    context.user_data[_WAITING_TEXT] = False
    context.user_data[_DRAFT_KEY] = text
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Yangi foydalanuvchilarga", callback_data="bar:set:new")],
        [InlineKeyboardButton("Hammaga", callback_data="bar:set:all")],
        [InlineKeyboardButton("Bekor qilish", callback_data="bar:cancel")],
    ])
    await message.reply_text("Kimlarga avtomatik javob yuborilsin?", reply_markup=keyboard)
    logger.info("AUTOREPLY_TEXT_ACCEPTED user_id=%s text_length=%s", user.id, len(text))
    return True


async def choice_callback(update, context):
    query = update.callback_query
    if not query:
        logger.warning("AUTOREPLY_CHOICE_IGNORED reason=NO_CALLBACK_QUERY")
        return
    await query.answer()
    user = query.from_user
    data = query.data or ""
    logger.info("AUTOREPLY_CHOICE_RECEIVED user_id=%s callback=%s", user.id, data)
    if data == "bar:cancel":
        context.user_data.pop(_DRAFT_KEY, None)
        context.user_data.pop(_WAITING_TEXT, None)
        await query.edit_message_text("Avtomatik javob sozlash bekor qilindi.")
        logger.info("AUTOREPLY_SETUP_CANCELLED user_id=%s", user.id)
        return
    mode = data.removeprefix("bar:set:")
    draft = context.user_data.get(_DRAFT_KEY)
    if mode not in ("new", "all") or not draft:
        logger.warning(
            "AUTOREPLY_SETUP_REJECTED user_id=%s reason=INVALID_MODE_OR_MISSING_DRAFT mode=%s",
            user.id, mode,
        )
        await query.edit_message_text("Sozlama muddati tugagan. Qayta /bossiyons buyrug‘ini yuboring.")
        return
    entry = business_storage.get_connection_for_user(user.id)
    usable, reason = business_storage.is_connection_usable(entry)
    logger.info(
        "AUTOREPLY_SAVE_CONNECTION_CHECK user_id=%s usable=%s reason=%s",
        user.id, usable, reason,
    )
    if not usable:
        logger.error("AUTOREPLY_SAVE_FAILED user_id=%s reason=%s", user.id, reason)
        await query.edit_message_text("Telegram Business ulanishi faol emas. Avval akkauntni qayta ulang.")
        return
    try:
        business_storage.set_auto_reply(user.id, draft, mode)
        logger.info(
            "AUTOREPLY_SETTINGS_SAVED user_id=%s mode=%s text_length=%s",
            user.id, mode, len(draft),
        )
    except Exception:
        logger.exception("AUTOREPLY_SETTINGS_SAVE_EXCEPTION user_id=%s", user.id)
        await query.edit_message_text("Sozlamani saqlashda xatolik yuz berdi. Logni tekshiring.")
        return
    context.user_data.pop(_DRAFT_KEY, None)
    audience = "faqat sizga birinchi marta yozganlarga" if mode == "new" else "sizga yozgan barcha foydalanuvchilarga (har bir xabar kelganda)"
    await query.edit_message_text(
        "✅ Avtomatik javob yoqildi.\n\n"
        f"Javob matni: {draft}\n\n"
        f"Rejim: {audience}.\n"
        "Hech kimga oldindan yoki ommaviy xabar yuborilmaydi. Javob faqat yangi shaxsiy xabar kelganda yuboriladi."
    )
    logger.info("AUTOREPLY_SETUP_COMPLETED user_id=%s mode=%s", user.id, mode)


async def cancel_cmd(update, context):
    user = update.effective_user
    had_state = bool(
        context.user_data.pop(_WAITING_TEXT, None)
        or context.user_data.pop(_DRAFT_KEY, None)
    )
    logger.info("AUTOREPLY_CANCEL_COMMAND user_id=%s had_pending_state=%s",
                getattr(user, "id", None), had_state)
    if had_state:
        await update.effective_message.reply_text("Avtomatik javob sozlash bekor qilindi.")


async def process_business_message(message, context):
    """Incoming Business private chatga egasi nomidan javob qaytaradi."""
    if not message:
        logger.warning("BUSINESS_AUTOREPLY_SKIP reason=EMPTY_MESSAGE")
        return
    connection_id = getattr(message, "business_connection_id", None)
    chat = getattr(message, "chat", None)
    logger.info(
        "BUSINESS_MESSAGE_RECEIVED connection_id=%s chat_id=%s chat_type=%s",
        connection_id, getattr(chat, "id", None), getattr(chat, "type", None),
    )
    if not connection_id or not chat:
        logger.warning("BUSINESS_AUTOREPLY_SKIP reason=MISSING_CONNECTION_OR_CHAT")
        return
    if chat.type != "private":
        logger.info("BUSINESS_AUTOREPLY_SKIP reason=NOT_PRIVATE chat_id=%s", chat.id)
        return
    owner_id = business_storage.get_user_id_for_connection(connection_id)
    if not owner_id:
        logger.error(
            "BUSINESS_AUTOREPLY_SKIP reason=UNKNOWN_CONNECTION connection_id=%s chat_id=%s",
            connection_id, chat.id,
        )
        return
    # Business auto-reply ham egasi boshqa funksiyada bo‘lsa xabar yubormaydi.
    try:
        import feature_guard
        if feature_guard.is_user_busy(context.application, owner_id):
            logger.info("BUSINESS_AUTOREPLY_SKIP reason=OWNER_ACTIVE_FEATURE owner_id=%s", owner_id)
            return
    except Exception:
        logger.exception("Business auto-reply busy-guard tekshiruvida xato owner_id=%s", owner_id)
        return
    sender = getattr(message, "from_user", None)
    if not sender:
        logger.warning("BUSINESS_AUTOREPLY_SKIP reason=NO_SENDER owner_id=%s chat_id=%s",
                       owner_id, chat.id)
        return
    if sender.is_bot or sender.id == owner_id:
        logger.info(
            "BUSINESS_AUTOREPLY_SKIP reason=BOT_OR_OWNER_MESSAGE owner_id=%s sender_id=%s",
            owner_id, sender.id,
        )
        return

    # Faqat sof MATNLI xabarlar uchun eski avtomatik javob ishlaydi.
    # Ovozli xabar, foto, video, hujjat va boshqa media uchun javob yuborilmaydi.
    if not (getattr(message, "text", None) or "").strip():
        logger.info(
            "BUSINESS_AUTOREPLY_SKIP reason=NON_TEXT_MEDIA owner_id=%s chat_id=%s",
            owner_id, chat.id,
        )
        return

    is_new_peer = business_storage.record_auto_reply_peer(owner_id, chat.id)
    settings = business_storage.get_auto_reply(owner_id)
    if not settings or not settings.get("text"):
        logger.info(
            "BUSINESS_AUTOREPLY_SKIP reason=NO_SETTINGS owner_id=%s chat_id=%s",
            owner_id, chat.id,
        )
        return
    mode = settings.get("mode")
    if mode == "new" and not is_new_peer:
        logger.info(
            "BUSINESS_AUTOREPLY_SKIP reason=NOT_NEW_PEER owner_id=%s chat_id=%s",
            owner_id, chat.id,
        )
        return
    if mode not in ("new", "all"):
        logger.error(
            "BUSINESS_AUTOREPLY_SKIP reason=INVALID_MODE owner_id=%s mode=%s",
            owner_id, mode,
        )
        return
    try:
        sent = await context.bot.send_message(
            business_connection_id=connection_id,
            chat_id=chat.id,
            text=settings["text"],
        )
        logger.info(
            "BUSINESS_AUTOREPLY_SEND_OK owner_id=%s chat_id=%s message_id=%s mode=%s",
            owner_id, chat.id, getattr(sent, "message_id", None), mode,
        )
    except Exception as exc:
        logger.exception(
            "BUSINESS_AUTOREPLY_SEND_FAILED owner_id=%s chat_id=%s mode=%s "
            "error_type=%s error=%s",
            owner_id, chat.id, mode, type(exc).__name__, exc,
        )
