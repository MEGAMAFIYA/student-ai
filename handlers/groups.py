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

import storage
import telegram_mtproto
from handlers import ads

logger = logging.getLogger(__name__)

ACTIONS = {"grp", "grpcol", "grpadd", "grpok", "grpstop", "grpset", "grpbatch", "grpdelay"}

COLLECT_DELAY_SEC = 1.0        # kontakt saqlashlar orasidagi pauza
ADD_DELAY_SEC = 4.0            # qo'shish so'rovlari orasidagi pauza (Telegram tez qo'shishni jazolaydi)
ADD_BATCH_SIZE = 2             # supergruppada BITTA so'rovda nechta odam qo'shiladi (standart: 2).
                               # /developer > Guruh > ⚙️ Sozlamalar orqali o'zgartiriladi (Telegram max = 100).
BATCH_CHOICES = (1, 2, 3, 5, 10, 20, 50, 100)
DELAY_CHOICES = (2, 4, 8, 15, 30)
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

def _batch_size() -> int:
    """Sozlamalardagi paket hajmi (saqlanmagan bo'lsa — ADD_BATCH_SIZE)."""
    try:
        return max(1, min(100, int(storage.get_group_setting("add_batch", ADD_BATCH_SIZE))))
    except (TypeError, ValueError):
        return max(1, int(ADD_BATCH_SIZE))


def _add_delay() -> float:
    """Sozlamalardagi so'rovlar orasidagi pauza (soniya)."""
    try:
        return max(0.0, float(storage.get_group_setting("add_delay", ADD_DELAY_SEC)))
    except (TypeError, ValueError):
        return float(ADD_DELAY_SEC)


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
    if _job is None:
        rows.append([InlineKeyboardButton("⚙️ Sozlamalar", callback_data="dev:grpset")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")])
    return InlineKeyboardMarkup(rows)


def settings_text() -> str:
    return (
        "⚙️ <b>Guruh sozlamalari</b> (📤 Kontakt qo'shish)\n\n"
        f"👥 Bir so'rovda qo'shiladigan odam: <b>{_batch_size()}</b>\n"
        f"⏱ So'rovlar orasidagi pauza: <b>{_add_delay():g}</b> soniya\n\n"
        "<i>Avval 1–2 tadan sinab ko'ring: hammasi qo'shilsa, asta-sekin oshiring. "
        "Telegram \"kuting\" desa yoki akkaunt spam cheklovida bo'lsa, kamaytiring. "
        "Katta paket (50–100) akkauntni tez cheklatadi. Faqat supergruppada ishlaydi; "
        "oddiy guruhda doim bittadan.</i>"
    )


def settings_keyboard() -> InlineKeyboardMarkup:
    cur_b, cur_d = _batch_size(), _add_delay()
    def mark(n, cur):
        return f"✅ {n}" if float(n) == float(cur) else str(n)
    b = [InlineKeyboardButton(mark(n, cur_b), callback_data=f"dev:grpbatch:{n}") for n in BATCH_CHOICES]
    d = [InlineKeyboardButton(mark(n, cur_d), callback_data=f"dev:grpdelay:{n}") for n in DELAY_CHOICES]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Bir so'rovda nechta odam:", callback_data="dev:grpset")],
        b[:4], b[4:],
        [InlineKeyboardButton("⏱ Pauza (soniya):", callback_data="dev:grpset")],
        d,
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:grp")],
    ])


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
        if _batch_size() > 1:
            note = (f"Supergruppada {_batch_size()} tadan paket qilib qo'shiladi "
                    f"(paketlar orasida ~{_add_delay():g} soniya pauza).")
        else:
            note = f"Telegram cheklovi uchun har biri orasida ~{_add_delay():g} soniya pauza bo'ladi."
        body = f"Akkauntdagi barcha kontaktlar{size} <b>{title}</b> guruhiga qo'shiladi. {note}"
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
    if state.get("members_before") is not None and state.get("members_after") is not None:
        lines.append(f"\n👥 Guruh a'zolari: {state['members_before']} → {state['members_after']}")
    if state.get("unconfirmed") and kind == "add":
        lines.append("⚠️ <i>Telegram \"qo'shildi\" deb javob berdi, lekin qo'shilganini tasdiqlamadi — "
                     "yuqoridagi a'zolar sonini tekshiring.</i>")
    if state.get("error"):
        lines.append(f"\n❌ {_esc(state['error'])}")
    if state.get("spam_report"):
        lines.append(f"\n🛡 <b>Akkaunt holati (@SpamBot):</b>\n<i>{_esc(state['spam_report'])}</i>")
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


def _is_channel(entity) -> bool:
    return type(entity).__name__ == "Channel"


def _invite_many_request(entity, users):
    """Supergruppa/kanalga bir so'rovda ko'p odam qo'shish (oddiy guruhda bunday imkon yo'q)."""
    from telethon.tl.functions.channels import InviteToChannelRequest
    return InviteToChannelRequest(entity, list(users))


def _missing_ids(result) -> set:
    """Paketdan qo'shib bo'lmagan (maxfiylik va h.k.) foydalanuvchilar ID lari."""
    return {getattr(m, "user_id", None) for m in (getattr(result, "missing_invitees", None) or [])}


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
            "stop": False, "error": None, "waiting": 0,
            "members_before": None, "members_after": None, "spam_report": None, "unconfirmed": 0}


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


def _added_ids(result):
    """Telegram javobidan HAQIQATAN qo'shilgan foydalanuvchilar ID lari (service xabarlardan).
    Aniqlab bo'lmasa — bo'sh to'plam (bunda a'zolar soni oxirida tekshiriladi)."""
    ids = set()
    updates = getattr(result, "updates", result)
    for upd in (getattr(updates, "updates", None) or []):
        action = getattr(getattr(upd, "message", None), "action", None)
        for uid in (getattr(action, "users", None) or []):
            try:
                ids.add(int(uid))
            except (TypeError, ValueError):
                pass
    return ids


async def _member_count(client, entity):
    try:
        participants = await client.get_participants(entity, limit=0)
        return getattr(participants, "total", None)
    except Exception as exc:
        logger.info("👥 A'zolar soni aniqlanmadi: %s: %s", type(exc).__name__, exc)
        return None


async def _spambot_report(client) -> str | None:
    """Akkauntga spam-cheklov qo'yilganini @SpamBot dan so'raydi va uning javobini qaytaradi."""
    try:
        async with client.conversation("SpamBot", timeout=20) as conv:
            await conv.send_message("/start")
            reply = await conv.get_response()
        text = (getattr(reply, "raw_text", None) or getattr(reply, "message", "") or "").strip()
        return text[:700] or None
    except Exception as exc:
        logger.info("👥 @SpamBot dan holat olinmadi: %s: %s", type(exc).__name__, exc)
        return None


def _needs_spam_check(state: dict) -> bool:
    """Qo'shish natijasi shubhali bo'lsa (PeerFlood, hech kim qo'shilmadi, 'qo'shildi' lekin son o'zgarmadi)."""
    err = state.get("error") or ""
    if "PeerFlood" in err or "spam" in err.lower() or "cheklov" in err.lower():
        return True
    if state["ok"] == 0 and state["failed"] > 0:
        return True
    before, after = state.get("members_before"), state.get("members_after")
    return bool(state["ok"] > 0 and before is not None and after is not None and after <= before)


async def _add_one(client, entity, user, state, on_progress, fails: int):
    """Bitta odamni qo'shadi. (fails, to'xtatish_kerakmi) qaytaradi."""
    try:
        await _call(client, _invite_request(entity, user), state, on_progress)
        state["ok"] += 1
        return 0, False
    except Exception as exc:
        name = type(exc).__name__
        if name in _FATAL_ERRORS:
            state["error"] = _fatal_message(exc)
            return fails, True
        reason = _ADD_REASONS.get(name)
        if reason == "allaqachon a'zo":
            state["skipped"] += 1
        else:
            state["failed"] += 1
            state["reasons"][reason or name] += 1
        if reason:
            return 0, False  # bu foydalanuvchining o'z sababi — umumiy muammo emas
        fails += 1
        logger.info("👥 Guruhga qo'shilmadi: user=%s %s: %s", getattr(user, "id", "?"), name, exc)
        if fails >= MAX_CONSECUTIVE_FAILS:
            state["error"] = f"Ketma-ket {fails} ta xato: {name}. Jarayon to'xtatildi."
            return fails, True
        return fails, False


async def _add_one_by_one(client, entity, users, state, on_progress) -> bool:
    """Bittalab qo'shadi (pauza bilan). To'xtatish kerak bo'lsa True qaytaradi."""
    fails = 0
    for user in users:
        if state["stop"]:
            return True
        fails, halt = await _add_one(client, entity, user, state, on_progress, fails)
        await on_progress()
        if halt:
            return True
        await asyncio.sleep(_add_delay())
    return False


async def add_contacts_to_group(client, entity, contacts, state, on_progress, my_id=None):
    """Akkaunt kontaktlarini guruhga qo'shadi (guruhdagilar o'tkazib yuboriladi).

    Supergruppa/kanalda sozlamadagi (standart 2) tadan paket qilib BIR so'rovda qo'shadi;
    oddiy guruhda Telegram faqat bittalab qo'shishga ruxsat beradi.
    """
    existing = set()
    try:
        async for member in client.iter_participants(entity):
            existing.add(member.id)
    except Exception as exc:  # a'zolar ro'yxati yopiq bo'lishi mumkin — shunchaki o'tkazib yuboramiz
        logger.info("👥 Guruh a'zolarini o'qib bo'lmadi: %s: %s", type(exc).__name__, exc)

    todo = []
    for user in contacts:
        if _is_skippable(user, my_id) or user.id in existing:
            state["skipped"] += 1
        else:
            todo.append(user)
    await on_progress()

    size = _batch_size()
    if not (_is_channel(entity) and size > 1):
        await _add_one_by_one(client, entity, todo, state, on_progress)
        return

    for i in range(0, len(todo), size):
        if state["stop"]:
            break
        batch = todo[i:i + size]
        try:
            result = await _call(client, _invite_many_request(entity, batch), state, on_progress)
        except Exception as exc:
            name = type(exc).__name__
            if name in _FATAL_ERRORS:
                state["error"] = _fatal_message(exc)
                break
            # Paket butunlay rad etildi (masalan, bitta odam tufayli) — shu paketni bittalab qo'shamiz
            logger.info("👥 Paket rad etildi (%s), bittalab urinamiz: %s", name, exc)
            if await _add_one_by_one(client, entity, batch, state, on_progress):
                break
            continue
        missing = _missing_ids(result)
        if missing:
            state["failed"] += len(missing)
            state["reasons"]["maxfiylik sozlamasi"] += len(missing)
        added = _added_ids(result) - missing
        if added:
            # Telegram o'zi qaysilar qo'shilganini aytdi — faqat shularni sanaymiz
            state["ok"] += len(added)
            rest = len(batch) - len(missing) - len(added)
            if rest > 0:
                state["failed"] += rest
                state["reasons"]["Telegram qo'shmadi"] += rest
        else:
            # Javobdan bilib bo'lmadi — vaqtincha "qo'shildi" deymiz, oxirida a'zolar sonini solishtiramiz
            state["ok"] += len(batch) - len(missing)
        await on_progress()
        if i + size < len(todo):
            await asyncio.sleep(_add_delay())


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
            state["members_before"] = await _member_count(client, entity)
            await add_contacts_to_group(client, entity, contacts, state, on_progress, my_id)
            state["members_after"] = await _member_count(client, entity)
            before, after = state["members_before"], state["members_after"]
            if state["ok"] > 0 and before is not None and after is not None and after <= before:
                state["unconfirmed"] = state["ok"]
            if _needs_spam_check(state):
                state["spam_report"] = await _spambot_report(client)
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

    if action == "grpset":
        context.user_data.pop("dev_action", None)
        await edit(query, settings_text(), reply_markup=settings_keyboard(), parse_mode="HTML")
        return DEV_MENU

    if action in ("grpbatch", "grpdelay"):
        try:
            value = int(parts[2])
        except (IndexError, ValueError):
            value = 0
        allowed = BATCH_CHOICES if action == "grpbatch" else DELAY_CHOICES
        if value in allowed:
            storage.set_group_setting("add_batch" if action == "grpbatch" else "add_delay", value)
        await edit(query, settings_text(), reply_markup=settings_keyboard(), parse_mode="HTML")
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
