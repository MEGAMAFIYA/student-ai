"""
💬 UNIVERSAL CHAT — hech qanday conversation faol bo'lmaganda ishlaydigan
asosiy matn handler.

- Shaxsiy chatda: barcha xabarlarga javob beradi (avvalgidek).
- Guruhda: STANDART holatda FAOL (bot guruhga qo'shilgan zahoti ishlaydi),
  faqat xabarda "dase" so'zi ishlatilganda YOKI botning oldingi xabariga
  reply qilinganda javob beradi. Agar guruh /ochirish bilan buni ANIQ
  o'chirsa, bu holat doimiy saqlanadi (Upstash/app_data.json) — bot qayta
  deploy qilinsa ham o'sha guruhda o'chirilgan holicha qoladi (/yoqish bilan
  qayta yoqilmaguncha).
- Suhbat tarixi har bir chat uchun saqlanadi (oxirgi bir necha savol-javob) —
  shu orqali bot oldingi savollarni "eslab qoladi".
- Agar xabarda boshqa funksiyaga tegishli buyruq va yetarli ma'lumot bo'lsa,
  o'sha funksiyani o'zi ishga tushirib, javobni foydalanuvchiga qaytaradi.
"""

import asyncio
import logging
import re
from io import BytesIO

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode, ChatAction

from config import UNIVERSAL_CHAT_AI, TRANSLATE_AI
from ai_clients import ask_ai
from pdf_tools import build_course_work_pdf, count_pdf_pages
from handlers.menu import main_menu_keyboard, MENU_CALLBACKS
from handlers import course_work, mention_dispatch
from handlers.tabrik import tabrik_cmd
from handlers.rasim import rasim_cmd
from handlers.vid import vid_cmd
from handlers.qoshiq import qoshiq_cmd
import storage

logger = logging.getLogger(__name__)

INTENT_KEYWORDS = {
    "course_work": ["kurs ishi", "kurs loyihasi", "kurs proyekti", "diplom ishi"],
    "translate": ["tarjima qil", "tarjima qilib ber", "tilga o'gir", "tiliga tarjima"],
    "images_pdf": ["suratlarni pdf", "rasmlarni pdf", "fotolarni pdf", "rasmlardan pdf"],
    "edit_pdf": ["pdfni tahrir", "pdf ni tahrir", "hujjatni tuzat", "pdfni tuzat"],
    "guide": ["qo'llanma tayyorla", "qollanma tayyorla", "savol-javob qollanma"],
}

TARGET_LANG_HINTS = {
    "ruscha": "Ruscha",
    "rus tiliga": "Ruscha",
    "inglizcha": "Inglizcha",
    "ingliz tiliga": "Inglizcha",
    "lotincha": "Lotincha (o'zbek)",
    "o'zbek tiliga": "Lotincha (o'zbek)",
    "kirilcha": "Kirilcha (o'zbek, krill)",
}

PAGE_RE = re.compile(r"(\d{1,3})\s*(bet|varoq|sahifa)", re.IGNORECASE)
TRIGGER_RE = re.compile(r"\bdase\b", re.IGNORECASE)

MAX_HISTORY_TURNS = 8  # nechta so'rov-javob juftligi saqlanadi (chatga qarab)

# 🧭 handlers/mention_dispatch.py orqali aniqlangan maxsus buyruq (masalan
# "@Bot /vid URL" yoki apostrofli "/qo'shiq ...") — CommandHandler
# ushlamagan holatlar uchun — shu yerda mos ASOSIY handler funksiyasiga
# yo'naltiriladi (duplicate mantiq yo'q, xuddi shu funksiyalar
# CommandHandler orqali ham chaqiriladi, qarang: bot.py).
SPECIAL_COMMAND_HANDLERS = {
    "tabrik": tabrik_cmd,
    "rasim": rasim_cmd,
    "vid": vid_cmd,
    "qoshiq": qoshiq_cmd,
}


def detect_intent(text: str) -> str:
    t = text.lower()
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return intent
    return "chat"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    is_group = chat.type in ("group", "supergroup")
    user_text = update.message.text.strip()

    # 🧭 AVVAL: bot mention qilingan maxsus buyruqmi ("@Bot /vid URL",
    # "@Bot /qo'shiq ...") yoki apostrofli "/qo'shiq ..." (Telegram buni
    # haqiqiy buyruq deb belgilamagani uchun oddiy MessageHandler orqali
    # shu yerga kelgan) — bo'lsa, mos handlerga TO'G'RIDAN-TO'G'RI
    # yo'naltiramiz. Bu tekshiruv guruh "dase"/faollik holatidan MUSTAQIL
    # ishlaydi — xuddi /tabrik va /rasim (alohida CommandHandler) kabi,
    # bu 4 ta funksiya universal chatning yoqilgan/o'chirilganiga bog'liq
    # emas.
    status, payload = mention_dispatch.resolve(user_text)
    if status == "command":
        handler_fn = SPECIAL_COMMAND_HANDLERS.get(payload.command)
        if handler_fn:
            logger.info(
                f"🧭 Maxsus buyruq mention/matn orqali aniqlandi: chat_id={chat.id}, "
                f"command='{payload.command}', mention={payload.had_mention}."
            )
            await handler_fn(update, context, override_text=payload.remainder_text)
            return

    # Agar bot mention qilingan bo'lsa-yu, undan keyingi matn maxsus buyruq
    # bo'lmasa (masalan "@Bot 4+3=?") — mention qismini olib tashlab, AI
    # chatga yuboramiz. Mavjud AI-mention xatti-harakati BUZILMAYDI, faqat
    # endi mention qismi aniq (tozalab) olib tashlanadi.
    force_ai_mention = status == "mention_ai"
    if force_ai_mention:
        user_text = payload.strip() or user_text

    if is_group:
        # Doimiy saqlashdan o'qiladi (Upstash/app_data.json) — deploy/restart
        # bo'lsa ham yo'qolmaydi. Standart holat: FAOL (True). Guruh /ochirish
        # bilan buni ANIQ o'chirmagan bo'lsa, bot javob beraveradi.
        if not storage.is_group_active(chat.id):
            return  # ushbu guruh o'chirib qo'ygan — deploy bo'lsa ham o'chgan qoladi

        is_reply_to_bot = bool(
            update.message.reply_to_message
            and update.message.reply_to_message.from_user
            and update.message.reply_to_message.from_user.id == context.bot.id
        )
        trigger_match = TRIGGER_RE.search(user_text)

        if not (trigger_match or is_reply_to_bot or force_ai_mention):
            return  # "dase" yo'q, bot xabariga reply ham emas, mention ham emas

        if trigger_match:
            stripped = TRIGGER_RE.sub("", user_text, count=1).strip(" ,:.!-")
            user_text = stripped or user_text

    intent = detect_intent(user_text)
    logger.info(f"💬 Universal chat xabari: chat_id={chat.id}, aniqlangan intent='{intent}'.")

    await context.bot.send_chat_action(chat.id, ChatAction.TYPING)

    if intent == "course_work":
        await _try_course_work(update, context, user_text)
        return

    if intent == "translate":
        if await _try_translate(update, context, user_text):
            return
        await _redirect_to_menu(update, "translate")
        return

    if intent in ("images_pdf", "edit_pdf", "guide"):
        await _redirect_to_menu(update, intent)
        return

    await _chat_with_history(update, context, user_text)


async def _chat_with_history(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    history = context.chat_data.setdefault("history", [])

    response = await ask_ai(
        UNIVERSAL_CHAT_AI,
        user_text,
        "Siz do'stona va bilimdon AI yordamchisiz (ismingiz — Dase). O'zbek tilida "
        "(agar foydalanuvchi boshqa tilda yozmasa), aniq va foydali javob bering. "
        "Suhbat tarixidan foydalanib, oldingi savollarni hisobga oling.",
        history=history,
    )

    if not response:
        logger.error(f"💬 Universal chat javob QAYTARMADI: chat_id={chat.id} — sababi yuqoridagi ai_clients loglarida.")
        await update.message.reply_text("❌ Hozircha javob berib bo'lmadi. Birozdan so'ng qayta urinib ko'ring.")
        return

    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": response})
    max_items = MAX_HISTORY_TURNS * 2
    if len(history) > max_items:
        del history[: len(history) - max_items]

    if len(response) > 3800:
        bio = BytesIO(response.encode("utf-8"))
        await update.message.reply_document(document=InputFile(bio, filename="javob.txt"), caption="💬 Javob uzun bo'lgani uchun fayl sifatida yubordim.")
    else:
        await update.message.reply_text(response)


async def _try_course_work(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    m = PAGE_RE.search(text)
    if not m:
        await _redirect_to_menu(update, "course_work")
        return

    pages = int(m.group(1))
    topic = (text[: m.start()] + text[m.end():]).strip()
    topic = course_work.clean_topic(topic)

    if not topic:
        await _redirect_to_menu(update, "course_work")
        return

    status = await update.message.reply_text(
        f"💬 Bu so'rovni *Kurs ishi* funksiyasiga yubordim.\n"
        f"⏳ *{topic}* mavzusida {pages}+ betlik kurs ishi tayyorlanmoqda...\nReja tuzilmoqda...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        result = await asyncio.wait_for(
            course_work.generate_course_work(topic, pages, status),
            timeout=course_work.OVERALL_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.error(f"Kurs ishi generatsiyasi vaqt chegarasidan oshdi ('{topic}').")
        result = None
    except Exception as e:
        logger.error(f"Kurs ishi generatsiyasida kutilmagan xato ('{topic}'): {type(e).__name__}: {e}", exc_info=True)
        result = None

    if not result:
        logger.error(f"💬 (universal chat) Kurs ishi YAKUNLANMADI ('{topic}') — sababi yuqoridagi loglarda.")
        await status.edit_text(
            "❌ Kurs ishini yaratib bo'lmadi — AI xizmatlari hozir javob bermayapti. "
            "Birozdan so'ng qayta urinib ko'ring."
        )
        return

    sections, pdf_buf, actual_pages = result

    await update.message.reply_document(
        document=InputFile(pdf_buf, filename=f"{topic[:40]}.pdf"),
        caption=(
            f"📄 {topic}\n📎 {actual_pages} bet (so'ralgan: {pages}+)\n"
            "✅ Titul, mundarija, kirish, 3 bob, xulosa va adabiyotlar ro'yxati bilan."
        ),
        reply_markup=main_menu_keyboard(),
    )
    try:
        await status.delete()
    except Exception:
        pass


async def _try_translate(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    t = text.lower()
    target = None
    for hint, lang in TARGET_LANG_HINTS.items():
        if hint in t:
            target = lang
            break
    if not target:
        return False

    if ":" in text:
        content = text.split(":", 1)[1].strip()
    else:
        content = re.sub(r"(?i)tarjima qil(ib ber)?|.*tiliga", "", text).strip()

    if not content:
        return False

    status = await update.message.reply_text(
        f"💬 Bu so'rovni *Tarjima* funksiyasiga yubordim.\n⏳ {target} tiliga tarjima qilinmoqda...",
        parse_mode=ParseMode.MARKDOWN,
    )

    system = (
        "Siz professional tarjimonsiz. Berilgan matnni so'ralgan tilga aniq va ravon "
        "tarjima qiling. Faqat tarjimani qaytaring."
    )
    translated = await ask_ai(TRANSLATE_AI, f"Quyidagi matnni {target} tiliga tarjima qil:\n\n{content}", system)

    if not translated:
        logger.error(f"💬 (universal chat) Tarjima ISHLAMADI: chat_id={update.effective_chat.id} — sababi yuqoridagi ai_clients loglarida.")
        await status.edit_text("❌ Tarjima qilib bo'lmadi.")
        return True

    await update.message.reply_text(
        f"✅ *{target}*:\n\n{translated}", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard()
    )
    try:
        await status.delete()
    except Exception:
        pass
    return True


async def _redirect_to_menu(update: Update, intent_key: str):
    label = MENU_CALLBACKS.get(intent_key, "Kerakli funksiya")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"menu:{intent_key}")]])
    await update.message.reply_text(
        "Bu vazifa uchun quyidagi funksiyadan foydalanamiz — tugmani bosing va "
        "so'ralgan ma'lumotni (masalan, fayl yoki qo'shimcha detal) yuboring:",
        reply_markup=kb,
    )
