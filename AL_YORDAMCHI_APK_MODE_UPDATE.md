# AL Yordamchi — APK rejimi yangilanishi

## Kiritilgan o'zgarishlar
- STUDENT menyusidagi eski `UNIVERSAL CHAT` bandi `AL YORDAMCHI` deb o'zgartirildi va bevosita AL chat rejimini ochadi.
- AL chat boshqa AI yo'llariga avtomatik o'tmaydi: AL endpoint xato bersa yoki internet uzilsa, foydalanuvchiga HTTP/xato sababi ko'rsatiladi. Universal chat yoki lokal model yashirincha javob bermaydi.
- `Eslab qol: ...` va `... ni eslab qol` shaklidagi buyruqlar AL foydalanuvchisining shaxsiy PostgreSQL xotirasiga saqlanadi. Saqlash muvaffaqiyatli bo'lsa alohida tasdiq, baza sozlanmagan yoki yozish xato bo'lsa aniq sabab qaytariladi.
- Android logcat'da AL rejimiga kirish/chiqish, so'rov, server javobi, amal turi va yakuniy holat yoziladi. Server logida ham so'rov, AI mavjudligi, xotiraga yozish va action parse holatlari qayd etiladi.

## Muhim sozlash
AL xotirasi ishlashi uchun foydalanuvchining AL Yordamchi sozlamalarida PostgreSQL `db_dsn` ulanishi bo'lishi kerak. AL AI javob berishi uchun foydalanuvchiga tegishli AL API provider/key/model (`config.get_al_user_key`) sozlangan bo'lishi kerak. Bu qiymatlar bo'lmasa, tizim boshqa universal AI'ga o'tmaydi va xato sababini ko'rsatadi.

## Tekshiruv
- `python -m py_compile mobile_api.py al_yordamchi_db.py` — muvaffaqiyatli.
- `pytest -q tests/test_mobile_login_api.py tests/test_mobile_auth.py` — 8 passed, 9 skipped.
- Ushbu muhitda Android Gradle Wrapper, `gradle` executable va Android SDK mavjud emas; shu sabab APK build hamda real telefonda ruxsat/telefon amallari sinovi bajarilmadi. Ularni qurilmada alohida tekshirish kerak.
