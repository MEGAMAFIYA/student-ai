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


## ⚠️ 1v1 chatda tugma botga o'tib qolmasligi uchun tekshiruv

Bu oqim `https://t.me/<bot>/<short_name>?startapp=...` **Direct Mini App**
havolasidan foydalanadi. Telegram bu URL'ni Mini App sifatida ochishi uchun
BotFather'da aynan shu `short_name` mavjud bo'lishi shart.

Loyihada username endi startup paytida `getMe` orqali Telegram'dan olinadi,
shuning uchun `BOT_USERNAME` noto'g'ri/stale bo'lsa ham 1v1 havola avtomatik
ravishda haqiqiy bot username'iga tuziladi.

BotFather:
1. `/newapp` orqali rasm Mini App yarating.
2. Short name'ni masalan `rasim` qiling.
3. URL: `https://<RENDER-DOMEN>/miniapp/rasim/`
4. `.env`da `DRAWING_APP_SHORT_NAME=rasim` bo'lsin.
5. Botni restart/redeploy qiling.
6. 1v1 chatda `@Student_ai_uz_bot /rasim` ni qayta yuboring va yangi hosil
   bo'lgan xabardagi tugmani bosing.

Agar 4-qadamdagi short name BotFather'dagi nom bilan bir xil bo'lmasa,
Telegram havolani Mini App emas, bot profil/chat sifatida ochishi mumkin.

### Nima tuzatildi
- haqiqiy bot username startup paytida olinadi va Direct Mini App URL'ga qo'yiladi;
- Direct Mini App URL xato bo'lsa inline oqim jim ishlamay, logga aniq sabab yozadi;
- `startapp` query/hash/initDataUnsafe variantlari frontendda qabul qilinadi;
- `room_id` qat'iy tekshiriladi;
- mavjud `/rasim` oddiy oqimi va 1v1 duel oqimi bir-biriga aralashtirilmaydi;
- ikkala rasm yuborilgandan keyingina Vision AI hakam ishga tushadi.
