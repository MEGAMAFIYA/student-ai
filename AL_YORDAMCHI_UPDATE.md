# AL YORDAMCHI + Business Auto Reply update

## O'zgargan
- Telegram Business auto-reply endi faqat `message.text` bo'lsa javob beradi.
- Voice, photo, video, document va boshqa media uchun statik auto-reply yuborilmaydi.
- Connected Business foydalanuvchilar uchun `/al_yordamchi` komandasi qo'shildi.
- AI key/model foydalanuvchining o'z `mening_kalitlarim.json` faylida `al_yordamchi` bo'limida saqlanadi.
- AI yordamchi botning umumiy AI keylariga fallback qilmaydi: faqat shu foydalanuvchi tanlagan provider/key/model ishlatiladi.
- AI memory uchun Neon yoki Supabase PostgreSQL connection string ishlatiladi.
- Xotira, peerlar va chat history alohida `al_*` jadvallarda saqlanadi.
- `Yangi foydalanuvchilar`, `Eski foydalanuvchilar`, `Maxsus tanishlar` rejimlari qo'shildi.
- Yangi foydalanuvchi bilan suhbat boshlanganidan keyin AI shu chatning davomiy xabarlariga ham javob beradi.
- Maxsus tanishlar audience rejimidan qat'i nazar ustuvor.
- Egasi Business xabar oqimi orqali barqaror fakt aytsa, AI ularni xotira DB'siga ajratib saqlashga harakat qiladi.

## Birinchi sozlash
1. Telegram Business akkauntni botga ulang.
2. `/al_yordamchi` ni bosing.
3. AI provider havolasidan key oling.
4. Provider tanlab, keyni yuboring.
5. Tavsiya qilingan modelni tanlang.
6. `🧠 Xotira qo'shish` orqali Neon yoki Supabase'dan PostgreSQL connection string yuboring.
7. `👤 Foydalanuvchi turi` dan `Yangi` yoki `Eski` ni tanlang.
8. Kerak bo'lsa `⭐ Maxsus tanishlar` orqali Telegram ID qo'shing.

## Muhim
`.env` fayli xavfsizlik sababli bu yangilangan ZIP ichiga kiritilmagan. Mavjud deploy `.env`/Environment Variables qiymatlarini o'zingiz saqlab qoling.
