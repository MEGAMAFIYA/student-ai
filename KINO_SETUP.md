# 🎬 Kino Mini App — Stage 8 production architecture

## Media oqimi

`Telegram → MTProto → Render → Mini App` — asosiy yo‘l.

MTProto vaqtincha ishlamasa yoki xato bersa:

`Telegram → Bot API/CDN → Render → Mini App` — fallback.

Brauzer faqat bitta `/api/kino/stream/...` URL bilan ishlaydi va qaysi transport ishlayotganini bilmaydi.

## Muhim qoida

- R2/S3/Cloudinary ishlatilmaydi.
- Render kino fayllarini doimiy saqlamaydi.
- `/tmp` kino cache ishlatilmaydi.
- DB faqat metadata saqlaydi: Telegram chat/message ID, document ID, access hash, file reference, MIME, size, filename va legacy `file_id`.
- Video HTTP Range orqali bo‘lib-bo‘lib uzatiladi.
- Telegram credentiallari faqat server env’da bo‘ladi.
- Mini App `initData` serverda tekshiriladi.

## Render env

Majburiy:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_SESSION`
- `TELEGRAM_TOKEN`

Tavsiya:

- `KINO_STREAM_TOKEN_SECRET` — alohida uzun random secret.
- `KINO_STREAM_CHUNK_SIZE` — default 1 MiB.
- `KINO_STREAM_MAX_CONCURRENT` — default 4.
- `KINO_STREAM_TIMEOUT_SEC` — default 35 soniya.

`TG_API_ID`, `TG_API_HASH`, `TG_SESSION` eski env nomlari sifatida ham qabul qilinadi.

## Eski kinolar

Eski katalogdagi filmda Telegram source metadata bo‘lmasa, `/kino_migration MOVIE_ID CHAT_ID MESSAGE_ID` orqali aynan video yuborilgan Telegram xabarini ko‘rsatib, metadata'ni to‘ldirish mumkin. Media qayta saqlanmaydi.

## Tekshirish

1. Admin Telegram’da video yuboradi va nomini beradi.
2. DB’da `telegram_chat_id` va `telegram_message_id` paydo bo‘ladi.
3. Mini App video uchun faqat `/api/kino/stream/...` endpointidan foydalanadi.
4. Player Range request yuboradi.
5. Server MTProto’dan chunk oladi. Xato bo‘lsa fallback ishlaydi.
6. Watch Party/chat/WebRTC oqimi alohida qoladi.
