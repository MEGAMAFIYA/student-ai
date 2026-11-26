Talaba AI — Referat/Insho bet sonini tugma orqali tanlash

Ushbu ZIP to‘liq loyiha arxivi emas; unda Git patch mavjud. GitHub’ga yozish ruxsati
403 bilan rad etilgani sababli, o‘zgartirishlarni avtomatik commit qila olmadim.

Qo‘llash:
1. Repozitoriy nusxasida ZIP ichidagi essay_pages_buttons.patch faylini loyiha ildiziga qo‘ying.
2. Terminalda: git apply essay_pages_buttons.patch
3. `git diff` bilan tekshiring.
4. Commit/push qiling va Render deploy loglarini kuzating.

O‘zgarishlar:
- Referat/Insho turi tanlangach 5, 10, 15, 20, 25, 30 bet tugmalari chiqadi.
- Tanlangan bet soni callback orqali saqlanadi va keyingi bosqich mavzuni so‘raydi.
- bot.py dagi ES_PAGES holatiga callback handler qo‘shiladi.

Eslatma: bu patch mavjud matn kontekstiga tayangan. `git apply --check` bilan avval
tekshirib oling; fayllar repozitoriyda o‘zgargan bo‘lsa, patchni moslashtirish kerak bo‘lishi mumkin.
