# Talaba AI — Android ilova (native Kotlin) — 1-bosqich

Bu — Telegram botdagi (`@Student_ai_uz_bot`) funksiyalarga ega, alohida
Android ilova (APK). Bot bilan **bir xil backend**dan foydalanadi —
hech qanday funksiya logikasi qayta yozilmagan, faqat qayta ishlatilgan.

## Nima ishlaydi (1-bosqich)

- **Login** — parol/SMS yo'q. "Telegramda kirish" bosiladi → Telegram
  ochiladi → botning shaxsiy chatida "START" bosiladi → ilova avtomatik
  kirgan bo'ladi.
- **STUDENT** tugmasi → botdagi `/start` menyusi bilan bir xil ro'yxat:
  - 💬 **UNIVERSAL CHAT** — to'liq ishlaydi (AI bilan suhbat, tarix bilan).
  - 📋 **Test/Viktorina** — to'liq ishlaydi: savollar soni, **Pro** rejim
    (variantlarsiz, matn javob) va **Jas** rejim (variantlar teskari) —
    botdagi inline `test <N> pro jas` bilan bir xil.
  - Qolgan bandlar (kurs ishi, tarjima, pptx va h.k.) — "Tez orada"
    yorlig'i bilan ko'rinadi, 2/3-bosqichda ulanadi.
- **KINO** tugmasi → kino katalogi + tomosha qilish (botdagi Watch Party
  streaming mexanizmi qayta ishlatiladi).
- Bosh ekranda TABRIK/RASM/VIDEO/QO'SHIQ/PRO/MENING KABINETIM tugmalari
  ham bor (bot buyruqlari bilan bir xil tartibda) — hozircha "Tez orada".

## Backend (Telegram bot loyihasida, allaqachon qo'shilgan)

Quyidagi fayllar bot loyihasiga (`student-ai-main/`) qo'shildi/o'zgardi —
**hech qanday alohida server kerak emas**, bot allaqachon ishlatayotgan
HTTP serverning o'zi (`bot.py > HealthHandler`) ishlatiladi:

- `mobile_auth.py` — YANGI. Login (deep-link + polling).
- `mobile_api.py` — YANGI. `/api/mobile/...` REST API.
- `movie_watch.py` — qo'shildi: `make_solo_stream_url()`.
- `bot.py` — 4 qatorli qo'shimcha (import + 2 ta route hook).
- `handlers/menu.py` — `start_cmd` ichida `login_<token>` payload'ini
  qabul qilish qo'shildi.

Botni **qayta ishga tushirish kifoya** — boshqa hech narsa o'zgartirish
shart emas.

## Ilovani sozlash

`app/build.gradle.kts` faylida:

```kotlin
buildConfigField("String", "BASE_URL", "\"https://student-ai-uz.onrender.com/\"")
```

`BASE_URL`'ni botingiz ishlab turgan HAQIQIY manzilga almashtiring
(Render'dagi https manzil, oxirida albatta "/" bilan). Bot username
ilova tomonida sozlanmaydi — login havolasini (`deep_link`) backend
o'zi (`config.BOT_USERNAME_FALLBACK` asosida) tayyorlab beradi.

## APK qanday yig'iladi

**A) Android Studio orqali (eng oson):**
1. Android Studio'da `File > Open` → shu papkani (`StudentAiApp/`) tanlang.
2. Gradle sinxronizatsiyasini kuting (birinchi marta internet kerak —
   kutubxonalarni yuklab oladi).
3. `Build > Build Bundle(s) / APK(s) > Build APK(s)`.
4. Tayyor `.apk` — `app/build/outputs/apk/debug/app-debug.apk`.

**B) GitHub Actions orqali (Android Studio shart emas):**
1. Shu papkani GitHub repozitoriyingizga yuklang (`git push`).
2. `.github/workflows/build-apk.yml` avtomatik ishga tushadi.
3. GitHub'da **Actions** bo'limi → oxirgi run → **Artifacts** →
   `student-ai-debug-apk` — shu yerdan `.apk`ni yuklab oling.

## Keyingi bosqichlar (2, 3, 4...)

Har bir yangi funksiya (kurs ishi, tarjima, pptx, pdf, rasim, tabrik,
vid, qo'shiq, wallet va h.k.) aynan shu naqsh bo'yicha qo'shiladi:
1. `mobile_api.py`ga yangi endpoint (mavjud `handlers/*.py` logikasini
   qayta ishlatib).
2. `network/ApiService.kt`ga mos so'rov.
3. Yangi Activity (yoki `ComingSoonActivity` o'rniga haqiqiy ekran).

Bu tartib botning ishlashiga HECH QANDAY ta'sir qilmaydi — backend'da
qo'shilgan hamma narsa faqat `/api/mobile/` yo'li ostida, alohida.
