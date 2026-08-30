"""
🎁 /tabrik buyrug'ining "sof mantiq" qismi — Telegram kutubxonasiga BOG'LIQ
EMAS, shuning uchun bu modul telegram o'rnatilmagan muhitda ham to'liq
unit-test qilinishi mumkin (tests/test_tabrik_logic.py'ga qarang).

Bu yerda:
- /tabrik buyrug'idan keyingi matnni ajratib olish (@BotUsername bilan ham).
- Har bir /tabrik chaqiruvi uchun matnni vaqtinchalik (xotirada) saqlash —
  callback_data 64 baytdan oshmasligi kerak bo'lgani uchun, to'liq matnni
  emas, faqat qisqa ID'ni tugmaga yozamiz.
- FAQAT quyidagi belgilardan foydalangan holda ("naqsh" palitrasi):
  • ~ ✓ « » - _ — + ×
  1) "Katta raqam" ASCII-art countdown (5→1) — har bir raqam 5x7
     nuqta-matritsa shaklida, shu palitradagi belgilar bilan chiziladi
     (oddiy "5, 4, 3..." matn EMAS).
  2) Undan keyingi "aylanayotgan naqsh" animatsiyasi — bezak chizig'i +
     aylanuvchi halqa, yana shu palitra bilan.
"""

import re
import time
import uuid

# ------------------------------------------------------------------
# 1) /tabrik dan keyingi matnni ajratib olish
# ------------------------------------------------------------------
# "/tabrik" yoki "/tabrik@Student_ai_uz_bot" bilan boshlanadi, keyin
# bo'sh joy(lar), keyin qolgan hammasi — tabrik matni.
_COMMAND_RE = re.compile(r"^/tabrik(?:@\w+)?\s*", re.IGNORECASE)


def parse_tabrik_text(raw_message_text: str) -> str | None:
    """Xabar matnidan (masalan '/tabrik@Bot Salom ...') buyruqni olib
    tashlab, faqat tabrik matnini qaytaradi. Agar buyruqdan keyin hech
    narsa yozilmagan bo'lsa — None qaytaradi (handler foydalanuvchiga
    namuna ko'rsatishi uchun)."""
    if not raw_message_text:
        return None
    text = _COMMAND_RE.sub("", raw_message_text, count=1).strip()
    return text or None


# ------------------------------------------------------------------
# 2) Qisqa muddatli xotiradagi "tabrik matni" ombori
# ------------------------------------------------------------------
# callback_data uzunligi cheklangani (64 bayt) uchun, uzun tabrik matnini
# to'g'ridan-to'g'ri tugmaga yozib bo'lmaydi — o'rniga qisqa (8 xonali)
# ID saqlaymiz. Xotira cheksiz o'smasligi uchun: eskirgan (TTL o'tgan)
# yozuvlar har safar yangisi qo'shilganda avtomatik tozalanadi, shuningdek
# umumiy hajm MAX_ENTRIES'dan oshsa eng eskilari o'chiriladi.
#
# MUHIM: tugma cheksiz marta qayta bosilishi mumkin bo'lgani uchun (2
# daqiqada bir marta "🎁 Tabriknomani qabul qilish" holatiga qaytadi), TTL
# yetarlicha uzun (1 kun) qilib belgilangan — aks holda uzoq vaqt osilib
# turgan tabrik tugmasi "muddati o'tgan" bo'lib qolar edi.
_STORE: dict[str, dict] = {}
ENTRY_TTL_SECONDS = 60 * 60 * 24  # 1 kun
MAX_ENTRIES = 2000                # xotira portlab ketmasligi uchun yuqori chegara


def _purge_expired(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    expired = [k for k, v in _STORE.items() if now - v["created_at"] > ENTRY_TTL_SECONDS]
    for k in expired:
        del _STORE[k]
    if len(_STORE) > MAX_ENTRIES:
        oldest = sorted(_STORE.items(), key=lambda kv: kv[1]["created_at"])
        for k, _ in oldest[: len(_STORE) - MAX_ENTRIES]:
            del _STORE[k]


def store_greeting(text: str) -> str:
    """Tabrik matnini saqlaydi va tugma uchun qisqa ID qaytaradi."""
    short_id = uuid.uuid4().hex[:10]
    _STORE[short_id] = {"text": text, "created_at": time.time()}
    _purge_expired()
    return short_id


def get_greeting(short_id: str) -> str | None:
    """ID bo'yicha tabrik matnini qaytaradi (muddati o'tgan/topilmagan
    bo'lsa None)."""
    _purge_expired()
    entry = _STORE.get(short_id)
    return entry["text"] if entry else None


def touch_greeting(short_id: str) -> None:
    """Tugma qayta bosilganda TTL'ni yangilaydi — faol ishlatilayotgan
    tabrik hech qachon "muddati o'tgan" bo'lib qolmasligi uchun."""
    entry = _STORE.get(short_id)
    if entry:
        entry["created_at"] = time.time()


# ------------------------------------------------------------------
# 3) NAQSH PALITRASI — barcha animatsiya freym'lari FAQAT shu
#    belgilardan foydalanadi.
# ------------------------------------------------------------------
DECOR_CHARS = ["•", "~", "✓", "«", "»", "-", "_", "—", "+", "×"]


def _ornament_line(offset: int, length: int = 21) -> str:
    """Har bir freym'da bir necha belgiga siljiydigan bezak chizig'i —
    "aylanish"/"naqsh" hissi beradi (palitradagi HAMMA belgi ishtirok
    etadi, faqat ketma-ketlik freym'dan freym'ga siljiydi)."""
    return "".join(DECOR_CHARS[(i + offset) % len(DECOR_CHARS)] for i in range(length))


# ------------------------------------------------------------------
# 4) "KATTA RAQAM" — 5x7 nuqta-matritsa shaklidagi ASCII-art raqamlar
#    (klassik dot-matrix displey shrifti), FAQAT DECOR_CHARS bilan
#    chiziladi — oddiy "5, 4, 3..." matn EMAS.
# ------------------------------------------------------------------
_DIGIT_BITMAPS: dict[int, list[str]] = {
    1: ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    2: ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    3: ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    4: ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    5: ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
}


def _render_big_digit(n: int) -> str:
    bitmap = _DIGIT_BITMAPS[n]
    rows = []
    for r, row in enumerate(bitmap):
        cells = []
        for c, cell in enumerate(row):
            if cell == "1":
                cells.append(DECOR_CHARS[(r * 5 + c + n) % len(DECOR_CHARS)])
            else:
                cells.append(" ")
        rows.append(" ".join(cells))
    return "\n".join(rows)


def build_countdown_frame(n: int) -> str:
    """Countdown freym (5→1) — raqam katta ASCII-art shaklida, FAQAT
    DECOR_CHARS palitrasi bilan chizilgan, monospace uchun kod blokida."""
    art = _render_big_digit(n)
    return f"🎁 Tabrik ochilmoqda...\n```\n{art}\n```"


# ------------------------------------------------------------------
# 5) "AYLANAYOTGAN NAQSH" — countdown tugagach ko'rsatiladigan bezak
#    animatsiyasi.
# ------------------------------------------------------------------
_RING_POSITIONS = 8
TOTAL_ROTATION_FRAMES = 16  # 8 pozitsiya x 2 to'liq aylanish — silliqroq ko'rinish uchun


def build_circle_frame(step: int) -> str:
    """`step` (0 dan TOTAL_ROTATION_FRAMES-1 gacha) bo'yicha "aylanayotgan
    naqsh" freym matnini qaytaradi. Faol pozitsiya ✓ bilan, qolganlari esa
    HAR SAFAR palitradan turli belgi bilan (statik takrorlanish emas, har
    freym'da yangilanadi) — shu orqali haqiqiy "naqsh" taassuroti beriladi."""
    active = step % _RING_POSITIONS
    ring = []
    for i in range(_RING_POSITIONS):
        if i == active:
            ring.append("✓")
        else:
            ring.append(DECOR_CHARS[(i + step) % len(DECOR_CHARS)])
    top = f" {ring[0]} {ring[1]} {ring[2]} "
    mid = f" {ring[7]}   {ring[3]} "
    bot = f" {ring[6]} {ring[5]} {ring[4]} "
    border = _ornament_line(step)
    art = f"{border}\n\n{top}\n{mid}\n{bot}\n\n{border}"
    return f"🎁 Tabrik tayyorlanmoqda...\n```\n{art}\n```"


def build_final_card(greeting_text: str) -> str:
    """Animatsiya tugagandan keyingi yakuniy chiroyli karta. Foydalanuvchi
    yozgan matn HTML/Markdown maxsus belgilaridan xoli deb hisoblanmaydi —
    shuning uchun handler bu matnni albatta escape qilib yuborishi kerak
    (parse_mode ishlatilsa)."""
    border = _ornament_line(0, 25)
    return f"```\n{border}\n```\n🎉 *TABRIK!* 🎉\n\n{greeting_text}\n\n```\n{border}\n```"


def build_ready_card() -> str:
    """Tugma hali bosilmagan (yoki 2 daqiqadan keyin qayta o'rniga
    qaytarilgan) holatdagi minimal matn — tabrik matni ko'rsatilmaydi."""
    return "🎁 Sizga tabrik bor!"
