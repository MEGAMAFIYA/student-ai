# Student AI — texnik yangilanish qaydlari

## Amalga oshirilgan o'zgarishlar

- Yangi foydalanuvchi uchun bir martalik 10 000 so'm welcome bonusi qo'shildi.
- `https://t.me/<bot>?start=ref_<user_id>` ko'rinishidagi doimiy referral havolasi qo'shildi.
- Referral orqali birinchi marta kirgan yangi foydalanuvchiga 10 000 so'm, taklif qiluvchiga 5 000 so'm beriladi. Ro'yxatga olish, bonuslar va tranzaksiya yozuvlari wallet lock ichida birga saqlanadi; takroriy `/start` bonusni qayta bermaydi.
- Avval botdan foydalangan foydalanuvchini aniqlash uchun `/start` profiling yozuvi bonus qaroridan keyinga ko'chirildi; doimiy `storage.all_users` ro'yxatida bor foydalanuvchiga referral bonusi berilmaydi.
- Balans yetarli bo'lmagandagi ekranga do'st taklif qilish tugmasi va shaxsiy referral sahifasi qo'shildi.
- Aqil charxi Telegram chatining o'zida qoladi; Mini App ochish talab qilinmaydi.
- Aqil charxi uchun 4×4, 4×6, 6×6 taxta va Mini App'dagi 6 turdagi belgilar tanlovi qo'shildi.
- 2 kishilik va jamoaviy rejim qo'shildi. Jamoalarda har bir tomonda 1–2 kishi bo'lishi mumkin. Navbat tartibi 1-guruh/2-guruhni navbatma-navbat aylantiradi; 2 ga qarshi 1 holatida ham yolg'iz jamoa a'zosi navbatida qayta qatnashadi.
- Navbat tekshiruvi server tomonda saqlanadi; navbati bo'lmagan foydalanuvchi katakni ocholmaydi.
- Chatdagi Aqil charxi, X-O va shashka o'yinlari tugagach qayta boshlash tugmasi qo'shildi.
- Yangi referral, jamoa navbatlari va qayta boshlash holatlari uchun testlar qo'shildi.

## Tekshiruv

Quyidagi fokuslangan testlar o'tdi:

```bash
python -m unittest tests.test_wallet tests.test_chat_games_audit tests.test_game_rules -q
```

Natija: barcha fokuslangan testlar muvaffaqiyatli.

To'liq test to'plamida esa o'zgartirilgan funksiyalarga aloqasi bo'lmagan mavjud muammolar kuzatildi: `test_drawing_room_url` import/stub xatosi va `test_pro_tabrik_business.DuplicateClickAndConcurrencyTests.test_concurrent_recipients_no_global_lock` qat'iy 60 ms vaqt chegarasidan oshishi (test muhiti taxminan 2 soniya o'lchadi). Shu sababli butun suite'ni to'liq yashil deb hisoblash mumkin emas.

## Muhim eslatmalar

- Telegram bot tokeni, API kalitlari yoki `.env` faylini repozitoriyga yuklamang. Paketga `.env` kiritilmagan.
- Referral ma'lumotlari mavjud wallet saqlash qatlamiga yoziladi. Ishlab turgan deploy'da `config.persist_read/persist_write` doimiy backend bilan sozlangan bo'lishi kerak.
- Telegram'da haqiqiy inline o'yin xabarlarini bir necha akkaunt bilan sinab ko'rish hali ham zarur; lokal unit testlar Telegram API bilan real ishlashni tekshirmaydi.
