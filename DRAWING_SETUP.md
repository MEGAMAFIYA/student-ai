# 🎨 Rasm chizish 1v1 — Telegram Direct Mini App sozlamasi

## Nega eski versiyada "Mini App ishga tayyor" chiqardi?

Eski `room_url()` quyidagi Main Mini App linkini ishlatgan:

`https://t.me/Student_ai_uz_bot?startapp=draw_<ROOM>`

Bu link botning **Main Mini App** routeriga boradi. Main app router `start_param`ni olmasa, `/webapp/index.html` dagi oddiy fallback matn ko‘rinadi.

Rasm duelini esa alohida **Direct Mini App** sifatida ochish kerak.

## BotFather'da bir marta

1. @BotFather'ni oching.
2. Botingizni tanlang.
3. Mini App / Apps bo‘limidan yangi Mini App yarating (`/newapp` yoki BotFather Mini App UI orqali).
4. Short name: `rasim`
5. Web App URL:
   `https://<SIZNING-RENDER-DOMENINGIZ>/miniapp/rasim/`
6. Saqlang.

Shundan keyin Direct Mini App manzili:

`https://t.me/Student_ai_uz_bot/rasim`

bo‘ladi.

## Kod nima qiladi?

Inline rejimda xona yaratilganda bot:

`https://t.me/Student_ai_uz_bot/rasim?startapp=draw_<ROOM>&mode=fullscreen`

linkini beradi.

Telegram shu `draw_<ROOM>` qiymatini Mini App `start_param`iga uzatadi. Frontend room ID'ni olib `/api/draw/join`ga yuboradi.

## Muhim

`.env`da quyidagini qo‘yish mumkin:

`DRAWING_APP_SHORT_NAME=rasim`

Agar BotFather'da boshqa short name tanlasangiz, shu qiymatni ham o‘zgartiring.
