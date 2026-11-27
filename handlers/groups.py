"""👥 /developer > Guruh — AKKAUNT (TG_SESSION) nomidan ikki amal:

📥 Kontakt yig'ish — ID berilgan guruhning barcha a'zolarini akkaunt kontaktlariga
                      (o'z ismi bilan) saqlaydi.
📤 Kontakt qo'shish — akkauntdagi barcha kontaktlarni ID berilgan guruhga qo'shadi.

Akkaunt va guruhni topish handlers/ads.py dagi funksiyalar orqali (bitta umumiy
Telethon client). Amal fon vazifasida bajariladi: jarayon xabari yangilanib turadi,
"🛑 To'xtatish" tugmasi bor. Telegram cheklovlariga (FloodWait / PeerFlood) hurmat:
sekin ishlaydi, jiddiy cheklovda o'zi to'xtaydi.
"""
import asyncio
import logging
import time
from collections import Counter

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import telegram_mtproto
from handlers import ads

logger = logging.getLogger(__name__)

ACTIONS = {"grp", "grpcol", "grpadd", "grpok", "grpstop"}

COLLECT_DELAY_SEC = 1.0        # kontakt saqlashlar orasidagi pauza
ADD_DELAY_SEC = 4.0            # guruhga qo'shishlar orasidagi pauza (Telegram tez qo'shishni jazolaydi)
MAX_FLOOD_WAIT_SEC = 120       # shundan uzoq FloodWait bo'lsa — kutmasdan to'xtaymiz
MAX_CONSECUTIVE_FAILS = 10     # ketma-ket shuncha xatodan keyin to'xtaymiz
PROGRESS_EVERY_SEC = 4.0       # jarayon xabarini yangilash oralig'i

# Davom ettirib bo'lmaydigan xatolar (akkaunt/guruh darajasidagi) — butun amal to'xtatiladi.
_FATAL_ERRORS = {
    "FloodWaitError", "PeerFloodError", "ChatAdminRequiredError", "ChatWriteForbiddenError",
    "UserBannedInChannelError", "ChannelPrivateError", "ChatRestrictedError", "ChannelBannedError",
    "AuthKeyUnregisteredError", "SessionRevokedError", "SessionExpiredError",
    "UserDeactivatedError", "UserDeactivatedBanError",
}

# Guruhga qo'shishda BITTA foydalanuvchiga tegishli (davom etiladigan) sabablar.
_ADD_REASONS = {
    "UserPrivacyRestrictedError": "maxfiylik sozlamasi",
    "UserNotMutualContactError": "maxfiylik sozlamasi",
    "UserAlreadyParticipantError": "allaqachon a'zo",
    "UserChannelsTooMuchError": "juda ko'p guruhda",
    "UserKickedError": "guruhdan chiqarilgan",
    "InputUserDeactivatedError": "o'chirilgan akkaunt",
    "UserIdInvalidError": "topilmadi",
    "UserBotError": "bot",
}

# Bitta ish bir vaqtda: {"kind", "title", "stop", "task"}
_job: dict | None = None


# ============================================================
# Ekranlar
# ============================================================

def _esc(value) -> str:
    return ads._esc(value)


def _back(cb: str = "dev:grp") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data=cb)]])


def menu_text() -> str:
    lines = [
        "👥 <b>Guruh</b>\n",
        "📥 <b>Kontakt yig'ish</b> — guruh a'zolarini akkaunt kontaktlariga o'z ismi bilan saqlaydi.",
        "📤 <b>Kontakt qo'shish</b> — akkauntdagi barcha kontaktlarni guruhga qo'shadi.\n",
        "<i>Ikkalasi ham akkaunt (TG_SESSION) nomidan bajariladi; akkaunt guruhda a'zo bo'lishi kerak.</i>",
    ]
    if not telegram_mtproto.is_configured():
        lines.append("\n⚠️ <i>Akkaunt ulanmagan: TG_API_ID, TG_API_HASH va TG_SESSION sozlang.</i>")
    if _job is not None:
        lines.append(f"\n⏳ Hozir ish ketmoqda: {_kind_label(_job['kind'])} — <b>{_esc(_job['title'])}</b>")
    return "\n".join(lines)


def menu_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if _job is not None:
        rows.append([InlineKeyboardButton("🛑 To'xtatish", callback_data="dev:grpstop")])
    else:
        rows.append([
            InlineKeyboardButton("📥 Kontakt yig'ish", callback_data="dev:grpcol"),
            InlineKeyboardButton("📤 Kontakt qo'shish", callback_data="dev:grpadd"),
        ])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")])
    return InlineKeyboardMarkup(rows)


def _kind_label(kind: str) -> str:
    return "📥 Kontakt yig'ish" if kind == "collect" else "📤 Kontakt qo'shish"


def _prompt_text(kind: str) -> str:
    what = (
        "a'zolari kontaktlarga saqlanadigan" if kind == "collect"
        else "akkaunt kontaktlari qo'shiladigan"
    )
    return (
        f"{_kind_label(kind)}\n\n"
        f"{what.capitalize()} guruhning ID sini yuboring.\n"
        "Masalan: <code>-1001234567890</code> yoki <code>@guruh_username</code>"
    )


def _confirm_text(pending: dict) -> str:
    title, count = _esc(pending["title"]), pending.get("count")
    if pending["kind"] == "collect":
        size = f" (taxminan {count} a'zo)" if count else ""
        body = (f"<b>{title}</b>{size} guruhining a'zolari akkaunt kontaktlariga o'z ismi bilan saqlanadi. "
                "Bot, o'chirilgan akkaunt va allaqachon kontaktda bor odamlar o'tkazib yuboriladi.")
    else:
        size = f" ({count} ta kontakt)" if count else ""
        body = (f"Akkauntdagi barcha kontaktlar{size} <b>{title}</b> guruhiga qo'shiladi. "
                f"Telegram cheklovi uchun har biri orasida ~{int(ADD_DELAY_SEC)} soniya pauza bo'ladi.")
    return f"{_kind_label(pending['kind'])}\n\n{body}\n\nBoshlaymizmi?"


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Boshlash", callback_data="dev:grpok")],
        [InlineKeyboardButton("⬅️ Bekor qilish", callback_data="dev:grp")],
    ])


def _stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑 To'xtatish", callback_data="dev:grpstop")]])


def _progress_text(kind: str, title: str, state: dict) -> str:
    done = state["ok"] + state["skipped"] + state["failed"]
    lines = [f"{_kind_label(kind)} — <b>{_esc(title)}</b>\n", f"⏳ Jarayon: {done}" + (
        f" / {state['total']}" if state.get("total") else "")]
    lines.append(f"✅ {'Saqlandi' if kind == 'collect' else 'Qo`shildi'}: {state['ok']}")
    lines.append(f"⏭ O'tkazildi: {state['skipped']}")
    if state["failed"]:
        lines.append(f"⚠️ Bajarilmadi: {state['failed']}")
    if state.get("waiting"):
        lines.append(f"\n⏸ Telegram cheklovi: {state['waiting']} soniya kutilmoqda...")
    return "\n".join(lines)


def _final_text(kind: str, title: str, state: dict) -> str:
    if state.get("error"):
        head = "⛔ <b>To'xtatildi</b>"
    elif state["stop"]:
        head = "🛑 <b>To'xtatildi</b>"
    else:
        head = "✅ <b>Tugadi</b>"
    lines = [f"{head} — {_kind_label(kind)}\n<b>{_esc(title)}</b>\n"]
    lines.append(f"✅ {'Saqlandi' if kind == 'collect' else 'Qo`shildi'}: {state['ok']}")
    lines.append(f"⏭ O'tkazildi: {state['skipped']}")
    if state["failed"]:
        lines.append(f"⚠️ Bajarilmadi: {state['failed']}")
    if state["reasons"]:
        detail = ", ".join(f"{name}: {n}" for name, n in state["reasons"].most_common())
        lines.append(f"<i>Sabablar — {_esc(detail)}</i>")
    if state.get("error"):
        lines.append(f"\n❌ {_esc(state['error'])}")
    return "\n".join(lines)


# ============================================================
# Telethon so'rovlari (alohida funksiyalar — testda almashtirish oson)
# ============================================================

def _contact_names(user) -> tuple[str, str]:
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    if not first and not last:
        username = getattr(user, "username", None)
        first = f"@{username}" if username else f"User {user.id}"
    return first, last


def _add_contact_request(user):
    from telethon.tl.functions.contacts import AddContactRequest
    first, last = _contact_names(user)
    return AddContactRequest(id=user, first_name=first, last_name=last, phone="")


def _invite_request(entity, user):
    if type(entity).__name__ == "Channel":
        from telethon.tl.functions.channels import InviteToChannelRequest
        return InviteToChannelRequest(entity, [user])
    from telethon.tl.functions.messages import AddChatUserRequest
    return AddChatUserRequest(chat_id=entity.id, user_id=user, fwd_limit=50)


async def _get_contacts(client) -> list:
    from telethon.tl.functions.contacts import GetContactsRequest
    result = await client(GetContactsRequest(hash=0))
    return list(getattr(result, "users", []) or [])


def _is_skippable(user, my_id) -> bool:
    return bool(
        getattr(user, "bot", False) or getattr(user, "deleted", False)
        or getattr(user, "is_self", False) or getattr(user, "id", None) == my_id
    )


# ============================================================
# Amallar
# ============================================================

def _new_state(total=None) -> dict:
    return {"ok": 0, "skipped": 0, "failed": 0, "reasons": Counter(), "total": total,
            "stop": False, "error": None, "waiting": 0}


def _fatal_message(exc: Exception) -> str:
    name = type(exc).__name__
    if name == "PeerFloodError":
        return ("Telegram akkauntni spam deb cheklab qo'ydi (PeerFlood). Bir necha soat/kun kutib, "
                "keyin urinib ko'ring.")
    if name == "FloodWaitError":
        return f"Telegram uzoq cheklov qo'ydi ({getattr(exc, 'seconds', '?')} soniya). Keyinroq qayta urinib ko'ring."
    return ads.describe_error(exc)


async def _call(client, request, state, on_progress):
    """So'rovni bajaradi; qisqa FloodWait bo'lsa kutib, bir marta qayta uradi."""
    for attempt in (0, 1):
        try:
            return await client(request)
        except Exception as exc:
            if type(exc).__name__ == "FloodWaitError" and attempt == 0:
                wait = int(getattr(exc, "seconds", 0) or 0)
                if wait <= MAX_FLOOD_WAIT_SEC:
                    state["waiting"] = wait
                    await on_progress(force=True)
                    await asyncio.sleep(wait + 1)
                    state["waiting"] = 0
                    continue
            raise


async def collect_contacts(client, entity, state, on_progress, my_id=None):
    """Guruh a'zolarini kontaktlarga saqlaydi."""
    fails = 0
    async for user in client.iter_participants(entity):
        if state["stop"]:
            break
        if _is_skippable(user, my_id) or getattr(user, "contact", False):
            state["skipped"] += 1
            await on_progress()
            continue
        try:
            await _call(client, _add_contact_request(user), state, on_progress)
            state["ok"] += 1
            fails = 0
        except Exception as exc:
            name = type(exc).__name__
            if name in _FATAL_ERRORS:
                state["error"] = _fatal_message(exc)
                break
            state["failed"] += 1
            state["reasons"][name] += 1
            fails += 1
            logger.info("👥 Kontakt saqlanmadi: user=%s %s: %s", getattr(user, "id", "?"), name, exc)
            if fails >= MAX_CONSECUTIVE_FAILS:
                state["error"] = f"Ketma-ket {fails} ta xato: {name}. Jarayon to'xtatildi."
                break
        await on_progress()
        await asyncio.sleep(COLLECT_DELAY_SEC)


async def add_contacts_to_group(client, entity, contacts, state, on_progress, my_id=None):
    """Akkaunt kontaktlarini guruhga qo'shadi (guruhdagilar o'tkazib yuboriladi)."""
    existing = set()
    try:
        async for member in client.iter_participants(entity):
            existing.add(member.id)
    except Exception as exc:  # a'zolar ro'yxati yopiq bo'lishi mumkin — shunchaki o'tkazib yuboramiz
        logger.info("👥 Guruh a'zolarini o'qib bo'lmadi: %s: %s", type(exc).__name__, exc)

    fails = 0
    for user in contacts:
        if state["stop"]:
            break
        if _is_skippable(user, my_id) or user.id in existing:
            state["skipped"] += 1
            await on_progress()
            continue
        try:
            await _call(client, _invite_request(entity, user), state, on_progress)
            state["ok"] += 1
            fails = 0
        except Exception as exc:
            name = type(exc).__name__
            if name in _FATAL_ERRORS:
                state["error"] = _fatal_message(exc)
                break
            reason = _ADD_REASONS.get(name)
            if reason == "allaqachon a'zo":
                state["skipped"] += 1
            else:
                state["failed"] += 1
                state["reasons"][reason or name] += 1
            if reason:
                fails = 0  # bu foydalanuvchining o'z sababi — umumiy muammo emas
            else:
                fails += 1
                logger.info("👥 Guruhga qo'shilmadi: user=%s %s: %s", getattr(user, "id", "?"), name, exc)
                if fails >= MAX_CONSECUTIVE_FAILS:
                    state["error"] = f"Ketma-ket {fails} ta xato: {name}. Jarayon to'xtatildi."
                    break
        await on_progress()
        await asyncio.sleep(ADD_DELAY_SEC)


async def _run_job(bot, chat_id, message_id, job: dict, group_chat_id: int):
    """Fon vazifasi: ishni bajaradi, jarayonni xabarda ko'rsatadi, oxirida hisobot beradi."""
    global _job
    from handlers import developer as dev
    kind, title = job["kind"], job["title"]
    state = job["state"]
    last_edit = 0.0

    async def edit(text, markup):
        try:
            await dev._safe_edit_bot(bot, chat_id, message_id, text, reply_markup=markup, parse_mode="HTML")
        except Exception as exc:
            logger.debug("👥 Jarayon xabarini yangilab bo'lmadi: %s", exc)

    async def on_progress(force=False):
        nonlocal last_edit
        now = time.monotonic()
        if force or now - last_edit >= PROGRESS_EVERY_SEC:
            last_edit = now
            await edit(_progress_text(kind, title, state), _stop_keyboard())

    try:
        client = await ads._account_client()
        entity = await ads._resolve_entity(client, group_chat_id)
        my_id = ads._session_user_id
        if kind == "collect":
            await collect_contacts(client, entity, state, on_progress, my_id)
        else:
            contacts = await _get_contacts(client)
            state["total"] = len(contacts)
            await add_contacts_to_group(client, entity, contacts, state, on_progress, my_id)
    except Exception as exc:
        logger.warning("👥 Guruh amali xato: %s: %s", type(exc).__name__, exc, exc_info=True)
        state["error"] = _fatal_message(exc)
    finally:
        _job = None
    await edit(_final_text(kind, title, state), _back("dev:grp"))


async def _group_info(chat_id: int, kind: str):
    """Tasdiqlash ekrani uchun son: guruh a'zolari yoki akkaunt kontaktlari soni (bilinmasa None)."""
    try:
        client = await ads._account_client()
        if kind == "collect":
            entity = await ads._resolve_entity(client, chat_id)
            participants = await client.get_participants(entity, limit=0)
            return getattr(participants, "total", None)
        return len(await _get_contacts(client))
    except Exception as exc:
        logger.info("👥 Son aniqlanmadi: %s: %s", type(exc).__name__, exc)
        return None


# ============================================================
# /developer ga ulanish
# ============================================================

async def handle_dev_callback(action, parts, update, context, query, DEV_MENU, DEV_WAIT_TEXT):
    global _job
    from handlers import developer as dev
    edit = dev._safe_edit_query

    if action == "grp":
        context.user_data.pop("dev_action", None)
        await edit(query, menu_text(), reply_markup=menu_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action in ("grpcol", "grpadd"):
        if _job is not None:
            await edit(query, menu_text(), reply_markup=menu_keyboard(), parse_mode="HTML")
            return DEV_MENU
        kind = "collect" if action == "grpcol" else "add"
        context.user_data["dev_action"] = {"type": f"grp_{kind}"}
        await edit(query, _prompt_text(kind), reply_markup=_back(), parse_mode="HTML")
        return DEV_WAIT_TEXT

    if action == "grpstop":
        if _job is not None:
            _job["state"]["stop"] = True
            await edit(query, "🛑 To'xtatilmoqda... (joriy qadam tugagach hisobot chiqadi)",
                       reply_markup=_stop_keyboard(), parse_mode="HTML")
        else:
            await edit(query, menu_text(), reply_markup=menu_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action == "grpok":
        pending = context.user_data.pop("dev_action", None) or {}
        if pending.get("type") != "grp_confirm":
            await edit(query, "⚠️ Sessiya topilmadi. Qaytadan boshlang.\n\n" + menu_text(),
                       reply_markup=menu_keyboard(), parse_mode="HTML")
            return DEV_MENU
        if _job is not None:
            await edit(query, menu_text(), reply_markup=menu_keyboard(), parse_mode="HTML")
            return DEV_MENU
        job = {"kind": pending["kind"], "title": pending["title"], "state": _new_state(pending.get("count")
               if pending["kind"] == "add" else None)}
        _job = job
        chat_id, message_id = query.message.chat_id, query.message.message_id
        await edit(query, _progress_text(job["kind"], job["title"], job["state"]),
                   reply_markup=_stop_keyboard(), parse_mode="HTML")
        job["task"] = asyncio.create_task(
            _run_job(context.bot, chat_id, message_id, job, int(pending["chat_id"]))
        )
        return DEV_MENU

    return DEV_MENU


async def handle_dev_text(action, update, context, DEV_MENU, DEV_WAIT_TEXT):
    """Guruh ID sini qabul qilib, tasdiqlash ekranini ko'rsatadi."""
    from handlers import developer as dev
    kind_type = action.get("type")
    raw = (update.message.text or "").strip()

    if kind_type in ("grp_collect", "grp_add"):
        kind = "collect" if kind_type == "grp_collect" else "add"
        chat, error = await ads.check_group(raw)
        if error:
            await dev._edit_menu(
                context, f"⚠️ <code>{_esc(raw[:40])}</code>: {error}\n\n" + _prompt_text(kind), _back()
            )
            context.user_data["dev_action"] = action
            return DEV_WAIT_TEXT
        count = await _group_info(chat.id, kind)
        pending = {"type": "grp_confirm", "kind": kind, "chat_id": chat.id,
                   "title": chat.title, "count": count}
        context.user_data["dev_action"] = pending
        await dev._edit_menu(context, _confirm_text(pending), _confirm_keyboard())
        return DEV_MENU

    # grp_confirm holatida matn yozilsa — e'tiborsiz, tugmalar kutiladi
    context.user_data["dev_action"] = action
    return DEV_MENU
