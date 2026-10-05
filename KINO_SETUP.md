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

## Qo'shimcha sozlamalar va buyruqlar (kino tuzatishlari)

| O'zgaruvchi | Default | Ma'nosi |
|---|---|---|
| `KINO_INIT_DATA_MAX_AGE_SEC` | 86400 | Mini App sessiyasi (initData) kino uchun shuncha vaqt yaroqli. Avval umumiy 1 soat edi — film 60-daqiqada uzilardi. |
| `KINO_STREAM_TOKEN_TTL_SEC` | 43200 | Stream token umri (12 soat). |
| `KINO_ROOM_TTL_SEC` | 21600 | Xona FAOL BO'LMAY qolgach shuncha soniyadan keyin o'chadi (avval: yaratilganidan boshlab). |
| `KINO_ROOM_MAX_AGE_SEC` | 86400 | Faol xonaning mutlaq maksimal umri. |
| `PUBLIC_BASE_URL` | — | Bo'sh bo'lsa mobil API `RENDER_EXTERNAL_URL` yoki so'rov `Host` sarlavhasidan foydalanadi. |

**MTProto talabi:** `TG_SESSION` — bu USER sessiyasi. Shu akkaunt kino saqlash kanaliga a'zo bo'lishi
(yoki kanal public `KINO_STORAGE_CHANNEL_USERNAME` ga ega bo'lishi) kerak, aks holda 20 MB dan katta
kinolar ochilmaydi (Bot API zaxirasi faqat ≤20 MB).

Admin buyruqlari:
- `/kino_check` — MTProto sessiyasi kanalni va kinolarni ocha olishini tekshiradi.
- `/kino_migration` — eski kinolarni MTProto manbasiga ulash (avval ro'yxatga olinmagan edi).

### Qo'shimcha (2-bosqich tuzatishlar)

| O'zgaruvchi | Default | Ma'nosi |
|---|---|---|
| `KINO_STREAM_MAX_RESPONSE_BYTES` | 8388608 | Bitta Range javobi shundan oshmaydi (qolganini brauzer keyingi so'rov bilan oladi). Seek qilinganda eski ulanishlar tez bo'shaydi. |
| `KINO_MAX_ROOMS` | 500 | Xotiradagi xonalar chegarasi. |
| `KINO_UNUSED_ROOM_TTL_SEC` | 1800 | Hech kim kirmagan (inline natija uchun yaratilgan) xona shuncha vaqtdan keyin o'chadi. |

- Xonalar xotirada saqlanadi. Server qayta ishga tushsa, mijoz xonani o'sha ID va kino bilan **o'zi tiklaydi**
  (havola endi `startapp=room_<xona>_<kino>` ko'rinishida; eski `room_<xona>` havolalar ham ishlaydi, lekin
  ular faqat xona hali tirik bo'lsa).
- Kino formati: **H.264 video + AAC audio bilan MP4**. MKV/AVI yuklashda rad etiladi, MOV/WebM/3GP ogohlantirish bilan
  qabul qilinadi. Konvertatsiya: `ffmpeg -i kirish.mkv -c:v libx264 -c:a aac -movflags +faststart chiqish.mp4`
  (`+faststart` sekin tarmoqda tez boshlanishi uchun muhim).
- Stream tokenlari kalit hosilasi o'zgargani sababli deploydan keyin eskilari yaroqsiz bo'ladi; ochiq sahifalar
  yangisini avtomatik oladi.


### Qo'shimcha (3-bosqich: lag tuzatishi — pipeline)

Avval har 1 MiB chunk ketma-ket olinardi (Telegramdan olish → brauzerga yozish → keyingisi). Endi
`telegram_mtproto.stream_range()` bitta `iter_download` bilan butun Range'ni fonda oldindan yuklaydi,
HTTP thread esa navbatdan olib brauzerga yozadi (parallel).

| O'zgaruvchi | Default | Ma'nosi |
|---|---|---|
| `KINO_STREAM_PREFETCH` | 4 | Oldindan yuklanadigan chunklar soni. RAM ≈ prefetch × `KINO_STREAM_CHUNK_SIZE` × faol oqimlar. |
| `KINO_STREAM_MAX_CONCURRENT` | 8 (avval 4) | Bir vaqtdagi oqimlar. |
| `KINO_STREAM_MAX_RESPONSE_BYTES` | 33554432 (avval 8 MiB) | Kam Range so'rovi → kam qayta ulanish. |

Eslatma: Render xotirasi kichik bo'lsa `KINO_STREAM_PREFETCH=2` qiling.
