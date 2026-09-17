# Talaba AI — Android + Telegram bot

Bu repository Telegram bot va native Android ilovasini bitta backend bilan ishlatadi.

## Android ilovada ishlaydigan bo'limlar

- Telegram orqali parolsiz login
- Universal AI chat
- Kurs ishi / loyiha (PDF)
- Referat / insho (PDF)
- Tarjima
- Test / Viktorina (developer qo'shgan faol testlar + AI fallback, Pro/Jas)
- Masala yechish (matn va rasm)
- Konspekt qisqartirish
- Imlo / grammatika
- Iqtibos generatori
- Suratlarni PDF qilish
- PDF tahrirlash
- Qo'llanma tayyorlash
- PPTX taqdimot
- Mening fayllarim / kabinet
- Eslatmalar
- Balans va to'lovlar tarixi
- Tabrik / Pro tabriknoma
- Qo'shiq qidirish
- Video yuklash
- Rasm chizish Mini App havolasi
- Kino katalogi va server oqimi

Android ilova `mobile_api.py` orqali botning o'zi ishlayotgan HTTP serverga ulanadi. Telegram login ma'lumotlari serverda tasdiqlanadi; mijoz faqat server bergan session tokenni saqlaydi. Telegram Mini Apps uchun `initData` kabi autentifikatsiya ma'lumotlarini serverda tekshirish kerak. See Telegram documentation: https://core.telegram.org/bots/webapps

## Render

Render Environment Variables ichida `PUBLIC_BASE_URL` botning haqiqiy HTTPS manziliga o'rnatilishi kerak. Android `app/build.gradle.kts` ichidagi `BASE_URL` ham aynan shu manzilga mos bo'lishi kerak.

## GitHub Actions — Release / Latest

`.github/workflows/build-apk.yml` har bir `main` commit yoki manual workflow ishga tushirilganda signed Release APK yig'adi va GitHub Releases ichida `Latest` release yaratadi.

Doimiy update uchun bir xil signing key ishlatiladi. GitHub Secrets:

- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_ALIAS`
- `ANDROID_KEY_PASSWORD`

Birinchi marta o'rnatilgan APK boshqa signing key bilan imzolangan bo'lsa, Android uni boshqa kalitdagi APK ustiga o'rnatmaydi. Keyingi barcha buildlar esa aynan bir xil key bilan imzolansa, `applicationId` o'zgarmagan holda eski APK ustiga yangilanadi.
