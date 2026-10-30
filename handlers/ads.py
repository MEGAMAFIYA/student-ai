"""
📢 Reklama — /developer > 📢 Reklama (FAQAT adminlar).

Oqim:
  1. 📢 Reklama bosiladi → FAOL reklamalar ro'yxati chiqadi (yo'q bo'lsa — bo'sh).
  2. ➕ Reklama qo'shish → guruh ID → matn → daqiqa (1..10) → tasdiqlash.
  3. Tasdiqlangach bot guruhni tekshiradi, BIRINCHI xabarni darhol yuboradi,
     so'ng har N daqiqada shu matnni qayta-qayta yuboradi.
  4. Har bir xabar ostida "🤖 Talaba AI" tugmasi bor (botga o'tish) — xuddi
     inline natijadagi tugma kabi (handlers/inline_query.py INLINE_MESSAGE_MARKUP).

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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, ChatMigrated, Forbidden, RetryAfter

import storage

logger = logging.getLogger(__name__)

MIN_INTERVAL = 1
MAX_INTERVAL = 10
MAX_TEXT_LEN = 4096          # Telegram bitta xabar chegarasi
MAX_FAILURES = 5             # ketma-ket shuncha xatodan keyin reklama to'xtatiladi
RESTART_FIRST_DELAY_SEC = 60  # qayta ishga tushgandan keyin birinchi yuborishgacha (maks.)
BUTTON_LABEL = "🤖 Talaba AI"

ACTIONS = {"ads", "adsadd", "adsview", "adsdel", "adsdelyes", "adsmin", "adsok"}

# Bu xatolar takrorlanmaydi — guruhga yuborishning o'zi imkonsiz bo'lib qolgan.
_PERMANENT_BAD_REQUEST = (
    "chat not found",
    "not enough rights",
    "have no rights",
    "chat_write_forbidden",
    "chat was deactivated",
    "peer_id_invalid",
    "need administrator rights",
)

_tasks: dict[str, asyncio.Task] = {}


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
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, Forbidden):
        return "Bot bu guruhda yo'q (yoki chiqarib yuborilgan) yoki xabar yozish huquqi yo'q."
    if "chat not found" in low or "peer_id_invalid" in low:
        return "Guruh topilmadi. ID to'g'ri ekanini va bot shu guruhga qo'shilganini tekshiring."
    if "not enough rights" in low or "have no rights" in low or "chat_write_forbidden" in low:
        return "Botda bu guruhga xabar yozish huquqi yo'q."
    if "parse entities" in low:
        return "Matn formati (HTML) noto'g'ri."
    return f"{type(exc).__name__}: {msg}"


# ============================================================
# Ekranlar
# ============================================================

def menu_text() -> str:
    ads = storage.get_ads()
    lines = ["📢 <b>Reklama</b>\n"]
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
        "Bot shu guruhda a'zo bo'lishi va xabar yozish huquqiga ega bo'lishi kerak. "
        "Ochiq guruh bo'lsa, <code>@username</code> ham bo'ladi."
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


async def check_group(bot, raw: str):
    """Guruhni tekshiradi. Qaytaradi: (chat, None) yoki (None, xato_matni)."""
    ref = parse_chat_ref(raw)
    if ref is None:
        return None, (
            "Guruh ID noto'g'ri. Raqam (masalan <code>-1001234567890</code>) "
            "yoki <code>@username</code> yuboring."
        )
    try:
        chat = await bot.get_chat(ref)
    except Exception as exc:
        logger.info("📢 Guruhni tekshirishda xato: ref=%s, %s: %s", ref, type(exc).__name__, exc)
        return None, _esc(describe_error(exc))

    if chat.type == "private":
        return None, "Bu guruh emas, shaxsiy chat ID si. Guruh ID sini yuboring."

    try:
        me = await bot.get_chat_member(chat.id, bot.id)
    except Exception as exc:
        return None, "Botning guruhdagi holatini aniqlab bo'lmadi: " + _esc(describe_error(exc))

    status = getattr(me, "status", "")
    if status in ("left", "kicked"):
        return None, "Bot bu guruhda a'zo emas. Avval botni guruhga qo'shing."
    if chat.type == "channel" and status not in ("administrator", "creator"):
        return None, "Kanalga yozish uchun bot kanalda administrator bo'lishi kerak."
    if status == "restricted" and getattr(me, "can_send_messages", True) is False:
        return None, "Botning bu guruhda xabar yozish huquqi cheklangan."
    return chat, None


# ============================================================
# Yuborish va fon rejasi
# ============================================================

async def send_ad(bot, ad: dict):
    """Reklamani guruhga bir marta yuboradi (matn ostida botga o'tish tugmasi bilan)."""
    markup = build_markup()
    try:
        return await bot.send_message(
            chat_id=ad["chat_id"], text=ad["text"], parse_mode="HTML", reply_markup=markup
        )
    except BadRequest as exc:
        if "parse entities" not in str(exc).lower():
            raise
        # HTML buzilgan bo'lsa ham reklama to'xtab qolmasin — oddiy matn sifatida yuboramiz.
        return await bot.send_message(
            chat_id=ad["chat_id"], text=_plain(ad["text"]), reply_markup=markup
        )


def _is_permanent(exc: Exception) -> bool:
    if isinstance(exc, Forbidden):
        return True
    if isinstance(exc, BadRequest):
        low = str(exc).lower()
        return any(marker in low for marker in _PERMANENT_BAD_REQUEST)
    return False


def _retry_seconds(exc: RetryAfter) -> float:
    value = exc.retry_after
    return float(value.total_seconds() if hasattr(value, "total_seconds") else value)


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
            await send_ad(application.bot, ad)
            failures = 0
            delay = ad["interval_min"] * 60
        except asyncio.CancelledError:
            raise
        except RetryAfter as exc:
            delay = _retry_seconds(exc) + 1
            logger.warning("📢 Flood limit: id=%s, %s soniyadan keyin qayta uriniladi.", ad_id, delay)
        except ChatMigrated as exc:
            # Oddiy guruh superguruhga aylangan — yangi ID bilan davom etamiz.
            storage.update_ad(ad_id, chat_id=exc.new_chat_id)
            logger.info("📢 Guruh ko'chirildi: id=%s yangi chat_id=%s", ad_id, exc.new_chat_id)
            delay = 1
        except Exception as exc:
            if _is_permanent(exc):
                await _stop_with_notice(application, ad, describe_error(exc))
                return
            failures += 1
            logger.error("📢 Reklamani yuborishda xato (%d/%d): id=%s %s: %s",
                         failures, MAX_FAILURES, ad_id, type(exc).__name__, exc)
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
    """Birinchi xabarni DARHOL yuboradi (shu bilan guruh ham tekshiriladi), keyin
    reklamani saqlab, takroriy yuborishni boshlaydi. Birinchi yuborish
    muvaffaqiyatsiz bo'lsa — hech narsa saqlanmaydi va xato ko'tariladi."""
    if len(storage.get_ads()) >= storage.MAX_ADS:
        raise ValueError(f"Faol reklamalar soni chegarasi ({storage.MAX_ADS}) to'lgan.")
    await send_ad(application.bot, {"chat_id": chat_id, "text": text})
    ad = storage.add_ad(chat_id, chat_title, text, interval_min, created_by)
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
        chat, error = await check_group(context.bot, raw)
        if error:
            await edit_menu(context, f"⚠️ {error}\n\n" + group_prompt_text(), _back("dev:ads"))
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
