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
import io
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup

import github_dev
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

import ai_clients
import render_api
import config
import pro_subscription
import storage
import video_tools
import wallet
from handlers import payment_admin as pay_ui

logger = logging.getLogger(__name__)

DEV_MENU, DEV_WAIT_TEXT, DEV_WAIT_BULK_MODEL, DEV_WAIT_AUDIO, DEV_WAIT_GITHUB = range(5)

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
    rows.append([InlineKeyboardButton("🎵 Qo'shiq qidirish", callback_data="dev:music")])
    rows.append([InlineKeyboardButton("➕ Barcha modellar", callback_data="dev:bulk")])
    rows.append([InlineKeyboardButton("📊 Statistika", callback_data="dev:stats")])
    rows.append([InlineKeyboardButton("☁️ RENDER", callback_data="dev:render")])
    rows.append([InlineKeyboardButton("🔍 Inline jurnali (@Bot ...)", callback_data="dev:inlinelog:all")])
    rows.append([InlineKeyboardButton("💎 Pro / Tabrik sozlamalari", callback_data="dev:pt")])
    rows.append([InlineKeyboardButton("💎 Pro obunalar", callback_data="dev:prosub")])
    rows.append([InlineKeyboardButton("💳 To'lovlar", callback_data="dev:pay"),
                 InlineKeyboardButton("💰 Balanslar", callback_data="dev:paybal")])
    rows.append([InlineKeyboardButton("📈 Moliyaviy statistika", callback_data="dev:finstats")])
    rows.append([InlineKeyboardButton("☁️ GitHub", callback_data="dev:github")])
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


# ============================================================
# 🔍 Inline jurnali (@Student_ai_uz_bot ...) — bot GURUHGA A'ZO
# BO'LMASDAN yoki SHAXSIY chatda "@Bot savol/buyruq" deb ishlatilganda
# (Telegram inline rejimi) qayd etilgan yozuvlar. Qarang:
# handlers/inline_query.py -> storage.record_inline_log().
# ============================================================

_INLINE_LOG_STATUS_LABEL = {
    "ok": "✅ ishladi",
    "error": "❌ ishlamadi",
    "redirect": "↪️ shaxsiy chatga yo'naltirildi",
    "instruction": "⚠️ buyruq to'liq emas edi",
}


def _inline_log_text(status_filter: str | None) -> str:
    entries = storage.get_inline_logs(limit=15, status_filter=status_filter)

    title_suffix = {
        None: "so'nggi 15 ta yozuv",
        "error": "faqat ishlamaganlar (so'nggi 15 ta)",
    }.get(status_filter, f"faqat: {status_filter}")

    lines = [
        f"🔍 <b>Inline jurnali</b> ({_esc(title_suffix)})\n",
        "<i>Foydalanuvchilar botni guruhga a'zo qilmasdan yoki shaxsiy "
        "chatda \"@Student_ai_uz_bot savol/buyruq\" deb ishlatganda shu "
        "yerda qayd etiladi.</i>\n",
    ]

    if not entries:
        lines.append("<i>Hozircha yozuv yo'q.</i>")
        return "\n".join(lines)

    for e in entries:
        ts = (e.get("ts") or "")[:16].replace("T", " ")
        who = _esc(e.get("username") or "") or f"id:{e.get('user_id')}"
        q = _esc((e.get("query") or "")[:120])
        status_label = _INLINE_LOG_STATUS_LABEL.get(e.get("status"), e.get("status") or "?")
        line = f"🕐 {ts} | 👤 {who}\n❓ {q}\n{status_label}"
        detail = e.get("detail")
        if detail:
            line += f"\n   ↳ sabab: {_esc(detail[:150])}"
        lines.append(line)

    result = "\n\n".join(lines[:2]) + "\n\n" + "\n\n".join(lines[2:])
    if len(result) > 3900:
        result = result[:3900] + "\n\n<i>… (qolganlari qisqartirildi)</i>"
    return result


def _inline_log_keyboard(status_filter: str | None) -> InlineKeyboardMarkup:
    all_cb = "dev:inlinelog:all"
    err_cb = "dev:inlinelog:error"
    rows = [
        [
            InlineKeyboardButton(
                ("• " if status_filter is None else "") + "📋 Barchasi",
                callback_data=all_cb,
            ),
            InlineKeyboardButton(
                ("• " if status_filter == "error" else "") + "❌ Faqat ishlamaganlar",
                callback_data=err_cb,
            ),
        ],
        [InlineKeyboardButton("🔄 Yangilash", callback_data=f"dev:inlinelog:{status_filter or 'all'}")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")],
    ]
    return InlineKeyboardMarkup(rows)


# ============================================================
# 💎 /pro + /tabrik umumiy sozlamalari
# ============================================================

def _pt_text() -> str:
    st = config.get_tabrik_settings()
    audio = "✅ o'rnatilgan" if st["audio_file_id"] else "❌ o'rnatilmagan"
    emojis = " ".join(st["emojis"])
    return (
        "💎 <b>Pro / Tabrik sozlamalari</b>\n\n"
        f"🎵 Qo'shiq: {audio}\n"
        f"😀 Emojilar: {_esc(emojis)}\n"
        f"⏱ Emoji soniyasi: <b>{st['emoji_delay']} soniya</b>\n"
        f"↩️ Matn qaytish daqiqasi: <b>{st['revert_minutes']} daqiqa</b>\n\n"
        "Bu sozlamalarning barchasi <b>/tabrik</b> va <b>/pro</b> uchun bir xil ishlaydi."
    )

def _pt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Qo'shiq yuklash", callback_data="dev:ptsong")],
        [InlineKeyboardButton("😀 Emoji tanlash", callback_data="dev:ptemoji")],
        [InlineKeyboardButton("⏱ Emoji soniyasi", callback_data="dev:ptdelay")],
        [InlineKeyboardButton("↩️ Matn qaytish daqiqasi", callback_data="dev:ptrevert")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")],
    ])

def _pt_choice_keyboard(kind: str, values: list[int], back="dev:pt") -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(values), 3):
        rows.append([InlineKeyboardButton(str(v), callback_data=f"dev:{kind}:{v}") for v in values[i:i+3]])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data=back)])
    return InlineKeyboardMarkup(rows)

# ============================================================
# 💎 Pro obunalar (kutilayotgan so'rovlar ro'yxati)
# ============================================================

def _prosub_menu_text() -> str:
    pending = pro_subscription.get_pending_requests()
    lines = [
        "💎 <b>Pro obuna so'rovlari</b>\n",
        f"💰 Narx: {config.PRO_SUBSCRIPTION_PRICE_SUM:,} so'm / {config.PRO_SUBSCRIPTION_DAYS} kun".replace(",", " "),
        f"💳 Karta: <code>{_esc(config.PAYMENT_CARD_NUMBER or '(sozlanmagan)')}</code>\n",
    ]
    if not pending:
        lines.append("<i>Hozircha kutilayotgan so'rov yo'q.</i>")
    else:
        lines.append(f"<b>Kutilayotgan so'rovlar ({len(pending)}):</b>")
        for req in pending:
            lines.append(f"  • user_id=<code>{req['user_id']}</code> — req_id=<code>{req['req_id']}</code>")
        lines.append("\nHar birini tasdiqlash/rad etish uchun pastdagi tugmalardan foydalaning:")
    return "\n".join(lines)


def _prosub_menu_keyboard() -> InlineKeyboardMarkup:
    pending = pro_subscription.get_pending_requests()
    rows = []
    for req in pending[:15]:  # bitta xabarga sig'ishi uchun cheklov
        rows.append([
            InlineKeyboardButton(f"✅ {req['user_id']}", callback_data=f"prosub:approve:{req['req_id']}"),
            InlineKeyboardButton(f"❌ {req['user_id']}", callback_data=f"prosub:reject:{req['req_id']}"),
        ])
    rows.append([InlineKeyboardButton("🔄 Yangilash", callback_data="dev:prosub")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")])
    return InlineKeyboardMarkup(rows)


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
# 🎵 Qo'shiq qidirish — manbalarni YOQISH/O'CHIRISH + Tekshirish
# ============================================================

_MUSIC_CHECK_STATUS = {
    "ok": "🟢 Ishlayapti",
    "partial": "🟡 Qisman ishlayapti",
    "error": "🔴 Ishlamayapti",
    "off": "⚪️ O'chirilgan",
}


def _music_status_label(source_id: str) -> str:
    return "🟢 YOQILGAN" if config.is_music_source_enabled(source_id) else "🔴 O'CHIRILGAN"


def _music_menu_text() -> str:
    lines = ["🎵 <b>Qo'shiq qidirish sozlamalari</b>\n"]
    for sid in config.MUSIC_SEARCH_SOURCE_IDS:
        label = _esc(config.MUSIC_SEARCH_SOURCE_LABELS[sid])
        lines.append(f"{label}: {_music_status_label(sid)}")
    if config.is_music_source_enabled("telegram") and not config.TG_SEARCH_ENABLED:
        lines.append(
            "\n⚠️ <i>Telegram manbasi bu yerda YOQILGAN, lekin TG_API_ID/"
            "TG_API_HASH/TG_SESSION/TG_SEARCH_CHANNELS to'liq sozlanmagan — "
            "shu sabab amalda ishlamaydi ('🔍 Tekshirish' orqali aniq "
            "sababni ko'rishingiz mumkin).</i>"
        )
    lines.append("\nManbani yoqish/o'chirish uchun tugmani bosing:")
    return "\n".join(lines)


def _music_menu_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for sid in config.MUSIC_SEARCH_SOURCE_IDS:
        label = config.MUSIC_SEARCH_SOURCE_LABELS[sid]
        state = "🟢 ON" if config.is_music_source_enabled(sid) else "🔴 OFF"
        rows.append([InlineKeyboardButton(f"{label}: {state}", callback_data=f"dev:musictoggle:{sid}")])
    rows.append([InlineKeyboardButton("🔍 Tekshirish", callback_data="dev:musiccheck")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")])
    return InlineKeyboardMarkup(rows)


def _music_check_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Qayta tekshirish", callback_data="dev:musiccheck")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:music")],
    ])


def _render_music_check_block(title: str, result: dict) -> str:
    status = result.get("status", "error")
    block = [title, _MUSIC_CHECK_STATUS.get(status, status)]
    for line in result.get("lines", []):
        block.append(_esc(line))
    return "\n".join(block)


def _check_telegram_music_search() -> dict:
    """telegram_search.py IXTIYORIY modul (telethon o'rnatilmasligi
    mumkin) — shu sabab import shu yerda, xato bo'lsa ham qolgan ikki
    manba tekshiruvi buzilmaydi. SINXRON (blocking, ichida `asyncio.run()`
    chaqiradi) — chaqiruvchi asyncio.to_thread() orqali chaqirishi kerak."""
    try:
        import telegram_search
    except Exception as e:
        return {"status": "error", "lines": [f"Ichki xato: {type(e).__name__}: {e}"]}
    return telegram_search.check_telegram_music_search()


async def _run_music_check() -> str:
    """Uchala manbani PARALLEL tekshiradi. Har biri o'z thread'ida (yt-dlp/
    Telethon — ikkalasi ham blocking) ishlaydi, bittasi xato bersa ham
    qolganlar davom etadi (har biri alohida try/except bilan o'ralgan)."""
    logger.info("🎵 Qo'shiq qidirish manbalari tekshirilmoqda (/developer > 🎵 Qo'shiq qidirish > Tekshirish)...")

    async def _safe_thread(fn, label: str) -> dict:
        try:
            return await asyncio.to_thread(fn)
        except Exception as e:
            logger.error(f"🎵 '{label}' tekshiruvida kutilmagan xato: {type(e).__name__}: {e}", exc_info=True)
            return {"status": "error", "lines": [f"Ichki xato: {type(e).__name__}: {e}"]}

    yt_result, web_result, tg_result = await asyncio.gather(
        _safe_thread(video_tools.check_youtube_music_search, "YouTube"),
        _safe_thread(video_tools.check_web_music_search, "Web"),
        _safe_thread(_check_telegram_music_search, "Telegram"),
    )

    return "\n\n".join([
        "🔍 <b>QIDIRUV MANBALARI TEKSHIRUVI</b>",
        _render_music_check_block("🎬 YouTube", yt_result),
        _render_music_check_block("🌐 Web", web_result),
        _render_music_check_block("📱 Telegram", tg_result),
    ])


# ============================================================
# ☁️ RENDER — Render API boshqaruv paneli
# ============================================================

_RENDER_LEVEL_FILTERS = {
    "all": None,
    "error": ["error", "critical", "alert", "emergency"],
    "medium": ["notice", "warning"],
}
_RENDER_LEVEL_LABELS = {
    "all": "📋 Barcha log",
    "error": "❌ Hato log",
    "medium": "🟡 O'rta log",
}


def _render_status_icon(status: str | None) -> str:
    return {
        "live": "🟢",
        "build_in_progress": "🔵",
        "update_in_progress": "🔵",
        "created": "⚪",
        "deactivated": "⚫",
        "suspended": "⏸️",
    }.get(str(status or "").lower(), "⚪")


def _render_service_name(service: dict) -> str:
    name = service.get("name") or service.get("slug") or service.get("id") or "Noma'lum servis"
    return str(name)


def _render_service_keyboard(services: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for service in services[:30]:
        sid = str(service.get("id", ""))
        if not sid:
            continue
        status = service.get("suspended") or service.get("status")
        label = f"{_render_status_icon(status)} {_render_service_name(service)}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"dev:rsvc:{sid}")])
    rows.append([InlineKeyboardButton("🔄 Yangilash", callback_data="dev:render")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")])
    return InlineKeyboardMarkup(rows)


def _render_menu_text(services: list[dict], owner_id: str) -> str:
    if not services:
        return (
            "☁️ <b>RENDER</b>\n\n"
            "Servis topilmadi. Render API kaliti yoki workspace sozlamasini tekshiring.\n\n"
            f"Workspace: <code>{_esc(owner_id or 'avtomatik')}</code>"
        )
    lines = [
        "☁️ <b>RENDER boshqaruv paneli</b>",
        f"🏢 Workspace: <code>{_esc(owner_id or 'avtomatik')}</code>",
        f"📦 Servislar: <b>{len(services)}</b>",
        "",
        "Render API orqali servis, deploy va loglarni boshqarishingiz mumkin.",
        "Servisni tanlang:",
    ]
    return "\n".join(lines)


def _render_service_text(service: dict) -> str:
    sid = str(service.get("id", ""))
    status = service.get("suspended") or service.get("status") or "—"
    service_type = service.get("type") or "—"
    repo = service.get("repo") or "—"
    branch = service.get("branch") or "—"
    auto = service.get("autoDeploy")
    url = service.get("serviceDetails", {}).get("url") if isinstance(service.get("serviceDetails"), dict) else None
    if not url:
        url = service.get("url") or "—"
    return (
        f"☁️ <b>{_esc(_render_service_name(service))}</b>\n\n"
        f"🆔 ID: <code>{_esc(sid)}</code>\n"
        f"📦 Turi: <code>{_esc(service_type)}</code>\n"
        f"📡 Holat: {_render_status_icon(str(status))} <code>{_esc(status)}</code>\n"
        f"🌿 Branch: <code>{_esc(branch)}</code>\n"
        f"🔄 Auto Deploy: <code>{_esc(auto if auto is not None else '—')}</code>\n"
        f"🔗 Repo: <code>{_esc(repo)}</code>\n"
        f"🌐 URL: <code>{_esc(url)}</code>\n\n"
        "Kerakli amalni tanlang:"
    )


def _render_service_keyboard_for(service: dict) -> InlineKeyboardMarkup:
    sid = str(service.get("id", ""))
    suspended = str(service.get("suspended", "")).lower() == "suspended"
    rows = [
        [
            InlineKeyboardButton("🚀 Deploy", callback_data=f"dev:rdeploy:{sid}"),
            InlineKeyboardButton("🔄 Restart", callback_data=f"dev:rrestart:{sid}"),
        ],
        [
            InlineKeyboardButton("📋 Deploylar", callback_data=f"dev:rdeploys:{sid}"),
            InlineKeyboardButton("⚙️ Sozlamalar", callback_data=f"dev:rsettings:{sid}"),
        ],
        [
            InlineKeyboardButton("📋 Barcha log", callback_data=f"dev:rlogs:{sid}:all"),
            InlineKeyboardButton("❌ Hato log", callback_data=f"dev:rlogs:{sid}:error"),
        ],
        [InlineKeyboardButton("🟡 O'rta log", callback_data=f"dev:rlogs:{sid}:medium")],
        [InlineKeyboardButton("📄 Loglarni ko'chirish (PDF)", callback_data=f"dev:rpdf:{sid}")],
        [
            InlineKeyboardButton("⏸️ Resume" if suspended else "⏸️ Suspend", callback_data=f"dev:r{'resume' if suspended else 'suspend'}:{sid}"),
            InlineKeyboardButton("🔄 Yangilash", callback_data=f"dev:rsvc:{sid}"),
        ],
        [InlineKeyboardButton("⬅️ RENDER", callback_data="dev:render")],
    ]
    return InlineKeyboardMarkup(rows)


def _render_settings_text(service: dict, env_vars: list[dict]) -> str:
    auto = service.get("autoDeploy")
    branch = service.get("branch") or "—"
    repo = service.get("repo") or "—"
    lines = [
        f"⚙️ <b>{_esc(_render_service_name(service))} — Render sozlamalari</b>",
        "",
        f"🔄 Auto Deploy: <code>{_esc(auto if auto is not None else '—')}</code>",
        f"🌿 Branch: <code>{_esc(branch)}</code>",
        f"🔗 Repo: <code>{_esc(repo)}</code>",
        "",
        f"🔐 Environment variables: <b>{len(env_vars)}</b>",
        "<i>Maxfiy qiymatlar Telegramda ko'rsatilmaydi.</i>",
    ]
    if env_vars:
        for item in env_vars[:30]:
            key = item.get("key") or item.get("name") or item.get("envVarKey") or "?"
            lines.append(f"• <code>{_esc(key)}</code>")
        if len(env_vars) > 30:
            lines.append(f"• … yana {len(env_vars) - 30} ta")
    return "\n".join(lines)[:3900]


def _render_settings_keyboard(service: dict) -> InlineKeyboardMarkup:
    sid = str(service.get("id", ""))
    auto = str(service.get("autoDeploy", "")).lower() in {"true", "yes"}
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕/✏️ ENV qo'shish/o'zgartirish", callback_data=f"dev:renvadd:{sid}")],
        [InlineKeyboardButton("🗑 ENV o'chirish", callback_data=f"dev:renvdel:{sid}")],
        [InlineKeyboardButton(
            "🔴 Auto Deploy o'chirish" if auto else "🟢 Auto Deploy yoqish",
            callback_data=f"dev:rautodeploy:{sid}:{'no' if auto else 'yes'}",
        )],
        [InlineKeyboardButton("🔄 Yangilash", callback_data=f"dev:rsettings:{sid}")],
        [InlineKeyboardButton("⬅️ Servis", callback_data=f"dev:rsvc:{sid}")],
    ])


def _render_deploys_text(service: dict, deploys: list[dict]) -> str:
    lines = [f"🚀 <b>{_esc(_render_service_name(service))} — deploylar</b>", ""]
    if not deploys:
        lines.append("<i>Deploy tarixi topilmadi.</i>")
    else:
        for d in deploys[:15]:
            status = d.get("status") or "—"
            created = str(d.get("createdAt") or d.get("created_at") or "")[:19].replace("T", " ")
            commit = d.get("commit", {}) if isinstance(d.get("commit"), dict) else {}
            commit_id = commit.get("id") or d.get("commitId") or "—"
            commit_id = str(commit_id)
            lines.append(
                f"{_render_status_icon(status)} <b>{_esc(status)}</b> | {_esc(created)}\n"
                f"   commit: <code>{_esc(commit_id[:12])}</code>"
            )
    lines.append("")
    lines.append("Faol deploy bo'lsa, uni bekor qilish uchun pastdagi tugmadan foydalaning.")
    return "\n".join(lines)[:3900]


def _render_deploy_keyboard(service_id: str, deploys: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🚀 Yangi deploy", callback_data=f"dev:rdeploy:{service_id}"),
            InlineKeyboardButton("🧹 Cache bilan", callback_data=f"dev:rdeployclear:{service_id}"),
        ],
        [InlineKeyboardButton("📋 Loglar", callback_data=f"dev:rlogs:{service_id}:all")],
    ]
    for deploy in deploys[:10]:
        status = str(deploy.get("status") or "").lower()
        deploy_id = str(deploy.get("id") or "")
        if deploy_id and status in {"created", "build_in_progress", "update_in_progress", "queued"}:
            rows.append([InlineKeyboardButton(f"🛑 Bekor qilish: {deploy_id[:10]}", callback_data=f"dev:rcancel:{service_id}:{deploy_id}")])
    rows.append([InlineKeyboardButton("🔄 Yangilash", callback_data=f"dev:rdeploys:{service_id}")])
    rows.append([InlineKeyboardButton("⬅️ Servis", callback_data=f"dev:rsvc:{service_id}")])
    return InlineKeyboardMarkup(rows)


def _render_logs_text(service: dict, logs: list[dict], category: str) -> str:
    label = _RENDER_LEVEL_LABELS.get(category, category)
    lines = [
        f"{label} — <b>{_esc(_render_service_name(service))}</b>",
        f"📊 Ko'rsatilgan: <b>{len(logs)}</b>",
        "",
    ]
    if not logs:
        lines.append("<i>Tanlangan turdagi log topilmadi.</i>")
        return "\n".join(lines)
    for log in logs[:24]:
        ts = str(log.get("timestamp") or log.get("time") or log.get("createdAt") or "")[:19].replace("T", " ")
        level = str(log.get("level") or "info").upper()
        message = str(log.get("message") or log.get("text") or log.get("msg") or "")
        message = message.replace("\x00", "")
        if len(message) > 230:
            message = message[:230] + "…"
        instance = log.get("instance") or log.get("instanceId") or ""
        suffix = f" | {_esc(str(instance)[:16])}" if instance else ""
        lines.append(f"<code>{_esc(ts)}</code> <b>{_esc(level)}</b>{suffix}\n{_esc(message)}")
    if len(logs) > 24:
        lines.append(f"\n<i>… yana {len(logs) - 24} ta log bor. PDF orqali to'liq nusxa olinadi.</i>")
    return "\n\n".join(lines)[:3950]


def _render_log_keyboard(service_id: str, category: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📋 Barchasi", callback_data=f"dev:rlogs:{service_id}:all"),
            InlineKeyboardButton("❌ Hato", callback_data=f"dev:rlogs:{service_id}:error"),
        ],
        [InlineKeyboardButton("🟡 O'rta", callback_data=f"dev:rlogs:{service_id}:medium")],
        [InlineKeyboardButton("🔄 Yangilash", callback_data=f"dev:rlogs:{service_id}:{category}")],
        [InlineKeyboardButton("📄 PDF — barcha log", callback_data=f"dev:rpdf:{service_id}")],
        [InlineKeyboardButton("⬅️ Servis", callback_data=f"dev:rsvc:{service_id}")],
    ]
    return InlineKeyboardMarkup(rows)


async def _render_api_error(query, exc: Exception, back_callback: str = "dev:render"):
    logger.exception("☁️ Render API xatosi")
    message = render_api.human_error(exc)
    await _safe_edit_query(
        query,
        "❌ <b>Render API xatosi</b>\n\n" + _esc(message),
        reply_markup=_back_keyboard(back_callback),
        parse_mode="HTML",
    )



# ============================================================
# ☁️ GitHub — repository / papka / fayl boshqaruvi
# ============================================================

def _github_menu_text() -> str:
    if not github_dev.configured():
        return (
            "☁️ <b>GitHub boshqaruvi</b>\n\n"
            "❌ <code>GITHUB_TOKEN</code> sozlanmagan.\n\n"
            "Render Environment Variables'ga GITHUB_TOKEN qo'shing. "
            "Token kamida repository uchun <b>Contents: Read and write</b> "
            "huquqiga ega bo'lishi kerak."
        )

    configured_repo = _esc(getattr(config, "GITHUB_REPO", "") or "(belgilanmagan)")
    return (
        "☁️ <b>GitHub boshqaruvi</b>\n\n"
        "Bu bo'lim tokeningiz kira oladigan repositorylarni GitHub'dan "
        "to'g'ridan-to'g'ri oladi.\n\n"
        f"⚙️ Eski default repo: <code>{configured_repo}</code>\n\n"
        "Repositoryni tanlang:"
    )


def _github_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Repositorilarni yuklash", callback_data="dev:gh:repos")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")],
    ])


def _github_repo_keyboard(repos: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i, repo in enumerate(repos):
        private = "🔒 " if repo.get("private") else "🌐 "
        rows.append([
            InlineKeyboardButton(
                f"{private}{repo['full_name']}"[:64],
                callback_data=f"dev:gh:repo:{i}",
            )
        ])
    rows.append([InlineKeyboardButton("🔄 Yangilash", callback_data="dev:gh:repos")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:github")])
    return InlineKeyboardMarkup(rows)


def _github_path_keyboard(repo: str, path_value: str, items: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i, item in enumerate(items):
        label = f"{github_dev.item_icon(item['type'])} {item['name']}"
        if item["type"] == "file":
            label += f"  ({github_dev.format_size(item['size'])})"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"dev:gh:item:{i}")])

    rows.append([InlineKeyboardButton("➕ Yangi fayl qo'shish", callback_data="dev:gh:new")])
    if path_value:
        rows.append([InlineKeyboardButton("⬆️ Yuqoriga", callback_data="dev:gh:up")])
    rows.append([InlineKeyboardButton("📦 Repositorilar", callback_data="dev:gh:repos")])
    rows.append([InlineKeyboardButton("⬅️ GitHub", callback_data="dev:github")])
    return InlineKeyboardMarkup(rows)


def _github_path_text(repo: str, path_value: str, items: list[dict]) -> str:
    title = f"📦 <b>{_esc(repo)}</b>"
    title += f"\n📁 <code>/{_esc(path_value)}</code>" if path_value else "\n📁 <code>/</code>"
    if not items:
        title += "\n\n<i>Bu papka bo'sh.</i>"
    else:
        dirs = sum(1 for x in items if x["type"] == "dir")
        files = sum(1 for x in items if x["type"] == "file")
        title += f"\n\n📁 Papkalar: <b>{dirs}</b>  📄 Fayllar: <b>{files}</b>"
    return title


def _github_file_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Tahrirlash", callback_data="dev:gh:edit")],
        [InlineKeyboardButton("🗑 O'chirish", callback_data="dev:gh:delete")],
        [InlineKeyboardButton("⬅️ Papkaga", callback_data="dev:gh:backfile")],
    ])


async def _github_open_path(context: ContextTypes.DEFAULT_TYPE, path_value: str = ""):
    repo = context.user_data.get("github_repo")
    branch = context.user_data.get("github_branch") or "main"
    if not repo:
        raise github_dev.GitHubDevError("Repository tanlanmagan.")
    items = await asyncio.to_thread(github_dev.list_directory, repo, path_value, branch)
    context.user_data["github_path"] = path_value
    context.user_data["github_items"] = items
    await _edit_menu(
        context,
        _github_path_text(repo, path_value, items),
        _github_path_keyboard(repo, path_value, items),
    )


async def _github_send_file_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    repo = context.user_data.get("github_repo")
    path_value = context.user_data.get("github_file")
    branch = context.user_data.get("github_branch") or "main"
    if not repo or not path_value:
        raise github_dev.GitHubDevError("Fayl tanlanmagan.")

    info = await asyncio.to_thread(github_dev.read_file, repo, path_value, branch)
    context.user_data["github_sha"] = info["sha"]
    context.user_data["github_file_text"] = info["text"]
    text = (
        f"📄 <b>{_esc(repo)}</b>\n"
        f"📁 <code>{_esc(info['path'])}</code>\n"
        f"📏 {github_dev.format_size(info['size'])}\n\n"
        f"<pre>{_esc(github_dev.display_text(info['text']))}</pre>"
    )
    if len(text) <= 4096:
        await _edit_menu(context, text, _github_file_keyboard())
    else:
        # Telegram matn limitidan oshadigan faylni hujjat sifatida yuboramiz.
        data = info["text"].encode("utf-8")
        await update.effective_chat.send_document(
            document=io.BytesIO(data),
            filename=path_value.rsplit("/", 1)[-1] or "file.txt",
            caption=f"📄 {_esc(repo)}:{_esc(path_value)}",
        )
        await _edit_menu(
            context,
            f"📄 <b>{_esc(path_value)}</b>\n\n"
            "Fayl katta bo'lgani uchun yuqorida to'liq ko'rinishida hujjat qilib yuborildi.",
            _github_file_keyboard(),
        )


def _github_confirm_delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Ha, o'chirish", callback_data="dev:gh:delete_yes")],
        [InlineKeyboardButton("⬅️ Bekor qilish", callback_data="dev:gh:file")],
    ])


async def _github_error(context: ContextTypes.DEFAULT_TYPE, exc: Exception, back: str = "dev:github"):
    logger.error("GitHub Developer xatosi: %s", exc, exc_info=True)
    await _edit_menu(
        context,
        f"❌ <b>GitHub xatosi</b>\n\n{_esc(str(exc))}",
        _back_keyboard(back),
    )


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
    if action not in ("edit", "bulkprov", "keyaddprov", "keyrepl", "keymodel", "keybulkscope", "priceedit", "balsearch", "ptsong", "ptemoji", "ptdelay", "ptrevert", "gh", "github"):
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


    # ---------- ☁️ GitHub ----------
    if action == "github":
        await _safe_edit_query(
            query, _github_menu_text(), reply_markup=_github_menu_keyboard(), parse_mode="HTML"
        )
        return DEV_MENU

    if action == "gh":
        sub = parts[2] if len(parts) > 2 else ""
        if sub == "repos":
            await _safe_edit_query(query, "☁️ <b>Repositorilar olinmoqda...</b>", parse_mode="HTML")
            try:
                repos = await asyncio.to_thread(github_dev.list_repositories)
                context.user_data["github_repos"] = repos
                if not repos:
                    await _safe_edit_query(
                        query,
                        "📦 <b>Repositorilar</b>\n\n❗ Token kira oladigan repository topilmadi.",
                        reply_markup=_github_menu_keyboard(), parse_mode="HTML",
                    )
                else:
                    text = "📦 <b>GitHub repositorilari</b>\n\n"
                    text += "\n".join(
                        f"{i+1}. {'🔒' if r['private'] else '🌐'} <code>{_esc(r['full_name'])}</code>"
                        for i, r in enumerate(repos)
                    )
                    text += f"\n\nJami: <b>{len(repos)}</b> ta. Keraklisini tanlang:"
                    if len(text) > 4096:
                        text = f"📦 <b>GitHub repositorilari</b>\n\nJami: <b>{len(repos)}</b> ta.\nKeraklisini pastdagi tugmalardan tanlang."
                    await _safe_edit_query(
                        query, text, reply_markup=_github_repo_keyboard(repos), parse_mode="HTML"
                    )
            except Exception as exc:
                await _github_error(context, exc)
            return DEV_MENU

        if sub == "repo":
            idx = int(parts[3])
            repos = context.user_data.get("github_repos") or []
            if not (0 <= idx < len(repos)):
                await _github_error(context, github_dev.GitHubDevError("Repository ro'yxati eskirgan. Yangilang."), "dev:gh:repos")
                return DEV_MENU
            repo_info = repos[idx]
            repo = repo_info["full_name"]
            branch = repo_info.get("default_branch") or "main"
            context.user_data["github_repo"] = repo
            context.user_data["github_branch"] = branch
            context.user_data["github_path"] = ""
            context.user_data["github_file"] = None
            await _safe_edit_query(query, f"📦 <b>{_esc(repo)}</b>\n\n📁 Repository ochilmoqda...", parse_mode="HTML")
            try:
                await _github_open_path(context, "")
            except Exception as exc:
                await _github_error(context, exc, "dev:gh:repos")
            return DEV_MENU

        if sub == "item":
            idx = int(parts[3])
            items = context.user_data.get("github_items") or []
            if not (0 <= idx < len(items)):
                await _github_error(context, github_dev.GitHubDevError("Papka ro'yxati eskirgan. Yangilang."), "dev:gh:repos")
                return DEV_MENU
            item = items[idx]
            if item["type"] == "dir":
                context.user_data["github_path"] = item["path"]
                try:
                    await _github_open_path(context, item["path"])
                except Exception as exc:
                    await _github_error(context, exc)
                return DEV_MENU
            if item["type"] != "file":
                await _safe_edit_query(query, "⚠️ Bu obyekt matnli fayl sifatida ochilmaydi.", reply_markup=_github_path_keyboard(
                    context.user_data.get("github_repo", ""), context.user_data.get("github_path", ""), items
                ))
                return DEV_MENU
            context.user_data["github_file"] = item["path"]
            await _safe_edit_query(query, "📄 Fayl o'qilmoqda...", parse_mode="HTML")
            try:
                await _github_send_file_view(update, context)
            except Exception as exc:
                await _github_error(context, exc)
            return DEV_MENU

        if sub == "file":
            try:
                await _github_send_file_view(update, context)
            except Exception as exc:
                await _github_error(context)
            return DEV_MENU

        if sub == "backfile":
            path_value = context.user_data.get("github_path") or ""
            try:
                await _github_open_path(context, path_value)
            except Exception as exc:
                await _github_error(context)
            return DEV_MENU

        if sub == "up":
            current = context.user_data.get("github_path") or ""
            parent = current.rsplit("/", 1)[0] if "/" in current else ""
            try:
                await _github_open_path(context, parent)
            except Exception as exc:
                await _github_error(context)
            return DEV_MENU

        if sub == "new":
            context.user_data["dev_action"] = {"type": "github_new_path"}
            await _safe_edit_query(
                query,
                "➕ <b>Yangi fayl</b>\n\n"
                "Avval fayl yo'lini yuboring.\n"
                "Masalan: <code>papka_nomi/fayil_nomi.py</code>\n\n"
                "So'ng fayl mazmunini yuborasiz. Yangi papkalar kerak bo'lsa, GitHub ularni avtomatik yaratadi.",
                reply_markup=_back_keyboard("dev:gh:file"),
                parse_mode="HTML",
            )
            return DEV_WAIT_GITHUB

        if sub == "edit":
            repo = context.user_data.get("github_repo")
            file_path = context.user_data.get("github_file")
            if not repo or not file_path:
                await _github_error(context, github_dev.GitHubDevError("Fayl tanlanmagan."), "dev:github")
                return DEV_MENU
            context.user_data["dev_action"] = {"type": "github_edit", "repo": repo, "path": file_path}
            await _safe_edit_query(
                query,
                f"✏️ <b>Faylni tahrirlash</b>\n\n<code>{_esc(file_path)}</code>\n\n"
                "Yangi <b>to'liq</b> fayl mazmunini yuboring.\n"
                "Katta fayl bo'lsa uni Telegram hujjati (.py/.js/.txt va hokazo) sifatida yuborishingiz mumkin.\n\n"
                "⚠️ Yuborilgan mazmun GitHub'dagi eski mazmunni to'liq almashtiradi.",
                reply_markup=_back_keyboard("dev:gh:file"),
                parse_mode="HTML",
            )
            return DEV_WAIT_GITHUB

        if sub == "delete":
            file_path = context.user_data.get("github_file")
            await _safe_edit_query(
                query,
                f"🗑 <b>Faylni o'chirish</b>\n\n<code>{_esc(file_path or '')}</code>\n\n"
                "⚠️ Bu amal GitHub'da darhol commit qilinadi. Davom etasizmi?",
                reply_markup=_github_confirm_delete_keyboard(),
                parse_mode="HTML",
            )
            return DEV_MENU

        if sub == "delete_yes":
            repo = context.user_data.get("github_repo")
            file_path = context.user_data.get("github_file")
            branch = context.user_data.get("github_branch") or "main"
            if not repo or not file_path:
                await _github_error(context, github_dev.GitHubDevError("Fayl tanlanmagan."), "dev:github")
                return DEV_MENU
            await _safe_edit_query(query, "🗑 GitHub'dan o'chirilmoqda...", parse_mode="HTML")
            try:
                await asyncio.to_thread(
                    github_dev.delete_file, repo, file_path, branch, context.user_data.get("github_sha")
                )
                await _safe_edit_query(
                    query,
                    f"✅ <b>Fayl o'chirildi.</b>\n\n<code>{_esc(file_path)}</code>",
                    reply_markup=_back_keyboard("dev:gh:backfile"),
                    parse_mode="HTML",
                )
            except Exception as exc:
                await _github_error(context, exc, "dev:gh:file")
            return DEV_MENU

    # ---------- ☁️ RENDER ----------
    if action == "render":
        if not config.RENDER_API_KEY:
            await _safe_edit_query(
                query,
                "☁️ <b>RENDER</b>\n\n❌ <code>RENDER_API_KEY</code> sozlanmagan.\n\n"
                "Render Dashboard → Account Settings → API Keys orqali API Key yarating "
                "va Render Environment Variables'ga <code>RENDER_API_KEY</code> nomi bilan qo'ying.",
                reply_markup=_back_keyboard("dev:menu"), parse_mode="HTML",
            )
            return DEV_MENU
        await _safe_edit_query(query, "☁️ Render servislar olinmoqda...", parse_mode="HTML")
        try:
            owner_id = config.RENDER_OWNER_ID
            services = await render_api.list_services(owner_id=owner_id)
            if config.RENDER_SERVICE_ID:
                services.sort(key=lambda item: str(item.get("id", "")) != config.RENDER_SERVICE_ID)
            if not owner_id and services:
                owner_id = str(services[0].get("ownerId") or services[0].get("owner_id") or "")
            context.user_data["render_owner_id"] = owner_id
            await _safe_edit_query(
                query, _render_menu_text(services, owner_id),
                reply_markup=_render_service_keyboard(services), parse_mode="HTML",
            )
        except Exception as exc:
            await _render_api_error(query, exc)
        return DEV_MENU

    if action == "rsvc":
        service_id = parts[2]
        await _safe_edit_query(query, "☁️ Servis ma'lumotlari olinmoqda...", parse_mode="HTML")
        try:
            service = await render_api.get_service(service_id)
            context.user_data["render_service"] = service
            await _safe_edit_query(
                query, _render_service_text(service),
                reply_markup=_render_service_keyboard_for(service), parse_mode="HTML",
            )
        except Exception as exc:
            await _render_api_error(query, exc)
        return DEV_MENU

    if action == "rdeploy":
        service_id = parts[2]
        await _safe_edit_query(query, "🚀 Deploy ishga tushirilmoqda...", parse_mode="HTML")
        try:
            deploy = await render_api.trigger_deploy(service_id)
            status = deploy.get("status") or "queued"
            await _safe_edit_query(
                query,
                f"✅ <b>Deploy ishga tushdi.</b>\n\nHolat: <code>{_esc(status)}</code>\n"
                f"Deploy ID: <code>{_esc(deploy.get('id') or '—')}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Deploylar", callback_data=f"dev:rdeploys:{service_id}"),
                     InlineKeyboardButton("📋 Loglar", callback_data=f"dev:rlogs:{service_id}:all")],
                    [InlineKeyboardButton("⬅️ Servis", callback_data=f"dev:rsvc:{service_id}")],
                ]), parse_mode="HTML",
            )
        except Exception as exc:
            await _render_api_error(query, exc, f"dev:rsvc:{service_id}")
        return DEV_MENU

    if action == "rdeployclear":
        service_id = parts[2]
        await _safe_edit_query(query, "🧹🚀 Cache tozalanib deploy qilinmoqda...", parse_mode="HTML")
        try:
            deploy = await render_api.trigger_deploy(service_id, clear_cache=True)
            await _safe_edit_query(
                query,
                "✅ <b>Cache tozalangan deploy ishga tushdi.</b>\n\n"
                f"Holat: <code>{_esc(deploy.get('status') or 'queued')}</code>\n"
                f"Deploy ID: <code>{_esc(deploy.get('id') or '—')}</code>",
                reply_markup=_back_keyboard(f"dev:rsvc:{service_id}"), parse_mode="HTML",
            )
        except Exception as exc:
            await _render_api_error(query, exc, f"dev:rsvc:{service_id}")
        return DEV_MENU

    if action == "rcancel":
        service_id, deploy_id = parts[2], parts[3]
        await _safe_edit_query(query, "🛑 Deploy bekor qilinmoqda...", parse_mode="HTML")
        try:
            await render_api.cancel_deploy(service_id, deploy_id)
            await _safe_edit_query(
                query, f"✅ <b>Deploy bekor qilindi.</b>\n\nID: <code>{_esc(deploy_id)}</code>",
                reply_markup=_back_keyboard(f"dev:rdeploys:{service_id}"), parse_mode="HTML",
            )
        except Exception as exc:
            await _render_api_error(query, exc, f"dev:rdeploys:{service_id}")
        return DEV_MENU

    if action == "rrestart":
        service_id = parts[2]
        await _safe_edit_query(query, "🔄 Servis restart qilinmoqda...", parse_mode="HTML")
        try:
            await render_api.restart_service(service_id)
            await _safe_edit_query(
                query, "✅ <b>Restart buyrug'i Render'ga yuborildi.</b>",
                reply_markup=_back_keyboard(f"dev:rsvc:{service_id}"), parse_mode="HTML",
            )
        except Exception as exc:
            await _render_api_error(query, exc, f"dev:rsvc:{service_id}")
        return DEV_MENU

    if action == "rsuspend":
        service_id = parts[2]
        await _safe_edit_query(query, "⏸️ Servis to'xtatilmoqda...", parse_mode="HTML")
        try:
            await render_api.suspend_service(service_id)
            await _safe_edit_query(query, "✅ <b>Servis suspend qilindi.</b>", reply_markup=_back_keyboard(f"dev:rsvc:{service_id}"), parse_mode="HTML")
        except Exception as exc:
            await _render_api_error(query, exc, f"dev:rsvc:{service_id}")
        return DEV_MENU

    if action == "rresume":
        service_id = parts[2]
        await _safe_edit_query(query, "▶️ Servis tiklanmoqda...", parse_mode="HTML")
        try:
            await render_api.resume_service(service_id)
            await _safe_edit_query(query, "✅ <b>Servis resume qilindi.</b>", reply_markup=_back_keyboard(f"dev:rsvc:{service_id}"), parse_mode="HTML")
        except Exception as exc:
            await _render_api_error(query, exc, f"dev:rsvc:{service_id}")
        return DEV_MENU

    if action == "rsettings":
        service_id = parts[2]
        await _safe_edit_query(query, "⚙️ Render sozlamalari olinmoqda...", parse_mode="HTML")
        try:
            service = await render_api.get_service(service_id)
            env_vars = await render_api.list_env_vars(service_id)
            await _safe_edit_query(
                query, _render_settings_text(service, env_vars),
                reply_markup=_render_settings_keyboard(service), parse_mode="HTML",
            )
        except Exception as exc:
            await _render_api_error(query, exc, f"dev:rsvc:{service_id}")
        return DEV_MENU

    if action == "renvadd":
        service_id = parts[2]
        context.user_data["dev_action"] = {"type": "render_env_upsert", "service_id": service_id}
        await _safe_edit_query(
            query,
            "🔐 <b>Environment variable qo'shish/o'zgartirish</b>\n\n"
            "Shu formatda yuboring:\n<code>KEY=VALUE</code>\n\n"
            "Masalan: <code>RENDER_LOG_PDF_HOURS=24</code>\n"
            "Xabar qayta ishlangach Telegramdan o'chiriladi.",
            reply_markup=_back_keyboard(f"dev:rsettings:{service_id}"), parse_mode="HTML",
        )
        return DEV_WAIT_TEXT

    if action == "renvdel":
        service_id = parts[2]
        context.user_data["dev_action"] = {"type": "render_env_delete", "service_id": service_id}
        await _safe_edit_query(
            query,
            "🗑 <b>Environment variable o'chirish</b>\n\n"
            "O'chiriladigan KEY nomini yuboring.\nMasalan: <code>TEST_KEY</code>\n\n"
            "⚠️ Bu amal Render'dagi qiymatni darhol o'chiradi; keyin deploy qilish kerak bo'lishi mumkin.",
            reply_markup=_back_keyboard(f"dev:rsettings:{service_id}"), parse_mode="HTML",
        )
        return DEV_WAIT_TEXT

    if action == "rautodeploy":
        service_id, value = parts[2], parts[3]
        await _safe_edit_query(query, "⚙️ Auto Deploy sozlanmoqda...", parse_mode="HTML")
        try:
            await render_api.update_service(service_id, {"autoDeploy": value})
            service = await render_api.get_service(service_id)
            env_vars = await render_api.list_env_vars(service_id)
            await _safe_edit_query(
                query, "✅ <b>Auto Deploy yangilandi.</b>\n\n" + _render_settings_text(service, env_vars),
                reply_markup=_render_settings_keyboard(service), parse_mode="HTML",
            )
        except Exception as exc:
            await _render_api_error(query, exc, f"dev:rsettings:{service_id}")
        return DEV_MENU

    if action == "rdeploys":
        service_id = parts[2]
        await _safe_edit_query(query, "📋 Deploylar olinmoqda...", parse_mode="HTML")
        try:
            service = await render_api.get_service(service_id)
            deploys = await render_api.list_deploys(service_id, limit=30)
            await _safe_edit_query(
                query, _render_deploys_text(service, deploys),
                reply_markup=_render_deploy_keyboard(service_id, deploys), parse_mode="HTML",
            )
        except Exception as exc:
            await _render_api_error(query, exc, f"dev:rsvc:{service_id}")
        return DEV_MENU

    if action == "rlogs":
        service_id = parts[2]
        category = parts[3] if len(parts) > 3 else "all"
        if category not in _RENDER_LEVEL_FILTERS:
            category = "all"
        await _safe_edit_query(query, f"☁️ {_RENDER_LEVEL_LABELS[category]} olinmoqda...", parse_mode="HTML")
        try:
            service = await render_api.get_service(service_id)
            logs = await render_api.list_logs_for_service(
                service_id=service_id,
                owner_id=str(service.get("ownerId") or service.get("owner_id") or config.RENDER_OWNER_ID or ""),
                levels=_RENDER_LEVEL_FILTERS[category],
                limit=100,
            )
            await _safe_edit_query(
                query, _render_logs_text(service, logs, category),
                reply_markup=_render_log_keyboard(service_id, category), parse_mode="HTML",
            )
        except Exception as exc:
            await _render_api_error(query, exc, f"dev:rsvc:{service_id}")
        return DEV_MENU

    if action == "rpdf":
        service_id = parts[2]
        await _safe_edit_query(query, "📄 <b>PDF tayyorlanmoqda...</b>\nRender'dan mavjud loglar olinmoqda, biroz kuting.", parse_mode="HTML")
        try:
            service = await render_api.get_service(service_id)
            owner_id = str(service.get("ownerId") or service.get("owner_id") or config.RENDER_OWNER_ID or "")
            logs = await render_api.list_logs_for_service(
                service_id=service_id, owner_id=owner_id, levels=None, limit=5000,
                hours=config.RENDER_LOG_PDF_HOURS,
            )
            pdf_path = render_api.create_logs_pdf(service, logs)
            try:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=pdf_path,
                    caption=(
                        f"📄 {_render_service_name(service)} — barcha Render loglari\n"
                        f"🧾 Jami: {len(logs)} ta\n"
                        f"⏱ Oxirgi {config.RENDER_LOG_PDF_HOURS} soat"
                    ),
                )
            finally:
                render_api.remove_temp_file(pdf_path)
            await _safe_edit_query(query, "✅ <b>PDF tayyor va yuborildi.</b>", reply_markup=_back_keyboard(f"dev:rsvc:{service_id}"), parse_mode="HTML")
        except Exception as exc:
            await _render_api_error(query, exc, f"dev:rsvc:{service_id}")
        return DEV_MENU

    if action == "stats":
        await _safe_edit_query(query, _stats_text(), reply_markup=_stats_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "inlinelog":
        status_filter = parts[2] if len(parts) > 2 else "all"
        status_filter = None if status_filter == "all" else status_filter
        await _safe_edit_query(
            query,
            _inline_log_text(status_filter),
            reply_markup=_inline_log_keyboard(status_filter),
            parse_mode="HTML",
        )
        return DEV_MENU

    if action == "keycheck":
        await _safe_edit_query(query, "🩺 Tekshirilmoqda, biroz kuting...")
        report = await _run_key_check()
        await _edit_menu(context, report, _keys_menu_keyboard())
        return DEV_MENU

    # ---------- 🎵 Qo'shiq qidirish ----------
    if action == "music":
        await _safe_edit_query(query, _music_menu_text(), reply_markup=_music_menu_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "musictoggle":
        source_id = parts[2]
        config.set_music_search_source(source_id, not config.is_music_source_enabled(source_id))
        await _safe_edit_query(query, _music_menu_text(), reply_markup=_music_menu_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "musiccheck":
        await _safe_edit_query(query, "🔍 Manbalar tekshirilmoqda, biroz kuting...")
        report = await _run_music_check()
        await _edit_menu(context, report, _music_check_keyboard())
        return DEV_MENU

    # ---------- 💎 /pro + /tabrik umumiy sozlamalari ----------
    if action == "pt":
        await _safe_edit_query(query, _pt_text(), reply_markup=_pt_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "ptsong":
        context.user_data["dev_action"] = {"type": "pt_audio"}
        await _safe_edit_query(
            query,
            "🎵 <b>Qo'shiq yuklash</b>\n\nMenga audio fayl yuboring. U <b>/tabrik</b> va <b>/pro</b> ochilganda ishlatiladi.\n\n"
            "MP3/M4A/OGG kabi Telegram audio fayl yuborishingiz mumkin.",
            reply_markup=_back_keyboard("dev:pt"),
            parse_mode="HTML",
        )
        return DEV_WAIT_AUDIO

    if action == "ptemoji":
        context.user_data["dev_action"] = {"type": "pt_emoji"}
        await _safe_edit_query(
            query,
            "😀 <b>5 ta emoji tanlash</b>\n\nAynan 5 ta emoji yuboring.\nMasalan: 😍 🥳 🎉 ❤️ ✨",
            reply_markup=_back_keyboard("dev:pt"),
            parse_mode="HTML",
        )
        return DEV_WAIT_TEXT

    if action == "ptdelay":
        await _safe_edit_query(
            query, "⏱ <b>Emoji soniyasi</b>\n\nEmojilar qancha soniya ko'rinib turishini tanlang:",
            reply_markup=_pt_choice_keyboard("ptdelayset", list(range(1, 7))),
            parse_mode="HTML",
        )
        return DEV_MENU

    if action == "ptdelayset":
        value = int(parts[2])
        config.set_tabrik_setting("emoji_delay", value)
        await _safe_edit_query(query, "✅ Emoji soniyasi saqlandi.\n\n" + _pt_text(), reply_markup=_pt_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "ptrevert":
        await _safe_edit_query(
            query, "↩️ <b>Matn qaytish daqiqasi</b>\n\nYakuniy tabrik matni qancha vaqtdan keyin boshlang'ich holatga qaytishini tanlang:",
            reply_markup=_pt_choice_keyboard("ptrevertset", list(range(1, 5))),
            parse_mode="HTML",
        )
        return DEV_MENU

    if action == "ptrevertset":
        value = int(parts[2])
        config.set_tabrik_setting("revert_minutes", value)
        await _safe_edit_query(query, "✅ Qaytish vaqti saqlandi.\n\n" + _pt_text(), reply_markup=_pt_keyboard(), parse_mode="HTML")
        return DEV_MENU

    # ---------- 💎 Pro obunalar ----------
    if action == "prosub":
        await _safe_edit_query(query, _prosub_menu_text(), reply_markup=_prosub_menu_keyboard(), parse_mode="HTML")
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

    if action_type == "github_new_path":
        # Path is intentionally accepted separately from content to support
        # arbitrary nested directories without a fragile delimiter protocol.
        file_path = raw_value.strip().strip("/")
        if (
            not file_path
            or file_path.endswith("/")
            or "\n" in file_path
            or file_path.startswith(".git/")
            or "/.git/" in file_path
            or file_path == ".git"
        ):
            await _edit_menu(
                context,
                "❌ Fayl yo'li noto'g'ri.\n\nMasalan: <code>src/main.py</code>",
                _back_keyboard("dev:gh:file"),
            )
            context.user_data["dev_action"] = action
            return DEV_WAIT_GITHUB
        context.user_data["dev_action"] = {"type": "github_new_content", "path": file_path}
        await _edit_menu(
            context,
            f"📝 <b>Yangi fayl mazmuni</b>\n\n<code>{_esc(file_path)}</code>\n\n"
            "To'liq mazmunni yuboring. Katta faylni hujjat sifatida ham yuborishingiz mumkin.",
            _back_keyboard("dev:gh:file"),
        )
        return DEV_WAIT_GITHUB

    if action_type in ("github_new_content", "github_edit"):
        content = raw_value
        if action_type == "github_new_content":
            repo = context.user_data.get("github_repo")
            file_path = action.get("path")
            branch = context.user_data.get("github_branch") or "main"
            if not repo or not file_path:
                await _edit_menu(context, "❌ Repository yoki fayl yo'li topilmadi.", _github_menu_keyboard())
                context.user_data.pop("dev_action", None)
                return DEV_MENU
            try:
                result = await asyncio.to_thread(github_dev.create_file, repo, file_path, content, branch)
                context.user_data.pop("dev_action", None)
                await _edit_menu(
                    context,
                    f"✅ <b>Yangi fayl GitHub'ga qo'shildi.</b>\n\n"
                    f"📦 <code>{_esc(repo)}</code>\n📄 <code>{_esc(file_path)}</code>\n"
                    f"🔗 Commit: <code>{_esc((result.get('commit') or {}).get('sha', '')[:12])}</code>",
                    _back_keyboard("dev:gh:file"),
                )
            except Exception as exc:
                context.user_data["dev_action"] = action
                await _edit_menu(context, f"❌ {_esc(str(exc))}", _back_keyboard("dev:gh:file"))
                return DEV_WAIT_GITHUB
            return DEV_MENU

        repo = action.get("repo") or context.user_data.get("github_repo")
        file_path = action.get("path") or context.user_data.get("github_file")
        branch = context.user_data.get("github_branch") or "main"
        if not repo or not file_path:
            await _edit_menu(context, "❌ Repository yoki fayl topilmadi.", _github_menu_keyboard())
            context.user_data.pop("dev_action", None)
            return DEV_MENU
        try:
            sha = context.user_data.get("github_sha")
            if not sha:
                current = await asyncio.to_thread(github_dev.read_file, repo, file_path, branch)
                sha = current["sha"]
            result = await asyncio.to_thread(
                github_dev.write_file,
                repo, file_path, content,
                f"Update {file_path}",
                branch, sha,
            )
            context.user_data["github_sha"] = (result.get("content") or {}).get("sha")
            context.user_data["github_file_text"] = content
            context.user_data.pop("dev_action", None)
            await _edit_menu(
                context,
                f"✅ <b>Fayl GitHub'da yangilandi.</b>\n\n"
                f"📦 <code>{_esc(repo)}</code>\n📄 <code>{_esc(file_path)}</code>\n"
                f"📝 Yangi hajm: <b>{github_dev.format_size(len(content.encode('utf-8')))}</b>",
                _github_file_keyboard(),
            )
        except Exception as exc:
            context.user_data["dev_action"] = action
            await _edit_menu(context, f"❌ {_esc(str(exc))}", _back_keyboard("dev:gh:file"))
            return DEV_WAIT_GITHUB
        return DEV_MENU

    if action_type == "pt_emoji":
        # Telegram sticker/emoji uchun alohida parser o'rniga mavjud emoji
        # parseridan foydalanamiz: matnda faqat 5 ta emoji bo'lishi kerak.
        import tabrik_logic
        emojis, rest = tabrik_logic.extract_emojis(raw_value.strip())
        if rest.strip() or len(emojis) != 5:
            await _edit_menu(
                context,
                "⚠️ Aynan 5 ta emoji yuboring.\nMasalan: 😍 🥳 🎉 ❤️ ✨",
                _back_keyboard("dev:pt"),
            )
            context.user_data["dev_action"] = action
            return DEV_WAIT_TEXT
        config.set_tabrik_setting("emojis", emojis)
        await _edit_menu(context, "✅ 5 ta emoji saqlandi.\n\n" + _pt_text(), _pt_keyboard())
        context.user_data.pop("dev_action", None)
        return DEV_MENU

    if action_type == "render_env_upsert":
        service_id = action.get("service_id")
        if not service_id:
            return DEV_MENU
        if "=" not in raw_value:
            await update.message.reply_text("❌ Format noto'g'ri. <code>KEY=VALUE</code> ko'rinishida yuboring.", parse_mode="HTML")
            return DEV_WAIT_TEXT
        key, value = raw_value.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            await update.message.reply_text("❌ KEY nomi noto'g'ri. Faqat harf, raqam va _ ishlating; raqam bilan boshlamang.")
            return DEV_WAIT_TEXT
        try:
            await render_api.upsert_env_var(service_id, key, value)
            await update.message.reply_text(
                f"✅ <code>{_esc(key)}</code> Render Environment Variables'ga saqlandi.\n"
                "Qiymat xavfsizlik sababli ko'rsatilmaydi. Deploy qilish uchun RENDER → 🚀 Deploy ni bosing.",
                parse_mode="HTML",
            )
        except Exception as exc:
            await update.message.reply_text("❌ " + _esc(render_api.human_error(exc)), parse_mode="HTML")
        context.user_data.pop("dev_action", None)
        return DEV_MENU

    if action_type == "render_env_delete":
        service_id = action.get("service_id")
        key = raw_value.strip()
        if not service_id or not key:
            return DEV_MENU
        if not key.replace("_", "").isalnum() or key[0].isdigit():
            await update.message.reply_text("❌ KEY nomi noto'g'ri.")
            return DEV_WAIT_TEXT
        try:
            await render_api.delete_env_var(service_id, key)
            await update.message.reply_text(f"✅ <code>{_esc(key)}</code> o'chirildi. Deploy qilish kerak bo'lishi mumkin.", parse_mode="HTML")
        except Exception as exc:
            await update.message.reply_text("❌ " + _esc(render_api.human_error(exc)), parse_mode="HTML")
        context.user_data.pop("dev_action", None)
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


async def on_github_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle GitHub edit/create content sent as text or a UTF-8 document."""
    if not _is_admin(update) or not update.message:
        return ConversationHandler.END
    action = context.user_data.get("dev_action") or {}
    if action.get("type") not in ("github_new_content", "github_edit"):
        return DEV_MENU

    content: str | None = None
    if update.message.text is not None:
        content = update.message.text
    elif update.message.document:
        doc = update.message.document
        try:
            tg_file = await doc.get_file()
            data = await tg_file.download_as_bytearray()
            content = bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            content = None
        except Exception as exc:
            await _edit_menu(context, f"❌ Hujjatni o'qib bo'lmadi: {_esc(exc)}", _back_keyboard("dev:gh:file"))
            return DEV_WAIT_GITHUB

    if content is None:
        await _edit_menu(
            context,
            "⚠️ Faqat matn yoki UTF-8 kod faylini yuboring.",
            _back_keyboard("dev:gh:file"),
        )
        return DEV_WAIT_GITHUB

    # Reuse the exact same write logic as text input, without exposing the
    # uploaded content back to the Telegram chat.
    if action["type"] == "github_new_content":
        repo = context.user_data.get("github_repo")
        file_path = action.get("path")
        branch = context.user_data.get("github_branch") or "main"
        try:
            result = await asyncio.to_thread(github_dev.create_file, repo, file_path, content, branch)
            await _edit_menu(
                context,
                f"✅ <b>Yangi fayl GitHub'ga qo'shildi.</b>\n\n"
                f"📦 <code>{_esc(repo)}</code>\n📄 <code>{_esc(file_path)}</code>",
                _back_keyboard("dev:gh:file"),
            )
            context.user_data.pop("dev_action", None)
            return DEV_MENU
        except Exception as exc:
            await _edit_menu(context, f"❌ {_esc(str(exc))}", _back_keyboard("dev:gh:file"))
            return DEV_WAIT_GITHUB

    repo = action.get("repo") or context.user_data.get("github_repo")
    file_path = action.get("path") or context.user_data.get("github_file")
    branch = context.user_data.get("github_branch") or "main"
    try:
        sha = context.user_data.get("github_sha")
        if not sha:
            current = await asyncio.to_thread(github_dev.read_file, repo, file_path, branch)
            sha = current["sha"]
        await asyncio.to_thread(
            github_dev.write_file, repo, file_path, content,
            f"Update {file_path}", branch, sha,
        )
        context.user_data["github_sha"] = None
        context.user_data["github_file_text"] = content
        context.user_data.pop("dev_action", None)
        await _edit_menu(
            context,
            f"✅ <b>Fayl GitHub'da yangilandi.</b>\n\n"
            f"📦 <code>{_esc(repo)}</code>\n📄 <code>{_esc(file_path)}</code>",
            _github_file_keyboard(),
        )
        return DEV_MENU
    except Exception as exc:
        await _edit_menu(context, f"❌ {_esc(str(exc))}", _back_keyboard("dev:gh:file"))
        return DEV_WAIT_GITHUB


async def on_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update) or not update.message:
        return ConversationHandler.END
    action = context.user_data.get("dev_action") or {}
    if action.get("type") != "pt_audio":
        return DEV_MENU
    file_id = None
    if update.message.audio:
        file_id = update.message.audio.file_id
    elif update.message.document:
        mime = (update.message.document.mime_type or "").lower()
        if mime.startswith("audio/") or (update.message.document.file_name or "").lower().endswith((".mp3", ".m4a", ".ogg", ".wav", ".flac")):
            file_id = update.message.document.file_id
    if not file_id:
        await _edit_menu(context, "⚠️ Iltimos, audio fayl yuboring (MP3/M4A/OGG).", _back_keyboard("dev:pt"))
        context.user_data["dev_action"] = action
        return DEV_WAIT_AUDIO
    config.set_tabrik_setting("audio_file_id", file_id)
    try:
        await update.message.delete()
    except Exception:
        pass
    await _edit_menu(context, "✅ Qo'shiq saqlandi va /tabrik + /pro uchun yoqildi.\n\n" + _pt_text(), _pt_keyboard())
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
