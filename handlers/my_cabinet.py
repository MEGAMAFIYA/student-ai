"""
👤 /my — "Mening kabinetim": foydalanuvchining shaxsiy menyusi.

Bo'limlar:
  - 🖼 Rasim yuklash — GitHub'dagi shaxsiy papkaga rasm yuklaydi (FAQAT
    Pro obunachilar uchun — bu rasmlar keyin /pro tabriknomasida
    slайд-shou sifatida ishlatiladi, qarang: handlers/pro_tabrik.py).
  - 💎 Pro obuna — 1 oylik Pro obunaga yozilish so'rovi. To'lov QO'LDA
    (karta raqamiga o'tkazma) amalga oshiriladi, so'ng ADMIN buni DM
    orqali KELGAN xabardagi tugmalar bilan YOKI /developer > 💎 Pro
    obunalar bo'limi orqali tasdiqlaydi/rad etadi (ikkalasi ham BIR XIL
    `prosub_decision_callback`ni chaqiradi).
  - 📇 Tabriknomam — ANIQ spetsifikatsiya berilmagani uchun HOZIRCHA
    "tez orada" stub sifatida qoldirilgan (keyinchalik to'ldiriladi).

Rasm yuklash oqimi: tugma bosilganda `context.user_data["awaiting_kabinet_photo"]`
bayrog'i o'rnatiladi, undan keyin foydalanuvchi yuborgan BIRINCHI rasm
`on_kabinet_photo`da ushlanadi (bot.py'da bu handler ALOHIDA guruhda
(group=1) ro'yxatdan o'tkazilgan — shu orqali boshqa conversation'lardagi
(masalan "Suratlarni PDF qilish") rasm handlerlariga XALAQIT bermaydi).
"""

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
import github_storage
import pro_subscription

logger = logging.getLogger(__name__)


# ============================================================
# Asosiy menyu
# ============================================================

def _main_menu_text(user_id: int) -> str:
    is_pro = pro_subscription.is_pro(user_id)
    status = "💎 Faol (Pro)" if is_pro else "— Oddiy (Pro emas)"
    return f"👤 *Mening kabinetim*\n\nHolat: {status}\n\nBo'limni tanlang:"


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Rasim yuklash", callback_data="mycab:upload")],
        [InlineKeyboardButton("💎 Pro obuna", callback_data="mycab:prosub")],
        [InlineKeyboardButton("📇 Tabriknomam", callback_data="mycab:cards")],
    ])


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="mycab:back")]])


async def my_cabinet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, override_text: str | None = None):
    """`override_text` — /rasim, /tabrik va boshqalar bilan bir xil
    signatura uchun qabul qilinadi (mention_dispatch orqali chaqirilganda),
    lekin /my uchun ishlatilmaydi."""
    if not update.message:
        return
    user_id = update.effective_user.id
    await update.message.reply_text(
        _main_menu_text(user_id), parse_mode=ParseMode.MARKDOWN, reply_markup=_main_menu_keyboard(),
    )


# ============================================================
# Pro obuna — to'lov ko'rsatmasi
# ============================================================

def _prosub_prompt_text() -> str:
    card = config.PAYMENT_CARD_NUMBER or "(karta raqami hali sozlanmagan — administratorga xabar bering)"
    holder = f" ({config.PAYMENT_CARD_HOLDER})" if config.PAYMENT_CARD_HOLDER else ""
    price = f"{config.PRO_SUBSCRIPTION_PRICE_SUM:,}".replace(",", " ")
    return (
        "💎 *Pro obuna*\n\n"
        "Pro obuna quyidagilarni beradi:\n"
        "• 🖼 Shaxsiy rasmlaringizni saqlash\n"
        "• 💎 /pro — shaxsiy rasmli tabriknoma yuborish\n\n"
        f"💳 Karta: `{card}`{holder}\n"
        f"💰 Narx: {price} so'm / {config.PRO_SUBSCRIPTION_DAYS} kun\n\n"
        "To'lovni amalga oshirgach, pastdagi tugmani bosing — administrator "
        "tasdiqlagach obunangiz avtomatik faollashadi."
    )


def _prosub_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ To'ladim, tasdiqlashga yuborish", callback_data="mycab:pro_confirm")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="mycab:back")],
    ])


def prosub_admin_keyboard(req_id: str) -> InlineKeyboardMarkup:
    """Admin DM xabari VA /developer > 💎 Pro obunalar bo'limi — IKKALASI
    HAM shu tugmalarni ishlatadi (bir xil callback_data format)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"prosub:approve:{req_id}"),
        InlineKeyboardButton("❌ Rad etish", callback_data=f"prosub:reject:{req_id}"),
    ]])


async def _notify_admins_new_request(context: ContextTypes.DEFAULT_TYPE, user_id: int, req_id: str) -> None:
    text = f"💎 *Yangi Pro obuna so'rovi!*\n\nFoydalanuvchi ID: `{user_id}`\nSo'rov ID: `{req_id}`"
    keyboard = prosub_admin_keyboard(req_id)
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        except Exception as e:
            logger.warning(f"💎 Adminga Pro obuna so'rovi xabari yuborilmadi (admin_id={admin_id}): {type(e).__name__}: {e}")


# ============================================================
# /my menyusi callback'lari
# ============================================================

async def my_cabinet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    action = query.data.split(":", 1)[1]

    if action == "back":
        await query.answer()
        await query.edit_message_text(_main_menu_text(user_id), parse_mode=ParseMode.MARKDOWN, reply_markup=_main_menu_keyboard())
        return

    if action == "upload":
        if not github_storage.is_configured():
            await query.answer()
            await query.edit_message_text(
                "❌ Bu funksiya hozircha sozlanmagan (server tomonida GITHUB_TOKEN/GITHUB_REPO kerak). "
                "Administratorga xabar bering.",
                reply_markup=_back_keyboard(),
            )
            return
        if not pro_subscription.is_pro(user_id):
            await query.answer()
            await query.edit_message_text(_prosub_prompt_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=_prosub_prompt_keyboard())
            return
        context.user_data["awaiting_kabinet_photo"] = True
        await query.answer()
        await query.edit_message_text(
            "🖼 Endi rasm yuboring — u shaxsiy papkangizga saqlanadi va /pro tabriknomalaringizda ishlatiladi.",
            reply_markup=_back_keyboard(),
        )
        return

    if action == "prosub":
        await query.answer()
        await query.edit_message_text(_prosub_prompt_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=_prosub_prompt_keyboard())
        return

    if action == "pro_confirm":
        req_id = pro_subscription.create_request(user_id)
        await query.answer("So'rovingiz yuborildi!")
        await query.edit_message_text(
            "✅ So'rovingiz administratorga yuborildi. Tasdiqlangach, sizga xabar beramiz.",
            reply_markup=_back_keyboard(),
        )
        await _notify_admins_new_request(context, user_id, req_id)
        return

    if action == "cards":
        await query.answer()
        await query.edit_message_text(
            "📇 Bu bo'lim tez orada qo'shiladi.", reply_markup=_back_keyboard(),
        )
        return


# ============================================================
# Admin: tasdiqlash / rad etish (DM tugmasi VA /developer'dan BIR XIL)
# ============================================================

async def prosub_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id not in config.ADMIN_IDS:
        await query.answer("⚠️ Bu tugma faqat adminlar uchun.", show_alert=True)
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("⚠️ Noto'g'ri so'rov.", show_alert=True)
        return
    action, req_id = parts[1], parts[2]

    if action == "approve":
        approved_user_id = pro_subscription.approve_request(req_id)
        if approved_user_id is None:
            await query.answer("⚠️ Bu so'rov allaqachon ko'rib chiqilgan yoki topilmadi.", show_alert=True)
            return
        await query.answer("✅ Tasdiqlandi.")
        try:
            await query.edit_message_text(f"✅ Pro obuna TASDIQLANDI: user_id=`{approved_user_id}`", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
        try:
            await context.bot.send_message(
                approved_user_id,
                f"🎉 Tabriklaymiz! Pro obunangiz {config.PRO_SUBSCRIPTION_DAYS} kunga faollashtirildi.\n"
                "Endi /my orqali rasm yuklashingiz va /pro bilan shaxsiy rasmli tabriknoma yuborishingiz mumkin.",
            )
        except Exception as e:
            logger.warning(f"💎 Foydalanuvchiga tasdiqlash xabari yuborilmadi (user_id={approved_user_id}): {e}")
        logger.info(f"💎 Admin Pro obunani tasdiqladi: admin_id={update.effective_user.id}, user_id={approved_user_id}.")

    elif action == "reject":
        rejected_user_id = pro_subscription.reject_request(req_id)
        if rejected_user_id is None:
            await query.answer("⚠️ Bu so'rov allaqachon ko'rib chiqilgan yoki topilmadi.", show_alert=True)
            return
        await query.answer("❌ Rad etildi.")
        try:
            await query.edit_message_text(f"❌ Pro obuna RAD ETILDI: user_id=`{rejected_user_id}`", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
        try:
            await context.bot.send_message(
                rejected_user_id, "❌ Pro obuna so'rovingiz rad etildi. Savol bo'lsa administratorga murojaat qiling.",
            )
        except Exception as e:
            logger.warning(f"💎 Foydalanuvchiga rad etish xabari yuborilmadi (user_id={rejected_user_id}): {e}")
        logger.info(f"💎 Admin Pro obunani rad etdi: admin_id={update.effective_user.id}, user_id={rejected_user_id}.")


# ============================================================
# Rasm yuklashni qabul qilish
# ============================================================

async def on_kabinet_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har qanday kelgan rasm shu yerga tushadi (bot.py'da ALOHIDA
    guruhda ro'yxatdan o'tkazilgan), lekin FAQAT foydalanuvchi oldin
    "🖼 Rasim yuklash"ni bosgan bo'lsa amal qiladi — aks holda darhol
    (hech narsa qilmasdan) chiqib ketadi, shunda boshqa (masalan
    "Suratlarni PDF qilish") rasm oqimlariga xalaqit bermaydi."""
    if not context.user_data.get("awaiting_kabinet_photo"):
        return
    context.user_data.pop("awaiting_kabinet_photo", None)

    if not update.message or not update.message.photo:
        return

    user_id = update.effective_user.id
    status = await update.message.reply_text("⏳ Yuklanmoqda...")

    try:
        photo = update.message.photo[-1]  # eng katta o'lchamdagi versiya
        file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await file.download_as_bytearray())
        url, error = await asyncio.to_thread(github_storage.upload_user_photo, user_id, image_bytes)
    except Exception as e:
        logger.error(f"🖼 Kabinet rasmini yuklashda kutilmagan xato (user_id={user_id}): {type(e).__name__}: {e}", exc_info=True)
        await status.edit_text(f"❌ Rasmni saqlashda kutilmagan xatolik yuz berdi.\n\nSabab: {type(e).__name__}: {e}")
        return

    if url:
        await status.edit_text("✅ Rasm muvaffaqiyatli saqlandi! Yana rasm yuklash uchun /my ni qayta bosing.")
        logger.info(f"🖼 Kabinet rasmi muvaffaqiyatli yuklandi: user_id={user_id}.")
    else:
        logger.error(f"🖼 Kabinet rasmi GitHub'ga yuklanmadi (user_id={user_id}): {error}")
        await status.edit_text(
            "❌ Rasmni saqlashda xatolik yuz berdi.\n\n"
            f"Sabab: {error}\n\n"
            "Birozdan so'ng qayta urinib ko'ring yoki administratorga shu sababni yuboring."
        )
