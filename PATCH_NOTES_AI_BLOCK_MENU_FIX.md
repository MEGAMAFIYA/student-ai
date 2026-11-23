# AI blokirovkasi va menyu tuzatishlari

## Kiritilgan o'zgarishlar
- `feature_guard.py` orqali faol ConversationHandler, kutish holati va boshqa ishlayotgan update vazifalari tekshiriladi.
- Universal chat faol funksiya davomida AI javobini yubormaydi.
- AL Yordamchi va Telegram Business avtomatik javobi egasi boshqa funksiyada bo'lsa javob yubormaydi.
- AL Yordamchi callback query muddati o'tgan bo'lsa handler yiqilmaydi.
- AL Yordamchi sozlamalarini saqlash `asyncio.to_thread` orqali bajariladi.
- Foydalanuvchi kalitlari JSON faylini yozishda per-user va GitHub path lock qo'shildi; parallel yozuvlar bir jarayon ichida ketma-ket bajariladi.
- Kurs ishi va boshqa hujjat/PDF natijalaridagi menyu hujjatga emas, alohida matnli xabarga chiqariladi. Shu sababli menyu tugmalari media xabarni `edit_message_text` qilishga urinmaydi.

## Tekshiruv
- `compileall` muvaffaqiyatli.
- `tests/test_business_storage.py`: 14 passed.
- `tests/test_wallet.py`: 59 passed.
- `tests/test_pptx_wizard.py`: 44 passed.
- To'liq test yig'ish muhiti 3 ta mustaqil dependency/import xatosi sabab to'xtadi (`google.generativeai` yo'q; lokal `httpx` modulida `Timeout` yo'q).

## Muhim
`.env` fayli xavfsizlik sababli tarqatma ZIP ichiga qo'shilmadi. Deploydan oldin loyihangizdagi mavjud `.env` yoki Render Environment Variables qiymatlarini saqlab qoling.
