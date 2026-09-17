# APK yangilanishlari

Ilova keyingi APKlarni o'chirmasdan ustiga o'rnatishi uchun:

- `applicationId` o'zgarmasligi kerak: `uz.studentai.mobile`
- `versionCode` har buildda oshib boradi (`github.run_number`).
- APK doim bir xil signing key bilan imzolanadi.

GitHub repository -> Settings -> Secrets and variables -> Actions -> New repository secret orqali quyidagi 4 ta secret kerak:

- `ANDROID_KEYSTORE_BASE64` — doimiy `.keystore` faylining Base64 ko'rinishi
- `ANDROID_KEYSTORE_PASSWORD` — keystore paroli
- `ANDROID_KEY_ALIAS` — key aliasi
- `ANDROID_KEY_PASSWORD` — key paroli

Muhim: keystore yoki parolni repository ichiga qo'shmang.

Eslatma: avvalgi GitHub Actions debug APK boshqa, vaqtinchalik runner key bilan imzolangan bo'lsa, uni yangi doimiy key bilan imzolangan APK ustiga Android o'rnatmaydi. Bunday holatda faqat bir marta eski APKni olib tashlab, yangi update-ready APKni o'rnatish kerak. Shundan keyin keyingi versiyalar o'chirmasdan yangilanadi.
