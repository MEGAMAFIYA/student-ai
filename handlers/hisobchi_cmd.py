"""
🧮 /hisobchi — FAQAT adminlar (config.ADMIN_IDS), FAQAT shaxsiy chatda.

Ma'lumotlar hisobchi.py'dan o'qiladi (xotiradagi tayyor hisob — hech qanday
tarmoq/AI so'rovi yo'q), shuning uchun buyruq bir zumda javob beradi va botning
boshqa funksiyalariga ta'sir qilmaydi.

Ekranlar: asosiy hisobot • so'nggi ishlar • AI kalitlari • funksiyalar •
foydalanuvchilar • bitta foydalanuvchi kartasi (/hisobchi <id yoki @username>).
"""

import html
import logging
import time
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import config
import hisobchi

logger = logging.getLogger(__name__)

_MAX_TEXT = 3900
_PER_PAGE = 6
_USERS_PER_PAGE = 5

_EXTRA_LABELS = {"vid": "🎬 Video yuklash", "qoshiq": "🎵 Qo'shiq", "unknown": "❔ Noma'lum"}


# ---------------- yordamchilar ----------------

def _e(v) -> str:
    return html.escape(str(v), quote=False)


def _is_admin(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id in config.ADMIN_IDS)


def _sum(n: int) -> str:
    return f"{int(n):,}".replace(",", " ") + " so'm"


def _ts(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, hisobchi.TZ)
    if dt.strftime("%Y-%m-%d") == hisobchi.today_str():
        return dt.strftime("%H:%M")
    return dt.strftime("%d.%m %H:%M")


def _label(feature: str) -> str:
    try:
        import storage
        lab = storage._FUNCTION_LABELS_FOR_STATS.get(feature)
        if lab:
            return lab
    except Exception:
        pass
    if feature in _EXTRA_LABELS:
        return _EXTRA_LABELS[feature]
    try:
        import wallet
        for fid, f in wallet.list_features():
            if hisobchi.norm_feature(fid) == feature and f.get("name"):
                return f["name"]
    except Exception:
        pass
    return feature


def _user_name(uid: int) -> str:
    """Bosiladigan ism (tg://user) — admin bir bosishda foydalanuvchi profiliga o'ta oladi."""
    if not uid:
        return "❔ noma'lum foydalanuvchi"
    try:
        import storage
        p = storage.get_user_profile(uid)
    except Exception:
        p = {}
    name = " ".join(x for x in (p.get("first_name", ""), p.get("last_name", "")) if x).strip() or str(uid)
    uname = f" @{_e(p['username'])}" if p.get("username") else ""
    return f'<a href="tg://user?id={uid}">{_e(name)}</a>{uname} · <code>{uid}</code>'


def _ai_key_label(key: str, rec: dict | None = None) -> str:
    provider = (rec or {}).get("p") or key.split("#")[0].replace("user:", "")
    pl = config.PROVIDER_LABELS.get(provider, provider.capitalize())
    if key.startswith("user:"):
        return f"👤 Shaxsiy kalitlar · {pl}"
    if "#" in key:
        return f"{pl} #{key.split('#', 1)[1]}"
    return f"{pl} (asosiy kalit)"


def _quota_note(key: str, rec: dict) -> str:
    """Gemini kalitining limit kutish (cooldown) holati — bor bo'lsa ko'rsatiladi."""
    if rec.get("p") != "gemini" or not rec.get("m"):
        return ""
    try:
        import ai_clients
        left = int(ai_clients._gemini_quota_remaining(rec["m"]))
        if left > 0:
            return f"\n   ⏳ hozir limitda: ~{max(1, left // 60)} daq"
    except Exception:
        pass
    return ""


def _ai_line(ai: dict) -> str:
    return " · ".join(f"{_e(_ai_key_label(k))} ×{n}" for k, n in sorted(ai.items()))


def _trim(text: str) -> str:
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 1] + "…"


def _nav(*extra_rows) -> InlineKeyboardMarkup:
    rows = [list(r) for r in extra_rows]
    rows.append([InlineKeyboardButton("🏠 Asosiy", callback_data="hsb:main")])
    return InlineKeyboardMarkup(rows)


def _main_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardButton
    return InlineKeyboardMarkup([
        [b("🧾 So'nggi ishlar", callback_data="hsb:feed:0"), b("🤖 AI kalitlari", callback_data="hsb:ai")],
        [b("📦 Funksiyalar", callback_data="hsb:fn"), b("👥 Foydalanuvchilar", callback_data="hsb:usr:a:0")],
        [b("🔄 Yangilash", callback_data="hsb:main")],
    ])


# ---------------- ekranlar ----------------

def _render_main() -> tuple[str, InlineKeyboardMarkup]:
    s = hisobchi.summary()
    t, a = s["today"], s["total"]
    since = s["since"][:10] if s["since"] else "?"
    net = lambda d: d["spent"] - d["refund"]
    text = (
        f"🧮 <b>HISOBCHI</b> — {_e(s['day'])} (Toshkent vaqti)\n\n"
        f"<b>📅 Bugun</b>\n"
        f"✅ Tayyor ishlar: <b>{t['jobs']}</b>   ❌ Xato: <b>{t['fail']}</b>\n"
        f"🤖 AI chaqiruv: ✅ {t['ai_ok']}   ❌ {t['ai_fail']} (shundan limit: {t['quota']})\n"
        f"💰 Sarflangan: <b>{_sum(net(t))}</b>  (yechildi {_sum(t['spent'])}, qaytdi {_sum(t['refund'])})\n"
        f"➕ Balans to'ldirildi: {_sum(t['topup'])}\n"
        f"👥 Bugun faol: <b>{s['active_today']}</b> / jami kuzatilgan {s['users']}\n\n"
        f"<b>📈 Jami</b> ({_e(since)} dan)\n"
        f"✅ {a['jobs']}   ❌ {a['fail']}   🤖 ✅ {a['ai_ok']} / ❌ {a['ai_fail']}\n"
        f"💰 Sarflangan: {_sum(net(a))}   ➕ To'ldirilgan: {_sum(a['topup'])}\n\n"
        f"<i>Foydalanuvchi: /hisobchi &lt;id yoki @username&gt;</i>"
    )
    return _trim(text), _main_kb()


def _event_block(ev: dict, with_user: bool = True) -> str:
    icon = "✅" if ev.get("ok") else "❌"
    lines = [f"{icon} {_e(_label(ev.get('f', '')))} · {_ts(ev['t'])}"]
    if with_user:
        lines.append(f"👤 {_user_name(ev.get('u', 0))}")
    if ev.get("topic"):
        pg = f" ({ev['pg']} bet)" if ev.get("pg") else ""
        lines.append(f"📝 {_e(ev['topic'])}{pg}")
    if ev.get("ai"):
        extra = ""
        if ev.get("aif"):
            extra = f" · ⚠️ {ev['aif']} xato" + (f", {ev['lim']} limit" if ev.get("lim") else "")
        lines.append(f"🤖 {_ai_line(ev['ai'])}{extra}")
    elif ev.get("aif"):
        lines.append(f"🤖 ⚠️ {ev['aif']} xato" + (f", {ev['lim']} limit" if ev.get("lim") else ""))
    if ev.get("price") or ev.get("rf"):
        money = f"💰 {_sum(ev.get('price', 0))}"
        if ev.get("rf"):
            money += f" (↩️ qaytdi {_sum(ev['rf'])})"
        lines.append(money)
    if ev.get("why"):
        lines.append(f"❗ {_e(ev['why'])}")
    return "\n".join(lines)


def _render_feed(page: int, user_id: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
    page = max(0, page)
    evs, total = hisobchi.recent_events(_PER_PAGE, page * _PER_PAGE, user_id)
    if not evs and page:
        return _render_feed(0, user_id)
    head = "🧾 <b>So'nggi ishlar</b>" + (f" — {_user_name(user_id)}" if user_id else "")
    body = "\n\n".join(_event_block(e, with_user=user_id is None) for e in evs) or "Hozircha yozuv yo'q."
    pages = max(1, -(-total // _PER_PAGE))
    text = _trim(f"{head}  ({page + 1}/{pages})\n\n{body}")
    base = f"hsb:ufeed:{user_id}:" if user_id else "hsb:feed:"
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"{base}{page - 1}"))
    nav.append(InlineKeyboardButton("🔄", callback_data=f"{base}{page}"))
    if (page + 1) * _PER_PAGE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"{base}{page + 1}"))
    return text, _nav(nav)


def _render_ai() -> tuple[str, InlineKeyboardMarkup]:
    rows = hisobchi.ai_table()
    today = hisobchi.today_str()
    if not rows:
        return "🤖 <b>AI kalitlari</b>\n\nHozircha AI chaqiruvi qayd etilmagan.", _nav()
    parts = ["🤖 <b>AI kalitlari</b> (bugun | jami)\n"]
    for key, r in rows:
        td = r["days"].get(today, [0, 0])
        bad = []
        for k, name in (("quota", "limit"), ("paid", "pullik"), ("invalid", "yaroqsiz"), ("error", "xato")):
            if r.get(k):
                bad.append(f"{name} {r[k]}")
        last = _ts(r["last"]) if r.get("last") else "—"
        model = f" — <code>{_e(r['m'])}</code>" if r.get("m") else ""
        parts.append(
            f"<b>{_e(_ai_key_label(key, r))}</b>{model}\n"
            f"   bugun ✅ {td[0]} ❌ {td[1]} | jami ✅ {r['ok']} ❌ {r['fail']}"
            + (f" ({', '.join(bad)})" if bad else "")
            + f"\n   oxirgi ishlatilgan: {last}" + _quota_note(key, r)
        )
    return _trim("\n".join(parts)), _nav([InlineKeyboardButton("🔄 Yangilash", callback_data="hsb:ai")])


def _render_fn() -> tuple[str, InlineKeyboardMarkup]:
    rows = hisobchi.fn_table()
    today = hisobchi.today_str()
    if not rows:
        return "📦 <b>Funksiyalar</b>\n\nHozircha ish qayd etilmagan.", _nav()
    parts = ["📦 <b>Funksiyalar</b> (bugun | jami)\n"]
    for feat, r in rows:
        parts.append(
            f"{_e(_label(feat))}\n"
            f"   bugun {r['days'].get(today, 0)} | jami ✅ {r['n']} ❌ {r['fail']}"
            f" · 🤖 {r['ai']} chaqiruv · 💰 {_sum(r['price'])}"
        )
    return _trim("\n".join(parts)), _nav([InlineKeyboardButton("🔄 Yangilash", callback_data="hsb:fn")])


def _render_users(sort: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    page = max(0, page)
    items, total = hisobchi.top_users(sort, _USERS_PER_PAGE, page * _USERS_PER_PAGE)
    if not items and page:
        return _render_users(sort, 0)
    title = "so'nggi faollik" if sort == "a" else "sarflangan summa"
    parts = [f"👥 <b>Foydalanuvchilar</b> — {title} bo'yicha ({total} ta)\n"]
    kb = []
    for uid, u in items:
        parts.append(
            f"{_user_name(uid)}\n"
            f"   ✅ {u['n']} ❌ {u['fail']} · 🤖 {u['ai']} · 💰 {_sum(u['spent'])} · oxirgi {_ts(u['last'])}"
        )
        kb.append([InlineKeyboardButton(f"👤 {uid}", callback_data=f"hsb:u:{uid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"hsb:usr:{sort}:{page - 1}"))
    other = "s" if sort == "a" else "a"
    nav.append(InlineKeyboardButton("💰 Summa bo'yicha" if sort == "a" else "🕒 Faollik bo'yicha",
                                    callback_data=f"hsb:usr:{other}:0"))
    if (page + 1) * _USERS_PER_PAGE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"hsb:usr:{sort}:{page + 1}"))
    return _trim("\n".join(parts)), _nav(*kb, nav)


def _render_user(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    u = hisobchi.user_stats(uid)
    try:
        import wallet
        balance = _sum(wallet.get_balance(uid))
    except Exception:
        balance = "—"
    if not u:
        text = f"👤 {_user_name(uid)}\n\nBu foydalanuvchi bo'yicha hisobchida yozuv yo'q.\n💳 Balans: {balance}"
        return _trim(text), _nav()
    fns = sorted(u["fn"].items(), key=lambda kv: kv[1], reverse=True)[:8]
    fn_txt = "\n".join(f"   {_e(_label(f))}: {n}" for f, n in fns) or "   —"
    evs, _ = hisobchi.recent_events(3, 0, uid)
    last = "\n\n".join(_event_block(e, with_user=False) for e in evs) or "—"
    text = (
        f"👤 {_user_name(uid)}\n\n"
        f"💳 Balans: <b>{balance}</b>\n"
        f"✅ Tayyor ishlar: {u['n']}   ❌ Xato: {u['fail']}\n"
        f"🤖 AI chaqiruvlar (muvaffaqiyatli): {u['ai']}\n"
        f"💰 Sarflagan (sof): {_sum(u['spent'])}   ➕ To'ldirgan: {_sum(u['topup'])}\n"
        f"🕒 Oxirgi faollik: {_ts(u['last'])}\n\n"
        f"<b>Funksiyalar</b>\n{fn_txt}\n\n<b>So'nggi ishlari</b>\n{last}"
    )
    return _trim(text), _nav([InlineKeyboardButton("🧾 Hammasi", callback_data=f"hsb:ufeed:{uid}:0")])


# ---------------- handlerlar ----------------

async def hisobchi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin bo'lmaganlarga buyruq mavjudligi bildirilmaydi (jim e'tiborsiz).
    if not _is_admin(update):
        return
    chat = update.effective_chat
    if chat and chat.type != "private":
        await update.effective_message.reply_text("🧮 /hisobchi faqat botning shaxsiy chatida ishlaydi.")
        return
    try:
        if context.args:
            arg = context.args[0].strip()
            uid = int(arg) if arg.lstrip("-").isdigit() else hisobchi.find_user_by_username(arg)
            if not uid:
                await update.effective_message.reply_text("❌ Foydalanuvchi topilmadi. ID yoki @username yozing.")
                return
            text, kb = _render_user(uid)
        else:
            text, kb = _render_main()
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001
        logger.error(f"🧮 /hisobchi xatosi: {type(e).__name__}: {e}", exc_info=True)
        await update.effective_message.reply_text("❌ Hisobchi hisobotini tayyorlashda xato chiqdi (logga qarang).")


async def hisobchi_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(update):
        await query.answer("🚫 Ruxsat yo'q.", show_alert=True)
        return
    parts = (query.data or "").split(":")
    try:
        view = parts[1] if len(parts) > 1 else "main"
        if view == "feed":
            text, kb = _render_feed(int(parts[2]))
        elif view == "ufeed":
            text, kb = _render_feed(int(parts[3]), int(parts[2]))
        elif view == "ai":
            text, kb = _render_ai()
        elif view == "fn":
            text, kb = _render_fn()
        elif view == "usr":
            text, kb = _render_users(parts[2] if parts[2] in ("a", "s") else "a", int(parts[3]))
        elif view == "u":
            text, kb = _render_user(int(parts[2]))
        else:
            text, kb = _render_main()
    except Exception as e:  # noqa: BLE001
        logger.error(f"🧮 hisobchi callback xatosi ({query.data}): {type(e).__name__}: {e}", exc_info=True)
        await query.answer("❌ Xato chiqdi.", show_alert=True)
        return
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb,
                                      disable_web_page_preview=True)
        await query.answer()
    except BadRequest as e:
        # Yangilash bosilganda matn o'zgarmagan bo'lsa Telegram shunday xato beradi — bu normal.
        await query.answer("Yangi o'zgarish yo'q." if "not modified" in str(e).lower() else "❌ Xato.")


async def begin_trace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """bot.py > group=-3 (block=True): har bir update uchun AI-iz boshlaydi. Mikrosekundlik, hech narsa kutmaydi."""
    u = update.effective_user
    hisobchi.begin(u.id if u else 0)
