# 🎨 Black Forest Labs va Stability AI + kurs ishi rasmlari

## Kalitlar
`/developer > 🔑 Kalitlar > 🔑 AI kalitlari` (admin) va `/my > 🔑 Shaxsiy kalitlarim` (foydalanuvchi)
menyularida **Black Forest Labs** va **Stability AI** boshqa providerlar bilan yonma-yon ko'rinadi.
Kalit → model (ro'yxatdan yoki qo'lda) → saqlash. Almashtirish, modelni o'zgartirish, o'chirish,
"🩺 tekshirish" (balans + model mavjudligi, **rasm yaratmaydi**), "🧪 Test rasm" (pullik, faqat admin) bor.
Kalitlar mavjud KEY_POOLS / user_ai_keys saqlash mexanizmida; xabarlarda maskalanadi; kalit yozilgan xabar o'chiriladi.

## Rasmiy API (tekshirilgan)
* BFL: `POST https://api.bfl.ai/v1/<model>` (`x-key`), `polling_url` orqali natija (`Ready` → `result.sample`, URL 10 daqiqa amal qiladi), balans `GET /v1/credits`.
  Modellar: flux-2-pro, flux-2-pro-preview, flux-2-flex, flux-2-max, flux-2-klein-4b/9b, flux-pro-1.1, flux-dev.
  **FLUX.1 [schnell] BFL rasmiy API'sida YO'Q** (faqat ochiq vaznlar) — shuning uchun qo'llab-quvvatlanmaydi.
* Stability: `POST /v2beta/stable-image/generate/{ultra|core|sd3}` (multipart, `Accept: image/*`), SDXL: `POST /v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image`, balans `GET /v1/user/balance`.

## Kurs ishi rasmlari
Kurs ishi matni tayyor bo'lgach AI rasm rejasini tuzadi → diagrammalar (blok-sxema, arxitektura, taqqoslash, statistik) **matplotlib** bilan aniq ma'lumotdan chiziladi (statistik qiymatlar bo'lim matnida bo'lishi shart), illyustratsiyalar BFL/Stability orqali yaratiladi → PDF'da tegishli kichik bo'limga "N-rasm. ..." izohi bilan qo'yiladi.
Foydalanuvchi sozlamalari: mavzu yuborish bosqichidagi "🖼 Rasmlar" tugmasi. Admin standarti: `/developer > Kalitlar > 🖼 Kurs ishi rasmlari`.

## ENV (hammasi ixtiyoriy)
`COURSE_IMAGES_ENABLED` (1/0), `COURSE_IMAGES_PROVIDER` (auto|bfl|stability), `COURSE_IMAGES_MODEL`,
`COURSE_IMAGES_TIMEOUT_SEC` (420), `IMAGE_HTTP_TIMEOUT_SEC` (60), `IMAGE_POLL_TIMEOUT_SEC` (150),
`IMAGE_MAX_RETRIES` (2), `IMAGE_CONCURRENCY` (2), `BFL_API_BASE` (EU/US uchun), `STABILITY_API_BASE`.
API kalitlari ENV'ga yozilmaydi — faqat /developer yoki /my orqali.
