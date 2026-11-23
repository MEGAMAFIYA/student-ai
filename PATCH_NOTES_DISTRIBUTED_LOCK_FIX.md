# Render duplicate polling va GitHub 409 tuzatishi

Ushbu patch oldingi `student-ai-fixed.zip` ustiga qo'yish uchun mo'ljallangan.

## O'zgargan fayllar

- `bot.py` — `run_polling()` ishga tushishidan oldin GitHub'dagi umumiy `telegram_polling` lease-lock olinadi. Yangi Render process eski process lockni bo'shatmaguncha kutadi. Lease har 60 soniyada yangilanadi va bot to'xtaganda lock bo'shatiladi.
- `config.py` — GitHub Contents API yozuvlari uchun processlararo `github_contents_writes` lease-lock qo'shildi. Endi oddiy `threading.RLock` bilan birga Render'dagi alohida process/instance'lar ham bir vaqtda bir xil branch'ga yozmaydi. Lock eskirsa muddati tugagach qayta olinadi.

## Muhim sozlamalar

- Render'da bitta service va bitta instance qoldiring.
- Barcha deploy/processlar bir xil `GITHUB_TOKEN`, `GITHUB_REPO` va `GITHUB_BRANCH` dan foydalanishi kerak.
- GitHub token repo Contents API uchun `Contents: Read and write` huquqiga ega bo'lishi kerak.
- `.env` faylni ZIP'dan almashtirmang; Render Environment Variables o'z holicha qolsin.

## Ishlash tartibi

- Render rolling deploy vaqtida yangi process avval `telegram_polling` lockni kutadi; eski process to'xtab lockni bo'shatgach yangisi pollingni boshlaydi.
- GitHub'dagi barcha bot yozuvlari bitta `github_contents_writes` lock orqali navbat bilan bajariladi. Shu sababli bir nechta process bir faylning eski SHA qiymati bilan parallel PUT yubormaydi.
- Lock fayllari repo ichidagi `.bot_runtime_locks/` yo'lida vaqtincha yaratiladi va odatda ish tugagach o'chiriladi. Process kutilmaganda uzilsa lease muddati tugagach lockni boshqa process olishi mumkin.
- Telegram polling saqlab qolindi; Business update'lar uchun `Update.ALL_TYPES` o'zgartirilmadi.

## Tekshiruv

`compileall` muvaffaqiyatli. Tanlangan testlar: 117 passed.
