# Kino Watch Party — deployment/setup

## Muhim: Main Mini App

Kino inline natijasidagi `▶️ Birga ko'rish` tugmasi Telegram Direct Mini App linkidan foydalanadi:

`https://t.me/<BOT_USERNAME>?startapp=room_<ROOM_ID>&mode=fullscreen`

Shuning uchun @BotFather ichida bot uchun **Main Mini App** URL sifatida
Render'dagi shu loyihaning root URL'i yoki `/miniapp/` URL'i berilishi kerak.

Misol:
`https://YOUR-RENDER-DOMAIN.onrender.com/miniapp/`

Root URL ham endi Mini App router sifatida xizmat qiladi. Agar Telegram
`startapp=room_...` bilan ochsa, router foydalanuvchini avtomatik
`/miniapp/kino/?room=...` ga o'tkazadi.

## Kino oqimi

- `/kino` — admin katalogi.
- Kino bir marta Telegram `file_id` bilan katalogga yoziladi.
- Inline: `@Bot kino` yoki `@Bot kino ajdar uyi`.
- Natijani yuborganda `▶️ Birga ko'rish` tugmasi room'ni ochadi.
- Watch Party ichida play/pause/seek sinxronlanadi.
- Ichki chat va WebRTC kamera/mikrofon signaling mavjud.

## Eslatma

Cloud Bot API'da katta kino fayllari uchun fayl yuklab olish/streaming
cheklovlari mavjud. To'liq filmlar uchun keyingi bosqichda Local Bot API
yoki R2/S3 kabi object storage tavsiya qilinadi.

## WebRTC kamera/mikrofon (2-bosqich tuzatish)

Kamera va mikrofon WebRTC orqali uzatiladi. `KINO_TURN_*` o'zgaruvchilari ixtiyoriy:

- `KINO_TURN_URL` (bitta TURN URL uchun)
- `KINO_TURN_URLS` (ixtiyoriy: bir nechta URL, vergul/yangi qator bilan)
- `KINO_TURN_USERNAME`
- `KINO_TURN_CREDENTIAL`

STUN ko‘p tarmoqlarda yetadi. Mobil operator yoki qattiq NAT/firewall holatlarida TURN relay kerak bo‘lishi mumkin. TURN credentiallarni qisqa muddatli qilib berish tavsiya etiladi.

### Diagnostika

Mini App ichida kamera/mikrofon tugmasi bosilganda brauzer `getUserMedia` ruxsatini so‘raydi. Ruxsat berilmasa, foydalanuvchiga aniq xabar chiqadi. Ikki foydalanuvchi xonaga kirgandan keyin media ulanishi negotiation + ICE signaling orqali qayta o‘rnatiladi.

## Chat duplicate himoyasi

Chat POST so‘roviga `client_id` yuboriladi. Server bir xil `client_id`ni qayta yuborilgan bo‘lsa, yangi xabar yaratmaydi. Frontendda ham message ID bo‘yicha dedupe va polling lock mavjud.


## WebRTC sifat va barqarorlik

- Video 720p/30fps gacha olinadi va WebRTC bitrate limiti tarmoq holatiga qarab moslanadi.
- `getStats()` orqali paket yo'qotilishi, RTT va mavjud outgoing bitrate kuzatiladi.
- ICE `failed/disconnected` holatlarida cooldown bilan avtomatik restart qilinadi.
- Bir nechta TURN URL berilsa, brauzer mos relay transportini tanlaydi.
- Android/Telegram klaviaturasi ochilganda kino player sticky holatda ko'rinib turadi.

## ☁️ Cloudflare R2 / CDN (ixtiyoriy, tavsiya etiladi)

R2 yoqilganda admin yuklagan kino Telegramdan bir marta R2 bucket'ga ko'chiriladi.
Mini App keyingi tomoshalarda R2 public/CDN URL yoki 15 daqiqalik presigned URL orqali videoni oladi;
Render video baytlarini proxy qilmaydi. R2 sozlanmagan bo'lsa eski Telegram→Render fallback ishlaydi.

Render Environment Variables:

```text
R2_ACCOUNT_ID=Cloudflare Account ID
R2_ACCESS_KEY_ID=R2 API token access key
R2_SECRET_ACCESS_KEY=R2 API token secret key
R2_BUCKET=student-ai-kino
R2_PUBLIC_BASE_URL=https://cdn.example.com
R2_PRESIGNED_TTL_SEC=900
```

`R2_PUBLIC_BASE_URL` faqat bucket Cloudflare custom domain orqali public/read bo'lsa qo'yiladi.
Aks holda bo'sh qoldiring — backend presigned GET URL yaratadi.

Tavsiya: production uchun R2 bucket'ni public qilish o'rniga Cloudflare custom domain + Cache Rules yoki
private bucket + presigned URL ishlating. R2 credentials faqat Render Environment Variables'da bo'lsin.
