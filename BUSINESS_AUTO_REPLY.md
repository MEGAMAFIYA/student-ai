# Telegram Business Auto Reply

`/bossiyons` Telegram Business akkaunt egasi uchun avtomatik shaxsiy javobni sozlaydi.

1. Egasi Telegram Business akkauntini botga ulaydi va botga xabarlarni o'qish/javob berish huquqini beradi.
2. Bot egasining shaxsiy chatidagi buyruqlar menyusiga `/bossiyons` ni qo'shadi.
3. Buyruq orqali javob matni kiritiladi va auditoriya tanlanadi:
   - `Yangi foydalanuvchilarga`: har bir shaxsiy chatga birinchi kelgan xabarda bir marta javob.
   - `Hammaga`: har bir kelgan shaxsiy xabarga javob.
4. Hech qanday oldindan yoki ommaviy xabar yuborilmaydi. Javob faqat incoming Telegram Business private-message update kelganda jo'natiladi.

Sozlama va ko'rilgan chatlar `business_storage.py` orqali mavjud persistence mexanizmida saqlanadi.
