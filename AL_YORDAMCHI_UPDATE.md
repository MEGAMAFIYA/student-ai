# AL YORDAMCHI + Business Auto Reply update

## O'zgargan
- Telegram Business auto-reply endi faqat `message.text` bo'lsa javob beradi.
- Voice, photo, video, document va boshqa media uchun statik auto-reply yuborilmaydi.
- Connected Business foydalanuvchilar uchun `/al_yordamchi` komandasi qo'shildi.
- AL YORDAMCHI uchun API kalitlar foydalanuvchining `mening_kalitlarim.json` faylida saqlanmaydi.
- AL YORDAMCHI administrator boshqaradigan `/developer` → `Kalitlar` → `AI kalitlari` umumiy `KEY_POOLS` ro'yxatidan foydalanadi.
- Foydalanuvchi faqat provider va modelni tanlaydi; shu provider uchun umumiy ro'yxatdagi kalitlar navbat bilan sinab ko'riladi.
- AL yordamchi uchun tanlangan provider kalitlari bo'lmasa, administrator avval `/developer` → `Kalitlar` → `AI kalitlari` bo'limidan kalit qo'shishi kerak.
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

## 2026-11-23 — AL kalitlarini umumiy to'plamga o'tkazish

- AL API kalit kiritish bosqichi olib tashlandi.
- `/al_yordamchi` provider/model tanlaydi, kalit esa `config.KEY_POOLS` dan olinadi.
- Birinchi kalit ishlamasa, shu provider pool'dagi keyingi kalitlar ham sinab ko'riladi.
- `user_ai_keys.get_assistant_config()` eski `key` maydonini qaytarmaydi; `save_assistant_config()` uni yozmaydi.
- `/developer` → `Kalitlar` → `AI kalitlari` matnida AL Yordamchi ham shu pool'dan foydalanishi ko'rsatildi.
