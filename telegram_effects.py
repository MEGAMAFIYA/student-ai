"""
✨ Telegram Message Effect ID'lari — `data/telegram_message_effects.json`
(manba: MTProto `messages.getAvailableEffects`, /tabrik uchun uploaded fayl)
dan yuklanadi. Effect ID'lar HECH QACHON kodda qo'lda/taxmin bilan yozilmaydi —
faqat shu faylning o'zidan.

Bir emoji uchun faylda bir nechta effect_id bo'lishi mumkin (masalan 😍 uchun
o'nlab yozuv bor — turli sticker/animatsiya variantlari). Biz ENG BIRINCHI
(fayldagi tartib bo'yicha) mosini olamiz — bu odatda eng "asosiy"/eng ko'p
ishlatiladigan variant (fayl sarlavhasidagi eng yuqori qatorlar asosiy
reaction-effect'lar bilan mos keladi).

Fayl topilmasa yoki buzilgan bo'lsa — bot CRASH bo'lmasin: bo'sh xarita bilan
ishga tushadi, har bir so'rov uchun EFFECT_MAP_EMPTY/EFFECT_NOT_FOUND logi
yoziladi va chaqiruvchi effektsiz (message_effect_id=None) davom etadi.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "telegram_message_effects.json")
EFFECTS_JSON_PATH = os.getenv("TABRIK_EFFECTS_JSON_PATH", _DEFAULT_PATH)


def _load_map() -> dict:
    if not os.path.exists(EFFECTS_JSON_PATH):
        logger.error(f"✨ EFFECT_MAP_FILE_NOT_FOUND path={EFFECTS_JSON_PATH} — effektlar o'chirilgan holda ishlaydi.")
        return {}

    with open(EFFECTS_JSON_PATH, "r", encoding="utf-8") as f:
        text = f.read()

    # 🩹 Manba fayl ba'zan bitta yaxlit JSON emas, balki bir nechta
    # "qism" (part) hujjat KETMA-KET yozilgan holda keladi (masalan
    # Telegram API'dan 6 ta bo'lak qilib olib, ularni birlashtirmasdan
    # bitta faylga ulab qo'yish natijasi). Oddiy `json.load()` bunday
    # holatda "Extra data" xatosi bilan yiqiladi va xarita BUTUNLAY
    # bo'sh qolib ketadi (=> hamma emoji uchun EFFECT_NOT_FOUND, aslida
    # sabab boshqa edi). Shuning uchun faylni bitta emas, ketma-ket
    # kelgan BARCHA JSON hujjatlarni o'qib, ularning "effects"
    # ro'yxatlarini birlashtiramiz.
    decoder = json.JSONDecoder()
    docs: list[dict] = []
    pos, n = 0, len(text)
    try:
        while pos < n:
            while pos < n and text[pos] in " \n\t\r":
                pos += 1
            if pos >= n:
                break
            obj, end = decoder.raw_decode(text, pos)
            docs.append(obj)
            pos = end
    except Exception as e:
        logger.error(f"✨ EFFECT_MAP_PARSE_ERROR path={EFFECTS_JSON_PATH} error_type={type(e).__name__} error={e}")
        return {}

    if not docs:
        logger.error(f"✨ EFFECT_MAP_EMPTY_FILE path={EFFECTS_JSON_PATH} — faylda birorta ham JSON hujjat topilmadi.")
        return {}

    if len(docs) > 1:
        logger.info(f"✨ EFFECT_MAP_MULTI_PART_FILE path={EFFECTS_JSON_PATH} parts={len(docs)} — barchasi birlashtirilmoqda.")

    all_effects = []
    for doc in docs:
        effects = doc.get("effects") if isinstance(doc, dict) else None
        if isinstance(effects, list):
            all_effects.extend(effects)

    if not all_effects:
        logger.error("✨ EFFECT_MAP_MALFORMED — birorta hujjatda ham to'g'ri 'effects' massivi topilmadi.")
        return {}

    mapping: dict[str, str] = {}
    total = 0
    for item in all_effects:
        try:
            emoji = item.get("emoji")
            effect_id = item.get("effect_id")
        except AttributeError:
            continue
        if not emoji or not effect_id or effect_id == "None":
            continue
        total += 1
        if emoji not in mapping:  # birinchi (asosiy) variantni saqlaymiz
            mapping[emoji] = str(effect_id)

    logger.info(f"✨ EFFECT_MAP_LOADED path={EFFECTS_JSON_PATH} parts={len(docs)} total_effects={total} unique_emojis={len(mapping)}")
    return mapping


_EFFECT_MAP = _load_map()


def get_effect_id(emoji: str) -> str | None:
    """Berilgan emoji uchun message_effect_id qaytaradi, topilmasa None
    (chaqiruvchi shunda effektsiz yuborishga o'tishi kerak).

    MUHIM (real testda topilgan nozik joy): `data/telegram_message_effects.json`
    faylida ba'zi emojilar VARIATION SELECTOR'siz saqlangan (masalan "❤"
    U+2764, "\u2764" — VS16 "\uFE0F" QO'SHILMAGAN holda), lekin bizning
    DEFAULT_EMOJIS ro'yxatimizdagi "❤️" odatiy Unicode konvensiyasiga ko'ra
    VS16 bilan ("\u2764\uFE0F") yozilgan — bittasi "..." ikkinchisi
    "❤️" kabi ko'rinsa ham, satr sifatida TENG EMAS. Shuning uchun avval
    ANIQ (exact) moslikni qidiramiz, topilmasa VS16'ni olib tashlab yana
    bir bor qidiramiz — lekin natijada qaytariladigan effect_id baribir
    fayldagi ANIQ shu emojiga tegishli (taxmin qilinmagan)."""
    effect_id = _EFFECT_MAP.get(emoji)
    if effect_id is None and "\uFE0F" in emoji:
        stripped = emoji.replace("\uFE0F", "")
        effect_id = _EFFECT_MAP.get(stripped)
        if effect_id is not None:
            logger.info(f"✨ EFFECT_FOUND_VIA_VS16_STRIP emoji={emoji!r} matched={stripped!r}")
    if effect_id is None:
        if not _EFFECT_MAP:
            logger.warning(
                f"✨ EFFECT_NOT_FOUND emoji={emoji!r} — SABAB: xarita BUTUNLAY BO'SH "
                f"(EFFECT_MAP_LOADED/EFFECT_MAP_FILE_NOT_FOUND/EFFECT_MAP_PARSE_ERROR "
                f"loglariga qarang — fayl topilmagan yoki o'qilmagan)."
            )
        else:
            logger.warning(
                f"✨ EFFECT_NOT_FOUND emoji={emoji!r} — SABAB: xarita {len(_EFFECT_MAP)} ta "
                f"emoji bilan yuklangan, lekin ORASIDA aynan shu emoji yo'q "
                f"(mavjud namunalar: {list(_EFFECT_MAP.keys())[:10]}...)."
            )
    return effect_id


def reload() -> None:
    """Testlar yoki fayl ENV orqali almashtirilganda xaritani qayta yuklaydi."""
    global _EFFECT_MAP
    _EFFECT_MAP = _load_map()
