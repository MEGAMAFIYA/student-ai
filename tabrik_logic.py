"""
🎁 /tabrik buyrug'ining "sof mantiq" qismi — Telegram kutubxonasiga BOG'LIQ
EMAS, shuning uchun bu modul telegram o'rnatilmagan muhitda ham to'liq
unit-test qilinishi mumkin (tests/test_tabrik_logic.py'ga qarang).

Bu yerda:
- /tabrik buyrug'idan keyingi matnni ajratib olish (@BotUsername bilan ham).
- Har bir /tabrik chaqiruvi uchun matnni vaqtinchalik (xotirada) saqlash —
  callback_data 64 baytdan oshmasligi kerak bo'lgani uchun, to'liq matnni
  emas, faqat qisqa ID'ni tugmaga yozamiz.
- Faqat ruxsat etilgan belgilardan (- _ ✓ « » ~ +) foydalangan holda
  "aylanayotgan doira" ASCII animatsiyasi freym'larini generatsiya qilish.
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
_STORE: dict[str, dict] = {}
ENTRY_TTL_SECONDS = 60 * 60      # 1 soat — shuncha vaqtdan keyin tugma endi ishlamaydi
MAX_ENTRIES = 2000               # xotira portlab ketmasligi uchun yuqori chegara


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


# ------------------------------------------------------------------
# 3) "Aylanayotgan doira" ASCII animatsiyasi
# ------------------------------------------------------------------
# FAQAT quyidagi belgilar ishlatiladi: - _ ✓ « » ~ +  (bo'sh joy va
# qator ko'chirish — sof formatlash, "kontent belgisi" emas).
#
# G'oya: doira shaklidagi 8 ta pozitsiya (soat 12, 1:30, 3, 4:30, 6,
# 7:30, 9, 10:30) bor. Har bir freym'da FAQAT bitta pozitsiya "yonadi"
# (✓ bilan almashtiriladi), qolganlari o'zining "passiv" belgisida
# turadi — pozitsiya freym'dan freym'ga siljiganda, ko'zga xuddi bitta
# nuqta doira bo'ylab aylanayotgandek ko'rinadi.
_POSITIONS = ["~", "»", "+", "»", "_", "«", "+", "«"]  # passiv holatdagi belgilar (soat yo'nalishida)
TOTAL_ROTATION_FRAMES = 16  # 8 pozitsiya x 2 to'liq aylanish — silliqroq ko'rinish uchun


def build_circle_frame(step: int) -> str:
    """`step` (0 dan TOTAL_ROTATION_FRAMES-1 gacha) bo'yicha doira
    freym matnini qaytaradi. Faqat ruxsat etilgan belgilar ishlatiladi."""
    active_pos = step % len(_POSITIONS)
    cells = list(_POSITIONS)
    cells[active_pos] = "✓"
    # 8 pozitsiyani doira shaklida joylashtiramiz (3x3 panjara,
    # o'rtasi bo'sh — "aylana" hissi uchun):
    #   [0]   [1]   [2]
    #   [7]   ' '   [3]
    #   [6]   [5]   [4]
    top = f" {cells[0]} {cells[1]} {cells[2]} "
    mid = f" {cells[7]}   {cells[3]} "
    bot = f" {cells[6]} {cells[5]} {cells[4]} "
    return f"🎁 Tabrik tayyorlanmoqda...\n\n{top}\n{mid}\n{bot}"


def build_countdown_frame(n: int) -> str:
    """Countdown freym (5→1). Ruxsat etilgan belgilardan iborat oddiy
    "progress" chizig'i bilan: n soniya qolganda, (5-n) ta '✓' (o'tgan)
    va n ta '-' (qolgan) ko'rsatiladi — vizual jihatdan doira sekin
    "yig'ilib" boryotgandek taassurot beradi."""
    passed = "✓" * (5 - n)
    remaining = "-" * n
    return f"🎁 Tabrik {n} soniyadan so'ng ochiladi...\n\n« {passed}{remaining} »\n\n{n}"


def build_final_card(greeting_text: str) -> str:
    """Animatsiya tugagandan keyingi yakuniy chiroyli karta. Foydalanuvchi
    yozgan matn HTML/Markdown maxsus belgilaridan xoli deb hisoblanmaydi —
    shuning uchun handler bu matnni albatta escape qilib yuborishi kerak
    (parse_mode ishlatilsa)."""
    frame = "+ ~ « ✓ » ~ +"
    return f"{frame}\n🎉 TABRIK! 🎉\n{frame}\n\n{greeting_text}\n\n{frame}"
