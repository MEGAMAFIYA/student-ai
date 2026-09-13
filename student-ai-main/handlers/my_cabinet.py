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
  - 🔑 Shaxsiy kalitlarim — /developer > 🔑 AI kalitlari bilan BIR XIL
    prinsipda ishlaydi (ro'yxat, qo'shish, model/kalit almashtirish,
    o'chirish, tekshirish), lekin har bir foydalanuvchi FAQAT o'z
    kalitlarini ko'radi/boshqaradi, va ular GitHub'da o'sha
    foydalanuvchining shaxsiy papkasida saqlanadi (user_ai_keys.py).
    Bu kalitlar pullik funksiyalarda (masalan Kurs ishi) AVVAL sinaladi
    — muvaffaqiyatli bo'lsa narxning 50%i qaytariladi (qarang:
    handlers/course_work.py).
  - 📇 Tabriknomam — ANIQ spetsifikatsiya berilmagani uchun HOZIRCHA
    "tez orada" stub sifatida qoldirilgan (keyinchalik to'ldiriladi).

Rasm yuklash VA shaxsiy kalit qo'shish/tahrirlash — ikkalasi ham bitta xil
"kutilayotgan matn/rasm" bayrog'i naqshiga asoslangan
(`context.user_data["awaiting_..."]`), ConversationHandler ISHLATILMAYDI
— shunchaki keyingi xabar (matn yoki rasm) shu bayroqqa qarab ushlanadi.
Bular bot.py'da ALOHIDA guruhda (group=1) ro'yxatdan o'tkazilgan — shu
orqali boshqa conversation'lardagi (masalan "Suratlarni PDF qilish")
rasm/matn handlerlariga XALAQIT bermaydi.
"""

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import ai_clients
import config
import github_storage
import pro_subscription
import user_ai_keys
# _esc/_provider_label/_mask_key — /developer'dagi bilan BIR XIL kichik
# formatlash yordamchilari, duplicate qilmaslik uchun qayta ishlatiladi.
from handlers.developer import _esc, _provider_label, _mask_key

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
        [InlineKeyboardButton("🔑 Shaxsiy kalitlarim", callback_data="mycab:keys")],
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

    if action == "keys":
        await query.answer()
        await query.edit_message_text(_keys_menu_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_keys_menu_keyboard(user_id))
        return

    if action == "keyadd":
        await query.answer()
        await query.edit_message_text(_keyadd_text(), parse_mode=ParseMode.HTML, reply_markup=_keyadd_keyboard())
        return

    if action.startswith("keyaddprov:"):
        provider = action.split(":", 1)[1]
        context.user_data["awaiting_personal_key"] = {"provider": provider, "stage": "key"}
        await query.answer()
        await query.edit_message_text(
            f"🔑 {_esc(_provider_label(provider))} — API kalitingizni yuboring (matn sifatida):",
            parse_mode=ParseMode.HTML,
        )
        return

    if action.startswith("keyview:"):
        _, provider, idx = action.split(":")
        await query.answer()
        await query.edit_message_text(
            _key_view_text(user_id, provider, int(idx)), parse_mode=ParseMode.HTML,
            reply_markup=_key_view_keyboard(provider, int(idx)),
        )
        return

    if action.startswith("keyrepl:"):
        _, provider, idx = action.split(":")
        context.user_data["awaiting_personal_key"] = {"provider": provider, "stage": "replace_key", "index": int(idx)}
        await query.answer()
        await query.edit_message_text(f"🔁 {_esc(_provider_label(provider))} #{idx} — yangi kalitni yuboring:", parse_mode=ParseMode.HTML)
        return

    if action.startswith("keymodel:"):
        _, provider, idx = action.split(":")
        context.user_data["awaiting_personal_key"] = {"provider": provider, "stage": "replace_model", "index": int(idx)}
        await query.answer()
        await query.edit_message_text(f"✏️ {_esc(_provider_label(provider))} #{idx} — yangi model nomini yuboring:", parse_mode=ParseMode.HTML)
        return

    if action.startswith("keydel:"):
        _, provider, idx = action.split(":")
        user_ai_keys.delete_key(user_id, provider, int(idx))
        await query.answer("🗑 O'chirildi.")
        await query.edit_message_text(_keys_menu_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_keys_menu_keyboard(user_id))
        return

    if action == "keycheck":
        await query.answer()
        await query.edit_message_text("🩺 Tekshirilmoqda, biroz kuting...")
        report = await _run_personal_key_check(user_id)
        await query.edit_message_text(report, parse_mode=ParseMode.HTML, reply_markup=_keys_menu_keyboard(user_id))
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
        url = await asyncio.to_thread(github_storage.upload_user_photo, user_id, image_bytes)
    except Exception as e:
        logger.error(f"🖼 Kabinet rasmini yuklashda kutilmagan xato (user_id={user_id}): {type(e).__name__}: {e}", exc_info=True)
        await status.edit_text("❌ Rasmni saqlashda kutilmagan xatolik yuz berdi.")
        return

    if url:
        await status.edit_text("✅ Rasm muvaffaqiyatli saqlandi! Yana rasm yuklash uchun /my ni qayta bosing.")
        logger.info(f"🖼 Kabinet rasmi muvaffaqiyatli yuklandi: user_id={user_id}.")
    else:
        await status.edit_text("❌ Rasmni saqlashda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.")


# ============================================================
# 🔑 Shaxsiy AI kalitlari — /developer > 🔑 AI kalitlari bilan BIR XIL
# prinsip, lekin user_ai_keys.py (GitHub'dagi shaxsiy papka) orqali,
# har bir foydalanuvchi FAQAT o'zinikini ko'radi.
# ============================================================

def _keys_menu_text(user_id: int) -> str:
    pools = user_ai_keys.get_pools(user_id)
    lines = ["🔑 <b>Shaxsiy AI kalitlarim</b>\n"]
    if not pools:
        lines.append("<i>Hali hech qanday shaxsiy kalit qo'shmagansiz.</i>")
    else:
        for provider, pool in pools.items():
            lines.append(f"<b>{_esc(_provider_label(provider))}:</b>")
            for i, entry in enumerate(pool, start=1):
                model = _esc(entry.get("model") or "—")
                key_shown = _esc(_mask_key(entry.get("key", "")))
                lines.append(f"  {i}. <code>{key_shown}</code> — <code>{model}</code>")
            lines.append("")
    lines.append(
        "\n💡 Shaxsiy kalitingiz pullik funksiyalarda (masalan Kurs ishi) AVVAL "
        "sinaladi — ishlasa narxning 50%i sizga qaytariladi."
    )
    return "\n".join(lines)


def _keys_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    pools = user_ai_keys.get_pools(user_id)
    rows = []
    for provider, pool in pools.items():
        btns = [
            InlineKeyboardButton(f"{_provider_label(provider)} {i}", callback_data=f"mycab:keyview:{provider}:{i}")
            for i in range(1, len(pool) + 1)
        ]
        for j in range(0, len(btns), 3):
            rows.append(btns[j:j + 3])
    rows.append([InlineKeyboardButton("➕ Yangi kalit qo'shish", callback_data="mycab:keyadd")])
    if pools:
        rows.append([InlineKeyboardButton("🩺 Kalitlarni tekshirish", callback_data="mycab:keycheck")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="mycab:back")])
    return InlineKeyboardMarkup(rows)


def _keyadd_text() -> str:
    links = "\n".join(
        f"🔹 {_esc(_provider_label(p))}: {_esc(config.PROVIDER_KEY_LINKS.get(p, ''))}"
        for p in config.SUPPORTED_PROVIDERS
    )
    return (
        "➕ <b>Yangi shaxsiy kalit qo'shish</b>\n\n"
        "Bepul API kalit olish uchun havolalar:\n"
        f"{links}\n\n"
        "Qaysi AI turi uchun kalit qo'shmoqchisiz?"
    )


def _keyadd_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(_provider_label(p), callback_data=f"mycab:keyaddprov:{p}") for p in config.SUPPORTED_PROVIDERS]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="mycab:keys")])
    return InlineKeyboardMarkup(rows)


def _key_view_text(user_id: int, provider: str, index: int) -> str:
    entry = user_ai_keys.get_key(user_id, provider, index)
    if not entry:
        return "⚠️ Bu kalit topilmadi (o'chirilgan bo'lishi mumkin)."
    return (
        f"🔑 <b>{_esc(_provider_label(provider))} — Kalit #{index}</b>\n\n"
        f"Kalit: <code>{_esc(_mask_key(entry.get('key', '')))}</code>\n"
        f"Model: <code>{_esc(entry.get('model') or '—')}</code>"
    )


def _key_view_keyboard(provider: str, index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Kalitni almashtirish", callback_data=f"mycab:keyrepl:{provider}:{index}")],
        [InlineKeyboardButton("✏️ Modelni o'zgartirish", callback_data=f"mycab:keymodel:{provider}:{index}")],
        [InlineKeyboardButton("🗑 O'chirish", callback_data=f"mycab:keydel:{provider}:{index}")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="mycab:keys")],
    ])


async def _run_personal_key_check(user_id: int) -> str:
    """/developer'dagi `_run_key_check()` bilan BIR XIL g'oya, lekin
    FAQAT shu bitta foydalanuvchining shaxsiy kalitlari uchun."""
    pools = user_ai_keys.get_pools(user_id)
    tasks, meta = [], []
    for provider, pool in pools.items():
        for i, entry in enumerate(pool, start=1):
            key, model = entry.get("key", ""), entry.get("model", "")
            if key and model:
                tasks.append(ai_clients.test_key(provider, key, model, index=i))
                meta.append((provider, i))

    results = await asyncio.gather(*tasks) if tasks else []
    result_iter = iter(results)

    lines = ["🩺 <b>Shaxsiy kalitlar tekshiruvi</b>\n"]
    ok_count, fail_count = 0, 0
    for provider, i in meta:
        status, detail = next(result_iter)
        if status == "ok":
            ok_count += 1
            lines.append(f"✅ {_esc(_provider_label(provider))} #{i} — ishlayapti")
        else:
            fail_count += 1
            lines.append(f"❌ {_esc(_provider_label(provider))} #{i} — {_esc(detail)}")

    if not meta:
        lines.append("<i>Tekshirish uchun kalit topilmadi.</i>")
    else:
        lines.append(f"\n📊 Jami: {ok_count} ishlayapti, {fail_count} ishlamayapti.")
    return "\n".join(lines)


async def on_personal_key_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """"➕ Yangi kalit qo'shish" / "🔁 Almashtirish" / "✏️ Model o'zgartirish"
    bosilgandan keyingi MATN javoblari shu yerda ushlanadi
    (`context.user_data["awaiting_personal_key"]` bayrog'i orqali).

    MUHIM: bu funksiya ALOHIDA MessageHandler sifatida EMAS, balki
    handlers/universal_chat.py > handle_message() ICHIDAN, eng boshida
    chaqiriladi (xuddi mention_dispatch tekshiruvi kabi) — sababi:
    python-telegram-bot'da bitta "guruh" (handler group) ichida FAQAT
    birinchi mos kelgan handler ishlaydi, qolganlari o'tkazib
    yuboriladi. Agar buni ALOHIDA MessageHandler(filters.TEXT, ...)
    qilib ro'yxatdan o'tkazsak, u universal_chat'ning oddiy AI-chat
    handleri bilan BIR XIL guruhda RAQOBATLASHIB, tasodifiy ravishda
    oddiy xabarlarni "yutib" yuborishi yoki aksincha kalit matnini
    AI'ga yuborib yuborishi mumkin edi. Shu funksiyani to'g'ridan-to'g'ri
    chaqirish (va u True qaytarsa handle_message DARHOL to'xtashi) bu
    muammoni butunlay bartaraf etadi.

    Qaytaradi: True — xabar shu yerda "iste'mol qilindi" (chaqiruvchi
    boshqa hech narsa qilmasligi kerak), False — bu foydalanuvchi uchun
    kutilayotgan kalit yo'q edi (oddiy xabar, chaqiruvchi davom etsin)."""
    pending = context.user_data.get("awaiting_personal_key")
    if not pending:
        return False
    if not update.message or not update.message.text:
        return False

    text = update.message.text.strip()
    user_id = update.effective_user.id
    provider = pending["provider"]
    stage = pending["stage"]

    if stage == "key":
        pending["pending_key"] = text
        pending["stage"] = "model"
        context.user_data["awaiting_personal_key"] = pending
        await update.message.reply_text(
            "✅ Kalit qabul qilindi. Endi model nomini yuboring "
            "(masalan `gemini-2.0-flash` yoki `llama-3.3-70b-versatile` kabi):",
            parse_mode=ParseMode.MARKDOWN,
        )
        return True

    if stage == "model":
        context.user_data.pop("awaiting_personal_key", None)
        idx, ok = user_ai_keys.add_key(user_id, provider, pending["pending_key"], text)
        if ok:
            await update.message.reply_text(
                f"✅ {_provider_label(provider)} kalit #{idx} muvaffaqiyatli qo'shildi!\n\n"
                f"{_keys_menu_text(user_id)}",
                parse_mode=ParseMode.HTML,
                reply_markup=_keys_menu_keyboard(user_id),
            )
            logger.info(f"🔑 Shaxsiy kalit qo'shildi va GitHub'ga saqlandi: user_id={user_id}, provider={provider}.")
        else:
            await update.message.reply_text(
                "❌ Kalit qabul qilindi, lekin GitHub'ga saqlash muvaffaqiyatsiz bo'ldi. "
                "Iltimos, birozdan keyin qayta urinib ko'ring.",
                parse_mode=ParseMode.HTML,
                reply_markup=_keys_menu_keyboard(user_id),
            )
            logger.error(f"❌ Shaxsiy kalit GitHub'ga saqlanmadi: user_id={user_id}, provider={provider}.")
        return True

    if stage == "replace_key":
        context.user_data.pop("awaiting_personal_key", None)
        ok = user_ai_keys.update_key_field(user_id, provider, pending["index"], "key", text)
        await update.message.reply_text(
            "✅ Kalit almashtirildi." if ok else "❌ Kalitni saqlashda xato yuz berdi. Qayta urinib ko'ring.",
            parse_mode=ParseMode.HTML, reply_markup=_keys_menu_keyboard(user_id),
        )
        return True

    if stage == "replace_model":
        context.user_data.pop("awaiting_personal_key", None)
        ok = user_ai_keys.update_key_field(user_id, provider, pending["index"], "model", text)
        await update.message.reply_text(
            "✅ Model o'zgartirildi." if ok else "❌ Modelni saqlashda xato yuz berdi. Qayta urinib ko'ring.",
            parse_mode=ParseMode.HTML, reply_markup=_keys_menu_keyboard(user_id),
        )
        return True

    return True  # noma'lum stage — baribir bayroqni tozalab, xabarni "iste'mol qilingan" deb belgilaymiz
