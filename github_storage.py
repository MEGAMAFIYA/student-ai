"""
🖼 "Mening kabinetim" — foydalanuvchi rasmlarini GitHub repo'da saqlash.

Papka tuzilishi (repo ildizidan):
    {config.MENING_KABINETIM_DIR}/{user_id}/rasimlar/{uuid}.jpg

Bu modul FAQAT shu yo'l qurilishini bir joyda ushlab turadi — haqiqiy
GitHub so'rovlari config.py'dagi umumiy `github_upload_binary`/
`github_list_directory` orqali amalga oshadi (persist_read/write bilan
bir xil, allaqachon sinalgan HTTP mantiq).
"""

import logging

import config

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """GITHUB_TOKEN/GITHUB_REPO sozlanmagan bo'lsa, rasm yuklash funksiyasi
    butunlay o'chirilgan hisoblanadi (handlers/my_cabinet.py shu orqali
    foydalanuvchiga tushunarli xabar ko'rsatadi)."""
    return config.USE_GITHUB


def _user_photos_dir(user_id: int) -> str:
    return f"{config.MENING_KABINETIM_DIR}/{user_id}/rasimlar"


def upload_user_photo(user_id: int, image_bytes: bytes) -> tuple[str | None, str | None]:
    """Rasmni yuklaydi.

    Qaytaradi: (url, error_reason).
      - Muvaffaqiyatli bo'lsa: (raw_url, None)
      - Muvaffaqiyatsiz bo'lsa: (None, "<foydalanuvchiga ko'rsatsa bo'ladigan aniq sabab>")
    Xatoning to'liq tafsiloti (HTTP status, javob matni) config.py'da
    logger.error orqali allaqachon yozib qo'yiladi; bu yerda faqat
    yuqori darajadagi (user_id, path) konteksti qo'shib log qilinadi."""
    import uuid
    filename = f"{uuid.uuid4().hex}.jpg"
    path = f"{_user_photos_dir(user_id)}/{filename}"
    url, error = config.github_upload_binary(path, image_bytes, message=f"🖼 Rasm yuklandi: user_id={user_id}")
    if url:
        logger.info(f"🖼 Rasm GitHub'ga yuklandi: user_id={user_id}, path={path}.")
        return url, None
    logger.error(f"🖼 Rasm GitHub'ga YUKLANMADI: user_id={user_id}, path={path}, sabab: {error}.")
    return None, error


def list_user_photos(user_id: int) -> list[str]:
    """Foydalanuvchining barcha rasmlari (raw URL'lar ro'yxati). Hech qanday
    rasm yo'q bo'lsa yoki GitHub sozlanmagan bo'lsa — bo'sh ro'yxat."""
    return config.github_list_directory(_user_photos_dir(user_id))
 