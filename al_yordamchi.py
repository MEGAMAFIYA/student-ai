"""
🤖 /al_yordamchi — Telegram Business akkaunt uchun shaxsiy AI yordamchi.

Oqim:
1) Business akkaunt ulangan bo'lsa, foydalanuvchi /al_yordamchi ni ochadi.
2) Provider tanlanadi, foydalanuvchidan API kaliti so'raladi va model tanlangach
   kalit /developer > Kalitlar > AI kalitlari bilan bir xil KEY_POOLS to'plamiga yoziladi.
3) Keyingi safar faqat boshqaruv menyusi chiqadi.
4) Xotira uchun Neon/Supabase kabi PostgreSQL connection string kiritiladi.
5) "Yangi foydalanuvchi" rejimi yangi chatlardan boshlangan suhbatlarga,
   "Eski foydalanuvchi" rejimi oldindan mavjud peerlar bilan suhbatlarga javob beradi.
6) Maxsus tanishlar har doim ustuvor.
7) Incoming media (voice/photo/video/document) uchun hech qanday AI javob yuborilmaydi.
"""
import logging
import asyncio
import re
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

import ai_clients
import al_yordamchi_db
import business_storage
import config
import user_ai_keys

logger = logging.getLogger("al_yordamchi")

_WAIT_KEY = "al_waiting"
_TMP = "al_tmp"  # eski sessiyalarni bekor qilish uchun moslik

FREE_MODELS = {
    "gemini": ["gemini-3.6-flash", "gemini-2.5-flash"],
    "groq": ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"],
    "mistral": ["mistral-small-latest"],
    "openrouter": ["meta-llama/llama-3.3-70b-instruct:free", "google/gemma-3-27b-it:free"],
    "cerebras": ["llama3.1-8b"],
    "cloudflare": ["@cf/meta/llama-3.1-8b-instruct"],
    "sambanova": ["Meta-Llama-3.1-8B-Instruct"],
    "cohere": ["command-r-plus"],
    "huggingface": ["meta-llama/Llama-3.1-8B-Instruct"],
    "nvidia": ["meta/llama-3.1-8b-instruct"],
    "vercel": ["openai/gpt-4o-mini"],
}

def _label(provider):
    return config.PROVIDER_LABELS.get(provider, provider.capitalize())


def _esc(s):
    return escape(str(s or ""))


def _provider_key_entries(provider):
    """AL Yordamchi uchun /developer > Kalitlar bo'limidagi umumiy kalitlar."""
    return [
        dict(entry) for entry in config.KEY_POOLS.get(provider, [])
        if isinstance(entry, dict) and str(entry.get("key") or "").strip()
    ]


def _provider_links_text():
    return (
        "🔑 <b>AL YORDAMCHI uchun AI provayderini tanlang</b>\n\n"
        "Tanlaganingizdan keyin API kalitingizni yuborishingiz so'raladi. "
        "Kalit shaxsiy GitHub faylingizga emas, /developer → Kalitlar → AI kalitlari "
        "bo'limida ishlatiladigan umumiy KEY_POOLS saqlash joyiga qo'shiladi."
    )


def _provider_keyboard():
    providers = list(config.SUPPORTED_PROVIDERS)
    buttons = [InlineKeyboardButton(_label(p), callback_data=f"al:prov:{p}") for p in providers]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="al:cancel")])
    return InlineKeyboardMarkup(rows)


def _model_text(provider):
    models = []
    seen = set()
    for m in [config.DEFAULT_MODEL_BY_PROVIDER.get(provider, "")] + FREE_MODELS.get(provider, []):
        if m and m not in seen:
            models.append(m)
            seen.add(m)
    lines = [
        f"🤖 <b>{_esc(_label(provider))}</b> tanlandi.",
        "API kalit qabul qilindi. Endi modelni tanlang:",
        "",
        "Tavsiya qilinadigan modellar:",
    ]
    for m in models:
        lines.append(f"• <code>{_esc(m)}</code>")
    lines.append("\nPastdagi tugmadan birini tanlang yoki <b>✍️ Boshqa model</b> orqali model nomini yuboring.")
    return "\n".join(lines)


def _model_keyboard(provider):
    models = []
    seen = set()
    for m in [config.DEFAULT_MODEL_BY_PROVIDER.get(provider, "")] + FREE_MODELS.get(provider, []):
        if m and m not in seen:
            models.append(m)
            seen.add(m)
    rows = [[InlineKeyboardButton(m[:48], callback_data=f"al:model:{provider}:{i}")]
            for i, m in enumerate(models)]
    rows.append([InlineKeyboardButton("✍️ Boshqa model nomi", callback_data=f"al:model_manual:{provider}")])
    rows.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="al:cancel")])
    return InlineKeyboardMarkup(rows)


def _main_text(user_id):
    cfg = user_ai_keys.get_assistant_config(user_id)
    enabled = bool(cfg.get("enabled"))
    provider = cfg.get("provider")
    model = cfg.get("model")
    db_ready = bool(cfg.get("db_dsn"))
    mode = cfg.get("audience_mode")
    special = cfg.get("special_users") or []
    return (
        "🤖 <b>AL YORDAMCHI</b>\n\n"
        f"Holat: {'🟢 YOQILGAN' if enabled else '🔴 O‘CHIRILGAN'}\n"
        f"AI: <code>{_esc(_label(provider) if provider else 'ulanmagan')}</code>\n"
        f"Umumiy kalitlar: <code>{len(_provider_key_entries(provider)) if provider else 0} ta</code>\n"
        f"Model: <code>{_esc(model or 'tanlanmagan')}</code>\n"
        f"Xotira: {'🟢 ulangan' if db_ready else '⚪ ulanmagan'}\n"
        f"Foydalanuvchi turi: <code>{_esc(mode or 'tanlanmagan')}</code>\n"
        f"Maxsus tanishlar: <code>{len(special)} ta</code>\n\n"
        "AI yordamchi egasi nomidan faqat matnli xabarlarga javob beradi."
    )


def _main_keyboard(user_id):
    cfg = user_ai_keys.get_assistant_config(user_id)
    enabled = bool(cfg.get("enabled"))
    toggle = "⛔️ Yordamchini o‘chirish" if enabled else "▶️ Yordamchini yoqish"
    rows = [
        [InlineKeyboardButton("🧠 Xotira qo‘shish", callback_data="al:memory")],
        [InlineKeyboardButton("👤 Foydalanuvchi turi", callback_data="al:audience")],
        [InlineKeyboardButton("⭐ Maxsus tanishlar", callback_data="al:special")],
        [InlineKeyboardButton(toggle, callback_data="al:toggle")],
    ]
    if cfg.get("provider"):
        rows.append([InlineKeyboardButton("🔄 AI modelini almashtirish", callback_data="al:rekey")])
    return InlineKeyboardMarkup(rows)


async def al_yordamchi_cmd(update, context):
    user = update.effective_user
    message = update.effective_message
    if not user or not message or message.chat.type != "private":
        return
    entry = business_storage.get_connection_for_user(user.id)
    usable, reason = business_storage.is_connection_usable(entry)
    if not usable:
        await message.reply_text(
            "Avval Telegram Business akkauntingizni botga ulang va botga xabarlarni o‘qish/javob berish huquqini bering."
        )
        return

    cfg = user_ai_keys.get_assistant_config(user.id)
    if not cfg.get("provider") or not _provider_key_entries(cfg.get("provider")) or not cfg.get("model"):
        context.user_data[_WAIT_KEY] = {"stage": "provider"}
        await message.reply_text(_provider_links_text(), parse_mode=ParseMode.HTML, reply_markup=_provider_keyboard())
        return

    await message.reply_text(_main_text(user.id), parse_mode=ParseMode.HTML, reply_markup=_main_keyboard(user.id))


async def callback(update, context):
    q = update.callback_query
    if not q:
        return
    user_id = q.from_user.id
    data = q.data or ""
    try:
        await q.answer()
    except Exception as exc:
        # Telegram callback query muddati o'tgan bo'lsa ham sozlash jarayoni
        # davom etishi kerak; eskirgan query sabab handler yiqilmasin.
        logger.warning("AL_CALLBACK_ANSWER_SKIPPED user_id=%s error=%s", user_id, exc)

    if data == "al:cancel":
        context.user_data.pop(_WAIT_KEY, None)
        context.user_data.pop(_TMP, None)
        await q.edit_message_text("❌ AL YORDAMCHI sozlash bekor qilindi.")
        return

    if data.startswith("al:prov:"):
        provider = data.split(":", 2)[2]
        if provider not in config.SUPPORTED_PROVIDERS:
            await q.edit_message_text("⚠️ Provider topilmadi.")
            return
        context.user_data[_WAIT_KEY] = {"stage": "api_key", "provider": provider}
        await q.edit_message_text(
            f"🔐 <b>{_esc(_label(provider))}</b> API kalitingizni yuboring.\n\n"
            "Kalit /developer → Kalitlar → AI kalitlari bilan bir xil umumiy saqlash joyiga qo'shiladi.",
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("al:model:"):
        _, _, provider, idx = data.split(":")
        models = []
        seen = set()
        for m in [config.DEFAULT_MODEL_BY_PROVIDER.get(provider, "")] + FREE_MODELS.get(provider, []):
            if m and m not in seen:
                models.append(m); seen.add(m)
        i = int(idx)
        if not (0 <= i < len(models)):
            await q.edit_message_text("⚠️ Model topilmadi.")
            return
        pending = context.user_data.get(_WAIT_KEY) or {}
        api_key = str(pending.get("api_key") or "").strip()
        if pending.get("provider") != provider or not api_key:
            await q.edit_message_text("⚠️ API kalit bosqichi topilmadi. /al_yordamchi buyrug'idan qayta boshlang.")
            return
        config.add_key(provider, api_key, models[i])
        cfg = user_ai_keys.get_assistant_config(user_id)
        cfg.pop("key", None)
        cfg.update({"provider": provider, "model": models[i]})
        if not await asyncio.to_thread(user_ai_keys.save_assistant_config, user_id, cfg):
            await q.edit_message_text("❌ Sozlamani saqlashda xato. Qayta urinib ko‘ring.")
            return
        context.user_data.pop(_WAIT_KEY, None)
        context.user_data.pop(_TMP, None)
        await q.edit_message_text(
            "✅ API kalit va AL YORDAMCHI modeli /developer → Kalitlar → AI kalitlari umumiy to'plamiga saqlandi.\n\n" + _main_text(user_id),
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(user_id),
        )
        return

    if data.startswith("al:model_manual:"):
        provider = data.split(":", 2)[2]
        pending = context.user_data.get(_WAIT_KEY) or {}
        context.user_data[_WAIT_KEY] = {
            "stage": "model_manual", "provider": provider,
            "api_key": pending.get("api_key", ""),
        }
        await q.edit_message_text(
            f"✍️ {_esc(_label(provider))} uchun model nomini yuboring.",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "al:memory":
        cfg = user_ai_keys.get_assistant_config(user_id)
        await q.edit_message_text(
            "🧠 <b>AI xotirasi</b>\n\n"
            "Tavsiya: <b>Neon PostgreSQL</b> yoki <b>Supabase PostgreSQL</b> bepul tarifidan foydalaning.\n\n"
            "Neon: https://neon.tech\n"
            "Supabase: https://supabase.com\n\n"
            "Ikkalasidan ham PostgreSQL connection string olinadi. "
            "Bot uni faqat shu foydalanuvchining xotirasi uchun ishlatadi.\n\n"
            "👇 Tanlaganingizdan keyin connection stringni bitta xabar qilib yuboring.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 Neon", url="https://neon.tech")],
                [InlineKeyboardButton("🌐 Supabase", url="https://supabase.com")],
                [InlineKeyboardButton("🔑 Connection string yuborish", callback_data="al:memory_input")],
                [InlineKeyboardButton("⬅️ Orqaga", callback_data="al:back")],
            ]),
        )
        return

    if data == "al:memory_input":
        context.user_data[_WAIT_KEY] = {"stage": "db"}
        await q.edit_message_text(
            "🔐 PostgreSQL connection stringni yuboring.\n\n"
            "Masalan: <code>postgresql://user:password@host/db?sslmode=require</code>\n"
            "Xabar keyin o‘chirib tashlanadi.",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "al:audience":
        await q.edit_message_text(
            "👤 <b>Foydalanuvchi turi</b>\n\n"
            "Qaysi chatlarga AI javob bersin?",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🆕 Yangi foydalanuvchilar", callback_data="al:aud:new")],
                [InlineKeyboardButton("👴 Eski foydalanuvchilar", callback_data="al:aud:old")],
                [InlineKeyboardButton("⬅️ Orqaga", callback_data="al:back")],
            ]),
        )
        return

    if data.startswith("al:aud:"):
        mode = data.split(":", 2)[2]
        cfg = user_ai_keys.get_assistant_config(user_id)
        cfg["audience_mode"] = mode
        cfg["enabled"] = True
        # Business storage'da oldindan qayd etilgan peerlarni xotiraga urug'laymiz.
        recent_peers = list(dict.fromkeys(
            business_storage.get_recent_business_peers(user_id, 30 * 24 * 60 * 60)
            + business_storage.get_auto_reply_seen_peers(user_id)
        ))
        if cfg.get("db_dsn") and mode == "old":
            try:
                await al_yordamchi_db.a_seed_old_peers(cfg["db_dsn"], user_id, recent_peers)
            except Exception:
                logger.exception("Old peers seed failed user_id=%s", user_id)
        elif mode == "old":
            known = {int(x) for x in (cfg.get("known_peers") or []) if str(x).lstrip("-").isdigit()}
            known.update(recent_peers)
            cfg["known_peers"] = list(known)[-20000:]
        await asyncio.to_thread(user_ai_keys.save_assistant_config, user_id, cfg)
        label = "🆕 yangi" if mode == "new" else "👴 eski"
        await q.edit_message_text(
            f"✅ AI yordamchi yoqildi. Rejim: <b>{label} foydalanuvchilar</b>.\n\n" +
            _main_text(user_id),
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(user_id),
        )
        return

    if data == "al:special":
        cfg = user_ai_keys.get_assistant_config(user_id)
        special = cfg.get("special_users") or []
        text = (
            "⭐ <b>Maxsus tanishlar</b>\n\n"
            "Bu ro‘yxatdagi Telegram ID egasi yozsa, AI unga javob berishni "
            "audience rejimidan qat’i nazar ustuvor qiladi.\n\n"
            f"Hozir: {len(special)} ta.\n"
            "Yangi ID qo‘shish uchun tugmani bosing."
        )
        rows = [[InlineKeyboardButton("➕ ID qo‘shish", callback_data="al:special_add")]]
        if special:
            rows.append([InlineKeyboardButton("🗑 ID ni o‘chirish", callback_data="al:special_del")])
        rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="al:back")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "al:special_add":
        context.user_data[_WAIT_KEY] = {"stage": "special_add"}
        await q.edit_message_text("⭐ Javob beriladigan foydalanuvchining Telegram ID raqamini yuboring.")
        return

    if data == "al:special_del":
        cfg = user_ai_keys.get_assistant_config(user_id)
        special = cfg.get("special_users") or []
        rows = [[InlineKeyboardButton(str(x), callback_data=f"al:special_del_one:{x}")] for x in special[:50]]
        rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="al:special")])
        await q.edit_message_text("O‘chirish uchun ID ni tanlang:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("al:special_del_one:"):
        target = int(data.split(":")[-1])
        cfg = user_ai_keys.get_assistant_config(user_id)
        cfg["special_users"] = [x for x in (cfg.get("special_users") or []) if int(x) != target]
        await asyncio.to_thread(user_ai_keys.save_assistant_config, user_id, cfg)
        await q.edit_message_text(_main_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_main_keyboard(user_id))
        return

    if data == "al:toggle":
        cfg = user_ai_keys.get_assistant_config(user_id)
        if not cfg.get("enabled") and (not cfg.get("provider") or not _provider_key_entries(cfg.get("provider"))):
            context.user_data[_WAIT_KEY] = {"stage": "provider"}
            await q.edit_message_text(
                "⚠️ Yordamchini yoqishdan oldin /developer → Kalitlar → AI kalitlari bo'limida kalit bo'lishi kerak.\n\n" + _provider_links_text(),
                parse_mode=ParseMode.HTML, reply_markup=_provider_keyboard(),
            )
            return
        cfg["enabled"] = not bool(cfg.get("enabled"))
        await asyncio.to_thread(user_ai_keys.save_assistant_config, user_id, cfg)
        await q.edit_message_text(_main_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_main_keyboard(user_id))
        return

    if data == "al:rekey":
        context.user_data[_WAIT_KEY] = {"stage": "provider"}
        await q.edit_message_text(_provider_links_text(), parse_mode=ParseMode.HTML, reply_markup=_provider_keyboard())
        return

    if data == "al:back":
        await q.edit_message_text(_main_text(user_id), parse_mode=ParseMode.HTML, reply_markup=_main_keyboard(user_id))
        return


async def receive_setup_text(update, context):
    state = context.user_data.get(_WAIT_KEY)
    if not state:
        # Bir xil handler group ichida PTB faqat birinchi mos MessageHandler'ni
        # ishlatadi. Shuning uchun AL YORDAMCHI handleri oddiy holatda eski
        # /bossiyons matn kutish oqimini o'ziga delegatsiya qiladi.
        from handlers import business_autoreply
        # MUHIM: faqat /bossiyons matni haqiqatan qabul qilingandagina True qaytadi.
        # Aks holda (masalan /developer > Reklama'da guruh ID yozilganda) xabar
        # keyingi handlerlarga (ConversationHandler'larga) o'tishi kerak.
        return bool(await business_autoreply.receive_reply_text(update, context))
    message = update.effective_message
    user = update.effective_user
    if not message or not user or message.chat.type != "private" or not message.text:
        return False
    text = message.text.strip()
    stage = state.get("stage")

    if stage == "api_key":
        provider = state.get("provider")
        if not text or len(text) > 1000 or any(ch.isspace() for ch in text):
            await message.reply_text("⚠️ API kalit formati noto'g'ri. Kalitni bo'sh joylarsiz qayta yuboring.")
            return True
        context.user_data[_WAIT_KEY] = {"stage": "model_manual_or_pick", "provider": provider, "api_key": text}
        await message.reply_text(_model_text(provider), parse_mode=ParseMode.HTML, reply_markup=_model_keyboard(provider))
        return True

    if stage in ("model_manual", "model_manual_or_pick"):
        provider = state.get("provider")
        if not text or len(text) > 200:
            await message.reply_text("⚠️ Model nomi noto‘g‘ri. Qaytadan yuboring.")
            return True
        api_key = str(state.get("api_key") or "").strip()
        if not api_key:
            await message.reply_text("⚠️ API kalit topilmadi. /al_yordamchi buyrug'idan qayta boshlang.")
            context.user_data.pop(_WAIT_KEY, None)
            return True
        config.add_key(provider, api_key, text)
        cfg = user_ai_keys.get_assistant_config(user.id)
        cfg.pop("key", None)
        cfg.update({"provider": provider, "model": text})
        if not await asyncio.to_thread(user_ai_keys.save_assistant_config, user.id, cfg):
            await message.reply_text("❌ Sozlamani saqlashda xato.")
            return True
        context.user_data.pop(_WAIT_KEY, None)
        context.user_data.pop(_TMP, None)
        await message.reply_text(
            "✅ API kalit va AL YORDAMCHI modeli /developer → Kalitlar → AI kalitlari umumiy to'plamiga saqlandi.\n\n" + _main_text(user.id),
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(user.id),
        )
        return True

    if stage == "db":
        try:
            await message.delete()
        except Exception:
            pass
        ok, detail = await al_yordamchi_db.a_test_connection(text)
        if not ok:
            await message.reply_text(
                "❌ Database ulanmadi.\n\n"
                f"<code>{_esc(detail)}</code>\n\n"
                "Connection stringni tekshirib qayta yuboring.",
                parse_mode=ParseMode.HTML,
            )
            return True
        cfg = user_ai_keys.get_assistant_config(user.id)
        cfg["db_dsn"] = text
        cfg["db_provider"] = "postgresql"
        await asyncio.to_thread(user_ai_keys.save_assistant_config, user.id, cfg)
        context.user_data.pop(_WAIT_KEY, None)
        await message.reply_text(
            "✅ Xotira database ulandi va jadvallar tayyorlandi.\n\n" +
            _main_text(user.id),
            parse_mode=ParseMode.HTML,
            reply_markup=_main_keyboard(user.id),
        )
        return True

    if stage == "special_add":
        try:
            target = int(text)
        except ValueError:
            await message.reply_text("⚠️ Telegram ID faqat raqam bo‘lishi kerak.")
            return True
        cfg = user_ai_keys.get_assistant_config(user.id)
        special = [int(x) for x in (cfg.get("special_users") or [])]
        if target not in special:
            special.append(target)
        cfg["special_users"] = special[:100]
        await asyncio.to_thread(user_ai_keys.save_assistant_config, user.id, cfg)
        context.user_data.pop(_WAIT_KEY, None)
        await message.reply_text(
            f"✅ {target} maxsus tanishlar ro‘yxatiga qo‘shildi.",
            reply_markup=_main_keyboard(user.id),
        )
        return True

    return False


def _extract_reply_and_memories(raw: str):
    """AI javobidan ixtiyoriy XOTIRA blokini ajratadi."""
    if not raw:
        return "", []
    reply = raw.strip()
    memories = []
    m = re.search(r"XOTIRA(?:LAR)?\s*:\s*(.*)$", reply, re.I | re.S)
    if m:
        block = m.group(1).strip()
        reply = reply[:m.start()].strip()
        for line in block.splitlines():
            line = re.sub(r"^\s*[-•*]\s*", "", line).strip()
            if line and not re.match(r"^(yo'q|yo‘q|none|yozilmadi)$", line, re.I):
                memories.append(line[:1000])
    if reply.lower().startswith("javob:"):
        reply = reply.split(":", 1)[1].strip()
    return reply[:4096], memories[:5]


def _build_system(owner_name, memories):
    memory_text = "\n".join(f"- {m.get('memory','')}" for m in memories if m.get("memory"))
    if not memory_text:
        memory_text = "- Hozircha saqlangan xotira yo'q."
    return f"""
Siz {owner_name or 'egangiz'} nomidan yozadigan shaxsiy AI yordamchisiz.
Siz Telegram Business akkaunt egasi nomidan suhbatlashasiz.
Javobni tabiiy, qisqa va odamga o'xshash o'zbek tilida bering.
Hech qachon "men botman", "AI yordamchiman" deb aytmang, agar suhbatdosh
buni bevosita so'ramasa. Egasi nomidan gapiring va egasining shaxsiy
ma'lumotlarini begonalarga oshkor qilmang.

MUHIM XOTIRA:
Quyidagi ma'lumotlar egangiz haqidagi uzoq muddatli xotira:
{memory_text}

Agar egasi haqida kelajakda kerak bo'ladigan yangi barqaror fakt suhbat
davomida ma'lum bo'lsa, javobingiz oxirida alohida:
XOTIRA:
- <qisqa, aniq, keyin qidirishga qulay fakt>
formatida yozing. Agar saqlashga arzigulik yangi fakt bo'lmasa, XOTIRA:
yo'q deb yozing. Bu blok foydalanuvchiga yuborilmaydi.
""".strip()


async def _dispatch_with_shared_keys(provider, model, prompt, system, history, label):
    """AL uchun /developer KEY_POOLS'dagi kalitlarni navbat bilan sinaydi."""
    entries = _provider_key_entries(provider)
    last = (None, "missing_key", "Umumiy kalitlar to'plamida kalit yo'q.")
    for index, entry in enumerate(entries, start=1):
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        use_model = model or str(entry.get("model") or "").strip()
        if not use_model:
            last = (None, "missing_model", f"Kalit #{index} uchun model ko'rsatilmagan.")
            continue
        raw, status, detail = await ai_clients._dispatch(
            provider, key, use_model, "", prompt, system, history,
            f"{label} (umumiy kalit #{index})"
        )
        if raw:
            return raw, "ok", ""
        last = (raw, status, detail)
        # Limit/vaqtinchalik xatoda keyingi umumiy kalitni sinab ko'ramiz.
        logger.warning("AL_SHARED_KEY_FAILED provider=%s index=%s status=%s", provider, index, status)
    return last


async def process_business_message(message, context) -> bool:
    """AI yordamchi ustuvor bo'lsa True; aks holda eski auto-reply ishlashi uchun False."""
    if not message or not message.business_connection_id or not message.chat:
        return False
    if getattr(message.chat, "type", None) != "private":
        return False

    owner_id = business_storage.get_user_id_for_connection(message.business_connection_id)
    if not owner_id:
        return False

    # Egasi botdagi istalgan funksiyani ishlatayotgan bo‘lsa, Business AI jim turadi.
    try:
        import feature_guard
        if feature_guard.is_user_busy(context.application, owner_id):
            logger.info("AL_REPLY_BLOCKED owner=%s reason=ACTIVE_FEATURE", owner_id)
            return True
    except Exception:
        logger.exception("AL Yordamchi busy-guard tekshiruvida xato owner=%s", owner_id)
        return True

    cfg = user_ai_keys.get_assistant_config(owner_id)
    if not cfg.get("enabled") or not cfg.get("provider") or not cfg.get("model"):
        return False
    key_entries = _provider_key_entries(cfg.get("provider"))
    if not key_entries:
        logger.warning("AL_NO_SHARED_KEYS owner=%s provider=%s", owner_id, cfg.get("provider"))
        return True

    # Media/voice/video/photo/document uchun hech qachon javob yubormaymiz.
    text = (getattr(message, "text", None) or "").strip()
    if not text:
        return True

    sender = getattr(message, "from_user", None)
    if not sender or sender.is_bot:
        return True
    peer_id = int(message.chat.id)
    special = {int(x) for x in (cfg.get("special_users") or []) if str(x).lstrip("-").isdigit()}

    # Egasi tomonidan kelgan matn bo'lsa: javob bermaymiz, lekin AI xotirasini yangilaymiz.
    if sender.id == owner_id:
        try:
            memories = user_ai_keys.get_assistant_config(owner_id).get("db_dsn")
            if memories:
                db_dsn = memories
                existing = await al_yordamchi_db.a_list_memories(db_dsn, owner_id, 40)
                system = _build_system(
                    getattr(sender, "first_name", "") or "egasi", existing
                )
                prompt = (
                    "Egasi quyidagi gapni aytdi. Faqat xotiraga saqlashga arzigulik "
                    "barqaror faktlarni ajratib, XOTIRA blokida qaytaring. "
                    "Javob qisqa bo'lsin.\n\n" + text
                )
                raw, status, detail = await _dispatch_with_shared_keys(
                    cfg["provider"], cfg["model"], prompt, system, None, "AL YORDAMCHI memory"
                )
                if raw:
                    _, memories_to_save = _extract_reply_and_memories(raw)
                    for mem in memories_to_save:
                        await al_yordamchi_db.a_add_memory(db_dsn, owner_id, mem)
        except Exception:
            logger.exception("Owner memory processing failed user_id=%s", owner_id)
        return True

    # Egasi nomi BusinessConnection storage'dan olinadi.
    owner_entry = business_storage.get_connection_for_user(owner_id) or {}
    owner_name = " ".join(x for x in [
        owner_entry.get("first_name", "") or "",
        owner_entry.get("last_name", "") or "",
    ]).strip() or "egasi"

    # Peerni qayd etamiz; first=True bo'lsa bu assistant uchun yangi peer.
    try:
        if cfg.get("db_dsn"):
            is_new = await al_yordamchi_db.a_upsert_peer(
                cfg["db_dsn"], owner_id, peer_id,
                " ".join(x for x in [
                    getattr(sender, "first_name", "") or "",
                    getattr(sender, "last_name", "") or "",
                ]).strip(),
            )
        else:
            known = {int(x) for x in (cfg.get("known_peers") or []) if str(x).lstrip("-").isdigit()}
            is_new = peer_id not in known
            if is_new:
                known.add(peer_id)
                cfg["known_peers"] = list(known)[-20000:]
                await asyncio.to_thread(user_ai_keys.save_assistant_config, owner_id, cfg)
    except Exception:
        logger.exception("Peer state update failed user_id=%s peer=%s", owner_id, peer_id)
        is_new = True

    allowed = peer_id in special
    if not allowed:
        mode = cfg.get("audience_mode")
        active_peers = {int(x) for x in (cfg.get("active_peers") or []) if str(x).lstrip("-").isdigit()}
        if mode == "new":
            # "Yangi foydalanuvchi" faqat suhbatni yangi peer boshlab berganida
            # ishga tushadi, keyin shu chat davomida AI javob berishda davom etadi.
            if peer_id in active_peers:
                allowed = True
            elif is_new:
                allowed = True
                active_peers.add(peer_id)
                cfg["active_peers"] = list(active_peers)[-20000:]
                await asyncio.to_thread(user_ai_keys.save_assistant_config, owner_id, cfg)
        elif mode == "old":
            # Old rejim — assistant yoqilishidan oldin tanilgan peerlar.
            allowed = not is_new
        else:
            allowed = False
    if not allowed:
        return True

    try:
        db_dsn = cfg.get("db_dsn")
        memories = await al_yordamchi_db.a_list_memories(db_dsn, owner_id, 40) if db_dsn else []
        history = await al_yordamchi_db.a_get_history(db_dsn, owner_id, peer_id, 16) if db_dsn else []
        system = _build_system(owner_name, memories)
        raw, status, detail = await _dispatch_with_shared_keys(
            cfg["provider"], cfg["model"], text, system, history, "AL YORDAMCHI"
        )
        if not raw:
            logger.warning(
                "AL AI failed owner=%s peer=%s status=%s detail=%s",
                owner_id, peer_id, status, detail,
            )
            return True
        reply, memories_to_save = _extract_reply_and_memories(raw)
        if not reply:
            return True
        sent = await context.bot.send_message(
            business_connection_id=message.business_connection_id,
            chat_id=peer_id,
            text=reply[:4096],
        )
        if db_dsn:
            await al_yordamchi_db.a_append_history(db_dsn, owner_id, peer_id, "user", text)
            await al_yordamchi_db.a_append_history(db_dsn, owner_id, peer_id, "assistant", reply)
            for mem in memories_to_save:
                await al_yordamchi_db.a_add_memory(db_dsn, owner_id, mem)
        logger.info(
            "AL_REPLY_OK owner=%s peer=%s message_id=%s",
            owner_id, peer_id, getattr(sent, "message_id", None),
        )
    except Exception:
        logger.exception("AL_REPLY_FAILED owner=%s peer=%s", owner_id, peer_id)
    return True


async def cancel_cmd(update, context):
    had = bool(context.user_data.pop(_WAIT_KEY, None) or context.user_data.pop(_TMP, None))
    if had and update.effective_message:
        await update.effective_message.reply_text("❌ AL YORDAMCHI amali bekor qilindi.")
        return True
    from handlers import business_autoreply
    await business_autoreply.cancel_cmd(update, context)
    return True
