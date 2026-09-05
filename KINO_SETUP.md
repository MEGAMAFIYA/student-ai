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
