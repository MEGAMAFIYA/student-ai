"""
💰 Foydalanuvchi uchun ICHKI BALANS interfeysi:
- 💰 Balansim
- ➕ Balansni to'ldirish (summa -> to'lov usuli -> chek/yo'naltirish)
- 🧾 To'lovlar tarixi
- require_payment() — pullik funksiyalarni "to'lov devori" bilan o'rab
  oluvchi dekorator (bot.py'da ishlatiladi, MAVJUD handlerlar o'zgarmaydi)

Pul bilan bog'liq BARCHA haqiqiy amallar wallet.py (yadro moduli) orqali
bajariladi — bu fayl faqat Telegram interfeysi (matn/tugmalar/holatlar).

Chek rasmi/hujjati AI (vision) orqali FAQAT ma'lumot AJRATISH uchun
ishlatiladi ("amount", "transaction_id" va h.k.) — AI xulosasi hech qachon
yagona/yakuniy to'lov tasdig'i sifatida ishlatilmaydi (spetsifikatsiya
talabi). Agar bank tomonidan avtomatik tekshiruv (payment_providers.py >
KapitalbankTransactionVerifier) mavjud bo'lmasa yoki muvaffaqiyatsiz
bo'lsa, to'lov albatta manual_review (admin qo'lda tekshiruvi) holatiga
tushadi.
"""

import functools
import json
import logging
import re
from io import BytesIO

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode, ChatAction

import config
import wallet
import payment_providers
from ai_clients import ask_gemini_multimodal
from handlers.menu import main_menu_keyboard

logger = logging.getLogger(__name__)

WT_AMOUNT, WT_CUSTOM_AMOUNT, WT_METHOD, WT_RECEIPT = range(4)

_PRESET_AMOUNTS = [10000, 20000, 50000, 100000]
_MIN_TOPUP = 1000
_MAX_TOPUP = 50_000_000
_MAX_RECEIPT_FILE_SIZE = 8 * 1024 * 1024  # 8 MB
_ALLOWED_RECEIPT_DOC_MIME = {"application/pdf", "image/jpeg", "image/png", "image/webp"}

_RECEIPT_SYSTEM_PROMPT = (
    "Siz to'lov chekini (bank/paynet o'tkazmasi skrinshoti yoki fotosurati) tahlil "
    "qiluvchi yordamchisiz. Rasmda/hujjatda ko'ringan ma'lumotlarni ANIQ, "
    "hech narsa O'YLAB TOPMASDAN ajratib oling. Agar biror maydon rasmda "
    "ko'rinmasa yoki aniq bo'lmasa, uning qiymatini null qiling. "
    "FAQAT quyidagi JSON formatida javob bering, boshqa hech qanday matn "
    "yozmang (izoh, markdown belgisi ‘```’ ham kerak emas):\n"
    '{"amount": <number yoki null>, "transaction_id": "<matn yoki null>", '
    '"date": "<matn yoki null>", "time": "<matn yoki null>", '
    '"sender": "<matn yoki null>", "receiver": "<matn yoki null>", '
    '"provider": "<matn yoki null>", "confidence": <0 dan 1 gacha son>}'
)


# ============================================================
# Yordamchi funksiyalar
# ============================================================

def _fmt_sum(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " so'm"


def insufficient_balance_text(feature_name: str, required: int, available: int) -> str:
    return (
        "❌ *Balansingiz yetarli emas.*\n\n"
        f"Funksiya: {feature_name}\n"
        f"Kerak: {_fmt_sum(required)}\n"
        f"Balans: {_fmt_sum(available)}\n\n"
        "Balansni to'ldirish uchun pastdagi tugmani bosing."
    )


def insufficient_balance_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Balansni to'ldirish", callback_data="menu:wallet_topup")],
        [InlineKeyboardButton("⬅️ Bosh menyu", callback_data="menu:back")],
    ])


def _status_label(status: str) -> str:
    return {
        wallet.STATUS_PENDING: "🕐 Tekshirilmoqda...",
        wallet.STATUS_PAID: "✅ To'lov tasdiqlandi",
        wallet.STATUS_FAILED: "❌ To'lov muvaffaqiyatsiz",
        wallet.STATUS_CANCELLED: "❌ Bekor qilingan",
        wallet.STATUS_EXPIRED: "⌛ Muddati o'tgan",
        wallet.STATUS_MANUAL_REVIEW: "⚠️ To'lov qo'lda tekshirilmoqda",
        wallet.STATUS_REJECTED: "❌ To'lov rad etildi",
        wallet.STATUS_SUSPICIOUS: "⚠️ Shubhali deb belgilangan",
    }.get(status, status)


def _extract_receipt_fingerprint(file_unique_id: str, extracted: dict) -> str:
    """Chek uchun ikkilamchi (fallback) fingerprint — reference/transaction
    ID matni asosida. file_unique_id ALOHIDA index sifatida ham
    ro'yxatga olinadi (wallet.register_receipt_fingerprint ikkalasi bilan
    ham chaqiriladi), shu orqali bir xil FAYL yoki bir xil TRANZAKSIYA
    RAQAMI qayta yuborilsa — ikkisi ham ushlanadi."""
    ref = (extracted or {}).get("transaction_id")
    if ref:
        return f"ref:{str(ref).strip().lower()}"
    return ""


async def _extract_receipt_data(image_bytes: bytes, mime_type: str) -> dict:
    text, status, detail = await ask_gemini_multimodal(
        config.VISION_AI, _RECEIPT_SYSTEM_PROMPT, image_bytes, mime_type, label="Chek (vision)"
    )
    if not text:
        logger.error(f"🧾 Chekni AI orqali o'qib bo'lmadi: status={status}, detail={detail}.")
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(json)?", "", cleaned).rsplit("```", 1)[0].strip()
    try:
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as e:
        logger.error(f"🧾 Chekdan olingan javob JSON emas: {e}. Javob (qisqartirilgan): {cleaned[:300]}")
        return {}


async def _notify_admins_new_receipt(context: ContextTypes.DEFAULT_TYPE, payment: dict, user) -> None:
    if not config.ADMIN_IDS:
        return
    extracted = (payment.get("receipt") or {}).get("extracted") or {}
    text = (
        "🧾 *Yangi chek yuborildi — tekshirish kerak*\n\n"
        f"👤 User: {user.full_name if user else '—'} (@{user.username if user and user.username else '—'})\n"
        f"🆔 Telegram ID: `{payment['user_id']}`\n"
        f"💰 Summa (bot ichida): {_fmt_sum(payment['amount'])}\n"
        f"💳 Usul: {payment['method']}\n"
        f"📌 Status: {_status_label(payment['status'])}\n\n"
        f"🔍 Chekdan o'qilgan (AI, TASDIQ EMAS): {json.dumps(extracted, ensure_ascii=False)}\n\n"
        f"payment_id: `{payment['payment_id']}`\n\n"
        "/developer -> 💳 To'lovlar bo'limidan ko'rib chiqing."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"🧾 Adminni ({admin_id}) xabardor qilib bo'lmadi: {e}")


# ============================================================
# 💰 Balansim
# ============================================================

async def entry_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    balance = wallet.get_balance(user.id)
    await query.edit_message_text(
        f"💰 *Mening balansim*\n\nBalans: {_fmt_sum(balance)}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Balansni to'ldirish", callback_data="menu:wallet_topup")],
            [InlineKeyboardButton("🧾 To'lovlar tarixi", callback_data="menu:wallet_history")],
            [InlineKeyboardButton("⬅️ Bosh menyu", callback_data="menu:back")],
        ]),
    )


# ============================================================
# 🧾 To'lovlar tarixi
# ============================================================

async def entry_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    txs = wallet.get_transactions(user.id)
    if not txs:
        text = "🧾 *To'lovlar tarixi*\n\nHali hech qanday operatsiya yo'q."
    else:
        lines = ["🧾 *To'lovlar tarixi*\n"]
        for t in txs:
            date_str = t["created_at"][:10]
            sign = "✅ +" if t["amount"] > 0 else "🔻 "
            lines.append(f"{date_str}\n{sign}{_fmt_sum(abs(t['amount']))}\n{t['description']}\n")
        text = "\n".join(lines)
    await query.edit_message_text(
        text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Bosh menyu", callback_data="menu:back")]]),
    )


# ============================================================
# ➕ Balansni to'ldirish — summa
# ============================================================

def _amount_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(_PRESET_AMOUNTS), 2):
        rows.append([
            InlineKeyboardButton(_fmt_sum(a), callback_data=f"wallet:amt:{a}")
            for a in _PRESET_AMOUNTS[i:i + 2]
        ])
    rows.append([InlineKeyboardButton("✏️ Boshqa summa", callback_data="wallet:amt:custom")])
    rows.append([InlineKeyboardButton("⬅️ Bosh menyu", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


async def entry_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text(
        "➕ *Balansni to'ldirish*\n\nSummani tanlang:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=_amount_keyboard(),
    )
    return WT_AMOUNT


async def amount_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = query.data.split(":")[2]

    if value == "custom":
        await query.edit_message_text(
            "✏️ To'ldirmoqchi bo'lgan summani (so'mda, faqat raqam) yozing:\n\n"
            f"Minimal: {_fmt_sum(_MIN_TOPUP)}, maksimal: {_fmt_sum(_MAX_TOPUP)}."
        )
        return WT_CUSTOM_AMOUNT

    context.user_data["topup_amount"] = int(value)
    await query.edit_message_text(
        f"➕ Summa: {_fmt_sum(int(value))}\n\nTo'lov usulini tanlang:",
        reply_markup=_method_keyboard(),
    )
    return WT_METHOD


async def custom_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(" ", "").replace("so'm", "").replace("сум", "")
    if not raw.isdigit():
        await update.message.reply_text("⚠️ Iltimos, faqat butun son yuboring (masalan: 15000).")
        return WT_CUSTOM_AMOUNT
    amount = int(raw)
    if amount < _MIN_TOPUP or amount > _MAX_TOPUP:
        await update.message.reply_text(
            f"⚠️ Summa {_fmt_sum(_MIN_TOPUP)} dan {_fmt_sum(_MAX_TOPUP)} gacha bo'lishi kerak."
        )
        return WT_CUSTOM_AMOUNT

    context.user_data["topup_amount"] = amount
    await update.message.reply_text(
        f"➕ Summa: {_fmt_sum(amount)}\n\nTo'lov usulini tanlang:",
        reply_markup=_method_keyboard(),
    )
    return WT_METHOD


# ============================================================
# ➕ Balansni to'ldirish — usul
# ============================================================

def _method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Kapitalbank E-commerce", callback_data="wallet:method:ecommerce")],
        [InlineKeyboardButton("🟡 Bank/Paynet + chek", callback_data="wallet:method:bank")],
        [InlineKeyboardButton("🟠 Admin qo'lda tekshirishi", callback_data="wallet:method:manual")],
        [InlineKeyboardButton("⬅️ Bekor qilish", callback_data="menu:back")],
    ])


async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    method_key = query.data.split(":")[2]
    user = update.effective_user
    amount = context.user_data.get("topup_amount")
    if not amount:
        await query.edit_message_text("⚠️ Sessiya eskirgan. Iltimos, /start bilan qaytadan boshlang.")
        return ConversationHandler.END

    if method_key == "ecommerce":
        payment = wallet.create_payment(user.id, amount, provider="kapitalbank", method=wallet.METHOD_ECOMMERCE)
        provider = payment_providers.get_ecommerce_provider()
        await query.edit_message_text("⏳ To'lov sessiyasi yaratilmoqda...")
        result = await provider.create_order(payment["payment_id"], amount)
        if not result.ok:
            wallet.set_payment_status(payment["payment_id"], wallet.STATUS_CANCELLED, reason=result.error or "")
            await query.edit_message_text(
                result.user_message or "⚠️ Hozircha bu usul orqali to'lov qilib bo'lmaydi.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Boshqa usulni tanlash", callback_data="menu:wallet_topup")],
                    [InlineKeyboardButton("⬅️ Bosh menyu", callback_data="menu:back")],
                ]),
            )
            context.user_data.clear()
            return ConversationHandler.END

        await query.edit_message_text(
            f"🟢 To'lov havolasi tayyor:\n{result.payment_url}\n\n"
            "To'lovni amalga oshirgach, balansingiz avtomatik yangilanadi.",
            reply_markup=main_menu_keyboard(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    # bank yoki manual — ikkalasida ham chek so'raladi
    method = wallet.METHOD_BANK_RECEIPT if method_key == "bank" else wallet.METHOD_MANUAL_RECEIPT
    payment = wallet.create_payment(user.id, amount, provider="manual", method=method)
    context.user_data["payment_id"] = payment["payment_id"]

    requisites_lines = []
    if config.PAYMENT_CARD_NUMBER:
        requisites_lines.append(f"💳 Karta: `{config.PAYMENT_CARD_NUMBER}`")
    if config.PAYMENT_CARD_HOLDER:
        requisites_lines.append(f"👤 Egasi: {config.PAYMENT_CARD_HOLDER}")
    if config.PAYMENT_RECEIVER_NOTE:
        requisites_lines.append(config.PAYMENT_RECEIVER_NOTE)
    requisites_text = "\n".join(requisites_lines) if requisites_lines else (
        "⚠️ To'lov rekvizitlari hali administratordan sozlanmagan. "
        "Iltimos, admin bilan bog'laning."
    )

    note = (
        "\n\nℹ️ Chekingiz avval avtomatik tekshirishga urinib ko'riladi, natija "
        "bo'lmasa admin qo'lda tekshiradi."
        if method_key == "bank" else
        "\n\nℹ️ Chekingiz to'g'ridan-to'g'ri admin tomonidan qo'lda tekshiriladi."
    )

    await query.edit_message_text(
        f"➕ Summa: {_fmt_sum(amount)}\n\n{requisites_text}{note}\n\n"
        "To'lovni amalga oshirgach, CHEK RASMINI (yoki hujjat/PDF sifatida) shu yerga yuboring.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return WT_RECEIPT


# ============================================================
# 🧾 Chekni qabul qilish
# ============================================================

async def receive_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment_id = context.user_data.get("payment_id")
    if not payment_id:
        await update.message.reply_text("⚠️ Sessiya eskirgan. Iltimos, /start bilan qaytadan boshlang.")
        return ConversationHandler.END

    payment = wallet.get_payment(payment_id)
    if not payment:
        await update.message.reply_text("⚠️ To'lov topilmadi. Iltimos, qaytadan boshlang.")
        context.user_data.clear()
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    user = update.effective_user
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)

    file_id = None
    file_unique_id = None
    mime_type = "image/jpeg"

    if update.message.photo:
        photo = update.message.photo[-1]
        file_id, file_unique_id = photo.file_id, photo.file_unique_id
        if photo.file_size and photo.file_size > _MAX_RECEIPT_FILE_SIZE:
            await update.message.reply_text("⚠️ Rasm hajmi juda katta. Iltimos, kichikroq rasm yuboring.")
            return WT_RECEIPT
    elif update.message.document:
        doc = update.message.document
        if doc.mime_type not in _ALLOWED_RECEIPT_DOC_MIME:
            await update.message.reply_text(
                "⚠️ Faqat rasm (JPG/PNG/WEBP) yoki PDF fayl qabul qilinadi."
            )
            return WT_RECEIPT
        if doc.file_size and doc.file_size > _MAX_RECEIPT_FILE_SIZE:
            await update.message.reply_text("⚠️ Fayl hajmi juda katta (maksimal 8 MB).")
            return WT_RECEIPT
        file_id, file_unique_id = doc.file_id, doc.file_unique_id
        mime_type = doc.mime_type
    else:
        await update.message.reply_text("⚠️ Iltimos, chek RASMINI yoki PDF faylini yuboring.")
        return WT_RECEIPT

    status_msg = await update.message.reply_text("⏳ Chek tahlil qilinmoqda...")

    tg_file = await context.bot.get_file(file_id)
    bio = BytesIO()
    await tg_file.download_to_memory(out=bio)
    file_bytes = bio.getvalue()

    extracted = {}
    if mime_type != "application/pdf":
        extracted = await _extract_receipt_data(file_bytes, mime_type)

    # --- Duplicate himoyasi: xuddi shu fayl YOKI xuddi shu reference/tranzaksiya ---
    try:
        wallet.register_receipt_fingerprint(payment_id, f"file:{file_unique_id}")
    except wallet.DuplicateReceiptError as e:
        await status_msg.edit_text(
            "❌ Bu to'lov (chek) avval yuborilgan va allaqachon ko'rib chiqilgan.\n"
            f"Payment ID: `{e.existing_payment_id}`", parse_mode=ParseMode.MARKDOWN,
        )
        wallet.set_payment_status(payment_id, wallet.STATUS_REJECTED, reason="duplicate_receipt_file")
        context.user_data.clear()
        return ConversationHandler.END

    ref_fp = _extract_receipt_fingerprint(file_unique_id, extracted)
    if ref_fp:
        try:
            wallet.register_receipt_fingerprint(payment_id, ref_fp)
        except wallet.DuplicateReceiptError as e:
            await status_msg.edit_text(
                "❌ Bu to'lov avval ishlatilgan.\n"
                f"Payment ID: `{e.existing_payment_id}`", parse_mode=ParseMode.MARKDOWN,
            )
            wallet.set_payment_status(payment_id, wallet.STATUS_REJECTED, reason="duplicate_transaction_ref")
            context.user_data.clear()
            return ConversationHandler.END

    confidence = extracted.get("confidence") if isinstance(extracted, dict) else None
    wallet.attach_receipt(payment_id, file_id, file_unique_id, extracted=extracted, confidence=confidence)

    # --- 2-usul (bank): avtomatik tekshirishga URINIB ko'riladi ---
    verified_automatically = False
    if payment["method"] == wallet.METHOD_BANK_RECEIPT:
        verifier = payment_providers.get_bank_verifier()
        result = await verifier.verify_transaction(extracted, expected_amount=payment["amount"])
        if result.ok:
            wallet.confirm_payment(payment_id, actor_id="kapitalbank_verifier", source="bank_api_verified")
            verified_automatically = True

    if verified_automatically:
        await status_msg.edit_text(
            f"✅ To'lov tasdiqlandi! Balansingiz {_fmt_sum(payment['amount'])} ga oshdi.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        wallet.mark_manual_review(payment_id, reason="Avtomatik tekshiruv mavjud emas yoki muvaffaqiyatsiz.")
        await status_msg.edit_text(
            "⚠️ To'lov qo'lda tekshirilmoqda. Admin tasdiqlagach, balansingiz avtomatik "
            "yangilanadi va sizga xabar beriladi.",
            reply_markup=main_menu_keyboard(),
        )
        await _notify_admins_new_receipt(context, wallet.get_payment(payment_id), user)

    context.user_data.clear()
    return ConversationHandler.END


async def notify_user_payment_decision(bot, payment: dict, approved: bool, reason: str = "") -> None:
    """Admin panelidan (handlers/payment_admin.py) to'lov tasdiqlangan/rad
    etilgandan keyin foydalanuvchiga xabar berish uchun chaqiriladi."""
    try:
        if approved:
            text = (
                f"✅ To'lovingiz tasdiqlandi!\nBalansingiz {_fmt_sum(payment['amount'])} ga oshdi."
            )
        else:
            text = "❌ To'lovingiz rad etildi." + (f"\nSabab: {reason}" if reason else "")
        await bot.send_message(payment["user_id"], text)
    except Exception as e:
        logger.warning(f"🧾 Foydalanuvchiga ({payment['user_id']}) to'lov natijasi haqida xabar berib bo'lmadi: {e}")


# ============================================================
# 🔒 "To'lov devori" dekoratori — bot.py'da har bir pullik funksiyaning
# ENTRY nuqtasini (menyu tugmasi bosilgan payt) o'rab oladi.
# ============================================================
# MUHIM ARXITEKTURA QARORI: to'lov aynan shu yerda — funksiya "ISHGA
# TUSHIRILGANDA" (foydalanuvchi menyudan tugmani bosgan zahoti, HALI
# hech qanday ma'lumot (mavzu, sahifa soni va h.k.) so'ralmasdan turib)
# yechiladi. Bu spetsifikatsiyadagi "Foydalanuvchi pullik funksiyani
# ishga tushirganda" iborasiga aynan mos keladi va BARCHA funksiyalar
# uchun BITTA joyda (bot.py) qo'llaniladi — shuning uchun har bir
# alohida handler faylini (course_work.py, essay.py va h.k.) o'zgartirish
# SHART emas, mavjud kod BUTUNLAY saqlanib qoladi.
#
# Race condition himoyasi: wallet.charge_for_feature() ICHKARIDA
# threading.Lock bilan butunlay atomik — shuning uchun bitta foydalanuvchi
# bir tugmani juda tez-tez bossa ham (Telegram ba'zan bir nechta bosishni
# deyarli bir vaqtda yuborishi mumkin), balans FAQAT BIR MARTA yechiladi.
def require_payment(feature_id: str):
    """Dekorator: `handlers/xxx.py`dagi asl `entry()` funksiyasini
    o'zgartirmasdan, uni to'lov tekshiruvi bilan o'raydi. Ishlatilishi:
    `CallbackQueryHandler(require_payment("course_work")(course_work.entry), ...)`
    — bot.py'dagi conv builder funksiyalarida."""
    def decorator(handler_func):
        @functools.wraps(handler_func)
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            query = update.callback_query

            result = wallet.charge_for_feature(user.id, feature_id)

            if result.reason == "disabled":
                if query:
                    await query.answer("🚫 Bu funksiya hozircha o'chirilgan.", show_alert=True)
                return ConversationHandler.END

            if result.reason == "insufficient":
                feature = wallet.get_feature(feature_id)
                name = feature["name"] if feature else feature_id
                if query:
                    await query.answer()
                    await query.edit_message_text(
                        insufficient_balance_text(name, result.price, result.balance),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=insufficient_balance_keyboard(),
                    )
                return ConversationHandler.END

            # "free" yoki "charged" — funksiya haqiqatan ishga tushadi
            if result.reason == "charged":
                logger.info(
                    f"💳 Pullik funksiya uchun to'lov YECHILDI: feature={feature_id}, "
                    f"user_id={user.id}, narx={result.price}, yangi balans={result.balance}."
                )
            return await handler_func(update, context)

        return wrapped
    return decorator
