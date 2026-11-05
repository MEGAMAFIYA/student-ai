# 🧮 /hisobchi (faqat admin)

`/hisobchi` — botning shaxsiy chatida, faqat `ADMIN_IDS` ichidagilarga ishlaydi.
Ekranlar: bugun/jami hisobot • so'nggi ishlar (mavzu, foydalanuvchi, AI raqami, limit) •
AI kalitlari (gemini #2 ... bugun/jami, limit/xato) • funksiyalar • foydalanuvchilar •
`/hisobchi <id yoki @username>` — bitta foydalanuvchi kartasi.

Qotmasligi uchun: hook'lar faqat xotiraga yozadi (tarmoq/disk yo'q), xatolari yutiladi,
saqlash fon thread'da har 60 soniyada (HISOBCHI_SAVE_SEC), hajm chegaralangan.
O'chirish: `.env` da `HISOBCHI_ENABLED=0`.

Qo'shilgan hook'lar: ai_clients.py (ai_call), storage.py (record_usage/record_file),
wallet.py (_new_tx_locked), handlers/wallet_ui.py (finalize_success/failure),
handlers/course_work.py (mavzu), bot.py (group=-3 iz + /hisobchi).
Ma'lumot: `hisobchi_data.json` / Upstash kaliti `student_ai_hisobchi`.
