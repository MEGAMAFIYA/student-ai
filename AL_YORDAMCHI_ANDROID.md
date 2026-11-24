# AL Yordamchi — Android integratsiyasi

## Nimalar qo'shildi

- APK universal chat oynasi endi **AL Yordamchi** sifatida ishlaydi.
- Ovozli kiritish Android Speech Recognizer orqali, ovozli javob esa Android Text-to-Speech orqali bajariladi.
- Backend `/api/mobile/assistant/plan` endpointi foydalanuvchi buyruqlarini cheklangan JSON-amal rejasiga aylantiradi.
- Agar foydalanuvchining Telegram AL Yordamchi uchun shaxsiy provider kaliti/modeli bo'lsa, mobil chat aynan shu kalit/modeldan foydalanadi. Aks holda mavjud universal AI sozlamasiga qaytadi.
- AL Yordamchi PostgreSQL xotirasi ulangan bo'lsa, global xotiralar o'qiladi va mobil chat tarixi `peer_id=-1` alohida suhbatida saqlanadi.
- Android amallarini faqat ilovaning allow-list qurilma moduli bajaradi; model Android API'larini to'g'ridan-to'g'ri chaqira olmaydi.

## Hozir qo'llab-quvvatlanadigan amallar

- Budilnik: `AlarmClock.ACTION_SET_ALARM` orqali soat ilovasiga yuboriladi.
- Kalendar: `ACTION_INSERT` orqali tadbir yaratish oynasi ochiladi; foydalanuvchi kalendarda saqlashni tasdiqlaydi.
- Eslatma: Android `AlarmManager` orqali mahalliy rejalashtiriladi, bildirishnoma ko'rsatiladi va qurilma qayta yoqilganda kelajakdagi eslatmalar qayta tiklanadi.
- Ilova ochish: launcher ilovalari ichidan nom bo'yicha topiladi.
- Kontakt qidirish: foydalanuvchi kontakt ruxsatini bergan bo'lsa ishlaydi.
- Qo'ng'iroq: telefon raqami topilgach tasdiqlash oynasi ko'rsatiladi va faqat telefon terish oynasi ochiladi; qo'ng'iroqni foydalanuvchi o'zi boshlaydi.
- Sozlamalar: Wi-Fi, Bluetooth, tovush, ekran, batareya, joylashuv yoki umumiy sozlamalar oynasi ochiladi.

## Ruxsatlar va Android cheklovlari

Android APK o'rnatish jarayonida barcha runtime ruxsatlarni oldindan berishga yo'l qo'ymaydi. Shu sabab birinchi ishga tushirishda alohida sozlash sahifasi chiqadi va mikrofon, kontaktlar hamda bildirishnomalar ruxsatlarini tizim oynalari orqali ketma-ket so'raydi. Foydalanuvchi istalgan ruxsatni rad etishi mumkin.

Kalendar tadbiri Android kalendar ilovasining tasdiqlash oynasi orqali saqlanadi; shu sabab ilovaga keng `READ_CALENDAR`/`WRITE_CALENDAR` ruxsatlari berilmaydi. Budilnik standart Android soat ilovasi orqali yaratiladi. Qo'ng'iroq `ACTION_DIAL` orqali amalga oshiriladi; `CALL_PHONE` ruxsati so'ralmaydi.

**Doimiy wake-word tinglash (masalan, “Hey AL”) hozircha yoqilmagan.** Android bunday rejim uchun default digital assistant/VoiceInteractionService roli, qo'shimcha tizim sozlamalari va qurilma ishlab chiqaruvchisi cheklovlarini talab qiladi. Hozir fon rejimida rejalashtirilgan eslatmalar ilova yopiq bo'lsa ham ishlaydi; ovozli buyruq mikrofon tugmasi orqali boshlanadi. Ilova doimiy mikrofon tinglashni yashirincha bajarmaydi.

## Ishlatish

1. APK'ga kirish uchun Student AI hisobingiz bilan tizimga kiring.
2. Telegramda `/al_yordamchi` orqali shaxsiy AI provider, API kaliti va modelni sozlang. Sozlanmagan bo'lsa, mobil yordamchi mavjud universal AI sozlamasidan foydalanadi.
3. Student → AL Yordamchi oynasini oching.
4. Matn yozing yoki mikrofon tugmasini bosing.
5. Qo'ng'iroq kabi muhim amal oldidan tasdiqlash so'raladi. Kalendar tadbiri va budilnikda tegishli Android ilovasida yakuniy saqlash/amal bajarish oynasi ko'rsatiladi.

## APK build

Mavjud `.github/workflows/build-apk.yml` workflow Android SDK/NDK va Gradle 8.7 bilan APK yig'adi. GitHub Actions'da workflow'ni `workflow_dispatch` orqali ishga tushirish mumkin. Mahalliy build uchun Android SDK, NDK `27.2.12479018`, CMake `3.22.1`, Gradle 8.7 va `llama.cpp` manbasi kerak.
