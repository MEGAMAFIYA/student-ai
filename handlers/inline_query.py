"""
🔍 INLINE REJIM — foydalanuvchi istalgan chatda (hatto botni a'zo qilmasdan,
hatto ikki oddiy foydalanuvchi orasidagi shaxsiy chatda ham) "@BotUsername
savol" deb yozib, botning javobini o'sha chatga to'g'ridan-to'g'ri yuborishi
uchun handlerlar.

ISHLASH TARTIBI (Telegram inline rejimining o'zi shunday ishlaydi):
1. Foydalanuvchi istalgan chatda "@Student_ai_uz_bot savol" deb yozadi ->
   `on_inline_query` ishga tushadi. Bu bosqichda javob JUDA TEZ (bir necha
   yuz millisoniya ichida) qaytishi kerak, shuning uchun bu yerda AI'ga
   murojaat QILINMAYDI — faqat bitta "natija kartochkasi" tayyorlanadi.
2. Foydalanuvchi shu kartochkani bosib tanlaydi -> Telegram placeholder
   xabarni ("⏳ Javob tayyorlanmoqda...") o'sha chatga DARHOL joylab
   qo'yadi (xabar jo'natuvchi sifatida so'ragan foydalanuvchi ko'rsatiladi,
   pastida kichik "orqali @Student_ai_uz_bot" yozuvi chiqadi) va botga
   `on_chosen_inline_result` update'ini yuboradi.
3. Shu yerda AI'dan haqiqiy javob so'raladi (bu bir necha soniya olishi
   mumkin — endi bu muammo emas, chunki xabar allaqachon chatda ko'rinib
   turibdi) va u `edit_message_text` orqali placeholder o'rniga yoziladi.

MUHIM SOZLASH (BotFather orqali, kod bilan bog'liq emas):
  /setinline          -> inline rejimni yoqish (placeholder matn so'raladi)
  /setinlinefeedback   -> "Enabled" qilib qo'yish SHART, aks holda 2-3
                          bosqich (chosen_inline_result) ishlamaydi.

CHEKLOV: bu rejim faqat QISQA, TEZKOR matnli javoblar uchun mos (masalan
shu fayldagi misol: "eng katta ko'prik qayerda joylashgan"). Kurs ishi/PDF
generatsiyasi kabi og'ir vazifalar bu yerda ISHLAMAYDI — ular hali ham
botning shaxsiy chatida yoki guruhda (bot a'zo bo'lgan holda) so'ralishi
kerak, shuning uchun bunday so'rovlar aniqlansa foydalanuvchiga botning
shaxsiy chatiga o'tish tugmasi taklif qilinadi.
"""

import logging
import re
import uuid

from telegram import (
    Update,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import UNIVERSAL_CHAT_AI
from ai_clients import ask_ai

logger = logging.getLogger(__name__)

PLACEHOLDER_TEXT = "⏳ Javob tayyorlanmoqda..."
BOT_USERNAME = "Student_ai_uz_bot"  # faqat imzo uchun, agar username o'zgarsa shu yerni yangilang

# Foydalanuvchi so'ragan savol/matnni result_id bo'yicha eslab qolish uchun
# (chunki chosen_inline_result'da to'liq matn har doim kelavermaydi).
_MAX_CACHE = 500

# Savol joy/manzil haqida ekanligini aniqlash uchun kalit so'zlar — mos
# kelsa, AI'dan koordinata ham so'raladi va javobga Google Maps havolasi
# qo'shiladi.
LOCATION_HINTS = re.compile(
    r"joylash|qayerda|lokatsiy|manzil|xarita|koordinat", re.IGNORECASE
)
LOC_TAG_RE = re.compile(r"\[LOC:\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]")

# Og'ir vazifalarni (kurs ishi, tarjima fayli va h.k.) inline rejimda ushlab,
# shaxsiy chatga yo'naltirish uchun kalit so'zlar.
HEAVY_TASK_HINTS = re.compile(
    r"kurs ishi|kurs loyihasi|diplom ishi|pdf|hujjatni tuzat|suratlarni.*pdf",
    re.IGNORECASE,
)


async def on_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """1-bosqich: TEZKOR javob — AI so'ralmaydi, faqat kartochka tayyorlanadi."""
    query = update.inline_query.query.strip()
    if not query:
        return  # foydalanuvchi hali hech narsa yozmagan

    if HEAVY_TASK_HINTS.search(query):
        results = [
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="📄 Bu og'ir vazifa — botning shaxsiy chatida bajariladi",
                description="Bosing va botga o'ting, u yerda davom etadi",
                input_message_content=InputTextMessageContent(
                    f"📄 \"{query}\" — bu vazifani bajarish uchun botning shaxsiy "
                    f"chatiga o'ting: https://t.me/{BOT_USERNAME}?start=inline"
                ),
            )
        ]
        await update.inline_query.answer(results, cache_time=0, is_personal=True)
        return

    result_id = str(uuid.uuid4())
    cache = context.bot_data.setdefault("inline_queries", {})
    cache[result_id] = query
    if len(cache) > _MAX_CACHE:  # eski so'rovlarni tozalash (xotira toshib ketmasligi uchun)
        for old_key in list(cache.keys())[: len(cache) - _MAX_CACHE]:
            cache.pop(old_key, None)

    results = [
        InlineQueryResultArticle(
            id=result_id,
            title="💬 Talaba AI javobi",
            description=query[:120],
            input_message_content=InputTextMessageContent(PLACEHOLDER_TEXT),
        )
    ]
    await update.inline_query.answer(results, cache_time=0, is_personal=True)


async def on_chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """2-bosqich: foydalanuvchi kartochkani tanlagach ishga tushadi — endi AI'dan
    haqiqiy javob so'raymiz va placeholder xabarni shu javob bilan almashtiramiz."""
    chosen = update.chosen_inline_result
    inline_message_id = chosen.inline_message_id
    if not inline_message_id:
        # BotFaterda /setinlinefeedback yoqilmagan bo'lishi mumkin — bu holda
        # xabarni keyinroq tahrirlab bo'lmaydi.
        logger.warning(
            "chosen_inline_result keldi, lekin inline_message_id yo'q — "
            "BotFather'da /setinlinefeedback 'Enabled' qilinganini tekshiring."
        )
        return

    cache = context.bot_data.get("inline_queries", {})
    query = cache.pop(chosen.result_id, None) or chosen.query
    if not query:
        return

    wants_location = bool(LOCATION_HINTS.search(query))

    system = (
        "Siz do'stona va bilimdon AI yordamchisiz (ismingiz — Dase). O'zbek "
        "tilida (agar savol boshqa tilda bo'lmasa), aniq va Telegram xabari "
        "uchun mos qisqalikda (taxminan 3-8 gap) javob bering."
    )
    if wants_location:
        system += (
            " Savolda aniq geografik joy so'ralgan bo'lsa, javob oxiriga ANIQ "
            "shu formatda koordinata qo'shing: [LOC: kenglik,uzunlik] "
            "(masalan [LOC: 41.311081,69.240562]). Bu texnik belgi — uni matn "
            "ichida boshqa joyda ishlatmang, faqat eng oxiriga bir marta qo'shing."
        )

    answer = await ask_ai(UNIVERSAL_CHAT_AI, query, system)

    if not answer:
        try:
            await context.bot.edit_message_text(
                inline_message_id=inline_message_id,
                text="❌ Javob berib bo'lmadi. Botning shaxsiy chatida qayta urinib ko'ring: "
                f"https://t.me/{BOT_USERNAME}",
            )
        except Exception as e:
            logger.error(f"Inline xabarni tahrirlashda xato: {e}")
        return

    maps_line = ""
    m = LOC_TAG_RE.search(answer)
    if m:
        lat, lon = m.group(1), m.group(2)
        answer = LOC_TAG_RE.sub("", answer).strip()
        maps_line = f"\n\n📍 [Xaritada ko'rish](https://www.google.com/maps?q={lat},{lon})"

    final_text = f"{answer}{maps_line}\n\n🤖 _Talaba AI — @{BOT_USERNAME}_"

    try:
        await context.bot.edit_message_text(
            inline_message_id=inline_message_id,
            text=final_text,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Inline xabarni tahrirlashda xato (Markdown bilan): {e}")
        try:
            # Markdown formatlash sabab xato bo'lgan bo'lishi mumkin — oddiy matn bilan qayta urinamiz
            await context.bot.edit_message_text(inline_message_id=inline_message_id, text=answer)
        except Exception as e2:
            logger.error(f"Inline xabarni tahrirlashda takroriy xato: {e2}")
