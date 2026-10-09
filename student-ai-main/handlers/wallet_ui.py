"""
💰 Foydalanuvchi uchun ICHKI BALANS interfeysi:
- 💰 Balansim
- ➕ Balansni to'ldirish (summa -> to'lov usuli -> chek yuborish)
- 🧾 To'lovlar tarixi
- require_payment() — pullik funksiyalarni "to'lov devori" bilan o'rab
  oluvchi dekorator (bot.py'da ishlatiladi, MAVJUD handlerlar o'zgarmaydi)

Pul bilan bog'liq BARCHA haqiqiy amallar wallet.py (yadro moduli) orqali
bajariladi — bu fayl faqat Telegram interfeysi (matn/tugmalar/holatlar).

🧾 CHEKNI TEKSHIRISH OQIMI (spetsifikatsiya, "🧾 Bank cheki" usuli):
1. Foydalanuvchi chek rasmini yuboradi -> AI (vision) undan matnli
   ma'lumot AJRATIB oladi (karta raqami, qabul qiluvchi ismi, summa).
2. `receipt_check.verify_receipt()` shu ma'lumotni bizning rekvizitlarimiz
   (config.PAYMENT_CARD_NUMBER / PAYMENT_CARD_HOLDER) va foydalanuvchi
   tanlagan summa bilan SOF KOD orqali solishtiradi (AI xulosasi hech
   qachon yakuniy tasdiq sifatida ishlatilmaydi — u faqat "o'qish" uchun).
3a. Hammasi (karta + ism + summa + ishonchlilik) TO'G'RI bo'lsa ->
    to'lov `auto_hold` holatiga o'tadi, adminga xabar boradi (✅/❌/⚠️
    tugmalari bilan). Admin `config.PAYMENT_AUTO_CONFIRM_SECONDS` (standart
    1 soat) ichida javob bermasa, `handlers/payment_auto.py` fon vazifasi
    to'lovni AVTOMATIK qabul qiladi (`wallet.auto_confirm_payment`).
    Admin shu muddatdan OLDIN "✅ Tasdiqlash" bossa — pul DARHOL tushadi.
3b. Biror narsa (karta/ism/summa/ishonchlilik) MOS kelmasa -> to'lov
    to'g'ridan-to'g'ri `manual_review`ga tushadi (avtomatik qabul YO'Q,
    faqat admin qarori bilan tasdiqlanadi).
4. Bot avtomatik qabul qilgandan KEYIN ham admin to'lovni SOXTA deb
   topsa, "🚫 Soxta deb qaytarish" tugmasi orqali `wallet.revoke_payment()`
   chaqiriladi — summa foydalanuvchi balansidan AYRILADI (agar u pulni
   allaqachon ishlatgan bo'lsa, balans MANFIY — qarz — bo'lib qoladi).

"🟠 Admin qo'lda tekshiradi" usulida bot tekshiruvi UMUMAN o'tkazilmaydi —
chek to'g'ridan-to'g'ri manual_review'ga tushadi (foydalanuvchi ongli
ravishda avtomatikani xohlamagan holat uchun).
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
import receipt_check
from ai_clients import ask_gemini_multimodal
from handlers.menu import main_menu_keyboard
from handlers import payment_notify
from handlers import payment_auto

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
    "'receiver_card' — QABUL QILUVCHI (pul yuborilgan) kartaning ko'ringan "
    "raqami (masalan \"8600 12** **** 3456\" — yashirilgan qismi bo'lsa "
    "ham xuddi rasmda ko'ringanidek yozing, o'ylab to'ldirmang). "
    "'receiver' — QABUL QILUVCHI (pul yuborilgan tomon) ismi (jo'natuvchi "
    "EMAS). "
    "FAQAT quyidagi JSON formatida javob bering, boshqa hech qanday matn "
    "yozmang (izoh, markdown belgisi ‘```’ ham kerak emas):\n"
    '{"amount": <number yoki null>, "transaction_id": "<matn yoki null>", '
    '"date": "<matn yoki null>", "time": "<matn yoki null>", '
    '"sender": "<matn yoki null>", "receiver": "<matn yoki null>", '
    '"receiver_card": "<matn yoki null>", "provider": "<matn yoki null>", '
    '"confidence": <0 dan 1 gacha son>}'
)


# ============================================================
# Yordamchi funksiyalar
# ============================================================

def _fmt_sum(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " so'm"


def _fmt_duration(seconds: int) -> str:
    seconds = int(seconds)
    if seconds % 3600 == 0:
        h = seconds // 3600
        return f"{h} soat" if h != 1 else "1 soat"
    if seconds % 60 == 0:
        return f"{seconds // 60} daqiqa"
    return f"{seconds} soniya"


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
        wallet.STATUS_AUTO_HOLD: "🤖 Bot tekshirdi — admin javobi kutilmoqda",
        wallet.STATUS_REVOKED: "🚫 Soxta deb qaytarildi",
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


async def _notify_admins_new_receipt(context: ContextTypes.DEFAULT_TYPE, payment: dict, user, file_kind=None) -> None:
    """Admin(lar)ga: "@user hisobini X ga to'ldirdi, tasdiqlaysizmi?" +
    ✅/❌ tugmalari + chek fayli (handlers/payment_notify.py)."""
    await payment_notify.notify_admins_new_payment(context.bot, payment, user=user, file_kind=file_kind)


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
        [InlineKeyboardButton("🧾 Chek yuborish (bot avval tekshiradi)", callback_data="wallet:method:bank")],
        [InlineKeyboardButton("🟠 To'g'ridan admin tekshiradi", callback_data="wallet:method:manual")],
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

    # bank (bot avval tekshiradi) yoki manual (to'g'ridan admin) — ikkalasida ham chek so'raladi
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
        "\n\nℹ️ Chekingizni bot avval o'zi tekshiradi (karta raqami, ism, summa). "
        "Hammasi to'g'ri bo'lsa, adminga xabar boradi — admin tasdiqlasa DARHOL, "
        f"tasdiqlamasa {_fmt_duration(config.PAYMENT_AUTO_CONFIRM_SECONDS)}dan keyin "
        "balansingiz AVTOMATIK yangilanadi."
        if method_key == "bank" else
        "\n\nℹ️ Chekingiz to'g'ridan-to'g'ri admin tomonidan qo'lda tekshiriladi "
        "(avtomatik qabul qilinmaydi)."
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

    # --- "🧾 Chek yuborish" usuli: BOT o'zi tekshiradi (karta/ism/summa) ---
    bot_verified = False
    if payment["method"] == wallet.METHOD_BANK_RECEIPT:
        check = receipt_check.verify_receipt(
            extracted, expected_amount=payment["amount"],
            card_number=config.PAYMENT_CARD_NUMBER, card_holder=config.PAYMENT_CARD_HOLDER,
            min_confidence=config.PAYMENT_MIN_CONFIDENCE,
        )
        if check.ok:
            due_iso = wallet.mark_auto_hold(
                payment_id, delay_seconds=config.PAYMENT_AUTO_CONFIRM_SECONDS, bot_check=check.to_dict(),
            )
            if due_iso:
                payment_auto.schedule_auto_confirm(context.application, payment_id, due_iso)
            bot_verified = True

    if bot_verified:
        await status_msg.edit_text(
            "🤖 Bot chekni tekshirdi: karta raqami, ism va summa TO'G'RI.\n\n"
            "Admin tasdiqlasa DARHOL, tasdiqlamasa "
            f"{_fmt_duration(config.PAYMENT_AUTO_CONFIRM_SECONDS)}dan keyin balansingiz "
            "AVTOMATIK yangilanadi.",
            reply_markup=main_menu_keyboard(),
        )
        await _notify_admins_new_receipt(
            context, wallet.get_payment(payment_id), user,
            file_kind="photo" if update.message.photo else "document",
        )
    else:
        reason = "Admin qo'lda tekshiradi (foydalanuvchi shu usulni tanladi)."
        if payment["method"] == wallet.METHOD_BANK_RECEIPT:
            reason = "; ".join(check.notes) or "Bot tekshiruvidan o'tmadi."
        wallet.mark_manual_review(payment_id, reason=reason)
        await status_msg.edit_text(
            "⚠️ To'lov qo'lda tekshirilmoqda. Admin tasdiqlagach, balansingiz avtomatik "
            "yangilanadi va sizga xabar beriladi.",
            reply_markup=main_menu_keyboard(),
        )
        await _notify_admins_new_receipt(
            context, wallet.get_payment(payment_id), user,
            file_kind="photo" if update.message.photo else "document",
        )

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


async def notify_user_payment_auto_confirmed(bot, payment: dict) -> None:
    """Bot muddat (1 soat) tugagach to'lovni O'ZI qabul qilganda foydalanuvchiga xabar."""
    try:
        await bot.send_message(
            payment["user_id"],
            f"✅ To'lovingiz avtomatik tasdiqlandi!\nBalansingiz {_fmt_sum(payment['amount'])} ga oshdi.",
        )
    except Exception as e:
        logger.warning(f"🧾 Foydalanuvchiga ({payment['user_id']}) avtomatik tasdiq haqida xabar berib bo'lmadi: {e}")


async def notify_user_payment_revoked(bot, payment: dict, new_balance: int, reason: str = "") -> None:
    """To'lov admin tomonidan SOXTA deb topilib qaytarilganda (revoke_payment)
    foydalanuvchiga xabar. Balans manfiy (qarz) bo'lib qolgan bo'lsa alohida ogohlantiradi."""
    try:
        text = (
            f"🚫 To'lovingiz SOXTA deb topilib bekor qilindi.\n"
            f"Balansingizdan {_fmt_sum(payment['amount'])} ayrildi."
        )
        if reason:
            text += f"\nSabab: {reason}"
        if new_balance < 0:
            text += (
                f"\n\n⚠️ Joriy balansingiz: -{_fmt_sum(-new_balance)}\n"
                "Siz bu pulni allaqachon ishlatib bo'lgansiz — balansingiz QARZGA kirdi. "
                "Keyingi to'ldirishlaringiz avval shu qarzni yopadi."
            )
        else:
            text += f"\n\n💰 Joriy balansingiz: {_fmt_sum(new_balance)}"
        await bot.send_message(payment["user_id"], text)
    except Exception as e:
        logger.warning(f"🧾 Foydalanuvchiga ({payment['user_id']}) qaytarish haqida xabar berib bo'lmadi: {e}")


# ============================================================
# 🔒 "To'lov devori" dekoratori — bot.py'da har bir pullik funksiyaning
# ENTRY nuqtasini (menyu tugmasi bosilgan payt) o'rab oladi.
# ============================================================
# MUHIM ARXITEKTURA QARORI (RESERVATION/HOLD tizimi): endi funksiya
# "ISHGA TUSHIRILGANDA" pul DARHOL yechilmaydi — buning o'rniga
# wallet.reserve_for_feature() orqali summa faqat "band qilinadi"
# (status='reserved'). Balansdan HAQIQIY yechish faqat xizmat/AI
# MUVAFFAQIYATLI yakunlangandan keyin `finalize_success()` chaqirilganda
# sodir bo'ladi. Xizmat xato qilsa yoki bekor qilinsa `finalize_failure()`
# chaqiriladi — pul balansga umuman tegilmagani uchun "qaytarish" fizik
# jihatdan shart emas, faqat bandlik olib tashlanadi.
#
# Har bir alohida handler (course_work.py, essay.py, pptx_gen.py va h.k.)
# o'zining ANIQ yakunlanish (muvaffaqiyat/xato) nuqtasida
# `wallet_ui.finalize_success(context)` yoki
# `wallet_ui.finalize_failure(context, update=update)` ni chaqirishi kerak
# — reservation_id context.user_data['_reservation'] ichida saqlanadi,
# shuning uchun handler kodida reservation_id'ni o'zi hisoblab
# yurishi shart emas.
#
# CRASH SAFETY: agar handler HECH QACHON finalize_* chaqirmasa (masalan
# process crash bo'lsa, yoki foydalanuvchi suhbatni tark etsa), reservation
# wallet.py ichida RESERVATION_TTL_SECONDS o'tgach avtomatik "expired"ga
# o'tadi va band qilingan pul yana ishlatish uchun ochiladi (orphaned
# reservation abadiy qolib ketmaydi) — bunga hech qanday qo'shimcha kod
# kerak emas, chunki bu wallet.py'ning barcha o'qish funksiyalarida LAZY
# tarzda avtomatik bajariladi. Shuning ustiga: har bir ConversationHandler
# uchun bot.py'da `conversation_timeout` + `fallbacks` (pastga qarang)
# ORQALI ham, suhbat vaqt tugagach yoki /cancel bilan tashlab ketilgach,
# ochiq reservation zudlik bilan release qilinadi (20 daqiqalik TTL'ni
# kutmasdan) — buning uchun `release_dangling_reservation()` dan
# fallback/timeout handlerlarida foydalaning.
#
# Race condition himoyasi: wallet.reserve_balance() ICHKARIDA
# threading.Lock bilan butunlay atomik — shuning uchun bitta foydalanuvchi
# bir tugmani juda tez-tez bossa ham (Telegram ba'zan bir nechta bosishni
# deyarli bir vaqtda yuborishi mumkin), summa FAQAT BIR MARTA band qilinadi.
_RESERVATION_KEY = "_reservation"


def require_payment(feature_id: str):
    """Dekorator: `handlers/xxx.py`dagi asl `entry()` funksiyasini
    o'zgartirmasdan, uni to'lov (reservation) tekshiruvi bilan o'raydi.
    Ishlatilishi: `CallbackQueryHandler(require_payment("course_work")
    (course_work.entry), ...)` — bot.py'dagi conv builder funksiyalarida."""
    def decorator(handler_func):
        @functools.wraps(handler_func)
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            query = update.callback_query

            result = wallet.reserve_for_feature(user.id, feature_id)

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

            if result.reason == "reserved":
                logger.info(
                    f"🔒 Pullik funksiya uchun summa BAND QILINDI (hali yechilmagan): "
                    f"feature={feature_id}, user_id={user.id}, narx={result.price}, "
                    f"reservation_id={result.reservation_id}."
                )
                context.user_data[_RESERVATION_KEY] = {
                    "reservation_id": result.reservation_id,
                    "feature_id": feature_id,
                    "price": result.price,
                }
                # 5-band (UX): funksiya boshlanganda narx/balans ko'rsatiladi.
                if query:
                    try:
                        await query.answer(
                            f"💰 Narxi: {_fmt_sum(result.price)}   💳 Balans: {_fmt_sum(result.balance)}"
                        )
                    except Exception:
                        pass

            # "free" yoki "reserved" — funksiya haqiqatan ishga tushadi.
            try:
                return await handler_func(update, context)
            except Exception:
                # Handler ENTRY bosqichida (hali birinchi javob yozilmasdan)
                # kutilmagan xato bersa — band qilingan summa DARHOL
                # ozod qilinadi (foydalanuvchi pulsiz qolmasin) va xato
                # yana yuqoriga uzatiladi (bot.py'dagi umumiy error_handler
                # buni logga yozadi/adminlarga xabar beradi).
                if result.reason == "reserved":
                    await finalize_failure(context, update=update, reason="handler_exception", silent_if_missing=True)
                raise

        return wrapped
    return decorator


# ============================================================
# ✅❌ Reservation yakunlash — handlerlar (course_work.py, essay.py,
# pptx_gen.py va h.k.) xizmat/AI generatsiyasi HAQIQATAN muvaffaqiyatli
# yoki muvaffaqiyatsiz tugagan ANIQ nuqtada shularni chaqirishi kerak.
# ============================================================

def get_active_reservation(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    """Joriy suhbatda ochiq (hali finalize qilinmagan) reservation
    ma'lumotini qaytaradi (yoki bepul/reservationsiz funksiya bo'lsa None)."""
    return context.user_data.get(_RESERVATION_KEY)


async def send_processing_notice(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                   text: str = "⏳ Xizmat tayyorlanmoqda...") -> None:
    """Handler AI/xizmat generatsiyasini boshlashdan OLDIN chaqirishi mumkin
    bo'lgan ixtiyoriy UX xabari (5-band)."""
    try:
        if update.callback_query:
            await update.callback_query.answer()
        await context.bot.send_message(update.effective_chat.id, text)
    except Exception as e:
        logger.warning(f"⏳ 'Xizmat tayyorlanmoqda' xabarini yuborib bo'lmadi: {e}")


async def finalize_success(context: ContextTypes.DEFAULT_TYPE, update: Update | None = None,
                            chat_id: int | None = None, extra_note: str = "") -> bool:
    """Xizmat/AI MUVAFFAQIYATLI yakunlanganda chaqiriladi — band qilingan
    summa endi HAQIQATAN balansdan yechiladi va foydalanuvchiga tasdiq
    xabari yuboriladi (5-band). Bepul funksiya (reservation yo'q) bo'lsa
    hech narsa qilmaydi (True qaytaradi — "muvaffaqiyatli" holat).
    IDEMPOTENT: reservation allaqachon yakunlangan bo'lsa qayta hech
    narsa qilmaydi (wallet.complete_reservation() o'zi buni kafolatlaydi)."""
    reservation = context.user_data.pop(_RESERVATION_KEY, None)
    if not reservation:
        return True  # bepul funksiya edi — to'lov bilan bog'liq hech narsa qilinmaydi

    ok = wallet.complete_reservation(reservation["reservation_id"])
    price = reservation["price"]
    chat = chat_id or (update.effective_chat.id if update is not None else None)
    if ok and chat is not None:
        text = f"✅ Tayyor!\n💰 {_fmt_sum(price)} yechildi."
        if extra_note:
            text += f"\n{extra_note}"
        try:
            await context.bot.send_message(chat, text)
        except Exception as e:
            logger.warning(f"✅ Muvaffaqiyat xabarini yuborib bo'lmadi: {e}")
    return ok


async def finalize_failure(context: ContextTypes.DEFAULT_TYPE, update: Update | None = None,
                            chat_id: int | None = None, reason: str = "",
                            silent_if_missing: bool = False) -> bool:
    """Xizmat/AI MUVAFFAQIYATSIZ bo'lganda (yoki foydalanuvchi bekor
    qilganda) chaqiriladi — band qilingan summa OZOD qilinadi (balansga
    umuman tegilmagani uchun "qaytarish" shart emas, shunchaki bandlik
    bekor qilinadi) va foydalanuvchiga aniq xabar yuboriladi (5-band).
    IDEMPOTENT: reservation allaqachon yakunlangan bo'lsa qayta hech
    narsa qilmaydi."""
    reservation = context.user_data.pop(_RESERVATION_KEY, None)
    if not reservation:
        return not silent_if_missing  # bepul funksiya edi yoki reservation allaqachon yakunlangan

    ok = wallet.release_reservation(reservation["reservation_id"], reason=reason or "xizmat muvaffaqiyatsiz tugadi")
    price = reservation["price"]
    chat = chat_id or (update.effective_chat.id if update is not None else None)
    if ok and chat is not None:
        text = f"❌ Xizmatni bajarishda xatolik yuz berdi.\n💰 {_fmt_sum(price)} hisobingizga qaytarildi."
        try:
            await context.bot.send_message(chat, text)
        except Exception as e:
            logger.warning(f"❌ Xato xabarini yuborib bo'lmadi: {e}")
    return ok


async def release_dangling_reservation(context: ContextTypes.DEFAULT_TYPE, reason: str = "conversation_abandoned") -> None:
    """bot.py'dagi ConversationHandler `fallbacks=`/`conversation_timeout`
    orqali chaqiriladi — foydalanuvchi suhbatni /cancel qilsa yoki vaqt
    tugab avtomatik yopilsa, ORQADA "osilib qolgan" reservation (agar
    bo'lsa) DARHOL ozod qilinadi (20 daqiqalik TTL'ni kutish shart emas).
    Foydalanuvchiga xabar yubormaydi (jimgina tozalaydi) — chaqiruvchi
    kerak bo'lsa o'zi xabar beradi."""
    reservation = context.user_data.pop(_RESERVATION_KEY, None)
    if reservation:
        wallet.release_reservation(reservation["reservation_id"], reason=reason)
