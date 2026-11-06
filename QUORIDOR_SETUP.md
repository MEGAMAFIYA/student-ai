# 🧱 QUORIDOR — inline (chat ichidagi) o'yin

Telegram Mini App EMAS: o'yin chatning o'zida, inline xabar tugmalari orqali ishlaydi
(shaxsiy chat, guruh, istalgan chat). Mavjud `chat_games.py` / `game.py` / `cg:` callback
tizimiga qo'shilgan — Shashka, X-O, Xotira, Shaxmat va boshqa funksiyalar o'zgarmagan.

## Oldingi urinish nega ishlamadi (topilgan sabablar)

1. **Trigger faqat aynan `game` so'zi edi.** `@bot quoridor` yozilsa so'rov oddiy AI-javob
   oqimiga tushib ketardi. Endi `game`, `games`, `oyin`, `o'yinlar` — barcha o'yinlar,
   `quoridor` (`kvoridor`, `koridor`) — faqat Quoridor.
2. **Chatga tushgan xabarni AI "yutib" yuborardi.** Inline natija botning shaxsiy chatida
   oddiy matnli xabar sifatida ham keladi; `handlers/universal_chat.py` filtrida Quoridor
   yo'q edi, shuning uchun u AI'ga yuborilib, doska o'rniga AI javobi chiqishi mumkin edi.
   Endi `🧱 QUORIDOR` bilan boshlangan xabar AI oqimidan chiqariladi.
3. **O'yin qoidalari noto'g'ri edi:** yon tomonga sakrash (devor/chegara oldida) yo'q,
   diagonal sakrash yo'q, doska chizmasi devorlarni noto'g'ri ko'rsatardi, 1-o'yinchi
   ismi ko'rinmasdi. Hammasi qayta yozildi (`quoridor.py`).

## Fayllar

Yangi:
- `quoridor.py` — sof qoidalar dvigateli (yurish, sakrash, devor, BFS yo'l tekshiruvi, g'olib, doska chizish).
- `tests/test_quoridor.py` — 54 ta test.
- `QUORIDOR_SETUP.md` — shu fayl.

O'zgartirilgan:
- `chat_games.py` — eski Quoridor bloki yangisiga almashtirildi (matn, tugmalar, `quoridor_dispatch`).
- `bot.py` — `_chat_game_callback`: `qm/qd/qo/qr/qc/qp/qx/qs/qh` tugmalari `quoridor_dispatch` ga yo'naltirildi; chat tahriri xatolari endi jurnalga yoziladi.
- `game.py` — `create_room("quoridor")` yangi holat bilan; Mini App API orqali Quoridor xonasiga kirish bloklandi.
- `handlers/inline_query.py` — `game` / `quoridor` triggerlari, Quoridor natijasi haqiqiy doska bilan chiqadi.
- `handlers/universal_chat.py` — Quoridor xabari AI'ga yuborilmaydi.

`requirements.txt` o'zgarmadi (yangi kutubxona kerak emas). Handler qo'shilmadi — mavjud
`CallbackQueryHandler(_chat_game_callback, pattern=r"^cg:")` (bot.py, group=0) va
`InlineQueryHandler(on_inline_query)` ishlatiladi; callback nomlari (`cg:q*`) boshqalar bilan to'qnashmaydi.

## Telegram'da qanday boshlanadi

1. Istalgan chatda yozing: `@Student_ai_uz_bot quoridor` (yoki `@Student_ai_uz_bot game`).
2. Natijalardan **🧱 Quoridor** ni tanlang — doska chatga tushadi.
3. Ikkinchi o'yinchi shu xabardagi **👥 Qo'shilish** tugmasini bosadi → o'yin boshlanadi.
   Birinchi o'yinchi (xonani yaratgan) 🔵 pastdan boshlaydi va birinchi yuradi.

Boshqaruv (doska — matn, har yurishdan keyin xabar tahrirlanadi):
- **Yurish:** faqat ruxsat etilgan kataklar tugma bo'lib chiqadi (⬆️ e8, raqib ustidan ⏫ sakrash, yon ↖️ ↗️ ...).
- **Devor:** `🧱 Devor qo'yish` → ↔️/↕️ → qator (1–8) → ustun (a–h) → `🧱 Devorni qo'yish`.
  Devor 2×2 blok markazidan o'tadi; tanlangan blok chap-yuqori katagi bilan beriladi.
  Tanlash paytida doskada `═`/`║` bilan ko'rib chiqiladi; yo'lni to'sadigan devor uchun ⛔ chiqadi va tasdiqlash tugmasi yashirinadi.
- `📖 Qoidalar`, `🏳️ Taslim bo'lish` (15 soniya ichida ikki marta bosiladi), `🔄 Yangilash`, g'alabadan so'ng `🔄 Qayta o'ynash` (ranglar almashadi).

## BotFather sozlamalari (tekshiring)

- `/setinline` → botni tanlang → placeholder matn (masalan `game yoki quoridor`) — Inline Mode **yoqilgan** bo'lishi shart.
- `/setinlinefeedback` → **Enabled** (mavjud inline funksiyalar `chosen_inline_result` ishlatadi; Quoridor uchun majburiy emas).
- Callback tugmalari uchun qo'shimcha sozlama kerak emas. Mini App/domen sozlamasi ham kerak emas.

## Render'da deploy

1. O'zgargan fayllarni GitHub repo'ga push qiling (yoki ZIP ichidagi loyihani bilan almashtiring) → Render avtomatik deploy qiladi.
2. O'yin xonalari mavjud `game.py` saqlash tizimi (`game_rooms.json` / `DATABASE_URL`, Upstash yoki GitHub — `config.persist_*`) orqali saqlanadi,
   shuning uchun qayta ishga tushganda faol o'yinlar tiklanadi. **Doimiy saqlash (DATABASE_URL yoki Upstash) sozlanmagan bo'lsa, Render qayta deployida fayl o'chadi** — loyiha ishga tushganda shu haqda ogohlantiradi.
3. Xona muddati: `GAME_ROOM_TTL_SEC` (standart 12 soat).
4. Bitta token bilan faqat BITTA bot instansiyasi ishlashi kerak (aks holda Telegram `Conflict`).

## Test natijalari

```
python -m unittest tests.test_quoridor      → 54 test, OK
python -m unittest discover -s tests -t .   → 532 test (avval 478); xatolar soni o'zgarmadi
```
Butun to'plamda 1 FAIL + 3 ERROR bor — ular ORIGINAL ZIP'da ham aynan shunday (sandboxda `telegram`/`httpx` yo'qligi va vaqtga bog'liq
`test_concurrent_recipients_no_global_lock`), Quoridor bilan bog'liq emas.

Tekshirilganlar: boshlang'ich holat; yurish/navbat; diagonal va uzoq yurish rad etiladi; devor yurishni to'sadi; to'g'ri sakrash;
devor/doska chegarasi oldida yon sakrash; ikkala tomonning g'alabasi; sakrab maqsad qatoriga chiqish; devor chegaralari, ustma-ust tushish,
kesishish; 10 ta devor limiti; yo'lni butunlay to'sadigan devor rad etiladi (BFS, ikkala o'yinchi uchun); taslim; qayta o'ynash;
begona/navbati kelmagan o'yinchi bloklanadi; g'alabadan keyin eski tugmalar ishlamaydi; callback_data ≤ 64 bayt va qatorda ≤ 8 tugma;
bir nechta mustaqil xona; bir vaqtda 12 ta bir xil callback → faqat 1 tasi bajariladi; 10 ta bir vaqtdagi "Qo'shilish" → faqat 1 ta ikkinchi o'yinchi;
JSON saqlash/tiklashdan keyin o'yin davom etadi; 40 ta tasodifiy to'liq o'yinda invariantlar; `_chat_game_callback` soxta CallbackQuery bilan to'liq o'yin.

**Halol eslatma:** sandbox'da haqiqiy `python-telegram-bot` o'rnatilmagan va Telegram'ga chiqib bo'lmaydi, shuning uchun
**haqiqiy Telegram bilan sinov (inline natija chiqishi, tugmalar bosilishi) bajarilmadi** — bu qismlar mantiqan va soxta obyektlar bilan tekshirildi.
Deploydan keyin yuqoridagi "Telegram'da qanday boshlanadi" ketma-ketligini 2 ta akkaunt bilan bir marta sinab ko'ring.
