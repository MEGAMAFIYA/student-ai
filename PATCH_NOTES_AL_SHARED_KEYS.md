# AL Yordamchi — umumiy AI kalitlar to'plami

## O'zgarishlar
- AL Yordamchi endi foydalanuvchidan API key so'ramaydi.
- API kalitlar `/developer` → `Kalitlar` → `AI kalitlari` bo'limida boshqariladigan `config.KEY_POOLS` provider ro'yxatlaridan olinadi.
- `/al_yordamchi` faqat provider va modelni saqlaydi.
- Har bir AI so'rovida tanlangan provider pool'dagi kalitlar navbat bilan sinab ko'riladi.
- `user_ai_keys.py` AL sozlamalarini saqlaganda eski `key` maydonini chiqarib tashlaydi.
- Developer kalitlar sahifasida ushbu umumiy pool AL Yordamchi tomonidan ham ishlatilishi haqida izoh qo'shildi.

## Deploy
`handlers/al_yordamchi.py`, `handlers/developer.py`, `user_ai_keys.py` fayllarini almashtiring. `AL_YORDAMCHI_UPDATE.md` yangilangan hujjatdir.

## Eslatma
Mavjud `.env` va Render Environment Variables o'zgartirilmaydi. API kalitlar endi AL Yordamchi sozlamasiga emas, `/developer` → `Kalitlar` → `AI kalitlari` orqali tegishli provider pool'ga kiritiladi.
