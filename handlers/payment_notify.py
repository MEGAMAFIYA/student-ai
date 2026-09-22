"""
💳 Balans to'ldirish → ADMINGA AVTOMATIK XABAR.

Foydalanuvchi hisobini to'ldirganda (yoki chek yuborganda) barcha adminlarga
(config.ADMIN_IDS) shunday xabar boradi:

    👤 @mittivoy nomli foydalanuvchi hisobini 10 000 so'm ga to'ldirdi.
    Tasdiqlaysizmi?   [✅ Tasdiqlash] [❌ Rad etish] [⚠️ Shubhali]

+ to'lov tafsilotlari (Telegram ID, usul, status, payment ID, sana, hozirgi
balans, chekdan AI o'qigan ma'lumot) va chek rasmi/fayli.

Uch xil xabar bor:
1) notify_admins_new_payment()             — TASDIQ KUTAYOTGAN to'lov
   (bot tekshiruvidan o'tgan bo'lsa ham, hali ham ✅/❌/⚠️ tugmalari bilan —
   admin istalgan vaqt bot avtomatik qabul qilishidan OLDIN qaror qilishi
   mumkin).
2) notify_admins_payment_confirmed()       — admin o'zi tasdiqlagan/qo'lda
   tekshirilgan to'lov haqida MA'LUMOT (tugmasiz).
3) notify_admins_payment_auto_confirmed()  — BOT 1 soatdan keyin o'zi qabul
   qilgan to'lov haqida xabar, "🚫 Soxta deb qaytarish" tugmasi bilan
   (admin keyin ham soxta ekanini aniqlasa, shu tugma orqali qaytaradi).

Tugmalar callback_data: payn:approve:<payment_id> | payn:reject:<payment_id>
| payn:susp:<payment_id> | payn:revoke:<payment_id>. Ularni
admin_payment_decision_callback() qayta ishlaydi (bot.py'da pattern="^payn:"
bilan ro'yxatdan o'tkazilgan). Haqiqiy tasdiqlash/rad etish/qaytarish
/developer > 💳 To'lovlar paneli bilan BIR XIL mantiq orqali bajariladi
(payment_admin.apply_payment_action → wallet.py), shuning uchun balansga
pul qo'shish/ayirish yagona joyda (wallet.confirm_payment / revoke_payment)
va idempotent qoladi.
"""

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import wallet
from handlers import payment_admin

logger = logging.getLogger(__name__)

_METHOD_LABELS = {
    wallet.METHOD_BANK_RECEIPT: "Chek (bot avval tekshiradi)",
    wallet.METHOD_MANUAL_RECEIPT: "Chek (to'g'ridan admin tekshiradi)",
}

_STATUS_LABELS = {
    wallet.STATUS_PENDING: "🕐 Tekshirilmoqda",
    wallet.STATUS_PAID: "✅ Tasdiqlangan",
    wallet.STATUS_FAILED: "❌ Muvaffaqiyatsiz",
    wallet.STATUS_CANCELLED: "❌ Bekor qilingan",
    wallet.STATUS_EXPIRED: "⌛ Muddati o'tgan",
    wallet.STATUS_MANUAL_REVIEW: "⚠️ Qo'lda tekshirish kerak",
    wallet.STATUS_REJECTED: "❌ Rad etilgan",
    wallet.STATUS_SUSPICIOUS: "⚠️ Shubhali",
    wallet.STATUS_AUTO_HOLD: "🤖 Bot tasdiqladi — admin javobi kutilmoqda",
    wallet.STATUS_REVOKED: "🚫 Soxta deb qaytarilgan",
}

# Chekdan AI o'qigan maydonlar (faqat yordamchi ma'lumot — TASDIQ EMAS).
_RECEIPT_FIELDS = ("amount", "transaction_id", "date", "time", "sender", "receiver",
                    "receiver_card", "provider", "confidence")


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def fmt_sum(amount) -> str:
    return f"{int(amount):,}".replace(",", " ") + " so'm"


def method_label(method: str) -> str:
    return _METHOD_LABELS.get(method, str(method))


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, str(status))


def _user_phrase(user_id, username, full_name) -> str:
    """'@mittivoy nomli foydalanuvchi' — username bo'lmasa, ism bilan
    (Telegram profiliga havola qilib)."""
    if username:
        return f"@{_esc(username)} nomli foydalanuvchi"
    name = _esc(full_name) if full_name else str(user_id)
    return f'<a href="tg://user?id={int(user_id)}">{name}</a> ismli foydalanuvchi'


def _parse_amount(value):
    try:
        return int(round(float(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))))
    except (TypeError, ValueError):
        return None


def build_admin_text(payment: dict, username=None, full_name=None, *, awaiting_decision: bool,
                     source_label: str = "") -> str:
    """Adminga boradigan xabar matni (HTML)."""
    user_id = payment["user_id"]
    who = _user_phrase(user_id, username, full_name)
    amount = fmt_sum(payment["amount"])

    if awaiting_decision:
        lines = [
            "💳 <b>Yangi to'lov — tasdiqlash kerak</b>\n",
            f"👤 {who} hisobini <b>{amount}</b> ga to'ldirdi. Tasdiqlaysizmi?\n",
        ]
    else:
        suffix = f" ({_esc(source_label)})" if source_label else ""
        lines = [
            "💚 <b>Balans to'ldirildi</b>\n",
            f"👤 {who} hisobini <b>{amount}</b> ga to'ldirdi{suffix}.\n",
        ]

    lines += [
        f"🆔 Telegram ID: <code>{user_id}</code>",
        f"💳 Usul: {_esc(method_label(payment.get('method')))}",
        f"📌 Status: {_esc(status_label(payment.get('status')))}",
        f"🔢 Payment ID: <code>{_esc(payment['payment_id'])}</code>",
        f"📅 Yaratilgan: {_esc(str(payment.get('created_at', ''))[:16].replace('T', ' '))}",
        f"💰 Hozirgi balans: {fmt_sum(wallet.get_balance(user_id))}",
    ]
    if payment.get("provider_transaction_id"):
        lines.append(f"🏦 Provider tranzaksiya ID: <code>{_esc(payment['provider_transaction_id'])}</code>")

    if payment.get("status") == wallet.STATUS_AUTO_HOLD and payment.get("auto_confirm_at"):
        due = _esc(str(payment["auto_confirm_at"])[:16].replace("T", " "))
        lines.append(
            f"\n🤖 <b>Bot tekshirdi: karta/ism/summa TO'G'RI.</b> Javob bermasangiz, "
            f"bot to'lovni <b>{due}</b> da o'zi qabul qiladi."
        )

    extracted = ((payment.get("receipt") or {}).get("extracted")) or {}
    shown = [(k, extracted[k]) for k in _RECEIPT_FIELDS if extracted.get(k) is not None]
    if shown:
        lines.append("\n🧾 <b>Chekdan AI o'qigan</b> (TASDIQ EMAS, faqat yordamchi):")
        lines += [f"  {k}: {_esc(v)}" for k, v in shown]
        seen = _parse_amount(extracted.get("amount"))
        if seen is not None and seen != int(payment["amount"]):
            lines.append(
                f"\n⚠️ <b>Diqqat:</b> chekdagi summa ({fmt_sum(seen)}) foydalanuvchi "
                f"kiritgan summadan ({amount}) FARQ qiladi!"
            )

    bot_check = ((payment.get("extra") or {}).get("bot_check")) or {}
    notes = bot_check.get("notes")
    if notes and payment.get("status") not in (wallet.STATUS_AUTO_HOLD, wallet.STATUS_PAID):
        lines.append("\n🤖 <b>Bot tekshiruvi:</b> " + "; ".join(_esc(n) for n in notes))
    return "\n".join(lines)


def approval_keyboard(payment_id: str, allow_suspicious: bool = True) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"payn:approve:{payment_id}"),
        InlineKeyboardButton("❌ Rad etish", callback_data=f"payn:reject:{payment_id}"),
    ]]
    if allow_suspicious:
        rows.append([InlineKeyboardButton("⚠️ Shubhali deb belgilash", callback_data=f"payn:susp:{payment_id}")])
    return InlineKeyboardMarkup(rows)


def revoke_keyboard(payment_id: str) -> InlineKeyboardMarkup:
    """Bot avtomatik qabul qilgan (yoki admin allaqachon tasdiqlagan) to'lov
    KEYINCHALIK soxta deb topilsa — shu tugma orqali qaytariladi."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🚫 Soxta deb qaytarish", callback_data=f"payn:revoke:{payment_id}"),
    ]])


async def _resolve_user(bot, user_id: int, user=None):
    """(username, full_name). `user` berilmasa (masalan webhook) — Telegram'dan so'raladi."""
    if user is not None:
        return getattr(user, "username", None), getattr(user, "full_name", None)
    try:
        chat = await bot.get_chat(user_id)
        return getattr(chat, "username", None), getattr(chat, "full_name", None)
    except Exception as e:
        logger.info(f"💳 Foydalanuvchi ma'lumotini olib bo'lmadi (user_id={user_id}): {e}")
        return None, None


async def _send_receipt_file(bot, admin_id, file_id: str, file_kind, payment_id: str) -> None:
    caption = f"🧾 Chek — payment_id: {payment_id}"
    if file_kind == "document":
        await bot.send_document(admin_id, file_id, caption=caption)
    elif file_kind == "photo":
        await bot.send_photo(admin_id, file_id, caption=caption)
    else:  # turi noma'lum — avval rasm, bo'lmasa fayl
        try:
            await bot.send_photo(admin_id, file_id, caption=caption)
        except Exception:
            await bot.send_document(admin_id, file_id, caption=caption)


async def notify_admins_new_payment(bot, payment: dict, user=None, file_kind=None) -> int:
    """Tasdiq kutayotgan to'lov haqida adminlarga tugmali xabar yuboradi.
    Qaytaradi: xabar yetib borgan adminlar soni."""
    if not payment or not config.ADMIN_IDS:
        return 0
    username, full_name = await _resolve_user(bot, payment["user_id"], user)
    text = build_admin_text(payment, username, full_name, awaiting_decision=True)
    keyboard = approval_keyboard(payment["payment_id"])
    file_id = (payment.get("receipt") or {}).get("file_id")

    delivered = 0
    for admin_id in config.ADMIN_IDS:
        if file_id:
            try:
                await _send_receipt_file(bot, admin_id, file_id, file_kind, payment["payment_id"])
            except Exception as e:
                logger.warning(f"🧾 Chek faylini adminga ({admin_id}) yuborib bo'lmadi: {e}")
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=keyboard)
            delivered += 1
        except Exception as e:
            logger.warning(f"🧾 Adminni ({admin_id}) to'lov haqida xabardor qilib bo'lmadi: {e}")
    return delivered


async def notify_admins_payment_confirmed(bot, payment: dict, source_label: str = "", user=None) -> int:
    """Admin o'zi tasdiqlagan/qo'lda tekshirgan to'lov haqida faqat MA'LUMOT (tugmasiz)."""
    if not payment or not config.ADMIN_IDS:
        return 0
    username, full_name = await _resolve_user(bot, payment["user_id"], user)
    text = build_admin_text(payment, username, full_name, awaiting_decision=False, source_label=source_label)
    delivered = 0
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
            delivered += 1
        except Exception as e:
            logger.warning(f"💚 Adminni ({admin_id}) to'lov haqida xabardor qilib bo'lmadi: {e}")
    return delivered


async def notify_admins_payment_auto_confirmed(bot, payment: dict, user=None) -> int:
    """🤖 Bot to'lovni O'ZI (admin 1 soat ichida javob bermagani uchun) qabul
    qilganda adminlarga xabar — "🚫 Soxta deb qaytarish" tugmasi bilan, chunki
    admin buni keyin ham ko'rib chiqib soxta deb topishi mumkin."""
    if not payment or not config.ADMIN_IDS:
        return 0
    username, full_name = await _resolve_user(bot, payment["user_id"], user)
    text = build_admin_text(
        payment, username, full_name, awaiting_decision=False,
        source_label="bot 1 soatdan keyin avtomatik qabul qildi",
    )
    keyboard = revoke_keyboard(payment["payment_id"])
    delivered = 0
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=keyboard)
            delivered += 1
        except Exception as e:
            logger.warning(f"🤖 Adminni ({admin_id}) avtomatik qabul haqida xabardor qilib bo'lmadi: {e}")
    return delivered


# ============================================================
# Admin tugmalari: ✅ Tasdiqlash / ❌ Rad etish / ⚠️ Shubhali / 🚫 Qaytarish
# ============================================================

_ACTIONS = {"approve": "approve", "reject": "reject", "susp": "suspicious", "revoke": "revoke"}


async def admin_payment_decision_callback(update, context):
    query = update.callback_query
    admin = update.effective_user
    if admin is None or admin.id not in config.ADMIN_IDS:
        await query.answer("⚠️ Bu tugma faqat adminlar uchun.", show_alert=True)
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[1] not in _ACTIONS:
        await query.answer("⚠️ Noto'g'ri so'rov.", show_alert=True)
        return
    action = _ACTIONS[parts[1]]
    payment_id = parts[2]

    payment = wallet.get_payment(payment_id)
    if not payment:
        await query.answer("⚠️ To'lov topilmadi.", show_alert=True)
        return

    original = getattr(query.message, "text_html", None) or ""

    async def _edit(footer: str, keyboard):
        try:
            await query.edit_message_text(
                (original + "\n\n" if original else "") + footer,
                parse_mode="HTML", reply_markup=keyboard,
            )
        except Exception as e:
            logger.info(f"💳 Admin xabarini yangilab bo'lmadi: {e}")

    who = _esc(admin.full_name or admin.id)

    # 🚫 "Soxta deb qaytarish" — AKSINCHA, faqat ALLAQACHON 'paid' (bot
    # avtomatik yoki admin tasdiqlagan) to'lovga nisbatan ishlatiladi.
    if action == "revoke":
        if payment["status"] != wallet.STATUS_PAID:
            await query.answer(
                f"ℹ️ Faqat tasdiqlangan to'lovni qaytarish mumkin (hozirgi holat: {status_label(payment['status'])}).",
                show_alert=True,
            )
            return
        result = payment_admin.apply_payment_action(payment_id, action, admin.id)
        ok, message = result
        if not ok:
            await query.answer(message, show_alert=True)
            return
        await query.answer(message)
        payment = wallet.get_payment(payment_id) or payment
        await _edit(f"🚫 <b>SOXTA DEB QAYTARILDI</b> — {who}", None)

        from handlers import wallet_ui  # kech import: wallet_ui ham shu modulni import qiladi
        new_balance = wallet.get_balance(payment["user_id"])
        await wallet_ui.notify_user_payment_revoked(
            context.bot, payment, new_balance, reason="Admin tomonidan soxta deb topildi.",
        )
        logger.info(f"🚫 Admin qarori (tugma): admin_id={admin.id}, payment_id={payment_id}, action=revoke.")
        return

    # Boshqa admin (yoki /developer paneli) allaqachon hal qilgan bo'lsa — takror amal qilinmaydi.
    if payment["status"] in (wallet.STATUS_PAID, wallet.STATUS_REJECTED):
        await query.answer(f"ℹ️ Bu to'lov allaqachon ko'rib chiqilgan: {status_label(payment['status'])}", show_alert=True)
        await _edit(f"ℹ️ Allaqachon ko'rib chiqilgan: {_esc(status_label(payment['status']))}", None)
        return

    ok, message = payment_admin.apply_payment_action(payment_id, action, admin.id)
    if not ok:
        await query.answer(message, show_alert=True)
        return
    await query.answer(message)

    payment = wallet.get_payment(payment_id) or payment
    if action == "approve":
        await _edit(f"✅ <b>TASDIQLANDI</b> — {who}", None)
    elif action == "reject":
        await _edit(f"❌ <b>RAD ETILDI</b> — {who}", None)
    else:
        await _edit(f"⚠️ <b>Shubhali deb belgilandi</b> — {who}", approval_keyboard(payment_id, allow_suspicious=False))

    if action in ("approve", "reject"):
        from handlers import wallet_ui  # kech import: wallet_ui ham shu modulni import qiladi
        await wallet_ui.notify_user_payment_decision(
            context.bot, payment, approved=(action == "approve"),
            reason="" if action == "approve" else "Admin tomonidan rad etildi.",
        )
    logger.info(f"💳 Admin qarori (tugma): admin_id={admin.id}, payment_id={payment_id}, action={action}.")
