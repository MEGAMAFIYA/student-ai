Talaba AI — Referat/Insho bet soni muammosi uchun tuzatish

Repo tekshiruvi:
- Repozitoriy: MEGAMAFIYA/student-ai
- Branch: main
- Tekshirilgan fayllar: handlers/essay.py va bot.py
- Tekshiruvdagi blob SHA:
  handlers/essay.py: d5716faba1545020e9a4805e519ad18adef4188e
  bot.py:            61fc06a6dd86594b7bfeced55682f6be322f69e3

Nima tuzatildi:
1. Bet soni uchun 5, 10, 15, 20, 25, 30 inline tugmalari qo'shildi.
2. Tanlangan qiymat callback orqali es_pages ga yoziladi va keyingi bosqich mavzu so'raydi.
3. Matn orqali kiritish zaxira yo'li saqlandi, lekin faqat shu 6 qiymat qabul qilinadi.
4. bot.py dagi _al_private_text_router flow == "essay" bo'lsa, xabarni AL yordamchi routeriga bermaydi. Bu asosiy bloklanish sababini tuzatadi.
5. build_essay_conv ga bet tugmalari callback handleri qo'shildi.

Qo'llash:
1. ZIP ichidagi essay_pages_buttons.patch faylini repo ildiziga joylang.
2. Avval tekshiring: git apply --check essay_pages_buttons.patch
3. Tekshiruv xatosiz o'tsa: git apply essay_pages_buttons.patch
4. `git diff --check` va `python -m compileall handlers/essay.py bot.py` bajaring.
5. O'zgarishlarni commit/push qiling, keyin Render deploy loglarini tekshiring.

MUHIM:
- Bu ZIP to'liq loyiha nusxasi emas; ikki repo fayliga qo'llanadigan aniq unified diff.
- GitHub'ga yozish avval 403 bilan rad etilgan. Shu sababli repo'ga commit/push qilinmadi.
- Unified diff sintaksisi `git apply --numstat` orqali tekshirildi. Repo fayllarining to'liq checkout nusxasi bu muhitda yo'qligi sababli `git apply --check`ni aynan sizning checkout'ingizda bajarish shart. Sizdagi branch bu SHA'lardan keyin o'zgargan bo'lsa, git apply --check mos kelmasligini ko'rsatadi.
- Telegram/Render jonli muhitida ishga tushirib ko'rish bu sessiyada imkoni bo'lmadi; sintaksis va patch qo'llash tekshiruvlari alohida bajarilishi kerak.
