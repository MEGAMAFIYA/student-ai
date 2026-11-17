# Slayd yaratish — to'liq vizual tanlov tizimi

Tartib: mavzu → slaydlar soni → dizayn → ramka → ranglar → shrift → yakuniy tasdiq → PPTX / PPTX+PDF.
Har bir tanlov bosqichi bitta rasmli xabar: `[⬅️ Orqaga] [Keyingisi ➡️]`, `[✅ TASDIQLASH]`, `[❌ Bekor qilish]`.
Keyingi bosqichlar **edit_media** bilan shu xabarning o'zida almashadi (chatda xabarlar to'planmaydi).

## Fayllar
- `slide_previews.py` (YANGI) — preview dvigateli (Telegram'siz). Namuna haqiqiy `pptx_tools.build_presentation`
  natijasidan Pillow bilan 1280x720 JPEG'ga chiziladi → preview doim PPTX parametrlariga mos.
  Xotira (LRU) + disk keshi (`data/slide_preview_cache/`, `SLIDE_PREVIEW_CACHE_DIR`); kod o'zgarsa kesh avtomatik yangilanadi.
  Xato bo'lsa zaxira rasm qaytadi (bot to'xtamaydi).
- `handlers/pptx_gen.py` — umumiy "carousel" boshqaruvchi, bloklash, xatolarni ushlash, bekor qilish, eskirgan tugmalar.
- `bot.py` — yangi patternlar (`^px:cnt:` …), fallbacks (`cancel_cb`, `stale_cb`), global `stale_cb`, fon prewarm.
- `pptx_tools.py` — `_font_available`: PPTX'ga foydalanuvchi tanlagan shrift nomi yoziladi (avval serverda topilmasa DejaVu yozilardi).
- `tests/test_pptx_wizard.py` (YANGI) — 44 ta test.

## callback_data
`px:<bosqich>:<amal>:<indeks>` (bosqich: cnt/dsg/frm/pal/fnt; amal: p/n/ok) — ~12 bayt. Yakuniy: `pptx:pdf:yes|no`.
Navigatsiya paytida sozlama O'ZGARMAYDI; «Tasdiqlash» faqat ekrandagi indeksni saqlaydi (eskirgan tugma rad etiladi).

## Sozlash (ixtiyoriy)
`SLIDE_PREVIEW_PREWARM=0` (startda oldindan yaratishni o'chirish), `SLIDE_PREVIEW_CACHE_DIR`, `SLIDE_PREVIEW_MEM_ITEMS`.
Shrift previewlari uchun serverda `fonts-liberation`, `fonts-crosextra-carlito` o'rnatilgani yaxshi; bo'lmasa DejaVu zaxirasi ishlatiladi
(rasmda "o'xshash shrift bilan ko'rsatildi" izohi chiqadi, PPTX'ga esa tanlangan nom yoziladi).

## Testlar
`python3 -m unittest tests.test_pptx_wizard -v`
