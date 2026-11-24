# AL Yordamchi routing va diagnostika tuzatishi

## Nima o‘zgardi

- AL Yordamchi yoqilgan bo‘lsa, egasining shaxsiy chatidagi oddiy matn `process_private_message()` orqali AL’ning o‘z shaxsiy provider kaliti va tanlangan modeliga yuboriladi.
- Router `group=-4` da, Universal Chat va ConversationHandler’lardan oldin ishlaydi. AL xabarni qabul qilgach `ApplicationHandlerStop` bilan keyingi handlerlarni to‘xtatadi.
- Universal Chat ichida ham ikkinchi himoya bor: AL yoqilgan holatda u xabarni qabul qilmaydi.
- `/bossiyons` uchun matn kiritish jarayoni AL chatidan oldin tekshiriladi; mavjud auto-reply sozlash oqimi saqlanadi.
- AL sozlash bosqichidagi xabarlar ham keyingi AI handlerlariga o‘tmaydi.
- AL shaxsiy chatida DB ulangan bo‘lsa suhbat tarixi va AI ajratgan xotira faktlari saqlanadi. DB vaqtincha ishlamasa AI javob berishda davom etadi va DB xatosi alohida loglanadi. DB ulanmagan bo‘lsa xotira faktlari saqlanmasligi logda ogohlantiriladi.
- Universal Chat’dagi xato javob logida mavjud bo‘lgan aniqlanmagan `chat` o‘zgaruvchisi xatosi tuzatildi; endi `update.effective_chat.id` ishlatiladi.

## Render loglarida qidiriladigan belgilar

| Log belgisi | Ma’nosi |
|---|---|
| `AL_ROUTER_REGISTERED` | Router to‘g‘ri ro‘yxatdan o‘tgan |
| `AL_ROUTE_SELECTED` | Xabar AL Yordamchiga yo‘naltirilgan |
| `AL_PRIVATE_REQUEST_START` | AL AI so‘rovi boshlandi; provider/model ko‘rsatiladi |
| `AL_PRIVATE_CONTEXT_LOADED` | Xotira va suhbat tarixi yuklandi |
| `AL_PRIVATE_AI_FAILED` | Provider/model javob bermadi; status/detail yoziladi |
| `AL_PRIVATE_REPLY_OK` | AL javobi Telegram’ga yuborildi |
| `AL_PRIVATE_CONTEXT_SAVED` | Tarix va xotira saqlandi |
| `AL_PRIVATE_CONTEXT_LOAD_FAILED` / `SAVE_FAILED` | DB o‘qish/yozish xatosi |
| `AL_PRIVATE_ROUTER_UNHANDLED_ERROR` | Router ichida ushlanmagan xato |
| `UNIVERSAL_CHAT_BLOCKED ... AL_PRIVATE_MODE_ACTIVE` | Universal Chat AL faol bo‘lgani uchun bloklandi |
| `UNIVERSAL_AI_REQUEST_START` / `RESPONSE_RECEIVED` / `REQUEST_FAILED` | Universal AI oqimining boshlanishi va yakuni |

## Log fayllari

- `logs/al_yordamchi.log` — AL Yordamchi diagnostikasi.
- `logs/business_autoreply.log` — Telegram Business auto-reply diagnostikasi.
- Ikkala logger ham stdout’ga ham yozadi, shuning uchun Render dashboard loglarida ham ko‘rinadi. Fayl yo‘llarini mos ravishda `AL_YORDAMCHI_LOG_FILE` va `BUSINESS_AUTOREPLY_LOG_FILE` bilan o‘zgartirish mumkin.

## Muhim maxfiylik

Logga foydalanuvchi xabarining to‘liq matni, API kalitlari yoki PostgreSQL connection string yozilmaydi. Faqat update/user/chat ID, matn uzunligi, provider/model, status, xato turi va davomiylik qayd qilinadi.

## Tekshiruv chegarasi

`py_compile` va tegishli mavjud testlar bajarildi. Ushbu offline muhitda `python-telegram-bot`, `google-generativeai` va to‘liq `httpx` runtime kutubxonalari o‘rnatilmagan; shu sababli haqiqiy Telegram polling, real AI API chaqiruvi va butun test to‘plamini ishga tushirish bu yerda tasdiqlanmadi. Render deploydan keyin `AL_ROUTER_REGISTERED`, `AL_ROUTE_SELECTED`, `AL_PRIVATE_REPLY_OK` va shu update uchun `UNIVERSAL_CHAT_BLOCKED` loglarini tekshirish kerak.
