"""
📢 Reklama — /developer > 📢 Reklama (FAQAT adminlar).

Oqim:
  1. 📢 Reklama bosiladi → FAOL reklamalar ro'yxati chiqadi (yo'q bo'lsa — bo'sh).
  2. ➕ Reklama qo'shish → guruh ID → matn → daqiqa (1..10) → tasdiqlash.
  3. Tasdiqlangach BIRINCHI xabar darhol yuboriladi, so'ng har N daqiqada
     shu matn qayta-qayta yuboriladi.

Xabar BOT nomidan emas, ulangan Telegram AKKAUNT (Telethon StringSession:
TG_API_ID / TG_API_HASH / TG_SESSION — telegram_mtproto.py) nomidan yuboriladi.
Oddiy akkaunt xabar ostiga tugma qo'sha olmaydi, shuning uchun yuborish INLINE
orqali amalga oshadi: akkaunt "@Bot ad:<id>" inline so'rovini bajaradi, bot
(handlers/inline_query.py > on_inline_ad_query) reklama matni va "🤖 Talaba AI"
tugmasi bilan natija qaytaradi, akkaunt uni guruhga yuboradi. Natijada xabar
akkauntdan ("via @Bot" belgisi bilan) chiqadi va ostida botga o'tish tugmasi bo'ladi.

Saqlash: storage.py (ads) — deployda yo'qolmaydi (Neon/Upstash/GitHub bo'lsa).
Rejalashtirish: handlers/reminders.py dagi kabi asyncio.create_task; bot qayta
ishga tushganda bot.py > _post_init > reschedule_all() hammasini tiklaydi.

Matn HTML sifatida saqlanadi (Telegram'da yozilgan qalin/kursiv/havola
formatlari saqlanadi); parse_mode="HTML" bilan yuboriladi.

Guruhga yuborib bo'lmay qolsa (bot chiqarib yuborilgan, guruh o'chirilgan,
yozish huquqi olingan) reklama AVTOMATIK to'xtatiladi va uni qo'shgan admin
xabardor qilinadi — bot behuda har daqiqada xato olib turmaydi.
"""

import asyncio
import html
import logging
import re
import uuid
from types import SimpleNamespace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import storage
import telegram_mtproto

logger = logging.getLogger(__name__)

MIN_INTERVAL = 1
MAX_INTERVAL = 10
MAX_TEXT_LEN = 4096          # Telegram bitta xabar chegarasi
MAX_FAILURES = 5             # ketma-ket shuncha xatodan keyin reklama to'xtatiladi
RESTART_FIRST_DELAY_SEC = 60  # qayta ishga tushgandan keyin birinchi yuborishgacha (maks.)
BUTTON_LABEL = "🤖 Talaba AI"

ACTIONS = {"ads", "adsadd", "adsview", "adsdel", "adsdelyes", "adsmin", "adsok"}

# Bu xatolar takrorlanmaydi — yuborishning o'zi imkonsiz bo'lib qolgan (Telethon
# xato klasslari nomi bo'yicha tekshiriladi: Telethon testlarda bo'lmasligi mumkin).
_PERMANENT_ERRORS = {
    "AdAccountError", "ChatWriteForbiddenError", "UserBannedInChannelError", "ChannelPrivateError",
    "ChatAdminRequiredError", "UserNotParticipantError", "ChannelBannedError", "ChatRestrictedError",
    "PeerIdInvalidError", "BotInlineDisabledError", "AuthKeyUnregisteredError", "SessionRevokedError",
    "SessionExpiredError", "UserDeactivatedError", "UserDeactivatedBanError", "ChatIdInvalidError",
}
_WAIT_ERRORS = {"FloodWaitError", "SlowModeWaitError"}

# Hozirgi ulangan akkaunt ID si (inline so'rovni faqat shu akkaunt/adminlar bajara olsin).
_session_user_id: int | None = None

_tasks: dict[str, asyncio.Task] = {}


class AdAccountError(Exception):
    """Akkaunt sozlanmagan / guruh topilmadi — qayta urinish foydasiz."""




# ============================================================
# Yordamchilar
# ============================================================

def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _plain(text_html: str) -> str:
    """HTML matndan teglarni olib tashlab, oddiy ko'rinishini qaytaradi (preview uchun)."""
    return html.unescape(re.sub(r"<[^>]+>", "", text_html or ""))


def _preview(text_html: str, limit: int = 300) -> str:
    plain = _plain(text_html).strip()
    return plain if len(plain) <= limit else plain[:limit].rstrip() + "…"


def _back(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data=callback_data)]])


def build_markup() -> InlineKeyboardMarkup:
    """Guruhga yuborilgan reklama ostidagi 'botga o'tish' tugmasi."""
    from handlers import mention_dispatch  # kech import: haqiqiy username post_init'da o'rnatiladi
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(BUTTON_LABEL, url=f"https://t.me/{mention_dispatch.get_bot_username()}")
    ]])


def describe_error(exc: Exception) -> str:
    """Adminga ko'rsatiladigan tushunarli xato matni."""
    name = type(exc).__name__
    if name == "AdAccountError":
        return str(exc)
    if name in ("ChatWriteForbiddenError", "ChatRestrictedError", "UserBannedInChannelError", "ChannelBannedError"):
        return "Akkauntning bu guruhga xabar yozish huquqi yo'q (yoki guruhdan cheklangan)."
    if name in ("ChannelPrivateError", "UserNotParticipantError", "PeerIdInvalidError", "ChatIdInvalidError"):
        return "Akkaunt bu guruhda a'zo emas yoki guruhga kira olmaydi."
    if name == "ChatAdminRequiredError":
        return "Bu yerga yozish uchun akkaunt administrator bo'lishi kerak (kanal bo'lishi mumkin)."
    if name == "BotInlineDisabledError":
        return "Bot uchun inline rejim o'chirilgan (@BotFather > Bot Settings > Inline Mode)."
    if name in ("AuthKeyUnregisteredError", "SessionRevokedError", "SessionExpiredError",
                "UserDeactivatedError", "UserDeactivatedBanError"):
        return "Akkaunt sessiyasi yaroqsiz — TG_SESSION ni qaytadan yarating."
    if name in _WAIT_ERRORS:
        return f"Telegram vaqtincha cheklov qo'ydi ({getattr(exc, 'seconds', '?')} soniya)."
    return f"{name}: {exc}"


# ============================================================
# Ekranlar
# ============================================================

def menu_text() -> str:
    ads = storage.get_ads()
    lines = ["📢 <b>Reklama</b>\n"]
    if not telegram_mtproto.is_configured():
        lines.append(
            "⚠️ <i>Akkaunt ulanmagan: TG_API_ID, TG_API_HASH va TG_SESSION sozlanmaguncha "
            "reklama yuborib bo'lmaydi.</i>\n"
        )
    else:
        lines.append("👤 <i>Xabarlar ulangan Telegram akkaunt nomidan yuboriladi.</i>\n")
    if not ads:
        lines.append("<i>Hozircha faol reklama yo'q.</i>")
    else:
        lines.append(f"Faol reklamalar: <b>{len(ads)}</b>\n")
        for i, ad in enumerate(ads, start=1):
            title = _esc(ad.get("chat_title") or ad["chat_id"])
            lines.append(
                f"{i}. 📍 <b>{title}</b> (<code>{ad['chat_id']}</code>)\n"
                f"   ⏱ har {ad['interval_min']} daqiqada\n"
                f"   💬 {_esc(_preview(ad['text'], 80))}"
            )
    return "\n".join(lines)


def menu_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for ad in storage.get_ads():
        label = f"📍 {ad.get('chat_title') or ad['chat_id']} · {ad['interval_min']} daq"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"dev:adsview:{ad['id']}")])
    rows.append([InlineKeyboardButton("➕ Reklama qo'shish", callback_data="dev:adsadd")])
    rows.append([InlineKeyboardButton("🔄 Yangilash", callback_data="dev:ads")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")])
    return InlineKeyboardMarkup(rows)


def view_text(ad: dict) -> str:
    return (
        "📢 <b>Reklama</b>\n\n"
        f"📍 Guruh: <b>{_esc(ad.get('chat_title') or '—')}</b> (<code>{ad['chat_id']}</code>)\n"
        f"⏱ Interval: har <b>{ad['interval_min']}</b> daqiqada\n\n"
        f"💬 Matn:\n{_esc(_preview(ad['text'], 800))}"
    )


def view_keyboard(ad_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 To'xtatish va o'chirish", callback_data=f"dev:adsdel:{ad_id}")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:ads")],
    ])


def group_prompt_text() -> str:
    return (
        "➕ <b>Reklama qo'shish</b> — 1/3\n\n"
        "Reklama yuboriladigan <b>guruh ID</b> raqamini yuboring.\n"
        "Masalan: <code>-1001234567890</code>\n\n"
        "Ulangan <b>akkaunt</b> shu guruhda a'zo bo'lishi va yozish huquqiga ega bo'lishi kerak "
        "(xabar akkaunt nomidan yuboriladi). Ochiq guruh bo'lsa, <code>@username</code> ham bo'ladi."
    )


def text_prompt_text(chat_title: str) -> str:
    return (
        "➕ <b>Reklama qo'shish</b> — 2/3\n\n"
        f"✅ Guruh: <b>{_esc(chat_title)}</b>\n\n"
        "Endi <b>reklama matnini</b> yuboring.\n"
        "Qalin, kursiv, havola kabi formatlar saqlanadi. "
        f"Matn ostiga avtomatik <b>{_esc(BUTTON_LABEL)}</b> tugmasi qo'shiladi."
    )


def minutes_prompt_text() -> str:
    return (
        "➕ <b>Reklama qo'shish</b> — 3/3\n\n"
        f"Necha <b>daqiqada</b> bir marta yuborilsin?\n"
        f"{MIN_INTERVAL} dan {MAX_INTERVAL} gacha tugmani bosing yoki raqam yozing."
    )


def minutes_keyboard() -> InlineKeyboardMarkup:
    nums = list(range(MIN_INTERVAL, MAX_INTERVAL + 1))
    rows = [
        [InlineKeyboardButton(str(n), callback_data=f"dev:adsmin:{n}") for n in nums[i:i + 5]]
        for i in range(0, len(nums), 5)
    ]
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:ads")])
    return InlineKeyboardMarkup(rows)


def confirm_text(action: dict) -> str:
    return (
        "✅ <b>Tasdiqlang</b>\n\n"
        f"📍 Guruh: <b>{_esc(action['chat_title'])}</b> (<code>{action['chat_id']}</code>)\n"
        f"⏱ Interval: har <b>{action['interval_min']}</b> daqiqada\n\n"
        f"💬 Matn:\n{_esc(_preview(action['text'], 600))}\n\n"
        "Tasdiqlasangiz, birinchi xabar <b>darhol</b> yuboriladi, keyin har "
        f"{action['interval_min']} daqiqada takrorlanadi."
    )


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Tasdiqlash", callback_data="dev:adsok")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="dev:ads")],
    ])


# ============================================================
# Guruhni tekshirish
# ============================================================

def parse_chat_ref(raw: str):
    """'-1001234567890' -> int, '@guruh' -> '@guruh', aks holda None."""
    raw = (raw or "").strip()
    if re.fullmatch(r"-?\d{5,20}", raw):
        return int(raw)
    if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", raw):
        return raw
    return None


def _peer_id(entity) -> int:
    """Telethon entity -> '-100...' (kanal/superguruh), '-...' (oddiy guruh) yoki user ID."""
    name = type(entity).__name__
    if name in ("Channel", "ChannelForbidden"):
        return int(f"-100{entity.id}")
    if name in ("Chat", "ChatForbidden"):
        return -int(entity.id)
    return int(entity.id)


def _candidates(ref) -> list:
    """Admin ko'pincha '-100' siz ID yuboradi (masalan 1276198486) — shuning uchun
    musbat son uchun superguruh (-100...), oddiy guruh (-...) va oxirida user variantlari sinaladi."""
    if isinstance(ref, str):
        return [ref]
    if ref < 0:
        return [ref]
    return [int(f"-100{ref}"), -ref, ref]


_DIALOGS_REWARM_SEC = 300


async def _account_client():
    global _session_user_id
    if not telegram_mtproto.is_configured():
        raise AdAccountError("Akkaunt ulanmagan: TG_API_ID, TG_API_HASH va TG_SESSION sozlang.")
    try:
        client = await telegram_mtproto.get_client()
    except RuntimeError as exc:
        raise AdAccountError(str(exc)) from exc
    if _session_user_id is None:
        me = await client.get_me()
        _session_user_id = int(me.id)
    return client


async def _resolve_entity(client, ref):
    """Akkaunt ko'ra oladigan guruhni topadi. StringSession entity keshini saqlamaydi,
    shuning uchun topilmasa dialoglar bir marta yuklanib, qayta uriniladi."""
    import time
    warmed = False
    for candidate in _candidates(ref):
        for attempt in (0, 1):
            try:
                return await client.get_entity(candidate)
            except (ValueError, KeyError):
                if attempt == 0 and not warmed and not isinstance(candidate, str):
                    last = getattr(client, "_ads_dialogs_warm_at", None)
                    now = time.monotonic()
                    if last is None or now - last > _DIALOGS_REWARM_SEC:
                        client._ads_dialogs_warm_at = now
                        try:
                            await client.get_dialogs(limit=1000)
                        except Exception as exc:
                            logger.warning("📢 get_dialogs xato: %s: %s", type(exc).__name__, exc)
                    warmed = True
                    continue
                break
            except Exception as exc:
                logger.info("📢 get_entity(%r) xato: %s: %s", candidate, type(exc).__name__, exc)
                break
    raise AdAccountError(
        "Akkaunt bu guruhni topa olmadi. ID to'g'ri ekanini va akkaunt shu guruhda a'zo "
        "ekanini tekshiring (guruh ID lari odatda -100 bilan boshlanadi)."
    )


async def check_group(raw: str):
    """Guruhni AKKAUNT orqali tekshiradi. Qaytaradi: (chat, None) yoki (None, xato_matni)."""
    ref = parse_chat_ref(raw)
    if ref is None:
        return None, (
            "Guruh ID noto'g'ri. Raqam (masalan <code>-1001234567890</code>) "
            "yoki <code>@username</code> yuboring."
        )
    try:
        client = await _account_client()
        entity = await _resolve_entity(client, ref)
    except Exception as exc:
        logger.info("📢 Guruhni tekshirishda xato: ref=%s, %s: %s", ref, type(exc).__name__, exc)
        return None, _esc(describe_error(exc))

    if type(entity).__name__ == "User":
        return None, "Bu guruh emas, shaxsiy chat (foydalanuvchi) ID si. Guruh ID sini yuboring."
    title = getattr(entity, "title", None) or (
        f"@{entity.username}" if getattr(entity, "username", None) else str(_peer_id(entity))
    )
    return SimpleNamespace(id=_peer_id(entity), title=title), None


# ============================================================
# Yuborish va fon rejasi
# ============================================================

async def send_ad(ad: dict):
    """Reklamani guruhga AKKAUNT nomidan yuboradi: akkaunt "@Bot ad:<id>" inline natijasini
    guruhga yuboradi — shu tufayli xabar ostida botga o'tish tugmasi bo'ladi."""
    from handlers import mention_dispatch
    client = await _account_client()
    entity = await _resolve_entity(client, int(ad["chat_id"]))
    results = await client.inline_query(mention_dispatch.get_bot_username(), f"ad:{ad['id']}", entity=entity)
    if not results:
        raise RuntimeError("Bot reklama uchun inline natija qaytarmadi (bot ishlayaptimi / inline yoqilganmi?).")
    await results[0].click(entity)


async def on_inline_ad_query(update, context):
    """Akkaunt "@Bot ad:<id>" so'raganda: reklama matni + botga o'tish tugmasi bilan natija."""
    from telegram import InlineQueryResultArticle, InputTextMessageContent
    inline = update.inline_query
    ad_id = inline.query.strip()[3:].strip()
    ad = storage.get_ad(ad_id)
    uid = getattr(inline.from_user, "id", None)
    allowed = uid in config.ADMIN_IDS or (_session_user_id is not None and uid == _session_user_id)
    if not ad or not allowed:
        await inline.answer([], cache_time=0, is_personal=True)
        return
    result = InlineQueryResultArticle(
        id=f"ads_{ad['id']}_{uuid.uuid4().hex[:8]}",
        title="📢 Reklama",
        description=_preview(ad["text"], 100),
        input_message_content=InputTextMessageContent(ad["text"], parse_mode="HTML"),
        reply_markup=build_markup(),
    )
    await inline.answer([result], cache_time=0, is_personal=True)


def _is_permanent(exc: Exception) -> bool:
    return type(exc).__name__ in _PERMANENT_ERRORS


async def _stop_with_notice(application, ad: dict, reason: str):
    storage.remove_ad(ad["id"])
    _tasks.pop(ad["id"], None)
    logger.warning("📢 Reklama avtomatik to'xtatildi: id=%s chat_id=%s sabab=%s", ad["id"], ad["chat_id"], reason)
    try:
        await application.bot.send_message(
            chat_id=ad["created_by"],
            text=(
                "⛔ <b>Reklama avtomatik to'xtatildi</b>\n\n"
                f"📍 Guruh: <b>{_esc(ad.get('chat_title') or '—')}</b> (<code>{ad['chat_id']}</code>)\n"
                f"Sabab: {_esc(reason)}\n\n"
                "Muammo hal bo'lgach, /developer → 📢 Reklama orqali qaytadan qo'shing."
            ),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.info("📢 Adminni xabardor qilib bo'lmadi: %s: %s", type(exc).__name__, exc)


async def _ad_loop(application, ad_id: str, first_delay: float):
    delay = first_delay
    failures = 0
    while True:
        await asyncio.sleep(max(0.0, delay))
        ad = storage.get_ad(ad_id)
        if not ad:
            return  # admin o'chirgan
        try:
            await send_ad(ad)
            failures = 0
            delay = ad["interval_min"] * 60
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            if name in _WAIT_ERRORS:
                delay = float(getattr(exc, "seconds", 30)) + 1
                logger.warning("📢 Telegram cheklovi (%s): id=%s, %s soniyadan keyin qayta uriniladi.", name, ad_id, delay)
                continue
            if _is_permanent(exc):
                await _stop_with_notice(application, ad, describe_error(exc))
                return
            failures += 1
            logger.error("📢 Reklamani yuborishda xato (%d/%d): id=%s %s: %s",
                         failures, MAX_FAILURES, ad_id, name, exc)
            if failures >= MAX_FAILURES:
                await _stop_with_notice(
                    application, ad,
                    f"{MAX_FAILURES} marta ketma-ket yuborib bo'lmadi ({describe_error(exc)})",
                )
                return
            delay = ad["interval_min"] * 60


def start_ad(application, ad: dict, first_delay: float | None = None) -> None:
    stop_task(ad["id"])
    if first_delay is None:
        first_delay = ad["interval_min"] * 60
    _tasks[ad["id"]] = asyncio.create_task(_ad_loop(application, ad["id"], first_delay))


def stop_task(ad_id: str) -> None:
    task = _tasks.pop(ad_id, None)
    if task and not task.done():
        task.cancel()


def reschedule_all(application) -> None:
    """Bot ishga tushganda (post_init) chaqiriladi — saqlangan barcha reklamalar tiklanadi."""
    ads = storage.get_ads()
    for ad in ads:
        start_ad(application, ad, first_delay=min(ad["interval_min"] * 60, RESTART_FIRST_DELAY_SEC))
    logger.info("📢 Reklamalar qayta rejalashtirildi: %d ta.", len(ads))


async def activate(application, *, chat_id: int, chat_title: str, text: str,
                   interval_min: int, created_by: int) -> dict:
    """Reklamani saqlaydi, birinchi xabarni DARHOL yuboradi (shu bilan guruh/huquq ham
    tekshiriladi) va takroriy yuborishni boshlaydi. Birinchi yuborish muvaffaqiyatsiz
    bo'lsa — reklama o'chiriladi (hech narsa qolmaydi) va xato ko'tariladi.
    (Inline handler reklamani storage'dan o'qiydi, shuning uchun avval saqlanadi.)"""
    if len(storage.get_ads()) >= storage.MAX_ADS:
        raise ValueError(f"Faol reklamalar soni chegarasi ({storage.MAX_ADS}) to'lgan.")
    ad = storage.add_ad(chat_id, chat_title, text, interval_min, created_by)
    try:
        await send_ad(ad)
    except BaseException:
        storage.remove_ad(ad["id"])
        raise
    start_ad(application, ad)
    return ad


# ============================================================
# /developer ga ulanish (handlers/developer.py shu funksiyalarni chaqiradi)
# ============================================================

async def handle_dev_callback(action, parts, update, context, query, DEV_MENU, DEV_WAIT_TEXT):
    from handlers import developer as dev

    edit = dev._safe_edit_query

    if action == "ads":
        context.user_data.pop("dev_action", None)
        await edit(query, menu_text(), reply_markup=menu_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "adsadd":
        if len(storage.get_ads()) >= storage.MAX_ADS:
            await edit(
                query, f"⚠️ Faol reklamalar soni chegarasi ({storage.MAX_ADS}) to'lgan.",
                reply_markup=_back("dev:ads"), parse_mode="HTML",
            )
            return DEV_MENU
        context.user_data["dev_action"] = {"type": "ads_group"}
        await edit(query, group_prompt_text(), reply_markup=_back("dev:ads"), parse_mode="HTML")
        return DEV_WAIT_TEXT

    if action == "adsview":
        ad = storage.get_ad(parts[2]) if len(parts) > 2 else None
        if not ad:
            await edit(query, "⚠️ Bu reklama topilmadi (o'chirilgan bo'lishi mumkin).",
                       reply_markup=_back("dev:ads"), parse_mode="HTML")
            return DEV_MENU
        await edit(query, view_text(ad), reply_markup=view_keyboard(ad["id"]), parse_mode="HTML")
        return DEV_MENU

    if action == "adsdel":
        ad_id = parts[2] if len(parts) > 2 else ""
        await edit(
            query,
            "🗑 <b>Reklamani to'xtatish</b>\n\nReklama o'chiriladi va guruhga boshqa yuborilmaydi. Davom etasizmi?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚠️ Ha, o'chirish", callback_data=f"dev:adsdelyes:{ad_id}")],
                [InlineKeyboardButton("⬅️ Bekor qilish", callback_data=f"dev:adsview:{ad_id}")],
            ]),
            parse_mode="HTML",
        )
        return DEV_MENU

    if action == "adsdelyes":
        ad_id = parts[2] if len(parts) > 2 else ""
        stop_task(ad_id)
        storage.remove_ad(ad_id)
        await edit(query, "✅ Reklama to'xtatildi va o'chirildi.\n\n" + menu_text(),
                   reply_markup=menu_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "adsmin":
        pending = context.user_data.get("dev_action") or {}
        if pending.get("type") != "ads_minutes":
            await edit(query, "⚠️ Reklama sessiyasi topilmadi. Qaytadan boshlang.\n\n" + menu_text(),
                       reply_markup=menu_keyboard(), parse_mode="HTML")
            return DEV_MENU
        try:
            minutes = int(parts[2])
        except (IndexError, ValueError):
            minutes = 0
        if not (MIN_INTERVAL <= minutes <= MAX_INTERVAL):
            await edit(query, "⚠️ Daqiqa noto'g'ri.\n\n" + minutes_prompt_text(),
                       reply_markup=minutes_keyboard(), parse_mode="HTML")
            return DEV_WAIT_TEXT
        confirm = {**pending, "type": "ads_confirm", "interval_min": minutes}
        context.user_data["dev_action"] = confirm
        await edit(query, confirm_text(confirm), reply_markup=confirm_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "adsok":
        pending = context.user_data.pop("dev_action", None) or {}
        if pending.get("type") != "ads_confirm":
            await edit(query, "⚠️ Reklama sessiyasi topilmadi (allaqachon tasdiqlangan bo'lishi mumkin).\n\n"
                       + menu_text(), reply_markup=menu_keyboard(), parse_mode="HTML")
            return DEV_MENU
        await edit(query, "⏳ Guruh tekshirilmoqda va birinchi xabar yuborilmoqda...", parse_mode="HTML")
        try:
            ad = await activate(
                context.application,
                chat_id=pending["chat_id"], chat_title=pending["chat_title"],
                text=pending["text"], interval_min=pending["interval_min"],
                created_by=update.effective_user.id,
            )
        except Exception as exc:
            logger.warning("📢 Reklamani faollashtirib bo'lmadi: %s: %s", type(exc).__name__, exc)
            await edit(
                query,
                "❌ <b>Reklama faollashtirilmadi.</b>\n\n" + _esc(describe_error(exc))
                + "\n\nHech narsa saqlanmadi.",
                reply_markup=_back("dev:ads"), parse_mode="HTML",
            )
            return DEV_MENU
        await edit(
            query,
            "✅ <b>Reklama faollashtirildi.</b>\n\n"
            f"Birinchi xabar <b>{_esc(ad['chat_title'])}</b> guruhiga yuborildi. "
            f"Keyingilari har {ad['interval_min']} daqiqada yuboriladi.\n\n" + menu_text(),
            reply_markup=menu_keyboard(), parse_mode="HTML",
        )
        return DEV_MENU

    return DEV_MENU


async def handle_dev_text(action, update, context, DEV_MENU, DEV_WAIT_TEXT):
    """Reklama qo'shish oqimidagi matnli javoblar (guruh ID, matn, daqiqa)."""
    from handlers import developer as dev

    edit_menu = dev._edit_menu
    kind = action.get("type")
    message = update.message
    raw = (message.text or "").strip()

    if kind == "ads_group":
        chat, error = await check_group(raw)
        if error:
            # Kiritilgan qiymat ham ko'rsatiladi: xato matni avvalgisi bilan bir xil bo'lsa,
            # Telegram "message is not modified" deb xabarni yangilamay qo'yadi (qotib qolgandek ko'rinadi).
            await edit_menu(
                context, f"⚠️ <code>{_esc(raw[:40])}</code>: {error}\n\n" + group_prompt_text(), _back("dev:ads")
            )
            context.user_data["dev_action"] = action
            return DEV_WAIT_TEXT
        title = chat.title or (f"@{chat.username}" if chat.username else str(chat.id))
        context.user_data["dev_action"] = {"type": "ads_text", "chat_id": chat.id, "chat_title": title}
        await edit_menu(context, text_prompt_text(title), _back("dev:ads"))
        return DEV_WAIT_TEXT

    if kind == "ads_text":
        if not raw:
            context.user_data["dev_action"] = action
            return DEV_WAIT_TEXT
        if len(raw) > MAX_TEXT_LEN:
            await edit_menu(
                context,
                f"⚠️ Matn juda uzun ({len(raw)} belgi). Telegram chegarasi — {MAX_TEXT_LEN} belgi.\n\n"
                + text_prompt_text(action["chat_title"]),
                _back("dev:ads"),
            )
            context.user_data["dev_action"] = action
            return DEV_WAIT_TEXT
        # text_html: admin yozgan qalin/kursiv/havola formatlarini HTML ko'rinishida saqlaydi
        # (<, >, & belgilari o'zi escape qilinadi, shuning uchun parse_mode="HTML" xavfsiz).
        context.user_data["dev_action"] = {
            "type": "ads_minutes",
            "chat_id": action["chat_id"],
            "chat_title": action["chat_title"],
            "text": message.text_html,
        }
        await edit_menu(context, minutes_prompt_text(), minutes_keyboard())
        return DEV_WAIT_TEXT

    if kind == "ads_minutes":
        if not raw.isdigit() or not (MIN_INTERVAL <= int(raw) <= MAX_INTERVAL):
            await edit_menu(
                context,
                f"⚠️ Faqat {MIN_INTERVAL} dan {MAX_INTERVAL} gacha butun son kiriting.\n\n" + minutes_prompt_text(),
                minutes_keyboard(),
            )
            context.user_data["dev_action"] = action
            return DEV_WAIT_TEXT
        confirm = {**action, "type": "ads_confirm", "interval_min": int(raw)}
        context.user_data["dev_action"] = confirm
        await edit_menu(context, confirm_text(confirm), confirm_keyboard())
        return DEV_MENU

    # ads_confirm holatida matn yozilsa — e'tiborsiz, tugmalar kutiladi
    context.user_data["dev_action"] = action
    return DEV_MENU
