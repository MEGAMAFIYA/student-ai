"""
/start menyusi, umumiy tugmalar va bosh menyuga qaytish.
"""

import logging

from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

import storage
import wallet

logger = logging.getLogger(__name__)

MENU_CALLBACKS = {
    "course_work": "📘 Kurs ishi / loyiha",
    "essay": "🗒 Referat/Insho",
    "translate": "🌐 Tarjima qilish",
    "pptx": "📊 Taqdimot (PPTX)",
    "quiz": "📋 Test/Viktorina",
    "solve": "🧮 Masala yechish",
    "summarize": "📑 Konspekt qisqartirish",
    "grammar": "✅ Imlo tekshirish",
    "citation": "📚 Iqtibos generatori",
    "images_pdf": "🖼 Suratlarni PDF qilish",
    "edit_pdf": "📝 PDF ni tahrirlash",
    "guide": "📖 Qo'llanma tayyorlash",
    "myfiles": "🗂 Mening fayllarim",
    "remind": "⏰ Eslatmalar",
}

# 💳 Balans/to'lov tugmalari — asosiy menyuning yuqori qismida, boshqalardan
# ALOHIDA qatorda ko'rsatiladi (main_menu_keyboard()ga qarang), shuning
# uchun MENU_CALLBACKS ichida EMAS (u pastdagi 2 ustunli funksiyalar
# panjarasi uchun ishlatiladi).
WALLET_MENU_CALLBACKS = {
    "wallet_balance": "💰 Balansim",
    "wallet_topup": "➕ Balansni to'ldirish",
    "wallet_history": "🧾 To'lovlar tarixi",
}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("💬 UNIVERSAL CHAT", callback_data="menu:universal")]]
    wallet_items = list(WALLET_MENU_CALLBACKS.items())
    for i in range(0, len(wallet_items), 2):
        rows.append([
            InlineKeyboardButton(label, callback_data=f"menu:{key}")
            for key, label in wallet_items[i:i + 2]
        ])
    items = list(MENU_CALLBACKS.items())
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(label, callback_data=f"menu:{key}") for key, label in items[i:i + 2]]
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def _send_quoridor_open_button(update: Update, rid: str) -> bool:
    """/start qg_<xona>: Quoridor Mini App'ini ochuvchi web_app tugmasini yuboradi. Muvaffaqiyatli bo'lsa True."""
    import re as _re
    try:
        import game
        from telegram import WebAppInfo
        rid = str(rid or "").strip()
        if not _re.fullmatch(r"[A-Za-z0-9_-]{6,64}", rid):
            return False
        url = game.webapp_url(rid)
        if not url:
            await update.message.reply_text("⚠️ Mini App manzili sozlanmagan (PUBLIC_BASE_URL). Admin sozlamani tekshirsin.")
            return True
        room = game.ROOMS.get(rid)
        if not room or room.get("game") != "quoridor":
            await update.message.reply_text("⚠️ Bu Quoridor xonasi topilmadi yoki muddati tugagan. Yangi o‘yin boshlang: @bot quoridor")
            return True
        await update.message.reply_text(
            "🧱 *Quoridor* tayyor!\nMini App'ni ochish uchun tugmani bosing 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎮 Quoridor'ni ochish", web_app=WebAppInfo(url=url))]]),
        )
        return True
    except Exception:
        logger.warning("Quoridor web_app tugmasini yuborishda xato", exc_info=True)
        return False


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        logger.warning("/start chaqirildi, lekin update.message yo'q edi — javob yuborilmadi.")
        return
    user = update.effective_user
    logger.info(f"/start bosildi: user_id={user.id if user else '?'}.")
    # Native Android login: /start login_<token> tasdiqlansa,
    # Telegram foydalanuvchisi mobil ilovadagi polling sessiyasiga bog'lanadi.
    payload = ""
    if context.args:
        payload = str(context.args[0]).strip()
    if payload.startswith("login_") and user:
        # MUHIM: bu yerda return QILINMAYDI. Ilovadan kirgan yangi foydalanuvchi
        # ham quyida botda ro'yxatdan o'tishi (hamyon, profil, bonus) va asosiy
        # menyuni olishi kerak — aks holda "ro'yxatdan o'tish" ishlamay qoladi.
        try:
            import mobile_auth
            if mobile_auth.complete_login(payload[6:], user):
                await update.message.reply_text("✅ Talaba AI ilovasiga kirish tasdiqlandi. Ilovaga qayting.")
                logger.info("📱 Mobile login tasdiqlandi: user_id=%s", user.id)
            else:
                await update.message.reply_text(
                    "⚠️ Kirish havolasi eskirgan yoki allaqachon ishlatilgan. "
                    "Ilovada «Telegramda kirish» tugmasini qaytadan bosing."
                )
        except Exception as e:
            logger.warning("📱 Mobile login payload xatosi: %s", e, exc_info=True)

    # Har bir Telegram akkaunti faqat birinchi /start'da ro'yxatdan o'tadi.
    # Referral payload: ref_<telegram_user_id>; login payload yuqorida alohida qayta ishlanadi.
    start_result = None
    if user:
        referrer_id = None
        if payload.startswith("ref_"):
            try:
                candidate = int(payload[4:])
                if candidate > 0 and candidate != user.id:
                    referrer_id = candidate
            except (TypeError, ValueError):
                referrer_id = None
        try:
            previously_seen = user.id in storage.get_all_users()
            start_result = wallet.register_start(user.id, referrer_id=referrer_id, previously_seen=previously_seen)
        except Exception:
            logger.exception("Foydalanuvchini ro'yxatdan o'tkazish/referral xatosi: %s", user.id)
        # /start yangi foydalanuvchi bo'lsa ham profil ro'yxatiga qo'shiladi;
        # lekin bu yozuv welcome/referral qarori qabul qilingandan keyin qilinadi.
        try:
            storage.record_user(user.id, getattr(user, "username", "") or "",
                                getattr(user, "first_name", "") or "",
                                getattr(user, "last_name", "") or "")
        except Exception:
            logger.debug("/start foydalanuvchi profilini saqlashda xato", exc_info=True)

    # 🧱 Quoridor zaxira yo'li: inline xabardagi "Bot orqali ochish" tugmasi /start qg_<xona> yuboradi.
    # Bu yerda Mini App'ni web_app tugmasi bilan ochamiz — BotFather'da Main Mini App sozlanmagan bo'lsa ham ishlaydi.
    if payload.startswith("qg_") and user:
        opened = await _send_quoridor_open_button(update, payload[3:])
        if opened and not (start_result and start_result.get("welcome_bonus")):
            return

    context.user_data.clear()
    await update.message.reply_text(
        "🤖 *Talaba AI botiga xush kelibsiz!*\n\n"
        "💬 *UNIVERSAL CHAT* — istalgan savolingizga javob beradi va agar boshqa "
        "funksiya kerak bo'lsa (kurs ishi, tarjima va h.k.), o'zi shu funksiyaga "
        "murojat qilib, javobni sizga qaytaradi.\n\n"
        "🎙 Ovozli xabar yuborsangiz ham tushunaman va javob beraman.\n\n"
        "Yoki quyidagi funksiyalardan birini tanlang:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(),
    )
    if start_result and start_result.get("welcome_bonus"):
        credited = int(start_result["welcome_bonus"])
        await update.message.reply_text(
            f"🎁 Boshlang‘ich bonus hisobingizga qo‘shildi: *{credited:,} so‘m*!\n"
            "💰 Balansingizni ‘💰 Balansim’ tugmasidan ko‘rishingiz mumkin.",
            parse_mode=ParseMode.MARKDOWN,
        )

async def universal_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"💬 'UNIVERSAL CHAT' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        await query.edit_message_text(
            "💬 *UNIVERSAL CHAT* faollashtirildi.\n\n"
            "Menga istalgan savolni yozing. Agar boshqa funksiya kerak bo'lsa "
            "(masalan: \"10 betlik sun'iy intellekt haqida kurs ishi yoz\"), "
            "buni ham shu yerga yozing — kerakli funksiyadan o'zim foydalanaman.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"💬 Universal chat tanlashda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise


def _cancel_everything(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Foydalanuvchining BARCHA jarayonlarini to'xtatadi: ishlayotgan vazifalar,
    kutish holati, band qilingan pul va suhbat ma'lumotlari. To'xtatilgan vazifalar soni qaytadi."""
    import cancel_registry
    import pending_input

    chat = update.effective_chat
    user = update.effective_user
    chat_id = chat.id if chat else None
    user_id = user.id if user else None

    # 💰 Band qilingan (reserved) pul bo'lsa — DARHOL ozod qilinadi.
    reservation = context.user_data.get("_reservation")
    if reservation:
        try:
            wallet.release_reservation(reservation["reservation_id"], reason="user_cancelled")
        except Exception as exc:
            logger.warning("❌ Pulni ozod qilishda xato: %s: %s", type(exc).__name__, exc)

    # ⏹ Ishlayotgan vazifalarni (AI, PDF, video...) to'xtatish — shu bo'lmasa jarayon davom etaveradi.
    stopped = cancel_registry.cancel_user_jobs(chat_id, user_id)
    if chat_id is not None and user_id is not None:
        pending_input.clear_pending(chat_id, user_id)
    context.user_data.clear()
    return stopped


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _cancel_everything(update, context)
    await update.message.reply_text("❌ Bekor qilindi.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


async def cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har qanday xabar ostidagi '❌ Bekor qilish' tugmasi (callback_data="cancel:all")."""
    query = update.callback_query
    _cancel_everything(update, context)
    try:
        await query.answer("❌ Bekor qilindi")
    except Exception:
        pass
    try:
        await query.edit_message_text("❌ Bekor qilindi.", reply_markup=main_menu_keyboard())
    except Exception:
        # Xabar tahrirlab bo'lmasa (rasm/hujjat, eskirgan va h.k.) — yangi xabar yuboramiz.
        try:
            await query.message.reply_text("❌ Bekor qilindi.", reply_markup=main_menu_keyboard())
        except Exception as exc:
            logger.info("❌ Bekor qilish xabarini yuborib bo'lmadi: %s: %s", type(exc).__name__, exc)
    return ConversationHandler.END


async def group_enable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text(
            "ℹ️ Bu buyruq faqat guruhlarda ishlatiladi. Shaxsiy chatda Universal chat "
            "har doim faol."
        )
        return
    storage.set_group_active(chat.id, True)
    await update.message.reply_text(
        "✅ *Universal chat* ushbu guruhda yoqildi (bu holat doimiy saqlanadi — "
        "bot qayta ishga tushirilsa ham FAOL qoladi).\n\n"
        "Menga murojaat qilish uchun xabaringizda *dase* so'zini ishlating "
        "(masalan: \"dase bugun qanaqa kun\") yoki mening xabarimga reply qiling.\n\n"
        "⚠️ Eslatma: guruhda barcha xabarlarni ko'rishim uchun @BotFather orqali "
        "\"Group Privacy\" sozlamasini o'chirib qo'yish kerak bo'lishi mumkin.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def group_disable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("ℹ️ Bu buyruq faqat guruhlarda ishlatiladi.")
        return
    storage.set_group_active(chat.id, False)
    await update.message.reply_text(
        "❌ Universal chat ushbu guruhda o'chirildi.\n"
        "Bu holat doimiy saqlanadi — bot qayta deploy qilinsa ham shu guruhda "
        "o'chirilgan holicha qoladi, /yoqish bilan qayta yoqmaguningizcha."
    )


async def my_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot biror guruhga QO'SHILGANDA (yoki admin qilinganda) chaqiriladi.
    Standart holat allaqachon FAOL (storage.is_group_active default=True),
    shuning uchun bu yerda alohida yozish shart emas — faqat guruhga
    xabar berib qo'yamiz, shu bilan birga agar bu guruh AVVAL /ochirish
    bilan o'chirilgan bo'lsa (masalan bot chiqarilib qayta qo'shilgan bo'lsa),
    o'sha "o'chirilgan" holat ATAYLAB o'zgartirilmaydi — chunki guruh buni
    ongli ravishda o'chirgan bo'lishi mumkin."""
    result = update.my_chat_member
    if not result:
        return
    chat = result.chat
    if chat.type not in ("group", "supergroup"):
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    bot_just_added = old_status in ("left", "kicked") and new_status in ("member", "administrator")
    if not bot_just_added:
        return

    active = storage.is_group_active(chat.id)
    status_label = "FAOL" if active else "O'CHIRILGAN"
    logger.info(f"👥 Bot guruhga qo'shildi: chat_id={chat.id}, chat_title='{chat.title}', joriy holat={status_label}.")

    if active:
        try:
            await context.bot.send_message(
                chat.id,
                "👋 Salom! Men shu guruhda *Universal chat* rejimida FAOL holatda ishga tushdim.\n\n"
                "Menga murojaat qilish uchun xabaringizda *dase* so'zini ishlating "
                "(masalan: \"dase bugun qanaqa kun\") yoki mening xabarimga reply qiling.\n\n"
                "Agar meni shu guruhda o'chirib qo'ymoqchi bo'lsangiz — /ochirish, "
                "qayta yoqish uchun — /yoqish buyrug'ini yuboring.\n\n"
                "⚠️ Eslatma: barcha xabarlarni ko'rishim uchun @BotFather orqali "
                "\"Group Privacy\" sozlamasini o'chirib qo'yish kerak bo'lishi mumkin.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.warning(f"Guruhga xush kelibsiz xabarini yuborib bo'lmadi: {e}")


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("🏠 Bosh menyu:", reply_markup=main_menu_keyboard())
    return ConversationHandler.END
