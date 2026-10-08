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
import storage

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
    elif p and p["status"] == wallet.STATUS_PAID:
        rows.append([InlineKeyboardButton("🚫 Soxta deb qaytarish", callback_data=f"dev:payact:revoke:{payment_id}")])
    if p:
        receipt = (p.get("receipt") or {})
        if receipt.get("file_id"):
            rows.append([InlineKeyboardButton("🖼 Chekni ko'rish", callback_data=f"dev:payphoto:{payment_id}")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data=f"dev:paylist:{tab_key}")])
    return InlineKeyboardMarkup(rows)


def apply_payment_action(payment_id: str, action: str, actor_id: int) -> tuple[bool, str]:
    """action: 'approve' | 'reject' | 'suspicious' | 'revoke'. Qaytaradi: (ok, xabar)."""
    if action == "approve":
        ok = wallet.approve_manual_payment(payment_id, actor_id=actor_id)
        return ok, ("✅ Tasdiqlandi." if ok else "⚠️ Tasdiqlab bo'lmadi (allaqachon tasdiqlangan yoki rad etilgan bo'lishi mumkin).")
    if action == "reject":
        ok = wallet.reject_payment(payment_id, actor_id=actor_id, reason="Admin tomonidan rad etildi.")
        return ok, ("❌ Rad etildi." if ok else "⚠️ Rad etib bo'lmadi.")
    if action == "suspicious":
        ok = wallet.mark_suspicious(payment_id, actor_id=actor_id, reason="Admin tomonidan shubhali deb belgilandi.")
        return ok, ("⚠️ Shubhali deb belgilandi." if ok else "⚠️ Belgilab bo'lmadi.")
    if action == "revoke":
        result = wallet.revoke_payment(payment_id, actor_id=actor_id, reason="Admin tomonidan soxta deb topildi.")
        if not result:
            return False, "⚠️ Qaytarib bo'lmadi (to'lov 'tasdiqlangan' holatida emas)."
        return True, f"🚫 Qaytarildi. Yangi balans: {_fmt_sum(result['balance_after'])}."
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
        f"🤖 Bot tasdiqladi, admin javobi kutilmoqda (auto_hold): {s['auto_hold_payments']} ta\n"
        f"↩️ Qaytarilgan/bekor qilingan operatsiyalar: {s['failed_refunded_ops']} ta\n"
        f"❌ Muvaffaqiyatsiz/qaytarilgan: {_fmt_sum(s['total_refunded'])}\n"
        f"🚫 Soxta deb qaytarilgan to'lovlar: {s['revoked_payments']} ta ({_fmt_sum(s['total_revoked'])})\n"
        f"⚠️ Qarzdor foydalanuvchilar (balans manfiy): {s['debt_users']} ta\n\n"
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
# 🎁 Admin sovg'asi + 👥 Foydalanuvchilar
# ============================================================

USERS_PER_PAGE = 10


def _user_label(user_id: int) -> str:
    profile = storage.get_user_profile(user_id)
    username = (profile.get("username") or "").strip()
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    name = " ".join(x for x in (first, last) if x).strip()
    if username:
        handle = f"@{username}"
    else:
        handle = f"ID {user_id}"
    if name:
        return f"{handle} — {name}"
    return handle


def _user_display(user_id: int) -> str:
    profile = storage.get_user_profile(user_id)
    username = (profile.get("username") or "").strip()
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    name = " ".join(x for x in (first, last) if x).strip() or "Noma'lum foydalanuvchi"
    lines = [f"👤 <b>{_esc(name)}</b>"]
    if username:
        lines.append(f"🔗 <b>@{_esc(username)}</b>")
    lines.append(f"🆔 ID: <code>{int(user_id)}</code>")
    lines.append(f"💰 Balans: {_fmt_sum(wallet.get_balance(user_id))}")
    return "\n".join(lines)


def _users_keyboard(user_ids: list[int], page: int, total: int) -> InlineKeyboardMarkup:
    rows = []
    for uid in user_ids:
        rows.append([InlineKeyboardButton(_user_label(uid)[:60], callback_data=f"dev:user:{uid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"dev:users:{page-1}"))
    if (page + 1) * USERS_PER_PAGE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"dev:users:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔄 Yangilash", callback_data=f"dev:users:{page}")])
    rows.append([InlineKeyboardButton("⬅️ Moliya", callback_data="dev:moliya")])
    return InlineKeyboardMarkup(rows)


def users_text(user_ids: list[int], page: int, total: int) -> str:
    start = page * USERS_PER_PAGE
    end = min(start + USERS_PER_PAGE, total)
    return (
        "👥 <b>Foydalanuvchilar</b>\n\n"
        f"Jami: <b>{total}</b> ta\n"
        f"Ko'rsatilmoqda: <b>{start + 1 if total else 0}–{end}</b>\n\n"
        "Foydalanuvchini tanlash uchun pastdagi tugmani bosing."
    )


async def refresh_user_profiles(bot, user_ids: list[int]) -> None:
    """Eski all_users yozuvlarida profil bo'lmasa Telegramdan imkon qadar yangilaydi."""
    for uid in user_ids:
        profile = storage.get_user_profile(uid)
        if profile.get("username") or profile.get("first_name"):
            continue
        try:
            chat = await bot.get_chat(chat_id=uid)
            storage.record_user(
                uid,
                getattr(chat, "username", "") or "",
                getattr(chat, "first_name", "") or "",
                getattr(chat, "last_name", "") or "",
            )
        except Exception as exc:
            logger.debug("Foydalanuvchi profilini olish imkoni bo'lmadi: user_id=%s: %s", uid, exc)


def users_page(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    all_ids = storage.get_all_users()
    # Yangi foydalanuvchilar yuqorida ko'rinsin.
    all_ids = list(reversed(all_ids))
    total = len(all_ids)
    max_page = max(0, (total - 1) // USERS_PER_PAGE)
    page = max(0, min(int(page), max_page))
    page_ids = all_ids[page * USERS_PER_PAGE:(page + 1) * USERS_PER_PAGE]
    return users_text(page_ids, page, total), _users_keyboard(page_ids, page, total)


def user_detail_text(user_id: int) -> str:
    return _user_display(user_id)


def user_detail_keyboard(user_id: int) -> InlineKeyboardMarkup:
    uid = int(user_id)
    profile = storage.get_user_profile(uid)
    username = (profile.get("username") or "").strip()
    chat_url = f"https://t.me/{username}" if username else f"tg://user?id={uid}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Chatiga o'tish", url=chat_url)],
        [InlineKeyboardButton("🆔 Foydalanuvchi ID", callback_data=f"dev:userid:{uid}")],
        [InlineKeyboardButton("💰 Balansi — hisob to'ldirish", callback_data=f"dev:usertopup:{uid}")],
        [InlineKeyboardButton("⬅️ Foydalanuvchilar", callback_data="dev:users:0")],
    ])


def gift_prompt_text() -> str:
    return (
        "🎁 <b>Sovg'a berish</b>\n\n"
        "Kimga berishni tanlang:"
    )


def gift_target_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Foydalanuvchi ID", callback_data="dev:gift_target:user")],
        [InlineKeyboardButton("👥 Hammasi (all)", callback_data="dev:gift_target:all")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:moliya")],
    ])


def gift_all_mode_text() -> str:
    return (
        "👥 <b>Barcha foydalanuvchilar</b>\n\n"
        "Qanday berilsin?"
    )


def gift_all_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Qo'shish", callback_data="dev:giftmode:add")],
        [InlineKeyboardButton("🎯 Oshmagan", callback_data="dev:giftmode:cap")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:gift")],
    ])


def gift_confirm_text(target: str, amount: int, count: int | None = None, mode: str = "add") -> str:
    target_text = (
        "👥 <b>BARCHA foydalanuvchilar</b>"
        if target == "all"
        else f"👤 Foydalanuvchi ID: <code>{_esc(target)}</code>"
    )
    extra = f"\n👥 Foydalanuvchilar soni: <b>{count}</b> ta" if count is not None else ""
    mode_text = "➕ Qo'shish" if mode == "add" else "🎯 Oshmagan"
    return (
        "🎁 <b>Sovg'ani tasdiqlash</b>\n\n"
        f"{target_text}{extra}\n"
        f"🎯 Rejim: <b>{mode_text}</b>\n"
        f"💰 Belgilangan summa: <b>{_fmt_sum(amount)}</b>\n\n"
        + (
            "Har bir foydalanuvchining balansiga shu summa qo'shiladi."
            if mode == "add"
            else "Balansi shu summadan kam bo'lgan foydalanuvchining balansi aynan shu summaga yetkaziladi. "
                 "Balansi shu summa yoki undan ko'p bo'lsa, hech narsa qo'shilmaydi."
        )
    )


def gift_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Tasdiqlash", callback_data="dev:giftconfirm"),
         InlineKeyboardButton("❌ Bekor qilish", callback_data="dev:giftcancel")],
    ])


def gift_amount_prompt(target: str, mode: str = "add") -> str:
    if target == "all":
        count = len(storage.get_all_users())
        mode_text = "➕ Qo'shish" if mode == "add" else "🎯 Oshmagan"
        return (
            f"👥 Barcha foydalanuvchilar: <b>{count}</b> ta.\n"
            f"🎯 Rejim: <b>{mode_text}</b>\n\n"
            + (
                "💰 Har bir foydalanuvchi balansiga qo'shiladigan summani yuboring."
                if mode == "add"
                else "💰 Foydalanuvchi balansi oshmaydigan chegaraviy summani yuboring."
            )
            + "\nMasalan: <code>10000</code>"
        )
    return (
        f"👤 ID: <code>{_esc(target)}</code>\n\n"
        "💰 Beriladigan summani so'mda yuboring.\n"
        "Masalan: <code>10000</code>"
    )


def admin_gift(target: str, amount: int, actor_id: int, mode: str = "add") -> dict:
    """Admin sovg'asi. `add` balansga qo'shadi, `cap` esa balansni
    berilgan summadan oshirmaydi: faqat balans < amount bo'lsa farqi kreditlanadi."""
    if amount <= 0:
        raise ValueError("Summa 0 dan katta bo'lishi kerak.")
    if mode not in ("add", "cap"):
        raise ValueError("Noto'g'ri sovg'a rejimi.")

    if target == "all":
        user_ids = storage.get_all_users()
        txs = []
        for uid in user_ids:
            current = wallet.get_balance(int(uid))
            credit = amount if mode == "add" else max(0, amount - current)
            if credit <= 0:
                continue
            txs.append(wallet.credit_balance(
                int(uid), credit,
                description=(
                    f"Admin sovg'asi: +{_fmt_sum(credit)}"
                    if mode == "add"
                    else f"Admin sovg'asi: balansni {_fmt_sum(amount)} gacha to'ldirish"
                ),
                tx_type=wallet.TX_ADMIN_ADJUST,
                actor_id=actor_id,
            ))
        return {
            "count": len(txs),
            "total": sum(int(tx.get("amount", 0)) for tx in txs),
            "skipped": len(user_ids) - len(txs),
            "mode": mode,
        }

    uid = int(target)
    current = wallet.get_balance(uid)
    credit = amount if mode == "add" else max(0, amount - current)
    if credit <= 0:
        return {
            "count": 0, "total": 0, "skipped": 1, "mode": mode,
            "tx": {"balance_after": current},
        }
    tx = wallet.credit_balance(
        uid, credit,
        description=(
            f"Admin sovg'asi: +{_fmt_sum(credit)}"
            if mode == "add"
            else f"Admin sovg'asi: balansni {_fmt_sum(amount)} gacha to'ldirish"
        ),
        tx_type=wallet.TX_ADMIN_ADJUST,
        actor_id=actor_id,
    )
    return {"count": 1, "total": credit, "skipped": 0, "tx": tx, "mode": mode}

def user_topup_prompt(user_id: int) -> str:
    return (
        f"💰 <b>Hisob to'ldirish</b>\n\n"
        f"👤 Foydalanuvchi ID: <code>{int(user_id)}</code>\n"
        f"💳 Hozirgi balans: <b>{_fmt_sum(wallet.get_balance(user_id))}</b>\n\n"
        "Faqat SHU foydalanuvchiga o'tkaziladigan summani so'mda yuboring.\n"
        "Masalan: <code>10000</code>"
    )


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
    def _status(ok: bool) -> str:
        return "✅ sozlangan" if ok else "❌ sozlanmagan"

    card_ok = bool(config.PAYMENT_CARD_NUMBER)
    holder_ok = bool(config.PAYMENT_CARD_HOLDER)
    return (
        "💳 <b>To'lov sozlamalari</b>\n\n"
        f"💳 Karta raqami (PAYMENT_CARD_NUMBER): {_status(card_ok)}\n"
        f"👤 Karta egasi ismi (PAYMENT_CARD_HOLDER): {_status(holder_ok)}\n"
        f"⏱ Avtomatik qabul qilish muddati: {config.PAYMENT_AUTO_CONFIRM_SECONDS // 60} daqiqa\n"
        f"🎯 Chekni o'qish uchun minimal ishonchlilik: {config.PAYMENT_MIN_CONFIDENCE}\n\n"
        "🤖 <b>Bot chek tekshiruvi:</b> foydalanuvchi chek yuborganda, bot chekdagi "
        "karta raqami, qabul qiluvchi ismi va summani yuqoridagi rekvizitlar bilan "
        "solishtiradi (receipt_check.py). Hammasi mos kelsa — to'lov adminga "
        "yuboriladi va admin javob bermasa yuqoridagi muddatdan keyin AVTOMATIK "
        "qabul qilinadi. Karta/ism sozlanmagan bo'lsa, bot shu qismni tekshirmay "
        "o'tkazib yuboradi (faqat summa solishtiriladi).\n\n"
        "Bu qiymatlar FAQAT Environment Variables (.env yoki Render Environment) "
        "orqali sozlanadi — xavfsizlik uchun shu yerdan o'zgartirib bo'lmaydi.\n\n"
        "Kerakli o'zgaruvchilar: PAYMENT_CARD_NUMBER, PAYMENT_CARD_HOLDER, "
        "PAYMENT_RECEIVER_NOTE, PAYMENT_AUTO_CONFIRM_SECONDS, PAYMENT_MIN_CONFIDENCE."
    )


def payment_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:moliya")]])
