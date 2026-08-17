"""
💬 UNIVERSAL CHAT — hech qanday conversation faol bo'lmaganda ishlaydigan
asosiy matn handler.

- Shaxsiy chatda: barcha xabarlarga javob beradi (avvalgidek).
- Guruhda: faqat /yoqish bilan faollashtirilgandan keyin, va faqat xabarda
  "dase" so'zi ishlatilganda YOKI botning oldingi xabariga reply qilinganda javob beradi.
- Suhbat tarixi har bir chat uchun saqlanadi (oxirgi bir necha savol-javob) —
  shu orqali bot oldingi savollarni "eslab qoladi".
- Agar xabarda boshqa funksiyaga tegishli buyruq va yetarli ma'lumot bo'lsa,
  o'sha funksiyani o'zi ishga tushirib, javobni foydalanuvchiga qaytaradi.
"""

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
from handlers import course_work

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

    if is_group:
        if not context.chat_data.get("group_active", False):
            return  # guruhda universal chat hali yoqilmagan

        is_reply_to_bot = bool(
            update.message.reply_to_message
            and update.message.reply_to_message.from_user
            and update.message.reply_to_message.from_user.id == context.bot.id
        )
        trigger_match = TRIGGER_RE.search(user_text)

        if not (trigger_match or is_reply_to_bot):
            return  # "dase" yo'q va bot xabariga reply ham emas

        if trigger_match:
            stripped = TRIGGER_RE.sub("", user_text, count=1).strip(" ,:.!-")
            user_text = stripped or user_text

    intent = detect_intent(user_text)

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
    strip_words = r"^(haqida|mavzusida|kurs ishi|kurs loyihasi|kurs proyekti|yoz|tayyorla)[\s:,-]*"
    topic = re.sub(strip_words, "", topic, flags=re.IGNORECASE).strip()
    strip_words_end = r"[\s:,-]*(haqida|mavzusida|kurs ishi|kurs loyihasi|kurs proyekti|yoz|tayyorla)$"
    topic = re.sub(strip_words_end, "", topic, flags=re.IGNORECASE).strip()

    if not topic:
        await _redirect_to_menu(update, "course_work")
        return

    status = await update.message.reply_text(
        f"💬 Bu so'rovni *Kurs ishi* funksiyasiga yubordim.\n"
        f"⏳ *{topic}* mavzusida {pages}+ betlik kurs ishi tayyorlanmoqda...\nReja tuzilmoqda...",
        parse_mode=ParseMode.MARKDOWN,
    )

    result = await course_work.generate_course_work(topic, pages, status)
    if not result:
        await status.edit_text("❌ Kurs ishini yaratib bo'lmadi.")
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
