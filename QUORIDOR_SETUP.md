# 🧱 QUORIDOR — Telegram Mini App rejimi

Quoridor endi **Mini App** sifatida ishlaydi (Shaxmat bilan bir xil mexanizm: `game.py` + `webapp/game/`).
Inline natija chatga tushadi, undagi **🎮 Quoridor o‘ynash** tugmasi o‘yinni to‘liq ekranli Mini App'da ochadi.
Mavjud o‘yinlar (Shaxmat, Shashka, X-O, Xotira), AI, admin panel, foydalanuvchilar bazasi — o‘zgarmagan.

## Nima o‘zgardi

O‘zgartirilgan fayllar:
- `game.py` — Quoridor Mini App backend: `join` (1-o‘yinchi = xona egasi, ikkinchi kirgan = 2-o‘yinchi, o‘yin avtomatik boshlanadi), `quoridor_move`, `quoridor_wall`, taslim, qayta o‘ynash (ranglar almashadi), `legal_moves` / `legal_walls` ro‘yxati. Barcha amallar `LOCK` ostida; holat mavjud saqlash tizimida (`game_rooms.json` / `DATABASE_URL` / Upstash / GitHub) saqlanadi.
- `handlers/inline_query.py` — `@bot quoridor` va `@bot game` natijasida Quoridor endi Mini App tugmali (URL) xabar bo‘lib chiqadi.
- `webapp/game/index.html`, `app.js`, `style.css` — Quoridor doskasi, devor qo‘yish interfeysi, kutish xonasi, "Qayta o‘ynash".

Yangi fayllar:
- `tests/test_quoridor_miniapp.py` — 24 ta test (qo‘shilish, navbat, noto‘g‘ri yurish, devorlar, yo‘lni to‘sish, g‘alaba, taslim, qayta o‘ynash, JSON tiklash, bir vaqtlilik).
- `QUORIDOR_SETUP.md` — shu fayl.

Tegilmagan: `quoridor.py` (qoidalar dvigateli, BFS) va `tests/test_quoridor.py`. `chat_games.py` dagi eski inline-tugmali Quoridor kodi
orqaga moslik uchun qoldirilgan (avval yuborilgan eski xabarlar ishlashda davom etadi), lekin yangi natijalar Mini App'ga olib boradi.
`requirements.txt` o‘zgarmadi.

## O‘yin interfeysi

- Doska 9×9; **har bir o‘yinchi o‘zini pastda ko‘radi** (2-o‘yinchiga doska avtomatik aylantiriladi). Yuqori qator (yashil) — sizning maqsadingiz.
- **Yurish:** sariq belgili kataklar (qonuniy yurishlar, raqib ustidan/yon tomonga sakrash bilan) — bosing.
- **Devor:** `🧱 Devor qo‘yish` → doskada devor markazi bo‘ladigan joyga (katak burchagiga) bosing → ↔️/↕️ yo‘nalish (xuddi shu joyga qayta bossangiz ham almashadi) → yashil bo‘lsa `✅ Devorni qo‘yish`. Qizil — mumkin emas (sababi yozib turiladi). `❌ Bekor qilish`.
- Yuqorida ikkala o‘yinchining ismi, rangi va qolgan devorlari; `📖 Qoidalar`, `🏳 Taslim`, g‘olib nomi va `🔄 Qayta o‘ynash` (ikkala o‘yinchi bossa).
- Mavjud Mini App xususiyatlari (chat, emoji, kamera/mikrofon) Quoridor'da ham bor.

## Sozlamalar (BotFather) — MUHIM

Shaxmat ishlayotgan bo‘lsa, hammasi tayyor. Aks holda:
1. `/mybots` → botni tanlang → **Bot Settings → Inline Mode → Turn on** (placeholder: `game yoki quoridor`).
2. **Bot Settings → Configure Mini App** (Main Mini App) → **Enable** → URL: `https://<RENDER_DOMEN>/miniapp/` (yoki `PUBLIC_BASE_URL`). Direct-link (`t.me/<bot>?startapp=game_...`) shu sozlamaga tayanadi.
3. Render muhit o‘zgaruvchilari: `PUBLIC_BASE_URL=https://<RENDER_DOMEN>` (majburiy — yo‘q bo‘lsa inline natija chiqmaydi), `BOT_USERNAME=<bot_username>` (standart `Student_ai_uz_bot`).
4. Qayta ishga tushganda xonalar saqlanishi uchun `DATABASE_URL` yoki Upstash yoki GitHub saqlash sozlangan bo‘lsin (aks holda Render qayta deployida `game_rooms.json` o‘chadi). Xona muddati: `GAME_ROOM_TTL_SEC` (standart 12 soat).
5. Bitta token bilan faqat BITTA bot nusxasi ishlasin (aks holda Telegram `Conflict`).

## Render'da deploy

1. O‘zgargan fayllarni GitHub repo'ga push qiling (yoki ZIP'dagi loyihani bilan almashtiring) → Render avtomatik deploy qiladi.
2. Deploy tugagach `https://<RENDER_DOMEN>/miniapp/game/` ochilsa sahifa ("Telegram ichidan oching" yozuvi bilan) chiqishi kerak.

## Telegram'da qanday boshlanadi

1. Istalgan chatda yozing: `@Student_ai_uz_bot quoridor` (yoki `@Student_ai_uz_bot game`).
2. **🧱 Quoridor** natijasini tanlab chatga yuboring.
3. Birinchi o‘yinchi (yuboruvchi) **🎮 Quoridor o‘ynash** tugmasini bosadi — Mini App ochiladi, kutish xonasi ko‘rinadi (`📤 Do‘stga havola yuborish` ham bor).
4. Do‘sti shu xabardagi o‘sha tugmani bosadi → o‘yin avtomatik boshlanadi. 🔵 1-o‘yinchi (xona egasi) birinchi yuradi.
5. Uchinchi kishi kirsa — "xona to‘la" xabari chiqadi.

## Test natijalari

```
python -m unittest tests.test_quoridor_miniapp   → 24 test, OK
python -m unittest tests.test_quoridor           → 54 test, OK (dvigatel — o‘zgarmagan)
python -m unittest discover -s tests -t .        → 556 test; xatolar ASL ZIP'dagi bilan bir xil:
   1 FAIL + 3 ERROR (sandboxda `telegram`/`httpx` yo‘qligi va vaqtga bog‘liq
   test_concurrent_recipients_no_global_lock) — Quoridor bilan bog‘liq emas.
```

Brauzerda (Playwright, 390×844) haqiqiy Mini App sahifasida ikki o‘yinchi bilan tekshirildi: kutish xonasi → ikkinchi o‘yinchi
qo‘shilishi → navbat bilan yurish (navbati kelmaganda yurish kataklari yo‘q) → devor oldindan ko‘rish va qo‘yish (10 → 9) →
g‘alaba va g‘olib nomi → ikkala tomon "Qayta o‘ynash" → ranglar almashadi. Shu jarayonda topilgan xato
(qatlamlar katak bosishini to‘sishi) tuzatildi.

**Halol eslatma:** sandboxda Telegram'ga chiqib bo‘lmaydi va `python-telegram-bot` o‘rnatilmagan, shuning uchun inline natijaning
haqiqiy Telegram'da chiqishi, `startapp` havolasi va `initData` imzosini Telegram bilan tekshirish bajarilmadi (imzo tekshiruvi
mavjud `webapp_security` orqali ishlaydi; testlarda soxtalashtirilgan). Deploydan keyin yuqoridagi ketma-ketlikni ikki akkaunt bilan
bir marta sinab ko‘ring.
