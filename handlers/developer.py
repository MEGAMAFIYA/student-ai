"""
👨‍💻 /developer — FAQAT adminlarga (config.ADMIN_IDS) ko'rinadigan buyruq.

Ikki asosiy bo'lim:
1. Har bir funksiya (Universal chat, Kurs ishi, Tarjima, PDF tahrirlash,
   Qo'llanma, Vision) uchun AI PROVIDER / MODEL / API_KEY / BASE_URL —
   bitta-kalit rejimi (agar shu provider uchun kalitlar to'plami bo'sh bo'lsa
   ishlatiladi).
2. 🔑 AI kalitlari — har bir provider (gemini, groq) uchun BIR NECHTA kalitdan
   iborat to'plam. Biri kunlik/daqiqalik limitga yoki "pullik" holatga
   o'tib qolsa, ai_clients.py avtomatik ravishda navbatdagi kalitga o'tadi.
   Har bir kalitning o'z modeli bor — shu orqali bitta provider ichida 2 xil
   model (masalan toq kalitlarga bittasi, juft kalitlarga boshqasi) qo'yish
   mumkin, model biri pullik bo'lib qolsa ikkinchisi ishlab turadi.
   Shu yerda kalitlarni sinab ko'rish (health-check) funksiyasi ham bor.

O'zgarishlar config.py orqali runtime_ai_config.json fayliga yoziladi,
shuning uchun bot qayta ishga tushganda ham saqlanib qoladi — .env faylni
tahrirlash shart emas.

MUHIM: bu yerda parse_mode="HTML" ishlatiladi, Markdown EMAS. Sabab — API
kalit, model nomi kabi qiymatlar foydalanuvchi tomonidan kiritiladi va ular
"*", "_", "`" kabi Markdown maxsus belgilarini o'z ichiga olishi mumkin
(masalan API kalitni "****" bilan bekitganda). Markdown rejimida bunday
juftlashmagan belgilar "can't find end of the entity" xatosiga olib keladi.
HTML rejimida esa faqat &, <, > belgilarini escape qilish kifoya (_esc()).
"""

import asyncio
import html
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

import ai_clients
import config
import storage
import wallet
from handlers import payment_admin as pay_ui

logger = logging.getLogger(__name__)

DEV_MENU, DEV_WAIT_TEXT, DEV_WAIT_BULK_MODEL = range(3)

_FIELD_LABELS = {
    "provider": "PROVIDER",
    "model": "MODEL",
    "api_key": "API_KEY",
    "base_url": "BASE_URL",
}

_SCOPE_LABELS = {"all": "barcha", "odd": "toq sondagi", "even": "juft sondagi"}

_STATUS_ICONS = {"ok": "✅", "quota": "⏳", "paid": "🚫", "invalid": "❌", "error": "⚠️"}
_STATUS_TEXT = {
    "ok": "ishlayapti",
    "quota": "limit tugagan",
    "paid": "model pullik",
    "invalid": "kalit yaroqsiz",
    "error": "xato",
}


# ============================================================
# Yordamchi funksiyalar
# ============================================================

def _esc(value) -> str:
    """HTML rejimida xavfsiz ko'rsatish uchun &, <, > belgilarini escape qiladi.
    Foydalanuvchi kiritgan HAR QANDAY qiymat (API kalit, model nomi, URL)
    ekranga chiqarilishidan oldin albatta shu orqali o'tishi kerak."""
    return html.escape(str(value), quote=False)


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in config.ADMIN_IDS)


def _provider_label(provider: str) -> str:
    """Provider uchun /developer da ko'rsatiladigan to'liq, chiroyli nom
    (masalan 'huggingface' -> 'Hugging Face', 'nvidia' -> 'NVIDIA NIM')."""
    return config.PROVIDER_LABELS.get(provider, provider.capitalize())


def _mask_key(value: str) -> str:
    if not value:
        return "❌ o'rnatilmagan"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _back_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    """Matn yuborishni kutayotgan HAR BIR ekranga qo'shiladigan yagona
    '⬅️ Orqaga' tugmasi. Bosilganda callback sifatida keladi va
    ConversationHandler shu holatda ham CallbackQueryHandler'ni tekshiradi
    (DEV_WAIT_TEXT/DEV_WAIT_BULK_MODEL holatlarida ham "^dev:" ro'yxatdan
    o'tgan), shuning uchun foydalanuvchi matn yozish o'rniga istalgan
    vaqtda bosib chiqib keta oladi — hech qachon "qotib qolmaydi"."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data=callback_data)]])


async def _safe_edit_query(query, text: str, reply_markup=None, parse_mode=None):
    """query.edit_message_text ni chaqiradi, lekin Telegram 'Message is not
    modified' xatosini (xuddi shu matn/tugmalar allaqachon ko'rsatilgan
    bo'lsa chiqadi — zararsiz holat) sekin e'tiborsiz qoldiradi."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def _safe_edit_bot(bot, chat_id, message_id, text: str, reply_markup=None, parse_mode=None):
    """_edit_menu() uchun xuddi shu maqsadda."""
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text,
            reply_markup=reply_markup, parse_mode=parse_mode,
        )
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def _edit_menu(context: ContextTypes.DEFAULT_TYPE, text: str, keyboard: InlineKeyboardMarkup):
    """Menyu xabarini saqlangan chat/message_id orqali yangilaydi (matnli
    javobdan keyin, query bo'lmaganda ishlatiladi)."""
    chat_id, message_id = context.user_data["dev_msg"]
    await _safe_edit_bot(context.bot, chat_id, message_id, text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================
# Asosiy menyu
# ============================================================

def _persistence_status_line() -> str:
    if config.USE_UPSTASH:
        return "💾 Doimiy saqlash: ✅ Upstash Redis (deployda yo'qolmaydi)"
    if config.USE_NEON:
        return "💾 Doimiy saqlash: ✅ Neon (Postgres) (deployda yo'qolmaydi)"
    if config.USE_GITHUB:
        return f"💾 Doimiy saqlash: ✅ GitHub repo ({_esc(config.GITHUB_REPO)}, avto-commit)"
    return (
        "💾 Doimiy saqlash: ⚠️ SOZLANMAGAN — o'zgarishlar faqat MAHALLIY faylda, "
        "qayta deployda YO'QOLADI! (DATABASE_URL, Upstash yoki GITHUB_TOKEN/GITHUB_REPO sozlang)"
    )


def _main_menu_text() -> str:
    return (
        "🔧 <b>Developer — AI sozlamalari</b>\n\n"
        f"{_persistence_status_line()}\n\n"
        "Har bir funksiya uchun AI provider, model, API kalit va bazaviy "
        "URL manzilini shu yerdan boshqarishingiz mumkin.\n\n"
        "Bo'limni tanlang:"
    )


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    rows = []
    prefixes = list(config.AI_FUNCTION_LABELS.items())
    for i in range(0, len(prefixes), 2):
        row = [
            InlineKeyboardButton(label, callback_data=f"dev:func:{prefix}")
            for prefix, label in prefixes[i:i + 2]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔑 AI kalitlari", callback_data="dev:keys")])
    rows.append([InlineKeyboardButton("➕ Barcha modellar", callback_data="dev:bulk")])
    rows.append([InlineKeyboardButton("📊 Statistika", callback_data="dev:stats")])
    rows.append([InlineKeyboardButton("💳 To'lovlar", callback_data="dev:pay"),
                 InlineKeyboardButton("💰 Balanslar", callback_data="dev:paybal")])
    rows.append([InlineKeyboardButton("📈 Moliyaviy statistika", callback_data="dev:finstats")])
    rows.append([InlineKeyboardButton("⚙️ Funksiya narxlari", callback_data="dev:payprice"),
                 InlineKeyboardButton("💳 To'lov sozlamalari", callback_data="dev:paysettings")])
    rows.append([InlineKeyboardButton("❌ Yopish", callback_data="dev:close")])
    return InlineKeyboardMarkup(rows)


# ============================================================
# Funksiya sozlamalari (bitta-kalit rejimi)
# ============================================================

def _func_menu_text(prefix: str) -> str:
    cfg = config.AI_FUNCTIONS[prefix]
    label = _esc(config.AI_FUNCTION_LABELS[prefix])
    base_url = _esc(cfg.get("base_url") or "(bo'sh — standart ishlatiladi)")
    provider = _esc(cfg.get("provider") or "—")
    model = _esc(cfg.get("model") or "—")
    api_key = _esc(_mask_key(cfg.get("api_key", "")))
    pool_note = ""
    if config.KEY_POOLS.get(cfg.get("provider", ""), []):
        pool_note = (
            f"\n\n⚠️ <i>{provider} uchun kalitlar to'plami mavjud — bu funksiya "
            "shu to'plamdagi kalitlarni ustuvor ishlatadi, quyidagi API_KEY "
            "e'tiborga olinmaydi (🔑 AI kalitlari bo'limiga qarang).</i>"
        )
    return (
        f"{label} — AI sozlamalari\n\n"
        f"PROVIDER: <code>{provider}</code>\n"
        f"MODEL: <code>{model}</code>\n"
        f"API_KEY: <code>{api_key}</code>\n"
        f"BASE_URL: <code>{base_url}</code>"
        f"{pool_note}\n\n"
        "O'zgartirish uchun maydonni tanlang:"
    )


def _func_menu_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ PROVIDER", callback_data=f"dev:editprov:{prefix}"),
            InlineKeyboardButton("✏️ MODEL", callback_data=f"dev:edit:{prefix}:model"),
        ],
        [
            InlineKeyboardButton("✏️ API_KEY", callback_data=f"dev:edit:{prefix}:api_key"),
            InlineKeyboardButton("✏️ BASE_URL", callback_data=f"dev:edit:{prefix}:base_url"),
        ],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")],
    ])


def _provider_choice_keyboard(back_callback: str, choose_prefix: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(_provider_label(p), callback_data=f"{choose_prefix}:{p}")
        for p in config.SUPPORTED_PROVIDERS
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data=back_callback)])
    return InlineKeyboardMarkup(rows)


# ============================================================
# 🔑 AI kalitlari (kalitlar to'plami)
# ============================================================

def _keys_menu_text() -> str:
    lines = ["🔑 <b>AI kalitlari</b>\n"]
    for provider in config.SUPPORTED_PROVIDERS:
        pool = config.KEY_POOLS.get(provider, [])
        lines.append(f"<b>{_esc(_provider_label(provider))} kalitlar:</b>")
        if not pool:
            lines.append("  <i>(hali kalit qo'shilmagan)</i>")
        else:
            for i, entry in enumerate(pool, start=1):
                model = _esc(entry.get("model") or "—")
                key_shown = _esc(_mask_key(entry.get("key", "")))
                lines.append(f"  {i}. <code>{key_shown}</code> — <code>{model}</code>")
        lines.append("")
    lines.append("Tahrirlash uchun pastdagi tugmalardan kalitni tanlang:")
    return "\n".join(lines)


def _keys_menu_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for provider in config.SUPPORTED_PROVIDERS:
        pool = config.KEY_POOLS.get(provider, [])
        btns = [
            InlineKeyboardButton(f"{_provider_label(provider)} {i}", callback_data=f"dev:keyview:{provider}:{i}")
            for i in range(1, len(pool) + 1)
        ]
        for j in range(0, len(btns), 3):
            rows.append(btns[j:j + 3])
    rows.append([InlineKeyboardButton("➕ Yangi kalit qo'shish", callback_data="dev:keyadd")])
    rows.append([InlineKeyboardButton("🔀 Modellarni o'zgartirish", callback_data="dev:keybulk")])
    rows.append([InlineKeyboardButton("🩺 Kalitlarni tekshirish", callback_data="dev:keycheck")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")])
    return InlineKeyboardMarkup(rows)


def _key_view_text(provider: str, index: int) -> str:
    pool = config.KEY_POOLS.get(provider, [])
    if not (1 <= index <= len(pool)):
        return "⚠️ Bu kalit topilmadi (o'chirilgan bo'lishi mumkin)."
    entry = pool[index - 1]
    return (
        f"🔑 <b>{_esc(_provider_label(provider))} — Kalit #{index}</b>\n\n"
        f"Kalit: <code>{_esc(_mask_key(entry.get('key', '')))}</code>\n"
        f"Model: <code>{_esc(entry.get('model') or '—')}</code>"
    )


def _key_view_keyboard(provider: str, index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Kalitni almashtirish", callback_data=f"dev:keyrepl:{provider}:{index}")],
        [InlineKeyboardButton("✏️ Modelni o'zgartirish", callback_data=f"dev:keymodel:{provider}:{index}")],
        [InlineKeyboardButton("🗑 O'chirish", callback_data=f"dev:keydel:{provider}:{index}")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:keys")],
    ])


def _keyadd_text() -> str:
    links = "\n".join(
        f"🔹 {_esc(_provider_label(p))}: {_esc(config.PROVIDER_KEY_LINKS.get(p, ''))}"
        for p in config.SUPPORTED_PROVIDERS
    )
    return (
        "➕ <b>Yangi kalit qo'shish</b>\n\n"
        "Bepul API kalit olish uchun havolalar:\n"
        f"{links}\n\n"
        "Qaysi AI turi uchun kalit qo'shmoqchisiz?"
    )


def _keyadd_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(_provider_label(p), callback_data=f"dev:keyaddprov:{p}") for p in config.SUPPORTED_PROVIDERS]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:keys")])
    return InlineKeyboardMarkup(rows)


def _keybulk_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(_provider_label(p), callback_data=f"dev:keybulkprov:{p}") for p in config.SUPPORTED_PROVIDERS]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:keys")])
    return InlineKeyboardMarkup(rows)


def _keybulk_scope_keyboard(provider: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Barchasi", callback_data=f"dev:keybulkscope:{provider}:all")],
        [InlineKeyboardButton("Toq (1, 3, 5...)", callback_data=f"dev:keybulkscope:{provider}:odd")],
        [InlineKeyboardButton("Juft (2, 4, 6...)", callback_data=f"dev:keybulkscope:{provider}:even")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:keybulk")],
    ])


def _stats_text() -> str:
    stats = storage.get_stats()
    lines = [
        "📊 <b>Statistika</b>\n",
        f"👥 Jami noyob foydalanuvchilar: <b>{stats['total_users']}</b>",
        f"🔢 Jami muvaffaqiyatli so'rovlar: <b>{stats['total_events']}</b>\n",
        "<b>Funksiya bo'yicha (jami / noyob foydalanuvchi / bugun):</b>",
    ]
    for label, total, unique, today in stats["per_function"]:
        if total == 0:
            lines.append(f"  <i>{_esc(label)} — hali ishlatilmagan</i>")
        else:
            lines.append(f"  {_esc(label)}: {total} / {unique} / {today}")
    return "\n".join(lines)


def _stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Yangilash", callback_data="dev:stats")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")],
    ])


async def _run_key_check() -> str:
    """Barcha provider'lardagi BARCHA kalitlarni parallel sinaydi va
    natijalarni HTML matn shaklida qaytaradi. Har bir kalitning natijasi
    (va ishlamasa — sababi) render logiga ham yoziladi, shuning uchun
    keyinchalik Render dashboard > Logs bo'limidan qaysi kalit nima uchun
    ishlamayotganini ko'rish mumkin."""
    logger.info("🩺 Kalitlarni tekshirish boshlandi (/developer > AI kalitlari > Tekshirish)...")
    tasks, meta = [], []
    for provider in config.SUPPORTED_PROVIDERS:
        for i, entry in enumerate(config.KEY_POOLS.get(provider, []), start=1):
            key, model = entry.get("key", ""), entry.get("model", "")
            if key and model:
                tasks.append(ai_clients.test_key(provider, key, model, index=i))
                meta.append((provider, i, model, True))
            else:
                meta.append((provider, i, model, False))

    results = await asyncio.gather(*tasks) if tasks else []
    result_iter = iter(results)

    by_provider: dict[str, list] = {p: [] for p in config.SUPPORTED_PROVIDERS}
    ok_count, fail_count = 0, 0
    for provider, i, model, has_data in meta:
        if has_data:
            status, detail = next(result_iter)
        else:
            status, detail = "invalid", "Kalit yoki model kiritilmagan."
        if status == "ok":
            ok_count += 1
        else:
            fail_count += 1
        by_provider[provider].append((i, status, model, detail))

    logger.info(f"🩺 Kalitlarni tekshirish tugadi: {ok_count} ishlayapti, {fail_count} ishlamayapti.")

    lines = ["🩺 <b>Kalitlar holati</b>\n"]
    any_key = False
    for provider in config.SUPPORTED_PROVIDERS:
        items = by_provider[provider]
        lines.append(f"<b>{_esc(_provider_label(provider))}:</b>")
        if not items:
            lines.append("  <i>(kalit yo'q)</i>")
        else:
            any_key = True
            for i, status, model, detail in items:
                icon = _STATUS_ICONS.get(status, "⚠️")
                text = _STATUS_TEXT.get(status, status)
                line = f"  {i}. {icon} {text} (<code>{_esc(model)}</code>)"
                if status != "ok" and detail:
                    line += f"\n     <i>{_esc(detail)}</i>"
                lines.append(line)
        lines.append("")
    if not any_key:
        lines.append("Hali birorta ham kalit qo'shilmagan.")
    return "\n".join(lines)


# ============================================================
# Kirish nuqtasi va callback dispatcher
# ============================================================

async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text("🚫 Bu buyruq faqat administratorlar uchun.")
        return ConversationHandler.END

    context.user_data.clear()
    msg = await update.message.reply_text(
        _main_menu_text(), reply_markup=_main_menu_keyboard(), parse_mode="HTML"
    )
    context.user_data["dev_msg"] = (msg.chat_id, msg.message_id)
    return DEV_MENU


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_admin(update):
        await _safe_edit_query(query, "🚫 Ruxsat yo'q.")
        return ConversationHandler.END

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    # Har qanday navigatsiya (orqaga tugmasi ham shu yo'l bilan ishlaydi)
    # oldingi "kutilayotgan matn kiritish" holatini tozalaydi — pastda
    # tegishli branch (edit/keyaddprov/keyrepl/keymodel/bulkprov/keybulkscope)
    # kerak bo'lsa uni qaytadan o'rnatadi.
    if action not in ("edit", "bulkprov", "keyaddprov", "keyrepl", "keymodel", "keybulkscope", "priceedit", "balsearch"):
        context.user_data.pop("dev_action", None)

    # ---------- Asosiy menyu ----------
    if action == "close":
        await _safe_edit_query(query, "✅ Yopildi.")
        context.user_data.clear()
        return ConversationHandler.END

    if action == "menu":
        await _safe_edit_query(query, _main_menu_text(), reply_markup=_main_menu_keyboard(), parse_mode="HTML")
        return DEV_MENU

    # ---------- Funksiya sozlamalari ----------
    if action == "func":
        prefix = parts[2]
        await _safe_edit_query(query, _func_menu_text(prefix), reply_markup=_func_menu_keyboard(prefix), parse_mode="HTML")
        return DEV_MENU

    if action == "editprov":
        prefix = parts[2]
        await _safe_edit_query(
            query,
            f"{_esc(config.AI_FUNCTION_LABELS[prefix])} — PROVIDER tanlang:",
            reply_markup=_provider_choice_keyboard(f"dev:func:{prefix}", f"dev:setprov:{prefix}"),
        )
        return DEV_MENU

    if action == "setprov":
        prefix, provider = parts[2], parts[3]
        config.update_ai_field(prefix, "provider", provider)
        await _safe_edit_query(
            query,
            f"✅ PROVIDER — <b>{_esc(provider)}</b> qilib o'rnatildi.\n\n" + _func_menu_text(prefix),
            reply_markup=_func_menu_keyboard(prefix), parse_mode="HTML",
        )
        return DEV_MENU

    if action == "edit":
        prefix, field = parts[2], parts[3]
        context.user_data["dev_action"] = {"type": "func_field", "prefix": prefix, "field": field}
        current = config.AI_FUNCTIONS[prefix].get(field, "")
        shown = _esc(_mask_key(current) if field == "api_key" else (current or "(bo'sh)"))
        await _safe_edit_query(
            query,
            f"{_esc(config.AI_FUNCTION_LABELS[prefix])} — <b>{_FIELD_LABELS[field]}</b>\n"
            f"Joriy qiymat: <code>{shown}</code>\n\n"
            "Yangi qiymatni xabar qilib yuboring.\n"
            "Bo'sh qilish uchun <code>-</code> yuboring.",
            reply_markup=_back_keyboard(f"dev:func:{prefix}"),
            parse_mode="HTML",
        )
        return DEV_WAIT_TEXT

    if action == "bulk":
        await _safe_edit_query(
            query,
            "➕ <b>Barcha modellar</b>\n\nAvval AI turini (provider) tanlang — shu "
            "turdagi BARCHA funksiyalarning modeli birdaniga o'zgaradi:",
            reply_markup=_provider_choice_keyboard("dev:menu", "dev:bulkprov"),
            parse_mode="HTML",
        )
        return DEV_MENU

    if action == "bulkprov":
        provider = parts[2]
        context.user_data["dev_action"] = {"type": "bulk_func_model", "provider": provider}
        affected = [
            config.AI_FUNCTION_LABELS[p] for p, c in config.AI_FUNCTIONS.items() if c.get("provider") == provider
        ]
        affected_txt = "\n".join(f"• {_esc(a)}" for a in affected) or "(hozircha bu provider'da hech qaysi funksiya yo'q)"
        await _safe_edit_query(
            query,
            f"➕ <b>Barcha modellar — {_esc(provider)}</b>\n\n"
            f"Ta'sir qiladigan funksiyalar:\n{affected_txt}\n\n"
            "Yangi model nomini xabar qilib yuboring:",
            reply_markup=_back_keyboard("dev:bulk"),
            parse_mode="HTML",
        )
        return DEV_WAIT_BULK_MODEL

    # ---------- 🔑 AI kalitlari ----------
    if action == "keys":
        await _safe_edit_query(query, _keys_menu_text(), reply_markup=_keys_menu_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "keyview":
        provider, idx = parts[2], int(parts[3])
        await _safe_edit_query(query, _key_view_text(provider, idx), reply_markup=_key_view_keyboard(provider, idx), parse_mode="HTML")
        return DEV_MENU

    if action == "keyadd":
        await _safe_edit_query(query, _keyadd_text(), reply_markup=_keyadd_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "keyaddprov":
        provider = parts[2]
        context.user_data["dev_action"] = {"type": "add_key", "provider": provider}
        if provider == "cloudflare":
            prompt_text = (
                f"➕ <b>{_esc(_provider_label(provider))}</b>\n\n"
                "Cloudflare uchun oddiy API kalit YETARLI EMAS — Account ID "
                "ham kerak. Ikkalasini quyidagi formatda, ORASIGA IKKI NUQTA "
                "qo'yib, BITTA xabar qilib yuboring:\n"
                "<code>account_id:api_key</code>\n\n"
                "(Account ID va API tokenni dash.cloudflare.com sahifasining "
                "o'ng tomonidagi \"Account ID\" va Workers AI bo'limidan olasiz.)"
            )
        else:
            prompt_text = f"➕ <b>{_esc(_provider_label(provider))}</b> — yangi API kalitni xabar qilib yuboring:"
        await _safe_edit_query(query, prompt_text, reply_markup=_back_keyboard("dev:keyadd"), parse_mode="HTML")
        return DEV_WAIT_TEXT

    if action == "keyrepl":
        provider, idx = parts[2], int(parts[3])
        context.user_data["dev_action"] = {"type": "key_field", "provider": provider, "index": idx, "field": "key"}
        await _safe_edit_query(
            query,
            f"🔁 {_esc(_provider_label(provider))} — Kalit #{idx}\nYangi API kalitni xabar qilib yuboring:",
            reply_markup=_back_keyboard(f"dev:keyview:{provider}:{idx}"),
        )
        return DEV_WAIT_TEXT

    if action == "keymodel":
        provider, idx = parts[2], int(parts[3])
        context.user_data["dev_action"] = {"type": "key_field", "provider": provider, "index": idx, "field": "model"}
        await _safe_edit_query(
            query,
            f"✏️ {_esc(_provider_label(provider))} — Kalit #{idx}\nYangi model nomini xabar qilib yuboring:",
            reply_markup=_back_keyboard(f"dev:keyview:{provider}:{idx}"),
        )
        return DEV_WAIT_TEXT

    if action == "keydel":
        provider, idx = parts[2], int(parts[3])
        config.delete_key(provider, idx)
        await _safe_edit_query(
            query, "🗑 O'chirildi.\n\n" + _keys_menu_text(), reply_markup=_keys_menu_keyboard(), parse_mode="HTML"
        )
        return DEV_MENU

    if action == "keybulk":
        await _safe_edit_query(
            query,
            "🔀 <b>Modellarni o'zgartirish</b>\n\nQaysi AI turi?",
            reply_markup=_keybulk_keyboard(), parse_mode="HTML",
        )
        return DEV_MENU

    if action == "keybulkprov":
        provider = parts[2]
        await _safe_edit_query(
            query,
            f"🔀 <b>{_esc(_provider_label(provider))}</b> — qaysi kalitlar o'zgartirilsin?",
            reply_markup=_keybulk_scope_keyboard(provider), parse_mode="HTML",
        )
        return DEV_MENU

    if action == "keybulkscope":
        provider, scope = parts[2], parts[3]
        context.user_data["dev_action"] = {"type": "bulk_pool_model", "provider": provider, "scope": scope}
        await _safe_edit_query(
            query,
            f"🔀 {_esc(_provider_label(provider))} — {_SCOPE_LABELS.get(scope, scope)} kalitlar uchun "
            "yangi model nomini xabar qilib yuboring:",
            reply_markup=_back_keyboard(f"dev:keybulkprov:{provider}"),
        )
        return DEV_WAIT_BULK_MODEL

    if action == "stats":
        await _safe_edit_query(query, _stats_text(), reply_markup=_stats_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "keycheck":
        await _safe_edit_query(query, "🩺 Tekshirilmoqda, biroz kuting...")
        report = await _run_key_check()
        await _edit_menu(context, report, _keys_menu_keyboard())
        return DEV_MENU

    # ---------- 💳 To'lovlar ----------
    if action == "pay":
        await _safe_edit_query(query, pay_ui.payments_menu_text(), reply_markup=pay_ui.payments_menu_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "paylist":
        tab_key = parts[2]
        await _safe_edit_query(
            query, pay_ui.payment_list_text(tab_key), reply_markup=pay_ui.payment_list_keyboard(tab_key), parse_mode="HTML"
        )
        return DEV_MENU

    if action == "payview":
        payment_id = parts[2]
        # Qaysi ro'yxatdan kelganini bilmasak ham, "orqaga" tugmasi xavfsiz
        # standart sifatida "pending" bo'limiga qaytaradi.
        await _safe_edit_query(
            query, pay_ui.payment_detail_text(payment_id),
            reply_markup=pay_ui.payment_detail_keyboard(payment_id), parse_mode="HTML",
        )
        return DEV_MENU

    if action == "payact":
        sub_action, payment_id = parts[2], parts[3]
        admin_id = update.effective_user.id
        ok, message = pay_ui.apply_payment_action(payment_id, sub_action, admin_id)
        if ok:
            from handlers import wallet_ui
            payment = wallet.get_payment(payment_id)
            if payment:
                await wallet_ui.notify_user_payment_decision(
                    context.bot, payment, approved=(sub_action == "approve"),
                    reason="" if sub_action == "approve" else "Admin tomonidan rad etildi.",
                )
        await _safe_edit_query(
            query, f"{message}\n\n" + pay_ui.payment_detail_text(payment_id),
            reply_markup=pay_ui.payment_detail_keyboard(payment_id), parse_mode="HTML",
        )
        return DEV_MENU

    if action == "payphoto":
        payment_id = parts[2]
        payment = wallet.get_payment(payment_id)
        receipt = (payment or {}).get("receipt") or {}
        if receipt.get("file_id"):
            try:
                await context.bot.send_document(update.effective_chat.id, receipt["file_id"], caption=f"🧾 Chek — payment_id: {payment_id}")
            except Exception:
                await context.bot.send_photo(update.effective_chat.id, receipt["file_id"], caption=f"🧾 Chek — payment_id: {payment_id}")
        return DEV_MENU

    # ---------- 💰 Balanslar ----------
    if action == "paybal":
        await _safe_edit_query(query, pay_ui.balances_text(), reply_markup=pay_ui.balances_keyboard(), parse_mode="HTML")
        return DEV_MENU

    # ---------- 📈 Moliyaviy statistika ----------
    if action == "finstats":
        await _safe_edit_query(query, pay_ui.financial_stats_text(), reply_markup=pay_ui.financial_stats_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "resactive":
        await _safe_edit_query(query, pay_ui.active_reservations_text(), reply_markup=pay_ui.active_reservations_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "balsearch":
        context.user_data["dev_action"] = {"type": "balance_search"}
        await _safe_edit_query(
            query, "🔍 Qidirilayotgan foydalanuvchining Telegram ID raqamini yuboring:",
            reply_markup=_back_keyboard("dev:paybal"),
        )
        return DEV_WAIT_TEXT

    # ---------- ⚙️ Funksiya narxlari ----------
    if action == "payprice":
        await _safe_edit_query(query, pay_ui.price_menu_text(), reply_markup=pay_ui.price_menu_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "pricefeat":
        feature_id = parts[2]
        await _safe_edit_query(
            query, pay_ui.price_detail_text(feature_id),
            reply_markup=pay_ui.price_detail_keyboard(feature_id), parse_mode="HTML",
        )
        return DEV_MENU

    if action == "priceedit":
        feature_id = parts[2]
        context.user_data["dev_action"] = {"type": "feature_price", "feature_id": feature_id}
        await _safe_edit_query(
            query, "✏️ Yangi narxni (so'mda, faqat butun son, 0 — bepul) yuboring:",
            reply_markup=_back_keyboard(f"dev:pricefeat:{feature_id}"),
        )
        return DEV_WAIT_TEXT

    if action == "pricetoggle":
        feature_id = parts[2]
        f = wallet.get_feature(feature_id)
        if f:
            wallet.set_feature_enabled(feature_id, not f.get("enabled", True), actor_id=update.effective_user.id)
        await _safe_edit_query(
            query, pay_ui.price_detail_text(feature_id),
            reply_markup=pay_ui.price_detail_keyboard(feature_id), parse_mode="HTML",
        )
        return DEV_MENU

    # ---------- 💳 To'lov sozlamalari ----------
    if action == "paysettings":
        await _safe_edit_query(query, pay_ui.payment_settings_text(), reply_markup=pay_ui.payment_settings_keyboard(), parse_mode="HTML")
        return DEV_MENU

    return DEV_MENU


# ============================================================
# Matnli javoblarni qabul qilish
# ============================================================

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return ConversationHandler.END

    action = context.user_data.get("dev_action") or {}
    action_type = action.get("type")
    raw_value = update.message.text.strip()

    # Xavfsizlik uchun: API kalit kabi maxfiy qiymat chatda uzoq turmasin.
    try:
        await update.message.delete()
    except Exception:
        pass

    if action_type is None:
        return DEV_MENU

    if action_type == "func_field":
        prefix, field = action["prefix"], action["field"]
        value = "" if raw_value in ("-", "bosh", "bo'sh") else raw_value
        config.update_ai_field(prefix, field, value)
        await _edit_menu(
            context,
            f"✅ {_FIELD_LABELS[field]} yangilandi.\n\n" + _func_menu_text(prefix),
            _func_menu_keyboard(prefix),
        )

    elif action_type == "key_field":
        provider, idx, field = action["provider"], action["index"], action["field"]
        value = "" if raw_value in ("-", "bosh", "bo'sh") else raw_value
        config.update_key_field(provider, idx, field, value)
        field_label = "Kalit" if field == "key" else "Model"
        await _edit_menu(
            context,
            f"✅ {field_label} yangilandi.\n\n" + _key_view_text(provider, idx),
            _key_view_keyboard(provider, idx),
        )

    elif action_type == "add_key":
        provider = action["provider"]
        if not raw_value:
            context.user_data.pop("dev_action", None)
            return DEV_MENU
        default_model = config.DEFAULT_MODEL_BY_PROVIDER.get(provider, "")
        idx = config.add_key(provider, raw_value, default_model)
        await _edit_menu(
            context,
            f"✅ {_esc(_provider_label(provider))} kalit #{idx} qo'shildi "
            f"(standart model: <code>{_esc(default_model)}</code>).\n"
            "Boshqa model qo'yish uchun kalitni ochib \"✏️ Modelni o'zgartirish\"ni bosing.\n\n"
            + _keys_menu_text(),
            _keys_menu_keyboard(),
        )

    elif action_type == "feature_price":
        feature_id = action["feature_id"]
        if not raw_value.isdigit():
            await _edit_menu(
                context,
                "⚠️ Iltimos, faqat butun son yuboring (masalan: 5000 yoki 0).\n\n" + pay_ui.price_detail_text(feature_id),
                pay_ui.price_detail_keyboard(feature_id),
            )
            context.user_data["dev_action"] = action
            return DEV_WAIT_TEXT
        price = int(raw_value)
        wallet.set_feature_price(feature_id, price, actor_id=update.effective_user.id)
        await _edit_menu(
            context, f"✅ Narx yangilandi.\n\n" + pay_ui.price_detail_text(feature_id),
            pay_ui.price_detail_keyboard(feature_id),
        )

    elif action_type == "balance_search":
        if not raw_value.isdigit():
            await _edit_menu(
                context, "⚠️ Iltimos, faqat Telegram ID raqamini yuboring.\n\n" + pay_ui.balances_text(),
                pay_ui.balances_keyboard(),
            )
            context.user_data["dev_action"] = action
            return DEV_WAIT_TEXT
        await _edit_menu(
            context, pay_ui.user_balance_text(int(raw_value)),
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:paybal")]]),
        )

    context.user_data.pop("dev_action", None)
    return DEV_MENU


async def on_bulk_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return ConversationHandler.END

    action = context.user_data.get("dev_action") or {}
    action_type = action.get("type")
    model = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    if not model:
        return DEV_MENU

    if action_type == "bulk_func_model":
        provider = action["provider"]
        updated = config.bulk_update_model_by_provider(provider, model)
        if updated:
            names = "\n".join(f"• {_esc(config.AI_FUNCTION_LABELS[p])}" for p in updated)
            text = f"✅ <b>{_esc(provider)}</b> uchun model <b>{_esc(model)}</b> ga o'zgartirildi:\n\n{names}"
        else:
            text = f"⚠️ <b>{_esc(provider)}</b> provider'ida hech qaysi funksiya topilmadi — hech narsa o'zgarmadi."
        await _edit_menu(context, text + "\n\n" + _main_menu_text(), _main_menu_keyboard())

    elif action_type == "bulk_pool_model":
        provider, scope = action["provider"], action["scope"]
        updated = config.bulk_update_pool_models(provider, scope, model)
        scope_label = _SCOPE_LABELS.get(scope, scope)
        if updated:
            idxs = ", ".join(f"#{i}" for i in updated)
            text = (
                f"✅ {_esc(_provider_label(provider))} — {scope_label} kalitlar ({idxs}) modeli "
                f"<b>{_esc(model)}</b> ga o'zgartirildi."
            )
        else:
            text = f"⚠️ {_esc(_provider_label(provider))} da mos keladigan kalit topilmadi."
        await _edit_menu(context, text + "\n\n" + _keys_menu_text(), _keys_menu_keyboard())

    context.user_data.pop("dev_action", None)
    return DEV_MENU


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bekor qilindi.")
    context.user_data.clear()
    return ConversationHandler.END
