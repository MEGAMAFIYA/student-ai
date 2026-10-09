# Diagnostics fix — Student AI

Ushbu yangilanish ikki muammoni tuzatadi:

## 🎨 `/rasim` / Vision AI

Har bir rasm yuborilishida quyidagi bosqichlar loglanadi:

- `DRAW_API_SUBMIT_REQUEST`
- `DRAW_SUBMIT_START`
- `DRAW_SUBMIT_ACCEPTED`
- `DRAW_EVAL_DISPATCH`
- `DRAW_AI_START`
- `DRAW_AI_ERROR` yoki `DRAW_AI_RESULT`
- `DRAW_EVAL_DONE`
- `DRAW_AI_SAVED`
- `DRAW_SHARE_PREPARE_START`
- `DRAW_SHARE_PREPARE_ERROR` yoki `DRAW_SHARE_PREPARE_SUCCESS`

Gemini'dan keladigan `status` va `detail` endi tashlab yuborilmaydi. Masalan:

- `quota` — limit tugagan
- `invalid` — API key/ruxsat muammosi
- `model_not_found` — model nomi topilmagan
- `bad_request` — Gemini so'rovi noto'g'ri
- `timeout`/`error` — boshqa API xatosi
- `parse_error` — Gemini JSON formatida javob bermagan

AI baholay olmasa, Telegram'dagi rasm captionida ham sabab ko'rsatiladi.

## 🎬 `/vid`

Instagram'ning:

`This content isn't available to everyone: It can't be seen by certain audiences.`

xatosi endi `Aniqlanmagan xato` bo'lib qolmaydi. Aniq Instagram auditoriya/login-cookies cheklovi sifatida tasniflanadi.

Shuningdek `/vid` uchun:

- `VID_REQUEST_START`
- `VID_DOWNLOAD_START`
- `VID_DOWNLOAD_ERROR`
- `VID_DOWNLOAD_NO_FILE`
- `VID_DOWNLOAD_SUCCESS`
- `VID_REQUEST_ERROR`

loglari qo'shildi.

## Muhim

Bu diagnostika yaxshilanishi Instagram tomonidan cheklangan Reel'ni chetlab o'tishini kafolatlamaydi. Agar Instagram kontentni serverga ochmasa, bot aniq sababni ko'rsatadi; kontentni yuklash uchun ruxsatli/public manba yoki mos autentifikatsiya kerak bo'lishi mumkin.
