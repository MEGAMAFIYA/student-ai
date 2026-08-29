"""
📋 Test/Viktorina — foydalanuvchi mavzu va savollar sonini beradi, AI
variantli (A/B/C/D) test savollarini JSON ko'rinishida tuzadi, so'ng
foydalanuvchi savollarga BIR-BIR, tugmalar orqali javob beradi — har
javobdan keyin darhol to'g'ri/noto'g'ri ko'rsatiladi. Oxirida umumiy ball
va natijalar PDF holida ham olinishi mumkin.
"""

import asyncio
import json
import logging
import re

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from telegram.error import BadRequest

from config import QUIZ_AI
from ai_clients import ask_ai
from pdf_tools import build_quiz_result_pdf
from handlers.menu import main_menu_keyboard
from handlers import wallet_ui
import storage

logger = logging.getLogger(__name__)

QZ_TOPIC, QZ_COUNT, QZ_ACTIVE = range(3)

_COUNT_OPTIONS = [5, 10, 15, 20]
_LETTERS = ["A", "B", "C", "D"]

_SYSTEM = (
    "Siz test/viktorina savollari tuzuvchi mutaxassissiz. Berilgan mavzu bo'yicha "
    "so'ralgan sondagi savol tuzing, har birida ANIQ 4 ta variant va FAQAT BITTA "
    "to'g'ri javob bo'lsin. Savollar mavzuni turli jihatdan qamrab olsin (bir xil "
    "savolni takrorlamang), oson-o'rtacha-qiyin aralash bo'lsin. FAQAT quyidagi JSON "
    "massiv formatida javob bering, boshqa hech qanday matn/izoh yozmang:\n"
    '[{"savol": "...", "variantlar": ["...", "...", "...", "..."], "togri": 0}, ...]\n'
    "'togri' — to'g'ri javobning variantlar ro'yxatidagi INDEKSI (0, 1, 2 yoki 3). "
    "FAQAT o'zbek tilida yozing."
)


def _count_keyboard() -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(str(n), callback_data=f"quiz:count:{n}") for n in _COUNT_OPTIONS]
    return InlineKeyboardMarkup([row])


def _answer_keyboard(options: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{_LETTERS[i]}) {opt[:40]}", callback_data=f"quiz:ans:{i}")] for i, opt in enumerate(options)]
    return InlineKeyboardMarkup(rows)


def _result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Natijani PDF qilib olish", callback_data="quiz:pdf")],
        [InlineKeyboardButton("🏠 Bosh menyu", callback_data="menu:back")],
    ])


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"📋 'Test/Viktorina' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        context.user_data["flow"] = "quiz"
        await query.edit_message_text(
            "📋 *Test/Viktorina tuzish*\n\nQaysi mavzu bo'yicha test kerak? Mavzuni yozing:",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"📋 Test menyusini ochishda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise
    return QZ_TOPIC


async def receive_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = update.message.text.strip()
    if not topic:
        await update.message.reply_text("❗️ Iltimos, mavzuni yozing.")
        return QZ_TOPIC
    context.user_data["quiz_topic"] = topic
    await update.message.reply_text(
        f"✅ Mavzu: *{topic}*\n\nNechta savol bo'lsin?", parse_mode=ParseMode.MARKDOWN, reply_markup=_count_keyboard()
    )
    return QZ_COUNT


async def receive_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    count = int(query.data.split(":")[-1])
    topic = context.user_data.get("quiz_topic", "")
    chat_id = update.effective_chat.id
    logger.info(f"📋 Test so'rovi: chat_id={chat_id}, mavzu='{topic}', savollar soni={count}.")

    await query.edit_message_text(f"⏳ *{topic}* mavzusida {count} ta savol tuzilmoqda...", parse_mode=ParseMode.MARKDOWN)

    questions = await _generate_questions(topic, count)
    if not questions:
        logger.error(f"📋 Test YARATILMADI: chat_id={chat_id}, mavzu='{topic}' — AI JSON javob bermadi (sababi yuqoridagi ai_clients loglarida).")
        await query.edit_message_text("❌ Testni yaratib bo'lmadi — AI xizmati hozir javob bermayapti. Birozdan so'ng qayta urinib ko'ring.")
        # 💰 AI test tuzolmadi — band qilingan summa ozod qilinadi.
        await wallet_ui.finalize_failure(context, update=update, reason="quiz_generation_failed")
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data["quiz_questions"] = questions
    context.user_data["quiz_answers"] = []
    context.user_data["quiz_idx"] = 0
    logger.info(f"📋 Test tayyor: chat_id={chat_id}, {len(questions)} ta savol. Boshlanmoqda...")
    # 💰 Test MUVAFFAQIYATLI tuzildi — xizmat aslida shu yerda bajarildi
    # (foydalanuvchi keyin javob berish-bermasligidan qat'iy nazar, test
    # tuzish xizmati yetkazib berildi), shuning uchun band qilingan summa
    # ENDI HAQIQATAN yechiladi.
    await wallet_ui.finalize_success(context, update=update, chat_id=chat_id)
    await _show_question(query, context)
    return QZ_ACTIVE


async def _show_question(query, context: ContextTypes.DEFAULT_TYPE):
    questions = context.user_data["quiz_questions"]
    idx = context.user_data["quiz_idx"]
    q = questions[idx]
    text = f"❓ *{idx + 1}/{len(questions)}-savol*\n\n{q['savol']}"
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=_answer_keyboard(q["variantlar"]))
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    questions = context.user_data.get("quiz_questions")
    if not questions:
        await query.edit_message_text("⚠️ Test sessiyasi topilmadi. /start bilan qaytadan boshlang.")
        return ConversationHandler.END

    idx = context.user_data["quiz_idx"]
    chosen = int(query.data.split(":")[-1])
    context.user_data["quiz_answers"].append(chosen)

    q = questions[idx]
    correct_idx = q.get("togri", -1)
    if chosen == correct_idx:
        feedback = f"✅ *To'g'ri!*\n\n{q['savol']}"
    else:
        correct_letter = _LETTERS[correct_idx] if 0 <= correct_idx < len(q["variantlar"]) else "?"
        correct_text = q["variantlar"][correct_idx] if 0 <= correct_idx < len(q["variantlar"]) else ""
        feedback = f"❌ *Noto'g'ri.* To'g'ri javob: {correct_letter}) {correct_text}\n\n{q['savol']}"

    await query.edit_message_text(feedback, parse_mode=ParseMode.MARKDOWN)

    idx += 1
    context.user_data["quiz_idx"] = idx

    if idx >= len(questions):
        return await _finish_quiz(update, context)

    await asyncio.sleep(1.2)
    await _show_question(query, context)
    return QZ_ACTIVE


async def _finish_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    questions = context.user_data["quiz_questions"]
    answers = context.user_data["quiz_answers"]
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0
    topic = context.user_data.get("quiz_topic", "")

    score = sum(1 for i, a in enumerate(answers) if a == questions[i].get("togri", -1))
    logger.info(f"📋 Test yakunlandi: chat_id={chat_id}, mavzu='{topic}', natija={score}/{len(questions)}.")

    if user_id:
        storage.record_usage("quiz", user_id)

    text = (
        f"🏁 *Test yakunlandi!*\n\n📋 Mavzu: {topic}\n"
        f"✅ Natija: *{score} / {len(questions)}* ({round(score / len(questions) * 100)}%)"
    )
    await context.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=_result_keyboard())
    return ConversationHandler.END  # test tugadi — "PDF olish"/"Bosh menyu" endi ALOHIDA (conv tashqarisidagi) handlerlar orqali ishlaydi


async def export_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    questions = context.user_data.get("quiz_questions")
    answers = context.user_data.get("quiz_answers", [])
    topic = context.user_data.get("quiz_topic", "Test")
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id

    if not questions:
        await query.edit_message_text("⚠️ Test ma'lumotlari topilmadi.")
        return ConversationHandler.END

    score = sum(1 for i, a in enumerate(answers) if i < len(questions) and a == questions[i].get("togri", -1))
    try:
        pdf_buf = await asyncio.to_thread(build_quiz_result_pdf, topic, questions, answers, score)
    except Exception as e:
        logger.error(f"📋 Test natijasi PDF yaratishda xato ('{topic}'): {type(e).__name__}: {e}", exc_info=True)
        await context.bot.send_message(chat_id, "❌ PDF yaratishda xatolik yuz berdi.")
        return ConversationHandler.END

    msg = await context.bot.send_document(
        chat_id,
        document=InputFile(pdf_buf, filename=f"test_{topic[:30]}.pdf"),
        caption=f"📋 {topic} — natija: {score}/{len(questions)}",
        reply_markup=main_menu_keyboard(),
    )
    logger.info(f"📋 Test natijasi PDF yuborildi: chat_id={chat_id}, mavzu='{topic}'.")
    if user_id and msg.document:
        storage.record_file(user_id, "quiz_pdf", topic, msg.document.file_id)

    context.user_data.clear()
    return ConversationHandler.END


async def _generate_questions(topic: str, count: int) -> list[dict] | None:
    prompt = f"Mavzu: '{topic}'\nSavollar soni: {count} ta."
    for attempt in range(1, 3):
        logger.info(f"📋 [SAVOLLAR] AI ga so'rov yuborilmoqda ({attempt}/2-urinish, provider={QUIZ_AI.get('provider')})...")
        raw = await ask_ai(QUIZ_AI, prompt, _SYSTEM)
        if not raw:
            logger.warning(f"📋 [SAVOLLAR] Bo'sh javob ({attempt}/2-urinish).")
            continue
        try:
            cleaned = re.sub(r"^```json\s*|^```\s*|```\s*$", "", raw.strip(), flags=re.MULTILINE).strip()
            data = json.loads(cleaned)
            questions = []
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict):
                    continue
                variantlar = [str(v).strip() for v in item.get("variantlar", [])]
                togri = item.get("togri")
                if item.get("savol") and len(variantlar) == 4 and isinstance(togri, int) and 0 <= togri <= 3:
                    questions.append({"savol": str(item["savol"]).strip(), "variantlar": variantlar, "togri": togri})
            if questions:
                logger.info(f"📋 [SAVOLLAR] ✅ {len(questions)} ta savol tuzildi.")
                return questions
            logger.warning(f"📋 [SAVOLLAR] JSON bo'sh yoki noto'g'ri struktura ({attempt}/2-urinish).")
        except Exception as e:
            logger.warning(f"📋 [SAVOLLAR] JSON parse xato ({attempt}/2-urinish): {e} — xom javob: {raw[:300]!r}")
    logger.error(f"📋 [SAVOLLAR] ❌ 2 marta urinishdan keyin ham to'g'ri JSON olinmadi ('{topic}').")
    return None
