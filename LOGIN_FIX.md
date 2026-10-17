# Login / ro'yxatdan o'tish tuzatishlari

1. **Telegram ID orqali kod hech qachon yetib bormasdi** (asosiy sabab). `mobile_api.py` HTTP thread ichida
   `asyncio.run(bot.send_message(...))` chaqirardi — PTB klienti boshqa event loop'ga bog'langan, shuning uchun
   har safar xato -> "Avval /start bosing". Endi `_send_telegram_text()` Bot API'ga to'g'ridan-to'g'ri HTTPS so'rov yuboradi.
2. Kod xabarida `\\n` literal ko'rinardi -> tuzatildi (menu.py bonus xabari va developer.py ham).
3. `/start login_<token>` oqimi `return` qilib, foydalanuvchini botda ro'yxatdan o'tkazmasdi (hamyon, profil, bonus, menyu).
   Endi tasdiqlangandan keyin oddiy /start ham bajariladi; eskirgan havola uchun aniq xabar.
4. Rate-limit Render proksisining IP'si bo'yicha edi: barcha foydalanuvchilar bitta chelakda -> poll/login 429.
   Endi ishonchli X-Forwarded-For (eng o'ng yozuv, faqat peer ichki IP bo'lsa) ishlatiladi.
5. Kod xavfsizligi: bir ID'ga 45 soniyada 1 marta kod (brute-force va spam oldini olish); yetkazilmasa kod bekor qilinadi.
6. `mobile_auth.py`: SQLite ulanishlari yopilmasdi (leak) -> `_connection()`; `MOBILE_AUTH_DB_PATH` yozib bo'lmasa
   bot import paytida yiqilmaydi (vaqtinchalik faylga o'tadi); eski bazaga migratsiya.
7. Android: login ekrani ScrollView + kod bo'limi kod yuborilgach ochiladi, server xabarlarini ko'rsatadi (429/400/503),
   "Kod yuborish" uchun taymer; `authStart` HTTP xatolari; Telegram ochilmasa polling to'xtaydi.
8. Android `SessionManager`: Keystore buzilsa ilova ochilishda yiqilardi -> qayta tiklanadi.
9. Eski `tests/test_kino_watch.py` testi xavfsiz kod bilan ziddiyatda edi -> moslashtirildi. Yangi: `tests/test_mobile_login_api.py`.

MUHIM: `.env` ichidagi Neon `DATABASE_URL` paroli zip'da ochiq edi — uni Neon'da almashtiring va .env ni repoga qo'ymang.
