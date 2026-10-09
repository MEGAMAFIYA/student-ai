# Student AI — APK yangilanish tizimi

Bu loyiha APKni GitHub Actions orqali **Release** sifatida chiqaradi.

## Natija

Har safar `main` branchga yangi commit tushganda:

1. `app-release.apk` build qilinadi.
2. `versionCode` avtomatik ravishda GitHub Actions run raqamiga oshadi.
3. APK `GitHub -> Releases` ichida yangi release sifatida chiqadi.
4. Yangi release `Latest` bo'ladi.
5. APK doim bir xil signing key bilan imzolanadi.

## Muhim: APKni ustiga o'rnatish

Android yangilanishni faqat quyidagilar bir xil bo'lsa qabul qiladi:

- `applicationId = uz.studentai.mobile`
- signing certificate/key
- yangi APKning `versionCode` qiymati eski APKnikidan katta bo'lishi

Shuning uchun `ANDROID_KEYSTORE_*` secretlari o'zgartirilmasligi kerak.

### Bir martalik GitHub Secrets

Repository -> **Settings -> Secrets and variables -> Actions -> New repository secret**:

- `ANDROID_KEYSTORE_BASE64` — doimiy `.keystore` faylining Base64 qiymati
- `ANDROID_KEYSTORE_PASSWORD` — keystore paroli
- `ANDROID_KEY_ALIAS` — alias
- `ANDROID_KEY_PASSWORD` — key paroli

Keystore faylini repositoryga commit qilmang.

### Base64 olish

Linux/macOS:

```bash
base64 -w 0 student-ai-release.keystore
```

macOS uchun:

```bash
base64 student-ai-release.keystore | tr -d '\n'
```

Chiqqan bitta uzun matn `ANDROID_KEYSTORE_BASE64` secretiga qo'yiladi.

## Eski APK haqida

Agar telefondagi hozirgi APK boshqa signing key bilan imzolangan bo'lsa, Android uni yangi key bilan imzolangan APK ustiga o'rnatmaydi. Bunday holatda **faqat bir marta** eski APKni olib tashlab, yangi signed Release APKni o'rnatish kerak. Shundan keyin shu workflow orqali chiqarilgan barcha keyingi versiyalar eski APKni o'chirmasdan ustiga yangilanadi.

## Nimalar o'zgartirildi

Faqat APK build/release tizimi o'zgartirildi:

- `.github/workflows/build-apk.yml`
- `APK_UPDATE_SIGNING.md`

Ilovaning Python/Kotlin/XML funksional kodi o'zgartirilmagan.
