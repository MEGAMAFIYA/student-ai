"""👥 /developer > Guruh — AKKAUNT (TG_SESSION) nomidan uch amal:

📥 Kontakt yig'ish — ID berilgan guruhning barcha a'zolarini akkaunt kontaktlariga
                      (o'z ismi bilan) saqlaydi.
📤 Kontakt qo'shish — akkauntdagi barcha kontaktlarni ID berilgan guruhga qo'shadi.
🔁 Kontakt ko'chirish — 1-guruh a'zolarini 2-guruhga qo'shadi (ikkala ID so'raladi).

Uchala amal ham TO'XTAB QOLSA (cheklov, xato, qayta ishga tushish, "To'xtatish") holati
storage'da saqlanadi (qaysi odamlar bajarilgani); "▶️ Davom etish" qolgan joydan davom ettiradi.
Qo'shish/ko'chirishda guruhda paydo bo'ladigan "X ... ni qo'shdi" xizmat xabarlari o'chiriladi.

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

ACTIONS = {"grp", "grpcol", "grpadd", "grpmov", "grpok", "grpstop", "grpset", "grpbatch", "grpdelay",
           "grpres", "grpdrop"}
KINDS = ("collect", "add", "transfer")
CHECKPOINT_EVERY_SEC = 3.0     # holatni saqlash oralig'i (davom ettirish uchun)

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


def _saved_jobs() -> list:
    jobs = storage.get_group_jobs()
    return [(k, jobs[k]) for k in KINDS if k in jobs]


def _processed(rec: dict) -> int:
    return int(rec.get("ok", 0)) + int(rec.get("skipped", 0)) + int(rec.get("failed", 0))


def menu_text() -> str:
    lines = [
        "👥 <b>Guruh</b>\n",
        "📥 <b>Kontakt yig'ish</b> — guruh a'zolarini akkaunt kontaktlariga o'z ismi bilan saqlaydi.",
        "📤 <b>Kontakt qo'shish</b> — akkauntdagi barcha kontaktlarni guruhga qo'shadi.",
        "🔁 <b>Kontakt ko'chirish</b> — bir guruh a'zolarini boshqa guruhga qo'shadi.\n",
        "<i>Hammasi akkaunt (TG_SESSION) nomidan bajariladi; akkaunt guruhlarda a'zo bo'lishi kerak. "
        "To'xtab qolgan ish saqlanadi va «▶️ Davom etish» bilan qolgan joydan davom etadi.</i>",
    ]
    if not telegram_mtproto.is_configured():
        lines.append("\n⚠️ <i>Akkaunt ulanmagan: TG_API_ID, TG_API_HASH va TG_SESSION sozlang.</i>")
    if _job is not None:
        lines.append(f"\n⏳ Hozir ish ketmoqda: {_kind_label(_job['kind'])} — <b>{_esc(_job['title'])}</b>")
    else:
        for kind, rec in _saved_jobs():
            total = f"/{rec['total']}" if rec.get("total") else ""
            why = f" — {_esc(rec['error'])}" if rec.get("error") else ""
            lines.append(
                f"\n💾 Saqlangan: {_kind_label(kind)} — <b>{_esc(rec.get('title', ''))}</b> "
                f"({_processed(rec)}{total}){why}"
            )
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
        rows.append([InlineKeyboardButton("🔁 Kontakt ko'chirish", callback_data="dev:grpmov")])
        for kind, rec in _saved_jobs():
            total = f"/{rec['total']}" if rec.get("total") else ""
            label = f"▶️ Davom etish: {_kind_short(kind)} ({_processed(rec)}{total})"
            rows.append([
                InlineKeyboardButton(label[:50], callback_data=f"dev:grpres:{kind}"),
                InlineKeyboardButton("🗑", callback_data=f"dev:grpdrop:{kind}"),
            ])
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
    return {"collect": "📥 Kontakt yig'ish", "transfer": "🔁 Kontakt ko'chirish"}.get(kind, "📤 Kontakt qo'shish")


def _kind_short(kind: str) -> str:
    return {"collect": "yig'ish", "transfer": "ko'chirish"}.get(kind, "qo'shish")


def _move_src_prompt() -> str:
    return (
        f"{_kind_label('transfer')} — 1/2\n\n"
        "Kontaktlari <b>olinadigan</b> guruhning ID sini yuboring.\n"
        "Masalan: <code>-1001234567890</code> yoki <code>@guruh_username</code>"
    )


def _move_dst_prompt(src_title: str) -> str:
    return (
        f"{_kind_label('transfer')} — 2/2\n\n"
        f"✅ Olinadigan guruh: <b>{_esc(src_title)}</b>\n\n"
        "Endi ular <b>qo'shiladigan</b> guruhning ID sini yuboring."
    )


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
    elif pending["kind"] == "transfer":
        size = f" (taxminan {count} a'zo)" if count else ""
        body = (f"<b>{title}</b>{size} guruhining a'zolari <b>{_esc(pending.get('target_title') or '')}</b> "
                f"guruhiga qo'shiladi (bot, o'chirilgan akkaunt va allaqachon a'zolar o'tkazib yuboriladi). "
                f"Supergruppada {_batch_size()} tadan, paketlar orasida ~{_add_delay():g} soniya pauza.")
    else:
        size = f" ({count} ta kontakt)" if count else ""
        if _batch_size() > 1:
            note = (f"Supergruppada {_batch_size()} tadan paket qilib qo'shiladi "
                    f"(paketlar orasida ~{_add_delay():g} soniya pauza).")
        else:
            note = f"Telegram cheklovi uchun har biri orasida ~{_add_delay():g} soniya pauza bo'ladi."
        body = f"Akkauntdagi barcha kontaktlar{size} <b>{title}</b> guruhiga qo'shiladi. {note}"
    extra = ""
    if pending["kind"] in ("add", "transfer"):
        extra += "\n🧹 Guruhda chiqadigan «... qo'shdi» xabarlari o'chiriladi (akkauntda o'chirish huquqi bo'lsa)."
    extra += "\n💾 Jarayon to'xtasa saqlanadi — «▶️ Davom etish» bilan qolgan joydan davom etadi."
    if storage.get_group_job(pending["kind"]):
        extra += "\n⚠️ Saqlangan to'xtagan ish bor — yangisi uni almashtiradi."
    return f"{_kind_label(pending['kind'])}\n\n{body}\n{extra}\n\nBoshlaymizmi?"


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Boshlash", callback_data="dev:grpok")],
        [InlineKeyboardButton("⬅️ Bekor qilish", callback_data="dev:grp")],
    ])


def _final_keyboard(kind: str, resumable: bool) -> InlineKeyboardMarkup:
    rows = []
    if resumable:
        rows.append([InlineKeyboardButton("▶️ Davom etish", callback_data=f"dev:grpres:{kind}")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:grp")])
    return InlineKeyboardMarkup(rows)


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
    if kind in ("add", "transfer"):
        if state.get("svc_deleted"):
            lines.append(f"🧹 «Qo'shdi» xabarlari o'chirildi: {state['svc_deleted']}")
        if state.get("svc_failed"):
            lines.append(f"⚠️ {state['svc_failed']} ta «qo'shdi» xabarini o'chirib bo'lmadi "
                         "(akkauntga guruhda xabarlarni o'chirish huquqi bering).")
    if state.get("unconfirmed") and kind in ("add", "transfer"):
        lines.append("⚠️ <i>Telegram \"qo'shildi\" deb javob berdi, lekin qo'shilganini tasdiqlamadi — "
                     "yuqoridagi a'zolar sonini tekshiring.</i>")
    if state.get("error"):
        lines.append(f"\n❌ {_esc(state['error'])}")
    if state.get("spam_report"):
        lines.append(f"\n🛡 <b>Akkaunt holati (@SpamBot):</b>\n<i>{_esc(state['spam_report'])}</i>")
    if state.get("error") or state["stop"]:
        lines.append("\n💾 <i>Jarayon saqlandi. Cheklov tugagach «▶️ Davom etish» ni bosing — qolgan joydan davom etadi.</i>")
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
            "members_before": None, "members_after": None, "spam_report": None, "unconfirmed": 0,
            "done": set(),            # bajarilgan foydalanuvchi ID lari (davom ettirishda o'tkaziladi)
            "svc_deleted": 0, "svc_failed": 0, "svc_error": None}


def _record(job: dict, status: str) -> dict:
    """Ish holatini saqlash uchun (JSON) yozuv."""
    st = job["state"]
    return {
        "kind": job["kind"], "title": job["title"], "chat_id": job["group_chat_id"],
        "target_id": job.get("target_id"), "target_title": job.get("target_title"),
        "done": sorted(st["done"]), "ok": st["ok"], "skipped": st["skipped"], "failed": st["failed"],
        "reasons": dict(st["reasons"]), "total": st.get("total"), "error": st.get("error"),
        "status": status, "created_ts": job.get("created_ts"), "updated_ts": time.time(),
    }


def _state_from_record(rec: dict) -> dict:
    st = _new_state(rec.get("total"))
    st.update(ok=int(rec.get("ok", 0)), skipped=int(rec.get("skipped", 0)), failed=int(rec.get("failed", 0)))
    st["reasons"] = Counter(rec.get("reasons") or {})
    st["done"] = {int(i) for i in (rec.get("done") or [])}
    return st


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
    """Guruh a'zolarini kontaktlarga saqlaydi. state["done"] dagilar (oldingi urinishda
    bajarilgan) jimgina o'tkaziladi — shu tufayli ish qolgan joydan davom etadi."""
    fails = 0
    done = state["done"]
    async for user in client.iter_participants(entity):
        if state["stop"]:
            break
        if user.id in done:
            continue
        if _is_skippable(user, my_id) or getattr(user, "contact", False):
            state["skipped"] += 1
            done.add(user.id)
            await on_progress()
            continue
        try:
            await _call(client, _add_contact_request(user), state, on_progress)
            state["ok"] += 1
            done.add(user.id)
            fails = 0
        except Exception as exc:
            name = type(exc).__name__
            if name in _FATAL_ERRORS:
                state["error"] = _fatal_message(exc)
                break  # bu odam bajarilmagan — davom ettirilganda qayta uriniladi
            state["failed"] += 1
            state["reasons"][name] += 1
            done.add(user.id)
            fails += 1
            logger.info("👥 Kontakt saqlanmadi: user=%s %s: %s", getattr(user, "id", "?"), name, exc)
            if fails >= MAX_CONSECUTIVE_FAILS:
                state["error"] = f"Ketma-ket {fails} ta xato: {name}. Jarayon to'xtatildi."
                break
        await on_progress()
        await asyncio.sleep(COLLECT_DELAY_SEC)


def _update_items(result) -> list:
    """Telegram javobidagi update'lar ro'yxati (InvitedUsers.updates.updates yoki Updates.updates)."""
    inner = getattr(result, "updates", None)
    if isinstance(inner, (list, tuple)):
        return list(inner)
    return list(getattr(inner, "updates", None) or [])


def _added_ids(result):
    """Telegram javobidan HAQIQATAN qo'shilgan foydalanuvchilar ID lari (service xabarlardan).
    Aniqlab bo'lmasa — bo'sh to'plam (bunda a'zolar soni oxirida tekshiriladi)."""
    ids = set()
    for upd in _update_items(result):
        action = getattr(getattr(upd, "message", None), "action", None)
        for uid in (getattr(action, "users", None) or []):
            try:
                ids.add(int(uid))
            except (TypeError, ValueError):
                pass
    return ids


def _service_message_ids(result) -> list:
    """Qo'shish natijasida guruhda paydo bo'lgan «X ... ni qo'shdi» xizmat xabarlari ID lari."""
    ids = []
    for upd in _update_items(result):
        msg = getattr(upd, "message", None)
        if getattr(getattr(msg, "action", None), "users", None) and getattr(msg, "id", None) is not None:
            ids.append(int(msg.id))
    return ids


async def _delete_ids(client, entity, ids, state) -> None:
    """Xizmat xabarlarini o'chiradi. Huquq yo'q bo'lsa — bir marta xato qayd etiladi va qayta urinilmaydi."""
    ids = sorted(set(ids))
    if not ids:
        return
    if state.get("svc_error"):
        state["svc_failed"] += len(ids)
        return
    try:
        await client.delete_messages(entity, ids)
        state["svc_deleted"] += len(ids)
    except Exception as exc:
        state["svc_error"] = type(exc).__name__
        state["svc_failed"] += len(ids)
        logger.info("👥 «Qo'shdi» xabarlarini o'chirib bo'lmadi: %s: %s", type(exc).__name__, exc)


async def _cleanup_service(client, entity, result, state) -> None:
    await _delete_ids(client, entity, _service_message_ids(result), state)


async def _sweep_service_messages(client, entity, state, my_id) -> None:
    """Oxirgi tekshiruv: akkaunt qo'shgan odamlar haqidagi qolib ketgan xizmat xabarlarini topib o'chiradi."""
    if my_id is None or state.get("svc_error") or state["ok"] <= 0:
        return
    ids = []
    try:
        async for msg in client.iter_messages(entity, limit=min(500, 50 + 4 * state["ok"])):
            action = getattr(msg, "action", None)
            if type(action).__name__ == "MessageActionChatAddUser" and getattr(msg, "sender_id", None) == my_id:
                ids.append(msg.id)
    except Exception as exc:
        logger.info("👥 Xizmat xabarlarini qidirib bo'lmadi: %s: %s", type(exc).__name__, exc)
        return
    await _delete_ids(client, entity, ids, state)


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
        result = await _call(client, _invite_request(entity, user), state, on_progress)
        state["ok"] += 1
        state["done"].add(user.id)
        await _cleanup_service(client, entity, result, state)
        return 0, False
    except Exception as exc:
        name = type(exc).__name__
        if name in _FATAL_ERRORS:
            state["error"] = _fatal_message(exc)
            return fails, True  # bu odam bajarilmagan — davom ettirilganda qayta uriniladi
        reason = _ADD_REASONS.get(name)
        if reason == "allaqachon a'zo":
            state["skipped"] += 1
        else:
            state["failed"] += 1
            state["reasons"][reason or name] += 1
        state["done"].add(user.id)
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
    """Odamlarni guruhga qo'shadi (guruhdagilar va oldin bajarilganlar o'tkazib yuboriladi).

    Supergruppa/kanalda sozlamadagi (standart 2) tadan paket qilib BIR so'rovda qo'shadi;
    oddiy guruhda Telegram faqat bittalab qo'shishga ruxsat beradi.
    """
    existing = set()
    try:
        async for member in client.iter_participants(entity):
            existing.add(member.id)
    except Exception as exc:  # a'zolar ro'yxati yopiq bo'lishi mumkin — shunchaki o'tkazib yuboramiz
        logger.info("👥 Guruh a'zolarini o'qib bo'lmadi: %s: %s", type(exc).__name__, exc)

    done = state["done"]
    todo = []
    for user in contacts:
        if user.id in done:
            continue  # oldingi urinishda bajarilgan
        if _is_skippable(user, my_id) or user.id in existing:
            state["skipped"] += 1
            done.add(user.id)
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
                break  # paket bajarilmagan — davom ettirilganda qayta uriniladi
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
        done.update(u.id for u in batch)
        await _cleanup_service(client, entity, result, state)
        await on_progress()
        if i + size < len(todo):
            await asyncio.sleep(_add_delay())


async def _add_phase(client, entity, people, state, on_progress, my_id) -> None:
    """Qo'shish ishining umumiy qismi (📤 va 🔁 uchun): hisoblash, qo'shish, tekshiruvlar."""
    state["members_before"] = await _member_count(client, entity)
    await add_contacts_to_group(client, entity, people, state, on_progress, my_id)
    state["members_after"] = await _member_count(client, entity)
    before, after = state["members_before"], state["members_after"]
    if state["ok"] > 0 and before is not None and after is not None and after <= before:
        state["unconfirmed"] = state["ok"]
    await _sweep_service_messages(client, entity, state, my_id)
    if _needs_spam_check(state):
        state["spam_report"] = await _spambot_report(client)


async def _run_job(bot, chat_id, message_id, job: dict):
    """Fon vazifasi: ishni bajaradi, holatini saqlab boradi, oxirida hisobot beradi."""
    global _job
    from handlers import developer as dev
    kind, title = job["kind"], job["title"]
    state = job["state"]
    last_edit = 0.0
    last_ck = 0.0

    def checkpoint(status="running"):
        try:
            storage.save_group_job(kind, _record(job, status))
        except Exception as exc:
            logger.warning("👥 Ish holatini saqlab bo'lmadi: %s: %s", type(exc).__name__, exc)

    async def edit(text, markup):
        try:
            await dev._safe_edit_bot(bot, chat_id, message_id, text, reply_markup=markup, parse_mode="HTML")
        except Exception as exc:
            logger.debug("👥 Jarayon xabarini yangilab bo'lmadi: %s", exc)

    async def on_progress(force=False):
        nonlocal last_edit, last_ck
        now = time.monotonic()
        if force or now - last_ck >= CHECKPOINT_EVERY_SEC:
            last_ck = now
            checkpoint()
        if force or now - last_edit >= PROGRESS_EVERY_SEC:
            last_edit = now
            await edit(_progress_text(kind, title, state), _stop_keyboard())

    checkpoint()  # bot shu zahoti qayta ishga tushsa ham, ish "Davom etish" da ko'rinadi
    try:
        client = await ads._account_client()
        my_id = ads._session_user_id
        if kind == "collect":
            entity = await ads._resolve_entity(client, job["group_chat_id"])
            await collect_contacts(client, entity, state, on_progress, my_id)
        elif kind == "transfer":
            src = await ads._resolve_entity(client, job["group_chat_id"])
            dst = await ads._resolve_entity(client, job["target_id"])
            people = []
            async for user in client.iter_participants(src):
                people.append(user)
            state["total"] = len(people)
            await _add_phase(client, dst, people, state, on_progress, my_id)
        else:
            entity = await ads._resolve_entity(client, job["group_chat_id"])
            contacts = await _get_contacts(client)
            state["total"] = len(contacts)
            await _add_phase(client, entity, contacts, state, on_progress, my_id)
    except Exception as exc:
        logger.warning("👥 Guruh amali xato: %s: %s", type(exc).__name__, exc, exc_info=True)
        state["error"] = _fatal_message(exc)
    finally:
        _job = None
    finished = not state.get("error") and not state["stop"]
    if finished:
        storage.remove_group_job(kind)
    else:
        checkpoint("paused")
    await edit(_final_text(kind, title, state), _final_keyboard(kind, not finished))


async def _group_info(chat_id: int, kind: str):
    """Tasdiqlash ekrani uchun son: guruh a'zolari yoki akkaunt kontaktlari soni (bilinmasa None)."""
    try:
        client = await ads._account_client()
        if kind in ("collect", "transfer"):
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

async def _launch(context, query, job: dict, edit) -> None:
    """Ishni fon vazifasi sifatida ishga tushiradi (yangi ish ham, davom ettirish ham)."""
    global _job
    _job = job
    chat_id, message_id = query.message.chat_id, query.message.message_id
    await edit(query, _progress_text(job["kind"], job["title"], job["state"]),
               reply_markup=_stop_keyboard(), parse_mode="HTML")
    job["task"] = asyncio.create_task(_run_job(context.bot, chat_id, message_id, job))


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

    if action == "grpmov":
        if _job is not None:
            await edit(query, menu_text(), reply_markup=menu_keyboard(), parse_mode="HTML")
            return DEV_MENU
        context.user_data["dev_action"] = {"type": "grp_move_src"}
        await edit(query, _move_src_prompt(), reply_markup=_back(), parse_mode="HTML")
        return DEV_WAIT_TEXT

    if action in ("grpres", "grpdrop"):
        context.user_data.pop("dev_action", None)
        kind = parts[2] if len(parts) > 2 else ""
        rec = storage.get_group_job(kind) if kind in KINDS else None
        if _job is not None or not rec:
            note = "" if rec else "⚠️ Saqlangan ish topilmadi.\n\n"
            await edit(query, note + menu_text(), reply_markup=menu_keyboard(), parse_mode="HTML")
            return DEV_MENU
        if action == "grpdrop":
            storage.remove_group_job(kind)
            await edit(query, "🗑 Saqlangan ish o'chirildi.\n\n" + menu_text(),
                       reply_markup=menu_keyboard(), parse_mode="HTML")
            return DEV_MENU
        job = {
            "kind": kind, "title": rec.get("title", ""), "group_chat_id": int(rec["chat_id"]),
            "target_id": rec.get("target_id"), "target_title": rec.get("target_title"),
            "state": _state_from_record(rec), "created_ts": rec.get("created_ts") or time.time(),
        }
        await _launch(context, query, job, edit)
        return DEV_MENU

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
        kind = pending["kind"]
        title = (f"{pending['title']} → {pending.get('target_title') or ''}" if kind == "transfer"
                 else pending["title"])
        job = {
            "kind": kind, "title": title, "group_chat_id": int(pending["chat_id"]),
            "target_id": pending.get("target_id"), "target_title": pending.get("target_title"),
            "state": _new_state(pending.get("count") if kind == "add" else None),
            "created_ts": time.time(),
        }
        await _launch(context, query, job, edit)
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

    if kind_type == "grp_move_src":
        chat, error = await ads.check_group(raw)
        if error:
            await dev._edit_menu(
                context, f"⚠️ <code>{_esc(raw[:40])}</code>: {error}\n\n" + _move_src_prompt(), _back()
            )
            context.user_data["dev_action"] = action
            return DEV_WAIT_TEXT
        context.user_data["dev_action"] = {"type": "grp_move_dst", "src_id": chat.id, "src_title": chat.title}
        await dev._edit_menu(context, _move_dst_prompt(chat.title), _back())
        return DEV_WAIT_TEXT

    if kind_type == "grp_move_dst":
        chat, error = await ads.check_group(raw)
        if not error and chat.id == action.get("src_id"):
            error = "Bu olinadigan guruhning o'zi. Boshqa guruh ID sini yuboring."
        if error:
            await dev._edit_menu(
                context,
                f"⚠️ <code>{_esc(raw[:40])}</code>: {error}\n\n" + _move_dst_prompt(action.get("src_title", "")),
                _back(),
            )
            context.user_data["dev_action"] = action
            return DEV_WAIT_TEXT
        count = await _group_info(action["src_id"], "transfer")
        pending = {"type": "grp_confirm", "kind": "transfer", "chat_id": action["src_id"],
                   "title": action.get("src_title", ""), "target_id": chat.id,
                   "target_title": chat.title, "count": count}
        context.user_data["dev_action"] = pending
        await dev._edit_menu(context, _confirm_text(pending), _confirm_keyboard())
        return DEV_MENU

    # grp_confirm holatida matn yozilsa — e'tiborsiz, tugmalar kutiladi
    context.user_data["dev_action"] = action
    return DEV_MENU
