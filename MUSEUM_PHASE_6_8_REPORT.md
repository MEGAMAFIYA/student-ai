# Xotiralar muzeyi — 6–8-bosqich bajarilgan ishlar hisoboti

## Amalga oshirilgan

- 6-bosqich: egaga yo'naltirish holati, mehmonning o'zi ergashishni yoqib/o'chirishi, keyingi suratni tanlash va 3D ramkani ajratib ko'rsatish qo'shildi. Egasi 12 soniya faol ko'rinmasa, yo'naltirish o'chadi.
- 7-bosqich: 256-bit token, 24 soatlik standart taklif muddati, barcha faol takliflarni bekor qilish, QR ko'rsatish, mehmonlar ro'yxati va egaga mehmonni chiqarish amallari qo'shildi. Har bir API/photo so'rovida Telegram initData va muzeyga kirish ruxsati tekshiriladi. Rasm javobi `private, no-store`; yuklanadigan rasm haqiqiy PNG/JPEG, 5 MB, 4096x4096 va 16 MP cheklovlari bilan tekshiriladi. Ikki faol ishtirokchi limiti qo'llanadi.
- 8-bosqich: deploy/storage yo'riqnomasi, `.env.example`, README havolasi va ushbu hisobot qo'shildi. `.env` ZIP tarqatmasiga kiritilmaydi.

## O'zgargan fayllar

- `museum.py`
- `webapp/museum/app.js`
- `webapp/museum/index.html`
- `tests/test_museum.py`
- `MUSEUM_SETUP.md`
- `MUSEUM_PHASE_6_8_REPORT.md`
- `.env.example`
- `README.md`

## Tekshiruvlar

- `python -m py_compile museum.py bot.py` — o'tdi.
- `node --check webapp/museum/app.js` — o'tdi.
- `pytest -q tests/test_museum.py` — 4 passed.
- To'liq pytest suite — collection bosqichida mavjud bo'lmagan `google.generativeai` va noto'g'ri/soxta `httpx` muhiti sabab 3 modulda yiqildi. Qolgan suite urinishida 60 soniyalik ish muhiti cheklovi sabab jarayon to'xtadi; to'liq regressiya tasdiqlanmadi.

## Deploy va qolgan ishlar

Render'da `/var/data` Persistent Disk ulang; `MUSEUM_DB_PATH`, `MUSEUM_MEDIA_DIR`, `PUBLIC_BASE_URL` ni `.env.example` va `MUSEUM_SETUP.md` bo'yicha sozlang. R2 ishlatilsa, maxfiy kalitlarni faqat Render Environment Variables ichiga kiriting.

Muhim: mavjud multiplayer implementatsiyasi HTTP polling asosida, WebSocket emas. Ikki alohida telefon/brauzerda qabul sinovi ushbu muhitda bajarilmadi. Shu sabab 8-bosqichdagi barcha qabul mezonlari yakuniy tasdiqdan o'tgan deb hisoblanmaydi. QR kodi tashqi QRCode.js CDN mavjudligiga bog'liq. Deploydan keyin ikki qurilmada invite/QR, ergashish, mustaqil harakat, guest kick, invite revoke, reconnect va mobil FPS sinovlarini o'tkazish kerak.
