"""
🎨 AI rasm chizdirish — kurs ishi, universal chat va inline uchun UMUMIY mantiq.

* Narxlar wallet "features" ichida saqlanadi (admin: /developer > Moliya >
  ⚙️ Funksiya narxlari): `ai_image` (1 ta rasm, standart 3000) va
  `course_work_20/30/40/50` (kurs ishi, bet soniga qarab).
* Pul AVVAL band qilinadi (reservation), rasm HAQIQATAN chizilgandan keyingina
  yechiladi; chizilmasa band bekor qilinadi.
"""

import asyncio
import logging

import config
import wallet
import image_clients
import course_images

logger = logging.getLogger(__name__)

COURSE_PAGE_OPTIONS = (20, 30, 40, 50)
COURSE_IMAGE_CHOICES = (0, 1, 2, 3)   # 0 = o'chiq
IMAGE_FEATURE_ID = "ai_image"
RESERVATION_TTL_SEC = 60 * 60         # kurs ishi uzoq davom etishi mumkin

_PROMPT_SYSTEM = (
    "You write prompts for an AI image generator. The user describes a picture in any "
    "language (usually Uzbek). Reply with ONE detailed English prompt (max 80 words) "
    "describing the scene, objects, style and lighting. No text/letters/numbers in the "
    "picture. Reply with the prompt only."
)


def fmt_sum(amount: int) -> str:
    return f"{int(amount):,}".replace(",", " ") + " so'm"


def course_feature_id(pages: int) -> str:
    return f"course_work_{int(pages)}"


def _price_of(feature_id: str) -> int:
    f = wallet.get_feature(feature_id)
    return int(f.get("price", 0)) if f else 0


def image_price() -> int:
    """Bitta rasm narxi (admin o'zgartiradi, standart 3000)."""
    return _price_of(IMAGE_FEATURE_ID)


def course_pages_price(pages: int) -> int:
    return _price_of(course_feature_id(pages))


def course_total(pages: int, images: int) -> int:
    return course_pages_price(pages) + image_price() * max(0, int(images))


def feature_enabled(feature_id: str) -> bool:
    f = wallet.get_feature(feature_id)
    return bool(f.get("enabled", True)) if f else True


class Hold:
    """reserve() natijasi. status: reserved | free | insufficient | disabled."""
    def __init__(self, status, amount=0, reservation_id=None, available=0):
        self.status = status
        self.amount = amount
        self.reservation_id = reservation_id
        self.available = available

    @property
    def ok(self):
        return self.status in ("reserved", "free")


def reserve(user_id: int, feature_id: str, amount: int) -> Hold:
    """`amount` so'mni (hali yechmasdan) band qiladi."""
    if not feature_enabled(feature_id):
        return Hold("disabled", amount)
    if amount <= 0:
        return Hold("free", 0)
    try:
        r = wallet.reserve_balance(user_id, feature_id, amount, ttl_seconds=RESERVATION_TTL_SEC)
    except wallet.InsufficientBalanceError as e:
        return Hold("insufficient", amount, available=e.available)
    return Hold("reserved", amount, reservation_id=r["reservation_id"])


def commit(hold: Hold | None) -> None:
    if hold and hold.status == "reserved":
        wallet.complete_reservation(hold.reservation_id)


def release(hold: Hold | None, reason: str = "") -> None:
    if hold and hold.status == "reserved":
        wallet.release_reservation(hold.reservation_id, reason=reason or "rasm chizilmadi")


def insufficient_text(hold: Hold) -> str:
    return (f"❌ Balansingizda mablag' yetarli emas.\n💰 Kerak: {fmt_sum(hold.amount)}\n"
            f"💳 Mavjud: {fmt_sum(hold.available)}\n\nBalansni /start → 💰 Balansim orqali to'ldiring.")


def drawing_available(user_id: int | None) -> bool:
    st = course_images.resolve_settings(user_id)
    return bool(course_images.candidate_keys(st, user_id))


async def expand_prompt(description: str) -> str:
    """Foydalanuvchi tasviri → batafsil inglizcha prompt (AI 'tasvirlaydi')."""
    description = (description or "").strip()[:500]
    try:
        from ai_clients import ask_ai  # kech import: modul testlarda ham yuklansin
        out = await ask_ai(config.UNIVERSAL_CHAT_AI, description, _PROMPT_SYSTEM)
    except Exception as e:
        logger.warning(f"🎨 Prompt kengaytirilmadi: {type(e).__name__}: {e}")
        out = None
    out = (out or "").strip().strip('"')
    return out[:700] if len(out) >= 15 else description


async def draw_image(user_id: int | None, description: str, aspect: str = "1:1") -> bytes | None:
    """Bitta rasm chizadi (JPEG/PNG baytlari) yoki None. Istisno ko'tarmaydi."""
    st = course_images.resolve_settings(user_id)
    cands = course_images.candidate_keys(st, user_id)
    if not cands:
        logger.warning("🎨 Rasm chizish uchun hech qanday AI kalit yo'q.")
        return None
    prompt = await expand_prompt(description)
    bad: set = set()
    for c in cands:
        if c["provider"] in bad:
            continue
        res = await image_clients.generate_image(c["provider"], c["key"], c["model"], prompt, aspect=aspect)
        if res.ok and res.data:
            return res.data
        if res.status in ("moderated", "bad_request", "model_not_found"):
            bad.add(c["provider"])
    return None
