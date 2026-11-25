STUDENT AI — MAVZUGA MOS DIZAYN TAVSIYASI

ZIP ichida:
- apply_topic_slide_recommendation.py — uchta faylga patch qo'llovchi skript.
- README_UZ.txt — yo'riqnoma.

QO'LLASH:
1. student-ai repozitoriyasining lokal nusxasini oching.
2. Shu ZIPni ochib, ikkala faylni student-ai papkasiga joylang.
3. Terminalni student-ai papkasida ochib bajaring:
   python apply_topic_slide_recommendation.py .
4. Test:
   pytest -q tests/test_pptx_professional.py
5. Uchta o'zgargan faylni tekshirib, GitHub main branch'ga commit/push qiling.
6. GitHub yangilangach Render auto-deploy qilishi mumkin.

O'ZGARISHLAR:
- slide_themes.py: mavzu kalit so'zlari asosida dizayn tavsiyasi.
- handlers/pptx_gen.py: mavzu kiritilgach avval tavsiya etilgan dizayn, keyin slaydlar soni.
- tests/test_pptx_professional.py: tavsiya funksiyasi uchun testlar.

MUHIM:
Bu ZIP to'liq repozitoriy emas. U lokal repozitoriydagi uchta faylni yangilaydigan patch-installer.
Skript GitHub'ga o'zi push qilmaydi va Render'ga deploy bermaydi.
