# 🎨 Rasm chizish 1v1 — Telegram Direct Mini App sozlamasi

## Nega "Telegram orqali ochilmagan" xatosi chiqardi?

Eski oqim Main Mini App routeridan `/miniapp/rasim/` sahifasiga `location.replace()` qilganda Telegram `initData` yo‘qolib qolardi. Natijada rasm chizish sahifasi ochilsa ham serverga foydalanuvchining tasdiqlangan Telegram sessiyasi yetib bormasdi.

Hozir router `Telegram.WebApp.initData`ni faqat shu Telegram originidagi `sessionStorage`ga vaqtincha saqlaydi, rasm Mini App esa uni o‘qib serverga yuboradi. Server baribir HMAC bilan tekshiradi.

Bundan tashqari, default oqim alohida Direct Mini App short name'ga bog‘lanmaydi: `t.me/<bot>?startapp=draw_<ROOM>` Main Mini App orqali ochiladi. Shu sabab `t.me/<bot>/rasim` bot profiliga qaytib ketishi muammosi ham yo‘q.

## BotFather'da bir marta

1. @BotFather'ni oching.
2. Botingizni tanlang.
3. Mini App / Apps bo‘limidan yangi Mini App yarating (`/newapp` yoki BotFather Mini App UI orqali).
4. Short name: `rasim`
5. Web App URL:
   `https://<SIZNING-RENDER-DOMENINGIZ>/miniapp/rasim/`
6. Saqlang.

Agar alohida Direct Mini App short name ishlatmoqchi bo‘lsangiz, `.env`ga:

`DRAWING_USE_DIRECT_APP_LINK=1`

qo‘ying. Shunda:

`https://t.me/Student_ai_uz_bot/rasim?startapp=draw_<ROOM>&mode=fullscreen`

ishlatiladi. Bu rejimda BotFather'da `rasim` short name mavjud bo‘lishi shart.

## Kod nima qiladi?

Default inline oqimda xona yaratilganda bot:

`https://t.me/Student_ai_uz_bot?startapp=draw_<ROOM>&mode=fullscreen`

linkini beradi. Bu Main Mini App orqali ochiladi va xona ID `start_param`dan olinadi.

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
- haqiqiy bot username startup paytida olinadi;
- default 1v1 havola Main Mini App deep-linkidan foydalanadi;
- Main Mini App -> `/miniapp/rasim/` o'tishida Telegram `initData` yo‘qolmaydi;
- `startapp` query/hash/initDataUnsafe variantlari frontendda qabul qilinadi;
- `room_id` qat'iy tekshiriladi;
- `_answer_rasim()` ichidagi noto‘g‘ri `user` o‘zgaruvchisi tuzatildi;
- Direct/Main Mini App uchun `answerWebAppQuery`ga tayanish olib tashlandi;
- chizilgan rasm Telegram 8.0+ `PreparedInlineMessage` + `WebApp.shareMessage()` orqali native ulashish oynasiga tayyorlanadi;
- ikkala rasm yuborilgandan keyingina Vision AI hakam ishga tushadi.
