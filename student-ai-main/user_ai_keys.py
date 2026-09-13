"""
🔑 Foydalanuvchining SHAXSIY AI kalitlari.

Format config.KEY_POOLS bilan BIR XIL: {"<provider>": [{"key","model"}, ...]}
— shuning uchun ai_clients.py'dagi mavjud _dispatch()/provider mantig'ini
qayta ishlatish mumkin (duplicate yo'q).

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


def get_pools(user_id: int) -> dict:
    """{"<provider>": [{"key","model"}, ...]} — hech narsa bo'lmasa yoki
    GitHub sozlanmagan bo'lsa bo'sh lug'at."""
    raw = config.github_read_text_file(_path(user_id))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"🔑 Foydalanuvchi kalitlarini o'qishda JSON xato (user_id={user_id}): {e}")
        return {}


def has_any_keys(user_id: int) -> bool:
    return any(pool for pool in get_pools(user_id).values())


def _save_pools(user_id: int, pools: dict, message: str) -> bool:
    """GitHub'ga yozadi va MUVAFFAQIYAT/MUVAFFAQIYATSIZLIKNI qaytaradi —
    chaqiruvchi funksiyalar (add_key/update_key_field/delete_key) shu
    natijani albatta tekshirib, foydalanuvchiga FAQAT haqiqatda saqlangan
    bo'lsa muvaffaqiyat xabarini ko'rsatishi kerak (qarang:
    handlers/my_cabinet.py)."""
    raw = json.dumps(pools, ensure_ascii=False, indent=2)
    ok = config.github_write_text_file(_path(user_id), raw, message=message)
    if not ok:
        logger.error(f"🔑 Foydalanuvchi kalitlari GitHub'ga YOZILMADI (user_id={user_id}): {message}")
    return ok


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
