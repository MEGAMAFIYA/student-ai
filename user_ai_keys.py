"""
🔑 Foydalanuvchining SHAXSIY AI kalitlari.

Format:
    {"<provider>": [{"key","model"}, ...], "al_yordamchi": {...}}
Old format (faqat provider kalitlari) ham backward-compatible.
`al_yordamchi` shu foydalanuvchining alohida AI yordamchi kaliti/modeli,
xotira DB ulanishi va yordamchi sozlamalarini saqlaydi.

FARQI: bular botning UMUMIY kalitlaridan (config.KEY_POOLS, /developer
orqali boshqariladi) MUSTAQIL — har bir foydalanuvchi FAQAT o'zining
kalitlarini ko'radi/boshqaradi (/my > 🔑 Shaxsiy kalitlarim), va ular
GitHub'da botning umumiy holatidan (GITHUB_DATA_DIR) ALOHIDA, shaxsiy
"mening kabinetim" papkasida saqlanadi:

    {MENING_KABINETIM_DIR}/{user_id}/mening_kalitlarim.json

Ishlatilish tartibi (ai_clients.ask_ai_with_source): pullik funksiya
chaqirilganda AVVAL shu yerdagi kalitlar sinaladi, ULAR ISHLAMASA
botning umumiy kalitiga (yoki zaxira provayderlarga) o'tiladi.
"""

import json
import logging

import config

logger = logging.getLogger(__name__)


def _path(user_id: int) -> str:
    return f"{config.MENING_KABINETIM_DIR}/{user_id}/mening_kalitlarim.json"


def _read_data(user_id: int) -> dict:
    """Shaxsiy faylning yangi/eskirgan formatini yagona ko'rinishga keltiradi."""
    raw = config.github_read_text_file(_path(user_id))
    if not raw:
        return {"keys": {}, "al_yordamchi": {}}
    try:
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"🔑 Foydalanuvchi kalitlarini o'qishda JSON xato (user_id={user_id}): {e}")
        return {"keys": {}, "al_yordamchi": {}}
    if not isinstance(data, dict):
        return {"keys": {}, "al_yordamchi": {}}
    if "keys" in data or "al_yordamchi" in data:
        keys = data.get("keys", {})
        assistant = data.get("al_yordamchi", {})
        return {
            "keys": keys if isinstance(keys, dict) else {},
            "al_yordamchi": assistant if isinstance(assistant, dict) else {},
        }
    # Eski format: {"gemini": [...], "groq": [...]}
    return {"keys": data, "al_yordamchi": {}}


def get_pools(user_id: int) -> dict:
    """{"<provider>": [{"key","model"}, ...]} — faqat oddiy shaxsiy AI kalitlari."""
    return _read_data(user_id)["keys"]


def get_assistant_config(user_id: int) -> dict:
    """Foydalanuvchining /AL YORDAMCHI uchun maxsus sozlamalari."""
    return dict(_read_data(user_id).get("al_yordamchi") or {})


def save_assistant_config(user_id: int, values: dict) -> bool:
    """AL YORDAMCHI konfiguratsiyasini mavjud shaxsiy kalitlarni buzmasdan saqlaydi."""
    data = _read_data(user_id)
    data["al_yordamchi"] = dict(values or {})
    return _save_all(user_id, data, f"🤖 AL YORDAMCHI sozlamalari yangilandi: user_id={user_id}")


def update_assistant_config(user_id: int, **updates) -> bool:
    data = _read_data(user_id)
    cfg = dict(data.get("al_yordamchi") or {})
    cfg.update(updates)
    return save_assistant_config(user_id, cfg)

def get_text_pools(user_id: int) -> dict:
    """Faqat MATN AI provayderlari (config.SUPPORTED_PROVIDERS) kalitlari —
    rasm provayderlari (bfl/stability) bu yerga kirmaydi, chunki ular matn
    so'rovlarida ishlatilmaydi."""
    return {p: pool for p, pool in get_pools(user_id).items() if p in config.SUPPORTED_PROVIDERS}


def get_image_pools(user_id: int) -> dict:
    """Faqat RASM provayderlari (config.IMAGE_PROVIDERS) kalitlari."""
    return {p: pool for p, pool in get_pools(user_id).items() if p in config.IMAGE_PROVIDERS and pool}


def has_any_keys(user_id: int) -> bool:
    """Foydalanuvchida MATN AI uchun shaxsiy kalit bormi (50% qaytarish
    mantig'i faqat matn kalitlariga bog'liq — rasm kalitlari buni o'zgartirmaydi)."""
    return any(pool for pool in get_text_pools(user_id).values())


def _save_all(user_id: int, data: dict, message: str) -> bool:
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    ok = config.github_write_text_file(_path(user_id), raw, message=message)
    if not ok:
        logger.error(f"🔑 Foydalanuvchi kalitlari GitHub'ga YOZILMADI (user_id={user_id}): {message}")
    return ok


def _save_pools(user_id: int, pools: dict, message: str) -> bool:
    """Oddiy shaxsiy kalitlarni saqlaydi va AL YORDAMCHI sozlamalarini asraydi."""
    data = _read_data(user_id)
    data["keys"] = pools
    return _save_all(user_id, data, message)

def add_key(user_id: int, provider: str, api_key: str, model: str) -> tuple[int, bool]:
    """Yangi kalitni MAVJUD to'plamga (boshqa provayderlar/kalitlar
    saqlanib qolgan holda) ro'yxat oxiriga qo'shadi.

    Qaytaradi: (yangi tartib raqami — 1 dan boshlab, GitHub'ga
    muvaffaqiyatli saqlandimi)."""
    message = f"🔑 Shaxsiy AI kaliti qo'shildi: user_id={user_id}, provider={provider}"
    last_index = 1
    for attempt in range(1, 4):
        pools = get_pools(user_id)
        pool = pools.setdefault(provider, [])
        pool.append({"key": api_key, "model": model})
        last_index = len(pool)
        if _save_pools(user_id, pools, message):
            return last_index, True
        logger.warning(
            f"🔑 Shaxsiy kalitni saqlash qayta uriniladi: user_id={user_id}, attempt={attempt}/3"
        )
    return last_index, False


def update_key_field(user_id: int, provider: str, index: int, field: str, value: str) -> bool:
    """`index` — 1 dan boshlanadi (foydalanuvchiga ko'rsatiladigan
    raqamlash bilan bir xil). Qaytaradi: MUVAFFAQIYATLI saqlandimi
    (index topilmasa yoki GitHub'ga yozish muvaffaqiyatsiz bo'lsa False)."""
    pools = get_pools(user_id)
    pool = pools.get(provider, [])
    if not (1 <= index <= len(pool)):
        return False
    pool[index - 1][field] = value
    return _save_pools(user_id, pools, f"🔑 Shaxsiy AI kaliti yangilandi: user_id={user_id}, provider={provider}#{index}")


def delete_key(user_id: int, provider: str, index: int) -> bool:
    """Qaytaradi: MUVAFFAQIYATLI o'chirilib, saqlandimi."""
    pools = get_pools(user_id)
    pool = pools.get(provider, [])
    if not (1 <= index <= len(pool)):
        return False
    pool.pop(index - 1)
    return _save_pools(user_id, pools, f"🔑 Shaxsiy AI kaliti o'chirildi: user_id={user_id}, provider={provider}#{index}")


def get_key(user_id: int, provider: str, index: int) -> dict | None:
    pool = get_pools(user_id).get(provider, [])
    if not (1 <= index <= len(pool)):
        return None
    return pool[index - 1]
