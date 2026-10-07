# Student AI — Draw/Kino fix

## Tuzatilgan muammolar

### 1. Rasm AI timeout
- Gemini multimodal timeout 90 soniyadan 120 soniyaga moslashtirildi.
- `/api/draw/submit` ichidagi tashqi future timeout 60 soniyadan 130 soniyaga moslashtirildi; endi ichki 90/120 soniyalik AI timeoutdan oldin HTTP handler noto'g'ri timeout bermaydi.
- `GEMINI_TIMEOUT_SEC` va `DRAW_EVAL_TIMEOUT_SEC` Environment Variable orqali sozlanadi.

### 2. Vision API key
- `VISION_API_KEY` mavjud bo'lsa, `VISION` funksiyasi uchun u `GEMINI_API_KEY`dan ustun ishlatiladi.
- Bo'sh persisted runtime key mavjud environment keyni bosib ketmasligi saqlab qolindi.

### 3. Inline duel natija xabari
- Inline Mini App'da bot foydalanuvchiga private `send_message` bilan o'z-o'zidan yozishga urinmaydi.
- `Forbidden: bot can't initiate conversation with a user` xatosining sababi bartaraf etildi.
- Yakuniy natija frontendning `/api/draw/status` polling orqali ko'rsatiladi.

### 4. Telegram yuqori X paneli bilan to'qnashuv
- Rasm chizish Mini App tepasiga 58px bo'sh zona qo'shildi.
- Kino Mini App tepasiga 58px bo'sh zona qo'shildi.
- Kino header tugmalari 58px pastga ko'chdi.
- Kino sticky player/header offsetlari yangi joylashuvga moslashtirildi.

## Render
Kerak bo'lsa:
- `GEMINI_TIMEOUT_SEC=120`
- `DRAW_EVAL_TIMEOUT_SEC=130`

Environment Variable sifatida berish mumkin. Kodda shu qiymatlar default sifatida ham mavjud.
