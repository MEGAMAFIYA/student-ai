Student AI — mavzuga mos slayd dizayni

Ushbu paket Student AI repozitoriyasidagi quyidagi 3 faylni yangilash uchun tayyorlangan patch skriptini o'z ichiga oladi:

1. slide_themes.py
   - recommend_theme(topic) funksiyasi qo'shiladi.
   - Tibbiyot, texnologiya, moliya, tarix, tabiat, fan, ta'lim va ijodiy mavzular uchun mos dizayn aniqlanadi.

2. handlers/pptx_gen.py
   - Mavzu kiritilgach, avval tavsiya etilgan dizayn ko'rsatiladi.
   - Foydalanuvchi boshqa dizaynlarni ham ko'rib tanlashi mumkin.
   - Dizayn tanlangach, slaydlar soni, ramka, rang va shrift bosqichlari davom etadi.

3. tests/test_pptx_professional.py
   - Mavzu bo'yicha dizayn tavsiyasini tekshiruvchi test qo'shiladi.

QO'LLASH:
1. ZIP arxivini Student AI loyihasi papkasiga chiqaring.
2. apply_topic_slide_recommendation.py faylini loyiha ildizida saqlang.
3. Terminalda loyiha ildizidan quyidagini bajaring:
   python apply_topic_slide_recommendation.py
4. Keyin testlarni ishga tushiring:
   pytest -q tests/test_pptx_professional.py

Eslatma:
- Bu paket avtomatik ravishda GitHub'ga push qilmaydi va Render'ga deploy qilmaydi.
- Skript o'zgartirishlarni mahalliy loyihadagi fayllarga qo'llaydi.
- Skript mos kelmaydigan kod blokini topsa, xato bilan to'xtaydi va faylni qisman almashtirmaydi.
