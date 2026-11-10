# Student AI — Xotiralar muzeyi (1–8 bosqich)

## Render deploy

1. To‘liq loyihani GitHub `main` branch'iga joylang va Render Web Service'ni deploy qiling.
2. `PUBLIC_BASE_URL` qiymatini HTTPS service URL qilib belgilang.
3. Render Persistent Disk ulang va mount path sifatida `/var/data` ishlating.
4. Environment Variables:
   - `MUSEUM_DB_PATH=/var/data/museum/museum.sqlite3`
   - `MUSEUM_MEDIA_DIR=/var/data/museum/photos`
   - `PUBLIC_BASE_URL=https://<service>.onrender.com`
5. Suratlarni deploylar orasida saqlash uchun R2 tavsiya etiladi. `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` qiymatlarini faqat Render Environment'ga kiriting.
6. Botda `/muzey` buyrug‘ini oching.

## 6-bosqich — yo‘l boshlash

Muzey egasi `Yo‘naltirish`ni yoqadi. Mehmon `Egaga ergashish`ni o‘zi bosadi; egasining joriy pozitsiyasi polling orqali kuzatiladi. `Keyingi surat` tanlangan ramkani ajratib ko‘rsatadi. Egasi 12 soniyadan ortiq ko‘rinmasa, yo‘l boshlash avtomatik o‘chadi. Mehmon istalgan vaqtda ergashishni to‘xtata oladi.

## 7-bosqich — invite va ruxsatlar

- Taklif tokeni 256-bit tasodifiy hex token; yangi token 24 soat amal qiladi.
- Egasi `Havolani bekor qilish` orqali barcha faol takliflarni bekor qiladi.
- QR-kod Mini App ichida QRCode.js CDN orqali chiziladi.
- Egasi faol mehmonlar ro‘yxatini ko‘radi va ularni chiqarishi mumkin.
- Xona 2 faol ishtirokchi bilan cheklangan.
- Foto endpoint har so‘rovda Telegram `initData` va owner/invite ruxsatini tekshiradi; javob `private, no-store`.
- `user_id` URL orqali autentifikatsiya sifatida ishlatilmaydi.
- Rasm URL'ini boshqalarga berishning o‘zi ruxsat bermaydi; har so‘rov autentifikatsiya talab qiladi.

## Storage

R2 sozlanmasa, rasm `MUSEUM_MEDIA_DIR` ichida saqlanadi. Render ephemeral disk doimiy emas: persistent disk yoki R2 talab qilinadi. SQLite metadata bazasi ham persistent diskda bo‘lishi kerak. R2 faqat rasm fayllarini saqlaydi, metadata bazasini emas.

## Muhim cheklovlar

- Multiplayer transporti hozircha WebSocket emas, HTTP polling. Harakatlar 0.9–1.1 soniya oralig‘ida yangilanadi; haqiqiy WebSocket sinovi va ikki jismoniy qurilmadagi qabul sinovi deploydan keyin bajarilishi shart.
- QR generatori tashqi CDN'ga bog‘liq.
- Rasmni ekran tasviridan yoki boshqa usulda nusxalashni mutlaq to‘xtatib bo‘lmaydi.
- Invite bekor qilingandan so‘ng mavjud mehmon sessiyasi ham presence'dan chiqariladi.
- Rasm va muzey ma'lumotlari ochiq statik URL orqali berilmaydi.

## Tekshiruv

Lokal:
```bash
python -m pytest -q
python -m py_compile museum.py bot.py
node --check webapp/museum/app.js
```

Deploydan so‘ng ikkita alohida Telegram akkaunti/telefon bilan: muzey yaratish, invite/QR, mehmon kirishi, avatar holati, ergashish, mustaqil yurish, guest kick, invite revoke va reconnect sinovlarini bajaring.

## 2026-11: muzey UI, musiqa va doimiy saqlash
- Muzey metama'lumotlari SQLite `MUSEUM_DB_PATH` bazasida, media esa `MUSEUM_MEDIA_DIR` papkasida saqlanadi.
- Render qayta deploydan keyin ham SQLite va mahalliy media saqlanishi uchun Render Persistent Disk ulang va `MUSEUM_DB_PATH=/var/data/museum/museum.sqlite3`, `MUSEUM_MEDIA_DIR=/var/data/museum/media` qiymatlarini belgilang.
- Alternativ: R2 sozlamalari (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`) media fayllarini obyekt omborida saqlaydi; SQLite bazasi uchun baribir persistent disk kerak.
- Muzey egasi 10 MB gacha bo'lgan 3 ta MP3 yuklashi mumkin. Mehmonlar kolonka boshqaruvidan ijro/pauza va keyingi/oldingi trekni boshqaradi.
- Inline ulashish: `@Student_ai_uz_bot muzey`.

## 2026-11: kamera, joystick va mehmon roziligi
- Joystick harakati vaqtga bog‘langan tezlik bilan hisoblanadi: oldinga/orqaga va yonlama yurish ishlaydi; yonlama yurish kamerani burmaydi.
- Sahnaning bo‘sh joyini barmoq bilan sudrash kameraning yaw/pitch yo‘nalishini o‘zgartiradi; bu harakat qahramonning joyini o‘zgartirmaydi.
- Suratni bosish raycast orqali aniqlanadi; kamera sudralganida suratni tasodifan ochmaydi.
- Mehmon `Egaga ergashish`ni bosganda rozilik tasdig‘i ko‘rsatiladi. Rad etsa, ergashish yoqilmaydi.
- Avatar geometriyasi Three.js asosiy buildida mavjud geometriyalardan tuzildi; `CapsuleGeometry` addoniga bog‘liqlik olib tashlandi.
