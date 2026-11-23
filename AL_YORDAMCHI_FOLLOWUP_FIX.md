# AL Yordamchi — shaxsiy AI kaliti va Gemini modeli

## Amaldagi ishlash tartibi

- AL Yordamchi foydalanuvchidan o'z API kalitini so'raydi.
- Kalit `config.AL_USER_KEYS` ichida Telegram egasining ID raqamiga bog'langan holda alohida saqlanadi.
- Bu saqlash joyi `/developer` → `Kalitlar` → `AI kalitlari` sahifasida alohida AL kalitlari bo'limi sifatida ko'rsatiladi.
- `config.KEY_POOLS` va bot funksiyalarining `AI_FUNCTIONS` kalitlari AL Yordamchi tomonidan ishlatilmaydi.
- Kalit bir marta kiritiladi; keyingi `/al_yordamchi` chaqirig'ida saqlangan kalit olinadi.
- Kalit darhol saqlanadi; model keyin tanlanadi. Shu sababli model tanlash vaqtida bot qayta ishga tushsa ham kalit yo'qolmaydi.
- Kalit doimiy runtime backend (Upstash, Neon yoki GitHub) orqali saqlanadi. Bunday backend bo'lmasa, bot kalit saqlandi deb tasdiqlamaydi.
- AL Business xabarlari boshqa bot funksiyalarining faol wizard holatlari bilan bloklanmaydi.

## Gemini

- AL uchun tavsiya etiladigan Gemini modeli `gemini-3.8-flash`.
- Eski saqlangan `gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-2.5-flash-lite` va `gemini-3.6-flash` sozlamalari `gemini-3.8-flash` ga avtomatik yangilanadi.

## Migratsiya

Oldingi noto'g'ri versiya AL API kalitini umumiy `KEY_POOLS` ga yozgan. Bu ro'yxatda bot funksiyalarining kalitlari ham bo'lishi mumkinligi sababli, qaysi kalit foydalanuvchining o'zi kiritganini xavfsiz aniqlab bo'lmaydi. Shu sababli eski umumiy pool'dan kalit avtomatik olinmaydi. Birinchi deploydan keyin foydalanuvchi o'z AL kalitini yana bir marta kiritadi; shundan so'ng u AL uchun alohida saqlanadi va qayta so'ralmaydi.
