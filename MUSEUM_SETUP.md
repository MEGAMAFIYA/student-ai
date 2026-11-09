# Student AI — Xotiralar muzeyi (1–5 bosqich)

## Ishga tushirish

1. Loyihani GitHub `main` branch'iga joylang va Render'da deploy qiling.
2. Render Environment Variables'da `PUBLIC_BASE_URL` HTTPS manzil bo'lishi kerak (masalan, `https://student-ai.example.com`).
3. Botda `/muzey` buyrug'ini yuboring. Mini App'da muzey yarating, surat yuklang va taklif havolasini do'stingizga yuboring.
4. Telegram bot sozlamalarida Main Mini App / Web App manzili root `PUBLIC_BASE_URL` bo'lishi tavsiya etiladi, chunki taklif havolasi `startapp` orqali `/` router'ga kiradi.

## Suratlarni doimiy saqlash

R2 sozlanmagan bo'lsa, suratlar `museum_data/photos/`, metadata esa `museum_data/museum.sqlite3` ichida saqlanadi. Render ephemeral disk ishlatilsa, qayta deploy/restart paytida bu fayllar yo'qolishi mumkin. Doimiy saqlash uchun Render Persistent Disk ulang va quyidagilarni qo'ying:

- `MUSEUM_DB_PATH=/var/data/museum/museum.sqlite3`
- `MUSEUM_MEDIA_DIR=/var/data/museum/photos`

**Muhim:** R2 faqat surat fayllarini saqlaydi. Muzey nomlari, egalik, taklif tokenlari va suratlar ro‘yxati `museum.sqlite3` bazasida turadi. Shu sabab R2 ishlatilganda ham SQLite bazasi uchun yuqoridagi Render Persistent Disk sozlamasi kerak. R2 o‘zi metadata bazasining o‘rnini bosmaydi.

Yoki suratlar uchun Cloudflare R2 sozlang:

- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET`

`boto3` ixtiyoriy kutubxona sifatida `requirements.txt`ga kiritilgan. To'rtta R2 qiymati mavjud bo'lsa, yangi suratlar R2'ga yuboriladi; bo'lmasa lokal saqlash ishlaydi. Mavjud lokal suratlar R2'ga avtomatik ko'chirilmaydi.

## Cheklovlar va xavfsizlik

- Telegram Mini App `initData` serverda HMAC bilan tekshiriladi.
- Mehmon faqat egasining faol taklif tokeni bilan xonaga kira oladi.
- Faqat egasi surat yuklaydi va yo'naltirishni yoqadi/o'chiradi.
- Bitta muzeyga 100 tagacha PNG/JPEG surat, har biri 5 MB gacha.
- Ikki va undan ortiq foydalanuvchi presence polling orqali ko'rinadi; har bir avatar ustida Telegram profili ismi ko'rsatiladi. Hozirgi versiya 3D holatni taxminan 1 soniyada yangilaydi (WebSocket emas).
- Suratlar API orqali faqat tasdiqlangan egasi yoki taklif tokeniga ega mehmonlarga beriladi.
- Uch o'lchamli sahna Three.js CDN'dan yuklanadi; internet ulanishi zarur.
