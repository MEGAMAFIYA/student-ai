"""
💳 Admin uchun TO'LOVLAR / BALANSLAR / FUNKSIYA NARXLARI paneli.

Bu modul HECH QANDAY o'z ConversationHandler'iga ega EMAS — u faqat matn/
tugma quruvchi va callback-mantiq funksiyalaridan iborat, handlers/developer.py
o'zining MAVJUD /developer conversation'i (DEV_MENU / DEV_WAIT_TEXT
holatlari) ICHIDAN chaqiradi. Shu tufayli developer.py'ning o'zi deyarli
o'zgarmaydi (faqat bir nechta menyu tugmasi + callback yo'naltirish qatori
qo'shiladi) — mavjud AI-sozlamalar paneli buzilmaydi.

Callback namespace: "dev:pay*", "dev:price*", "dev:paysettings" — bular
developer.py'dagi mavjud "^dev:" pattern ichida avtomatik ushlanadi.
"""

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import wallet
import payment_providers

logger = logging.getLogger(__name__)


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _fmt_sum(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " so'm"


# ============================================================
# 💳 To'lovlar (pending/paid/rejected/suspicious)
# ============================================================

_STATUS_TABS = [
    ("pending", "🕐 Tekshirilmagan", wallet.STATUS_GROUP_UNCHECKED),
    ("paid", "✅ Tasdiqlangan", wallet.STATUS_GROUP_APPROVED),
    ("rejected", "❌ Rad etilgan", wallet.STATUS_GROUP_REJECTED),
    ("suspicious", "⚠️ Shubhali", wallet.STATUS_GROUP_SUSPICIOUS),
]
_STATUS_TAB_MAP = {key: statuses for key, _, statuses in _STATUS_TABS}


def payments_menu_text() -> str:
    counts = []
    for key, label, statuses in _STATUS_TABS:
        n = len(wallet.list_payments(statuses=statuses))
        counts.append(f"{label}: {n}")
    return "💳 <b>To'lovlar</b>\n\n" + "\n".join(counts) + "\n\nBo'limni tanlang:"


def payments_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=f"dev:paylist:{key}")] for key, label, _ in _STATUS_TABS]
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:moliya")])
    return InlineKeyboardMarkup(rows)


def payment_list_text(tab_key: str) -> str:
    label = next((l for k, l, _ in _STATUS_TABS if k == tab_key), tab_key)
    statuses = _STATUS_TAB_MAP.get(tab_key, ())
    rows = wallet.list_payments(statuses=statuses)[:25]
    if not rows:
        return f"{label}\n\n<i>Bu bo'limda to'lov yo'q.</i>"
    lines = [f"{label} ({len(rows)}):\n"]
    for p in rows:
        lines.append(f"• <code>{_esc(p['payment_id'])}</code> — {_fmt_sum(p['amount'])} — {_esc(p['created_at'][:16])}")
    return "\n".join(lines)


def payment_list_keyboard(tab_key: str) -> InlineKeyboardMarkup:
    statuses = _STATUS_TAB_MAP.get(tab_key, ())
    rows_data = wallet.list_payments(statuses=statuses)[:25]
    rows = []
    for p in rows_data:
        rows.append([InlineKeyboardButton(
            f"{_fmt_sum(p['amount'])} — {p['payment_id'][-8:]}", callback_data=f"dev:payview:{p['payment_id']}"
        )])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:pay")])
    return InlineKeyboardMarkup(rows)


def payment_detail_text(payment_id: str) -> str:
    p = wallet.get_payment(payment_id)
    if not p:
        return "⚠️ Bu to'lov topilmadi (o'chirilgan bo'lishi mumkin)."
    receipt = p.get("receipt") or {}
    extracted = receipt.get("extracted") or {}
    lines = [
        "💳 <b>To'lov tafsilotlari</b>\n",
        f"🆔 Telegram ID: <code>{p['user_id']}</code>",
        f"💰 Summa: {_fmt_sum(p['amount'])}",
        f"🔢 Payment ID: <code>{_esc(p['payment_id'])}</code>",
        f"📅 Yaratilgan: {_esc(p['created_at'])}",
        f"💳 Usul: {_esc(p['method'])}",
        f"📌 Status: {_esc(p['status'])}",
    ]
    if p.get("provider_transaction_id"):
        lines.append(f"🏦 Provider tranzaksiya ID: <code>{_esc(p['provider_transaction_id'])}</code>")
    if extracted:
        lines.append("\n🧾 <b>Chekdan AI o'qigan ma'lumot</b> (bu TASDIQ EMAS, faqat yordamchi):")
        for k in ("amount", "transaction_id", "date", "time", "sender", "receiver", "provider", "confidence"):
            if extracted.get(k) is not None:
                lines.append(f"  {k}: {_esc(extracted.get(k))}")
    if p.get("reject_reason"):
        lines.append(f"\n❌ Rad etish sababi: {_esc(p['reject_reason'])}")
    return "\n".join(lines)


def payment_detail_keyboard(payment_id: str, tab_key: str = "pending") -> InlineKeyboardMarkup:
    p = wallet.get_payment(payment_id)
    rows = []
    if p and p["status"] not in (wallet.STATUS_PAID, wallet.STATUS_REJECTED):
        rows.append([
            InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"dev:payact:approve:{payment_id}"),
            InlineKeyboardButton("❌ Rad etish", callback_data=f"dev:payact:reject:{payment_id}"),
        ])
        rows.append([InlineKeyboardButton("⚠️ Shubhali deb belgilash", callback_data=f"dev:payact:suspicious:{payment_id}")])
        receipt = (p.get("receipt") or {})
        if receipt.get("file_id"):
            rows.append([InlineKeyboardButton("🖼 Chekni ko'rish", callback_data=f"dev:payphoto:{payment_id}")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data=f"dev:paylist:{tab_key}")])
    return InlineKeyboardMarkup(rows)


def apply_payment_action(payment_id: str, action: str, actor_id: int) -> tuple[bool, str]:
    """action: 'approve' | 'reject' | 'suspicious'. Qaytaradi: (ok, xabar)."""
    if action == "approve":
        ok = wallet.approve_manual_payment(payment_id, actor_id=actor_id)
        return ok, ("✅ Tasdiqlandi." if ok else "⚠️ Tasdiqlab bo'lmadi (allaqachon tasdiqlangan yoki rad etilgan bo'lishi mumkin).")
    if action == "reject":
        ok = wallet.reject_payment(payment_id, actor_id=actor_id, reason="Admin tomonidan rad etildi.")
        return ok, ("❌ Rad etildi." if ok else "⚠️ Rad etib bo'lmadi.")
    if action == "suspicious":
        ok = wallet.mark_suspicious(payment_id, actor_id=actor_id, reason="Admin tomonidan shubhali deb belgilandi.")
        return ok, ("⚠️ Shubhali deb belgilandi." if ok else "⚠️ Belgilab bo'lmadi.")
    return False, "Noma'lum amal."


# ============================================================
# 💰 Moliyaviy statistika (developer panel > 💰 Moliyaviy statistika)
# ============================================================
# wallet.get_admin_financial_stats() — ko'rilsin (wallet.py) — barcha
# raqamlarni BITTA atomik o'qishda (lock ichida) hisoblab qaytaradi.

def financial_stats_text() -> str:
    s = wallet.get_admin_financial_stats()
    return (
        "💰 <b>Moliyaviy statistika</b>\n\n"
        f"💵 Jami depozitlar (tasdiqlangan to'lovlar): {_fmt_sum(s['total_deposits'])}\n"
        f"💸 Jami sarflangan (yechilgan): {_fmt_sum(s['total_spending'])}\n"
        f"💳 Foydalanuvchilar joriy balanslari (jami): {_fmt_sum(s['total_balance'])}\n"
        f"🔒 Band qilingan (reserved) balanslar: {_fmt_sum(s['reserved_balance'])}\n"
        f"🕐 Kutilayotgan to'lovlar: {s['pending_payments']} ta\n"
        f"🧾 Qo'lda ko'rib chiqish (manual review): {s['manual_reviews']} ta\n"
        f"↩️ Qaytarilgan/bekor qilingan operatsiyalar: {s['failed_refunded_ops']} ta\n"
        f"❌ Muvaffaqiyatsiz/qaytarilgan: {_fmt_sum(s['total_refunded'])}\n\n"
        "<i>Raqamlar real vaqtda hisoblanadi (eskirgan reservation'lar avtomatik "
        "tozalangandan keyin).</i>"
    )


def financial_stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Yangilash", callback_data="dev:finstats")],
        [InlineKeyboardButton("🔒 Faol reservationlar", callback_data="dev:resactive")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:moliya")],
    ])


def active_reservations_text() -> str:
    rows = wallet.list_reservations(status=wallet.RES_STATUS_RESERVED)[:25]
    if not rows:
        return "🔒 <b>Faol (band qilingan) reservationlar</b>\n\n<i>Hozircha yo'q.</i>"
    lines = [f"🔒 <b>Faol reservationlar</b> ({len(rows)} ta, eng yuqori 25 tasi):\n"]
    for r in rows:
        feature = wallet.get_feature(r["feature_id"])
        fname = feature["name"] if feature else r["feature_id"]
        lines.append(
            f"• 🆔 <code>{r['user_id']}</code> — {_fmt_sum(r['amount'])} — {_esc(fname)}\n"
            f"  ⏰ {r['created_at'][:16]} → muddati: {r['expires_at'][:16]}"
        )
    return "\n".join(lines)


def active_reservations_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Yangilash", callback_data="dev:resactive")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:finstats")],
    ])


# ============================================================
# 💳 Balanslar
# ============================================================

def balances_text() -> str:
    rows = wallet.list_wallets(limit=25)
    if not rows:
        return "💳 <b>Balanslar</b>\n\n<i>Hali hech kimning balansi yo'q.</i>"
    lines = ["💳 <b>Balanslar</b> (eng yuqori 25 ta)\n"]
    for user_id, balance in rows:
        lines.append(f"🆔 <code>{_esc(user_id)}</code> — {_fmt_sum(balance)}")
    lines.append("\nMa'lum bir foydalanuvchini qidirish uchun uning Telegram ID raqamini yozing.")
    return "\n".join(lines)


def balances_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 ID bo'yicha qidirish", callback_data="dev:balsearch")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:moliya")],
    ])


def user_balance_text(user_id: int) -> str:
    balance = wallet.get_balance(user_id)
    txs = wallet.get_transactions(user_id, limit=10)
    lines = [f"🆔 <code>{user_id}</code>\n💰 Balans: {_fmt_sum(balance)}\n"]
    if txs:
        lines.append("Oxirgi operatsiyalar:")
        for t in txs:
            sign = "+" if t["amount"] > 0 else ""
            lines.append(f"  {t['created_at'][:16]} — {sign}{_fmt_sum(abs(t['amount']))} — {_esc(t['description'])}")
    return "\n".join(lines)


# ============================================================
# ⚙️ Funksiya narxlari
# ============================================================

def price_menu_text() -> str:
    return "⚙️ <b>Funksiya narxlari</b>\n\nO'zgartirish uchun funksiyani tanlang:"


def price_menu_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for fid, f in wallet.list_features():
        status_icon = "🟢" if f.get("enabled", True) else "🔴"
        rows.append([InlineKeyboardButton(
            f"{status_icon} {f['name']} — {_fmt_sum(f['price']) if f['price'] else 'bepul'}",
            callback_data=f"dev:pricefeat:{fid}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:moliya")])
    return InlineKeyboardMarkup(rows)


def price_detail_text(feature_id: str) -> str:
    f = wallet.get_feature(feature_id)
    if not f:
        return "⚠️ Bu funksiya topilmadi."
    price_str = _fmt_sum(f["price"]) if f["price"] else "0 (bepul)"
    status_str = "🟢 Yoqilgan" if f.get("enabled", True) else "🔴 O'chirilgan"
    return (
        f"{_esc(f['name'])}\n\n"
        f"Hozirgi narx: {price_str}\n"
        f"Holati: {status_str}\n\n"
        "[✏️ Narxni o'zgartirish] — yangi narxni xabar qilib yuborasiz (0 — bepul qiladi)."
    )


def price_detail_keyboard(feature_id: str) -> InlineKeyboardMarkup:
    f = wallet.get_feature(feature_id) or {}
    toggle_label = "🔴 O'chirish" if f.get("enabled", True) else "🟢 Yoqish"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Narxni o'zgartirish", callback_data=f"dev:priceedit:{feature_id}")],
        [InlineKeyboardButton(toggle_label, callback_data=f"dev:pricetoggle:{feature_id}")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:payprice")],
    ])


# ============================================================
# 💳 To'lov sozlamalari
# ============================================================

def payment_settings_text() -> str:
    ecommerce = payment_providers.get_ecommerce_provider()
    verifier = payment_providers.get_bank_verifier()

    def _status(ok: bool) -> str:
        return "✅ sozlangan" if ok else "❌ sozlanmagan"

    requisites_ok = bool(config.PAYMENT_CARD_NUMBER)
    return (
        "💳 <b>To'lov sozlamalari</b>\n\n"
        f"🟢 Kapitalbank E-commerce: {_status(ecommerce.is_configured())}\n"
        f"🟡 Kapitalbank tranzaksiya tekshiruvi: {_status(verifier.is_configured())}\n"
        f"🏦 Bank/Paynet rekvizitlari (karta raqami): {_status(requisites_ok)}\n\n"
        "Bu qiymatlar FAQAT Environment Variables (.env yoki Render Environment) "
        "orqali sozlanadi — xavfsizlik uchun shu yerdan o'zgartirib bo'lmaydi.\n\n"
        "Kerakli o'zgaruvchilar: KAPITALBANK_MERCHANT_ID, KAPITALBANK_API_BASE_URL, "
        "KAPITALBANK_API_KEY, KAPITALBANK_API_SECRET, KAPITALBANK_WEBHOOK_SECRET, "
        "PAYMENT_CARD_NUMBER, PAYMENT_CARD_HOLDER, PAYMENT_RECEIVER_NOTE."
    )


def payment_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:moliya")]])
