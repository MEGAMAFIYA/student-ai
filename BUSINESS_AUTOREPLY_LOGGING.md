# Telegram Business Auto Reply diagnostika loglari

Auto Reply bilan bog'liq barcha muhim bosqichlar quyidagi loggerga yoziladi:

- Hosting stdout/stderr loglari (Render: Logs; Replit: Console)
- `logs/business_autoreply.log` fayli (3 MB dan keyin aylantiriladi; 3 ta zaxira)

Ixtiyoriy ravishda `BUSINESS_AUTOREPLY_LOG_FILE` environment variable orqali fayl yo'lini o'zgartirish mumkin.

## Tekshirish tartibi

1. Deploy/restartdan so'ng `BUSINESS_AUTOREPLY_LOGGER_READY` qatorini toping.
2. `BUSINESS_AUTOREPLY_HANDLERS_REGISTERED group=-4` va
   `BUSINESS_UPDATE_HANDLERS_REGISTERED group=-5` chiqishi kerak.
3. Business ulanishni qayta yoqing. Logda quyidagi ketma-ketlik bo'lishi kerak:
   - `BUSINESS_CONNECTION_UPDATE_RECEIVED`
   - `BUSINESS_CONNECTION_PERSISTED`
   - `COMMAND_MENU_START`
   - `COMMAND_MENU_SET_OK`
   - `COMMAND_MENU_VERIFY_OK`
   - `BUSINESS_CONNECTION_COMMAND_MENU_RESULT ... success=True`
4. Agar `BUSINESS_CONNECTION_UPDATE_RECEIVED` umuman bo'lmasa, Telegram update bot serveriga yetib kelmayapti. `BOT_POLLING_START` va polling xatolarini tekshiring.
5. Agar `COMMAND_MENU_SET_FAILED` bo'lsa, shu qatordagi Telegram API xatosi aniq sababni ko'rsatadi.
6. Agar `COMMAND_MENU_VERIFY_FAILED` bo'lsa, Telegram API buyruqni qayta o'qishda qaytarmagan.
7. `/bossiyons` yuborilganda `BOSSIYONS_COMMAND_RECEIVED` chiqishi kerak.
8. Kiruvchi Business xabar uchun `BUSINESS_MESSAGE_UPDATE_RECEIVED`, keyin `BUSINESS_AUTOREPLY_SEND_OK` yoki aniq `BUSINESS_AUTOREPLY_SKIP` / `BUSINESS_AUTOREPLY_SEND_FAILED` yoziladi.

Xavfsizlik: foydalanuvchi yozgan avtomatik javob matni logga yozilmaydi; faqat matn uzunligi, user/chat ID va texnik holatlar qayd etiladi.
