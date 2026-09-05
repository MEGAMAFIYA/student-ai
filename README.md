# 🤖 Talaba AI — Telegram bot

Talabalar uchun ko'p funksiyali AI yordamchi. `/start` bosilganda quyidagi
funksiyalar chiqadi:

## 📚 O'quv ishlari

- 📘 **Kurs ishi / loyiha** — bet soni va mavzu so'raladi, shu mavzu doirasida
  (undan chiqmasdan), so'ralgan bet sonidan kam bo'lmagan hajmda, titul +
  mundarija + kirish + 3 bob + xulosa + adabiyotlar bilan to'liq PDF yaratadi.
- 🗒 **Referat/Insho** — kurs ishidan yengilroq, tezroq: kirish, asosiy qism
  (2-4 kichik bo'lim), xulosa, adabiyotlar.
- 📊 **Taqdimot (PPTX)** — mavzu va slaydlar sonini bering, AI mavzuni
  mantiqiy slaydlarga bo'lib, chiroyli, izchil dizaynli `.pptx` fayl quradi.
- 📋 **Test/Viktorina** — mavzu bo'yicha 4 variantli savollar tuziladi,
  savollarga birma-bir javob berasiz, darhol to'g'ri/noto'g'ri ko'rinadi,
  oxirida ball va natija PDF holida ham olinadi.
- 🧮 **Masala yechish** — matematika/fizika/kimyo masalasini matn qilib
  yozing YOKI rasmini (qo'lda yozilgan bo'lsa ham) yuboring — bosqichma-bosqich
  yechim beriladi.
- 📑 **Konspekt qisqartirish** — uzun matn yoki PDF ni asosiy fikrlarni
  saqlagan holda ixcham konspektga aylantiradi.
- ✅ **Imlo/Grammatika tekshirish** — matnni tekshirib, tuzatilgan versiyasi
  va topilgan xatolar ro'yxatini beradi.
- 📚 **Iqtibos generatori** — manba ma'lumotlarini bering (kitob/maqola/sayt),
  GOST yoki APA uslubida to'g'ri formatlangan iqtibos tuziladi; bir nechtasini
  ketma-ket qo'shib, to'liq adabiyotlar ro'yxatini oling.

## 📄 Hujjatlar bilan ishlash

- 🌐 **Tarjima qilish** — matn yoki PDF qabul qilinadi, til tanlanadi
  (ruscha / kirilcha / lotincha (o'zbek) / inglizcha / boshqa), tarjima asl
  format (matn yoki PDF) da qaytariladi.
- 🖼 **Suratlarni PDF qilish** — bir nechta surat ketma-ket qabul qilinadi,
  tasdiqlangach barchasi bitta PDF ga joylanadi.
- 📝 **PDF ni tahrirlash** — PDF va kamchilik tavsifi qabul qilinadi, AI
  matnni ko'rsatmaga muvofiq tuzatib qayta PDF qilib beradi. (Matn asosida
  qayta tuziladi — original grafik dizayn saqlanmaydi.)
- 📖 **Qo'llanma tayyorlash** — savollar yuboriladi, har biriga AI javob
  yozib, savol-javob PDF qo'llanma tayyorlaydi.

## 🛠 Qulayliklar

- 💬 **UNIVERSAL CHAT** — asosiy suhbat. Oddiy savolga javob beradi; agar
  xabarda boshqa funksiyaga tegishli buyruq (masalan "10 betlik ... haqida
  kurs ishi yoz", "test tuz", "taqdimot tayyorla") bo'lsa, o'sha funksiyani
  o'zi ishga tushiradi yoki tavsiya qiladi.
- 🎙 **Ovozli xabar** — istalgan paytda ovozli xabar yuboring, bot tinglab,
  aytilganini yozma tasdiqlab, javob beradi (hech qanday tugma bosish shart
  emas).
- 🗂 **Mening fayllarim** — bot orqali yaratilgan barcha fayllar (kurs ishi,
  referat, taqdimot va h.k.) tarixi — qayta generatsiya qilmasdan, bir tugma
  bilan qayta yuklab olinadi.
- ⏰ **Eslatmalar** — "ertaga 09:00", "3 soatdan keyin" kabi vaqt bilan
  eslatma o'rnatiladi, belgilangan vaqtda bot avtomatik xabar yuboradi.
- 🔍 **Inline rejim** — istalgan chatda `@BotUsername savol` deb yozib,
  botni a'zo qilmasdan ham undan javob olish mumkin.

## Loyihaning tuzilishi

```
bot.py              — asosiy fayl: barcha handlerlarni ro'yxatdan o'tkazadi
config.py           — .env o'qish, /developer runtime sozlamalari, DOIMIY SAQLASH
storage.py          — fayllar tarixi / statistika / eslatmalar (doimiy saqlash)
ai_clients.py       — Gemini/Groq/zaxira AI chaqiruvlari (matn + rasm/audio)
pdf_tools.py        — barcha PDF generatorlari
pptx_tools.py       — taqdimot (PPTX) generatori
handlers/
  menu.py                — /start, bosh menyu
  universal_chat.py      — asosiy suhbat + intent aniqlash
  course_work.py         — kurs ishi
  essay.py               — referat/insho
  translate.py           — tarjima
  edit_pdf.py            — PDF tahrirlash
  images_to_pdf.py       — suratlar -> PDF
  guide.py                — qo'llanma
  pptx_gen.py             — taqdimot (PPTX)
  quiz.py                  — test/viktorina
  solve.py                 — masala yechish
  summarize.py             — konspekt qisqartirish
  grammar.py                — imlo tekshirish
  citation.py                — iqtibos generatori
  my_files.py                 — fayllar tarixi
  reminders.py                 — eslatmalar
  voice.py                      — ovozli xabar
  inline_query.py                — inline rejim
  developer.py                    — /developer admin paneli
```

## O'rnatish (lokal)

```bash
pip install -r requirements.txt
cp .env.example .env   # so'ng TELEGRAM_TOKEN va kamida bitta AI kalitni to'ldiring
python bot.py
```

## Render'ga joylash (Free Web Service)

1. Repository'ni Render'ga ulang -> New Web Service.
2. Build command: `pip install -r requirements.txt`
3. Start command: `python bot.py`
4. Environment bo'limida `.env` dagi barcha o'zgaruvchilarni qo'shing
   (kamida `TELEGRAM_TOKEN` va `GEMINI_API_KEY` shart).
5. Bot ishga tushgach, `/start` yuboring.

Bot ichida oddiy HTTP health-check server ham ishlaydi (Render "Free Web
Service" uyquga ketmasligi/portni ko'rishi uchun) — qo'shimcha sozlash shart
emas.

## 🎬🎵 /vid va /qo'shiq — YouTube "Sign in to confirm you're not a bot"

Bulutli serverlarning (shu jumladan Render) IP manzillarini YouTube ba'zan
"shubhali" deb belgilaydi va videoni bermay, ushbu xatoni qaytaradi. Buni
kamaytirish uchun `video_tools.py` avtomatik ravishda bir nechta
player_client bilan qayta urinadi, lekin ENG ISHONCHLI yechim — cookies
fayli qo'shish:

1. Kompyuteringizda brauzerga "Get cookies.txt LOCALLY" kengaytmasini
   o'rnating, youtube.com'da tizimga kiring (alohida/incognito oynada,
   keyin oynani YOPMASDAN eksport qiling — logout qilmang, aks holda
   cookies darhol yaroqsiz bo'lib qoladi) va `cookies.txt` faylini
   yuklab oling.
2. Render loyihangizda **Settings -> Secret Files** bo'limiga o'ting va
   shu faylni aynan **`cookies.txt`** nomi bilan, yo'li **`/etc/secrets/cookies.txt`**
   bo'ladigan qilib yuklang (Render Secret Files har doim shu papkaga
   joylaydi).
3. Boshqa hech narsa qilish shart emas — kod `/etc/secrets/cookies.txt`
   ni AVTOMATIK topadi. Xohlasangiz, boshqa yo'l ko'rsatish uchun
   ixtiyoriy environment variable ham qo'yishingiz mumkin:
   `YOUTUBE_COOKIES_FILE=/etc/secrets/cookies.txt` (yoki boshqa yo'l).

Cookies fayli topilmasa ham bot ishlashda davom etadi (faqat
player_client almashtirish orqali urinadi) — shunchaki bot-tekshiruvga
duch kelsa, foydalanuvchiga "cookies kerak" degan aniq xabar chiqadi,
bot yiqilib qolmaydi. Cookies faylini HECH QACHON kodga yoki repo'ga
committing qilmang — u faqat Render "Secret Files" orqali (yoki
mahalliyda `.gitignore`langan holda) saqlanishi kerak.

## ⚠️ DOIMIY SAQLASH — MUHIM

`/developer` orqali qo'shilgan AI kalitlar, "Mening fayllarim" tarixi,
statistika va eslatmalar standart holatda MAHALLIY faylga yoziladi.
Render kabi vaqtinchalik-disk hostinglarda bu fayl har qayta deployda
o'chib ketadi. Buni oldini olish uchun quyidagi uch variantdan
BITTASINI sozlang (`.env` ga qarang):

1. **Neon (Postgres)** (tavsiya etiladi — haqiqiy database) — neon.tech
   da bepul loyiha yaratib, "Connection string"ni `DATABASE_URL` sifatida
   qo'ying. Kerakli jadvalni kod birinchi ishga tushganda o'zi yaratadi.
2. **Upstash Redis** — console.upstash.com dan bepul Redis database
   yaratib, `UPSTASH_REDIS_REST_URL` va `UPSTASH_REDIS_REST_TOKEN` ni
   qo'ying.
3. **GitHub repo** — `GITHUB_TOKEN` (Contents: Read/write huquqi bilan) va
   `GITHUB_REPO` ni qo'ysangiz, har bir o'zgarish avtomatik repo'ga commit
   qilinadi (yuqoridagilar sozlanmagan bo'lsa ishlaydi).

`/developer` menyusining tepasida qaysi usul faol ekani doim ko'rsatiladi.

## Har bir funksiya uchun alohida AI

Har bir funksiya `.env` orqali mustaqil sozlanadi: `<FUNKSIYA>_PROVIDER`,
`<FUNKSIYA>_MODEL`, `<FUNKSIYA>_API_KEY` (kerak bo'lsa `<FUNKSIYA>_BASE_URL`).
To'liq ro'yxat `.env` faylida. Agar funksiyaga alohida qiymat berilmasa,
standart `GEMINI_API_KEY` va `gemini-3.6-flash` ishlatiladi. Bularning
barchasini bot ichidan, `/developer` buyrug'i orqali ham (kod
o'zgartirmasdan) boshqarish mumkin — bu tavsiya etiladi.

🧮 Masala yechish va 🎙 Ovozli xabar rasm/audio qabul qiladi, shuning uchun
har doim Gemini provideriga ega bo'lishi kerak (boshqa provayderlar bu
loyihada multimodal emas).

## Kalit olish (barchasi bepul, karta talab qilmaydi)

- **Gemini** — https://aistudio.google.com/apikey (Google AI Studio,
  Flash/Flash-Lite modellar bepul tier'da)
- **Groq** — https://console.groq.com/keys (llama-3.3-70b-versatile,
  llama-3.1-8b-instant, gemma2-9b-it va h.k. bepul)

Qo'shimcha zaxira: agar sozlangan provider ishlamasa (masalan limit tugasa),
bot avtomatik ravishda Groq'ga, undan keyin kalitsiz-bepul Pollinations text
API (text.pollinations.ai) ga o'tadi — hech qanday sozlash talab
qilinmaydi.

⚠️ Eslatma: AI provayderlarning bepul model nomlari va limitlari tez-tez
o'zgarib turadi. `*_MODEL` qiymatini istalgan vaqt `.env` yoki `/developer`
orqali yangilashingiz mumkin — kodni o'zgartirish shart emas.

## Muammoni aniqlash (loglar)

Har bir funksiya — tugma bosilishidan tortib, AI so'rovi, muvaffaqiyat yoki
xato sababigacha — batafsil logga yoziladi (Render -> Logs). Nimadir
ishlamasa, avval shu yerga qarang: qaysi funksiya, qaysi bosqichda, nima
sababdan (limit tugagan/kalit yaroqsiz/timeout/boshqa xato) ishlamaganini
aniq ko'rasiz.

## 🎁 /tabrik — Telegram Business orqali do'stning private chatida animatsiya

`/tabrik` botga (yoki inline: `@BotUsername /tabrik matn`) yuborilganda,
bot **Telegram Business API** orqali AYNAN qabul qiluvchi bilan bo'lgan
chatga (bot a'zo bo'lmagan, ikki foydalanuvchi orasidagi chat) audio + 5 ta
emoji (Telegram Message Effect bilan) + yakuniy tabrik matnini yuboradi.
Batafsil arxitektura va Telegram API cheklovlari: `tabrik_business.py`
faylining boshidagi izoh va suhbatdagi FINAL_REPORT.

### BotFather sozlamalari

1. [@BotFather](https://t.me/BotFather) → botingizni tanlang.
2. **Bot Settings → Business Mode** — yoqing (Telegram interfeysida bu
   band nomi versiyaga qarab "Business Mode" yoki "Secretary Mode" kabi
   biroz farq qilishi mumkin).
3. **Bot Settings → Inline Mode** — yoqilganligiga ishonch hosil qiling
   (loyihada allaqachon yoqilgan bo'lishi kerak, boshqa inline
   funksiyalar — `/qoshiq`, `/vid`, `/pro` — ham shunga tayanadi).

### Telegram Business ulash (foydalanuvchi tomonidan, /tabrik yuboruvchi A uchun)

1. Telegram ilovasida: **Settings → Telegram Business → Chatbots**
   (nomi versiyaga qarab farq qilishi mumkin).
2. Botni (`@Student_ai_uz_bot`) tanlang, ulang.
3. Botga kamida quyidagi huquqlarni bering:
   - **Reply to messages** (`can_reply`)
   - **Delete messages it sent** (`can_delete_sent_messages`)
4. Kerak bo'lsa, "Chat access" bo'limida qaysi chatlarga botga ruxsat
   berilishini tanlang ("All chats" tavsiya etiladi — aks holda faqat
   ruxsat berilgan chatlarda animatsiya ishlaydi).

Ulanish holati bot tomonidan avtomatik saqlanadi (`business_storage.py`,
Render qayta ishga tushganda ham yo'qolmaydi — qarang: "DOIMIY SAQLASH"
bo'limi yuqorida, xuddi shu Upstash/Neon/GitHub mexanizmi ishlatiladi).

### ENV

- `TABRIK_AUDIO_PATH` (ixtiyoriy) — animatsiya boshida yuboriladigan audio
  fayl yo'li. Standart: `assets/tabrik/tabrik_music.mp3`. Fayl mavjud
  bo'lmasa, animatsiya audiosiz davom etadi (`AUDIO_NOT_FOUND` logi
  bilan) — bot CRASH bo'lmaydi.
- `TABRIK_EFFECTS_JSON_PATH` (ixtiyoriy) — `emoji -> message_effect_id`
  xaritasi fayli. Standart: `data/telegram_message_effects.json`.

### Telegram API cheklovlari (yashirilmagan, kodda hisobga olingan)

- Business account nomidan yuborilgan xabarlarga `callback_data` tugma
  qo'yib bo'lmaydi — shuning uchun "🎁 Tabriknomani qabul qilish" tugmasi
  (boshlang'ich HAM, 120 soniyadan keyin qayta chiqadigani HAM) faqat
  ASL inline xabarda (`inline_message_id` orqali) bo'ladi.
- Recipient (qabul qiluvchi)ning `chat_id`i inline callback'da to'g'ridan
  to'g'ri kelmaydi — kod buni `query.from_user.id` (Bot API'ning shaxsiy
  chat modeli: `chat_id == boshqa tomon user_id`) orqali aniqlaydi. Bu
  rasmiy hujjatda so'zma-so'z "kafolatlangan" IBORA emas, balki Bot
  API'ning barqaror xatti-harakati — real Telegram bilan tekshirilishi
  tavsiya etiladi (qarang: FINAL_REPORT.md, 20-band).
- Bot audio xabarini yubora oladi, lekin Telegram klientidagi "Play"
  tugmasini masofadan bosolmaydi (avtoijro botga bog'liq emas).
- `can_reply`/business chat eligibility Telegram tomonidan cheklangan
  bo'lishi mumkin (masalan oxirgi 24 soatda faollik bo'lmagan chatlar) —
  bunday holatda animatsiya `TABRIK_BUSINESS_CHAT_NOT_ELIGIBLE` logi
  bilan to'xtaydi, foydalanuvchiga aniq sabab ko'rsatiladi, hech qachon
  botning shaxsiy chatiga yashirincha redirect qilinmaydi.


## 🎮🎥 1v1 O'yinlar — Shaxmat va Rus shashkasi

Istalgan 1:1 Telegram chatida `@Student_ai_uz_bot game` yozilsa, ikkita
o'yin chiqadi: **♟ Shaxmat** va **⚪ Rus shashkasi**. Tanlangan o'yin chatga
1v1 xona tugmasi bilan joylanadi. Ikkala foydalanuvchi tugmani bosib bir xil
Mini App xonasiga kiradi, **Oq/Qora** tomonini va dona ko'rinishini
(**Klassik/Kristall/Neon**) tanlaydi.

- Shaxmat yurishlari serverda tekshiriladi: shohga shax, rokada, en passant,
  piyodani avtomatik farzin qilish, mat/pat va 50-yurish durangi hisobga olinadi.
- Rus shashkasida urish majburiy, oddiy toshlar orqaga ham uradi, damka
  diagonal bo'ylab uzoqqa yuradi.
- O'yin holati qayta ishlashlar orasida persistent saqlashga yuboriladi
  (Upstash/Neon/GitHub sozlangan bo'lsa restartdan keyin ham qayta tiklanadi).
- Harakat, urish va yakun animatsiyalari bor; lokal ovoz effektlari mavjud.
- Mini App ichida WebRTC **kamera + mikrofon** mavjud. Qattiq NAT/operator
  tarmoqlarida barqarorlik uchun `GAME_TURN_URL`, `GAME_TURN_USERNAME`,
  `GAME_TURN_CREDENTIAL` sozlamalarini berish mumkin.

### O'yin Mini App'ini yoqish

BotFather'da bot uchun **Main Mini App** URL sifatida:
`https://YOUR-RENDER-DOMAIN.onrender.com/miniapp/`
qo'yiladi. Mavjud root router `startapp=game_<ROOM_ID>` ni avtomatik ravishda
`/miniapp/game/` ga olib o'tadi.

## 🎬 Kino storage architecture

Kino now supports an optional Cloudflare R2/S3-compatible media layer. New admin uploads are still accepted by Telegram and their `file_id` is retained as a fallback, but when R2 credentials are configured the bot imports the movie once into R2. The Mini App then receives a direct R2/CDN URL or a short-lived presigned URL, so Render does not proxy the video bytes for normal playback.

Required Render variables for R2: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`. For a CDN/public custom domain set `R2_PUBLIC_BASE_URL`; otherwise leave it empty and the backend creates a short-lived presigned URL.

Existing catalog movies without `r2_key` continue to use the Telegram/Render fallback until they are re-uploaded or migrated.
