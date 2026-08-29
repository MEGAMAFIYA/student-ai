"""
🗒 Referat / Insho tayyorlash — kurs ishidan farqli, KICHIKROQ va SODDAROQ
struktura: KIRISH, ASOSIY QISM (2-4 kichik bo'lim), XULOSA, ADABIYOTLAR.
Mundarija (TOC) va 3-bob tuzilishi YO'Q — referat/insho odatda shunday
yoziladi. Generatsiya mantig'i (qayta urinish, to'liqlik tekshiruvi, PDF
sahifa sonini yetkazish) kurs ishi bilan bir xil sifat darajasida.
"""

import asyncio
import json
import logging
import re

from telegram import Update, InputFile
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode, ChatAction

from config import ESSAY_AI
from ai_clients import ask_ai
from pdf_tools import build_essay_pdf, count_pdf_pages
from handlers.menu import main_menu_keyboard
from handlers.course_work import clean_topic, _is_refusal, _clean_ai_text, _strip_duplicate_heading
from handlers import wallet_ui
import storage

logger = logging.getLogger(__name__)

ES_TYPE, ES_PAGES, ES_TOPIC = range(3)

WORDS_PER_PAGE = 380
MAX_PAGES = 40
MIN_ACCEPTABLE_WORDS = 50
MIN_PART_WORDS = 40
RETRY_ATTEMPTS = 2
RETRY_DELAY_SEC = 3
MAX_EXPAND_ROUNDS = 10
OVERALL_TIMEOUT_SEC = 12 * 60

_WORK_TYPES = {"referat": "REFERAT", "insho": "INSHO"}

_SYSTEM_TEMPLATE = (
    "Siz tajribali o'qituvchi va ilmiy muharrirsiz. Faqat '{topic}' mavzusi doirasida "
    "yozing, undan chetga chiqmang. FAQAT toza, adabiy o'zbek tilida yozing — boshqa "
    "tildan bitta so'z ham aralashtirmang. Ilmiy-ommabop uslubda yozing. Faqat so'ralgan "
    "bo'lim matnini yozing, sarlavha yoki qo'shimcha izoh qo'shmang. Hech qanday Markdown "
    "(**, ##, `, -) yoki LaTeX belgisi ishlatmang, oddiy matn abzaslari yozing. Bir xil "
    "fikrni qayta-qayta takrorlamang — har bir abzas yangi, aniq ma'lumot olib kelsin."
)


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"🗒 'Referat/Insho' tugmasi bosildi: user_id={user.id if user else '?'}.")
    try:
        await query.answer()
        context.user_data.clear()
        context.user_data["flow"] = "essay"
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📄 Referat", callback_data="essay:type:referat"),
            InlineKeyboardButton("✍️ Insho", callback_data="essay:type:insho"),
        ]])
        await query.edit_message_text("🗒 *Referat / Insho tayyorlash*\n\nQaysi turini xohlaysiz?", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    except Exception as e:
        logger.error(f"🗒 Referat/Insho menyusini ochishda xato (user_id={user.id if user else '?'}): {type(e).__name__}: {e}", exc_info=True)
        raise
    return ES_TYPE


async def receive_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    work_type = query.data.split(":")[-1]
    context.user_data["es_type"] = work_type
    label = "Referat" if work_type == "referat" else "Insho"
    await query.edit_message_text(
        f"✅ {label}.\n\nPDF necha betdan iborat bo'lishi kerak? (masalan: 5, 10, 15)",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ES_PAGES


async def receive_pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    digits = re.sub(r"[^0-9]", "", update.message.text.strip())
    if not digits or int(digits) <= 0:
        await update.message.reply_text("❗️ Iltimos, faqat son yuboring. Masalan: 10")
        return ES_PAGES
    pages = int(digits)
    if pages > MAX_PAGES:
        await update.message.reply_text(f"❗️ {MAX_PAGES} betdan katta bo'lmasin. Iltimos, kichikroq son kiriting.")
        return ES_PAGES
    context.user_data["es_pages"] = pages
    await update.message.reply_text(f"✅ {pages} bet.\n\nEndi mavzuni yuboring:", parse_mode=ParseMode.MARKDOWN)
    return ES_TOPIC


async def receive_topic_and_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = clean_topic(update.message.text.strip())
    pages = context.user_data.get("es_pages", 10)
    work_type = context.user_data.get("es_type", "referat")
    label = "Referat" if work_type == "referat" else "Insho"
    user_id = update.effective_user.id if update.effective_user else 0
    logger.info(f"🗒 {label} so'rovi: user_id={user_id}, mavzu='{topic}', bet={pages}.")

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    status = await update.message.reply_text(
        f"⏳ *{topic}* mavzusida {pages}+ betlik {label.lower()} tayyorlanmoqda...", parse_mode=ParseMode.MARKDOWN
    )

    try:
        result = await asyncio.wait_for(generate_essay(topic, pages, work_type, status), timeout=OVERALL_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        logger.error(f"🗒 {label} generatsiyasi {OVERALL_TIMEOUT_SEC}s ichida tugamadi ('{topic}').")
        result = None
    except Exception as e:
        logger.error(f"🗒 {label} generatsiyasida kutilmagan xato ('{topic}'): {type(e).__name__}: {e}", exc_info=True)
        result = None

    if not result:
        logger.error(f"🗒 {label} YAKUNLANMADI ('{topic}') — sababi yuqoridagi loglarda.")
        await status.edit_text("❌ Yaratib bo'lmadi — AI xizmatlari hozir javob bermayapti. Birozdan so'ng qayta urinib ko'ring.")
        # 💰 Band qilingan summa ozod qilinadi (hech qachon yechilmagan edi).
        await wallet_ui.finalize_failure(context, update=update, reason="essay_generation_failed")
        context.user_data.clear()
        return ConversationHandler.END

    sections, pdf_buf, actual_pages = result
    logger.info(f"🗒 {label} muvaffaqiyatli yakunlandi: '{topic}', {actual_pages} bet.")

    msg = await update.message.reply_document(
        document=InputFile(pdf_buf, filename=f"{topic[:40]}.pdf"),
        caption=f"🗒 {label}: {topic}\n📎 {actual_pages} bet (so'ralgan: {pages}+).",
        reply_markup=main_menu_keyboard(),
    )
    if user_id and msg.document:
        storage.record_file(user_id, "essay", topic, msg.document.file_id)
        storage.record_usage("essay", user_id)

    try:
        await status.delete()
    except Exception:
        pass

    # 💰 Xizmat MUVAFFAQIYATLI yakunlandi — band qilingan summa endi
    # HAQIQATAN balansdan yechiladi (avval emas!).
    await wallet_ui.finalize_success(context, update=update)

    context.user_data.clear()
    return ConversationHandler.END


async def _ask_retry(prompt: str, system: str, tag: str = "", raw: bool = False) -> str | None:
    label = tag or "so'rov"
    for i in range(1, RETRY_ATTEMPTS + 1):
        logger.info(f"🗒 [{label}] AI ga so'rov yuborilmoqda ({i}/{RETRY_ATTEMPTS}-urinish, provider={ESSAY_AI.get('provider')})...")
        try:
            result = await ask_ai(ESSAY_AI, prompt, system)
        except Exception as e:
            logger.error(f"🗒 [{label}] ask_ai xato ({i}/{RETRY_ATTEMPTS}): {e}", exc_info=True)
            result = None
        if result and result.strip() and not _is_refusal(result):
            logger.info(f"🗒 [{label}] ✅ Javob qabul qilindi ({len(result.split())} so'z).")
            return result if raw else _clean_ai_text(result)
        logger.warning(f"🗒 [{label}] ⚠️ Bo'sh/rad javob ({i}/{RETRY_ATTEMPTS}-urinish).")
        if i < RETRY_ATTEMPTS:
            await asyncio.sleep(RETRY_DELAY_SEC)
    logger.error(f"🗒 [{label}] ❌ Javob olinmadi.")
    return None


async def _generate_plan(topic: str, num_parts: int) -> list[str]:
    system = "Siz ilmiy-uslubiy rejalashtiruvchisiz. Faqat JSON qaytaring."
    prompt = (
        f"'{topic}' mavzusida referat/insho uchun {num_parts} ta asosiy qism (kichik bo'lim) "
        "nomini tuz — har biri qisqa va aniq (bir jumla). "
        'Javobni FAQAT {"qismlar": ["...", "...", ...]} JSON formatida qaytar.'
    )
    raw = await _ask_retry(prompt, system, tag="REJA", raw=True)
    default = [f"{topic} — {i + 1}-jihat" for i in range(num_parts)]
    if not raw:
        return default
    try:
        cleaned = re.sub(r"^```json\s*|^```\s*|```\s*$", "", raw.strip(), flags=re.MULTILINE).strip()
        data = json.loads(cleaned)
        parts = [str(x).strip() for x in data.get("qismlar", []) if str(x).strip()]
        return parts[:num_parts] if parts else default
    except Exception as e:
        logger.warning(f"🗒 [REJA] JSON parse xato: {e} — standart reja ishlatiladi.")
        return default


async def _generate_references(topic: str) -> str:
    system = (
        "Siz ilmiy adabiyotlar ro'yxati tuzuvchisiz. Faqat ro'yxatni qaytaring. "
        "Tekshirib bo'lmaydigan aniq URL yoki 'kirilgan sana' o'ylab topib yozmang."
    )
    prompt = (
        f"'{topic}' mavzusidagi referat/insho uchun kamida 10 ta yozuvdan iborat foydalanilgan "
        "adabiyotlar ro'yxatini tuz (darsliklar, ilmiy maqolalar, rasmiy manbalar), alifbo "
        "tartibida, raqamlangan. Faqat ro'yxatni yoz."
    )
    result = await _ask_retry(prompt, system, tag="ADABIYOTLAR", raw=True)
    return result or ""


async def generate_essay(topic: str, pages: int, work_type: str, status_msg=None):
    label = "Referat" if work_type == "referat" else "Insho"
    target_words = int(pages * WORDS_PER_PAGE * 1.1)
    num_parts = max(2, min(5, round(pages / 4)))
    logger.info(f"===== [{label.upper()} BOSHLANDI] mavzu='{topic}', bet={pages}, qismlar={num_parts} =====")

    if status_msg:
        try:
            await status_msg.edit_text(f"⏳ *{topic}*\nReja tuzilmoqda...", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    part_titles = await _generate_plan(topic, num_parts)

    kirish_words = int(target_words * 0.15)
    part_words = int(target_words * 0.6 / num_parts)
    xulosa_words = int(target_words * 0.15)

    kirish = await _generate_section(topic, "KIRISH", f"'{topic}' mavzusiga kirish so'zi yoz.", kirish_words)
    asosiy_qism = []
    for i, title in enumerate(part_titles, start=1):
        if status_msg:
            try:
                await status_msg.edit_text(f"⏳ *{topic}*\n{i}/{len(part_titles)}-qism yozilmoqda: {title}", parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
        content = await _generate_section(topic, title, f"'{title}' haqida batafsil yoz (mavzu: '{topic}').", part_words)
        asosiy_qism.append({"title": title, "content": content or ""})

    xulosa = await _generate_section(topic, "XULOSA", f"'{topic}' mavzusi bo'yicha xulosa yoz.", xulosa_words)
    adabiyotlar = await _generate_references(topic)

    # ===== TO'LIQLIK TEKSHIRUVI (nazoratchi) — bo'sh chiqqan qismlarni qayta yozdiradi =====
    for round_i in range(1, 3):
        problems = []
        if len(kirish.split()) < MIN_ACCEPTABLE_WORDS:
            problems.append("kirish")
        for i, p in enumerate(asosiy_qism):
            if len(p["content"].split()) < MIN_PART_WORDS:
                problems.append(f"qism{i}")
        if len(xulosa.split()) < MIN_ACCEPTABLE_WORDS:
            problems.append("xulosa")
        if not problems:
            break
        logger.warning(f"🗒 [NAZORATCHI] {round_i}-tekshiruv: {len(problems)} ta bo'sh/qisqa bo'lim topildi, qayta yozdirilmoqda: {problems}")
        for p in problems:
            if p == "kirish":
                new_val = await _generate_section(topic, "KIRISH", f"'{topic}' mavzusiga kirish so'zi yoz.", kirish_words)
                if new_val:
                    kirish = new_val
            elif p == "xulosa":
                new_val = await _generate_section(topic, "XULOSA", f"'{topic}' mavzusi bo'yicha xulosa yoz.", xulosa_words)
                if new_val:
                    xulosa = new_val
            elif p.startswith("qism"):
                idx = int(p[4:])
                title = asosiy_qism[idx]["title"]
                new_val = await _generate_section(topic, title, f"'{title}' haqida batafsil yoz (mavzu: '{topic}').", part_words)
                if new_val:
                    asosiy_qism[idx]["content"] = new_val

    sections = {"kirish": kirish or "", "asosiy_qism": asosiy_qism, "xulosa": xulosa or "", "adabiyotlar": adabiyotlar}

    if status_msg:
        try:
            await status_msg.edit_text(f"⏳ *{topic}*\nPDF yig'ilmoqda...", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    work_label = _WORK_TYPES.get(work_type, "REFERAT")
    pdf_buf = await asyncio.to_thread(build_essay_pdf, topic, sections, work_label)
    actual_pages = await asyncio.to_thread(count_pdf_pages, pdf_buf)
    logger.info(f"🗒 [PDF] Birinchi versiya: {actual_pages} bet (so'ralgan: {pages}).")

    # ===== PDF SAHIFASINI YETKAZISH — eng qisqa qismni kengaytirish =====
    rounds = 0
    while actual_pages < pages and rounds < MAX_EXPAND_ROUNDS:
        rounds += 1
        if not asosiy_qism:
            break
        shortest = min(asosiy_qism, key=lambda p: len(p["content"].split()))
        logger.info(f"🗒 [PDF-KENGAYTIRISH {rounds}] Eng qisqa qism: '{shortest['title']}'.")
        addition = await _generate_section(
            topic, shortest["title"],
            f"'{shortest['title']}' mavzusini ({topic} doirasida) YANADA CHUQURROQ, yangi "
            "misol/dalillar bilan davom ettirib yoz (avvalgi matn bilan takrorlanmasin).",
            300,
        )
        if not addition:
            logger.warning(f"🗒 [PDF-KENGAYTIRISH {rounds}] Javob olinmadi — kengaytirish {actual_pages} betda to'xtatildi.")
            break
        shortest["content"] = (shortest["content"] + "\n\n" + addition).strip()
        pdf_buf = await asyncio.to_thread(build_essay_pdf, topic, sections, work_label)
        actual_pages = await asyncio.to_thread(count_pdf_pages, pdf_buf)
        logger.info(f"🗒 [PDF-KENGAYTIRISH {rounds}] Yangi hajm: {actual_pages}/{pages} bet.")

    logger.info(f"===== [{label.upper()} TUGADI] mavzu='{topic}', yakuniy hajm={actual_pages} bet =====")
    return sections, pdf_buf, actual_pages


async def _generate_section(topic: str, section_label: str, instruction: str, target_words: int) -> str | None:
    prompt = f"[{section_label}]\n{instruction}\n\nTaxminan {max(target_words, 100)} so'zdan iborat bo'lsin."
    result = await _ask_retry(prompt, _SYSTEM_TEMPLATE.format(topic=topic), tag=section_label)
    return _strip_duplicate_heading(result, section_label) if result else result
