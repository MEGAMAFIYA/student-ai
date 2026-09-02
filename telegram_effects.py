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
    try:
        with open(EFFECTS_JSON_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.error(f"✨ EFFECT_MAP_PARSE_ERROR path={EFFECTS_JSON_PATH} error_type={type(e).__name__} error={e}")
        return {}

    effects = raw.get("effects") if isinstance(raw, dict) else None
    if not isinstance(effects, list):
        logger.error("✨ EFFECT_MAP_MALFORMED — 'effects' massiv emas yoki topilmadi.")
        return {}

    mapping: dict[str, str] = {}
    total = 0
    for item in effects:
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

    logger.info(f"✨ EFFECT_MAP_LOADED path={EFFECTS_JSON_PATH} total_effects={total} unique_emojis={len(mapping)}")
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
        logger.warning(f"✨ EFFECT_NOT_FOUND emoji={emoji!r} — effektsiz yuboriladi.")
    return effect_id


def reload() -> None:
    """Testlar yoki fayl ENV orqali almashtirilganda xaritani qayta yuklaydi."""
    global _EFFECT_MAP
    _EFFECT_MAP = _load_map()
