Student AI — mavzuga mos dizayn yangilanishi

ZIP ichida uchta TO‘LIQ Python fayli mavjud:
- slide_themes.py
- handlers/pptx_gen.py
- tests/test_pptx_professional.py

O‘rnatish:
1. student-ai repozitoriyasining asosiy papkasida ZIPni oching.
2. Fayllarni tegishli joylariga joylashtirib, mavjud fayllarni almashtiring.
3. Testlarni ishga tushiring: pytest -q tests/test_pptx_professional.py
4. Natija muvaffaqiyatli bo‘lsa, GitHub main branch'ga commit/push qiling.
5. Shundan keyin Render deploy qiladi (auto-deploy yoqilgan bo‘lsa).

O‘zgarish:
- Mavzu kiritilgach, recommend_theme() mos dizaynni tanlaydi.
- Bot avval tavsiya etilgan dizayn namunasini ko‘rsatadi.
- Foydalanuvchi dizaynni tasdiqlagach slaydlar sonini tanlaydi, so‘ng ramka, rang va shriftni tanlaydi.

Eslatma: fayllar sintaksis jihatidan tekshirildi. To‘liq integratsion pytest bu muhitda ishga tushirilmadi.
