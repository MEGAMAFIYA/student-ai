🤖 Talaba AI — Telegram bot
/start bosilganda quyidagi funksiyalar chiqadi:
💬 UNIVERSAL CHAT — asosiy suhbat. Oddiy savolga javob beradi; agar
xabarda boshqa funksiyaga tegishli buyruq (masalan "10 betlik ... haqida
kurs ishi yoz") va yetarli ma'lumot bo'lsa, o'sha funksiyani o'zi ishga
tushirib, natijani qaytaradi. Yetarli ma'lumot bo'lmasa, kerakli funksiya
tugmasini taklif qiladi.
📘 Kurs ishi / loyiha — bet soni va mavzu so'raladi, shu mavzu doirasida
(undan chiqmasdan), so'ralgan bet sonidan kam bo'lmagan hajmda PDF yaratadi.
🌐 Tarjima qilish — matn yoki PDF qabul qilinadi, til tanlanadi
(ruscha / kirilcha / lotincha (o'zbek) / inglizcha / boshqa), tarjima asl
format (matn yoki PDF) da qaytariladi.
🖼 Suratlarni PDF qilish — bir nechta surat ketma-ket qabul qilinadi,
tasdiqlangach barchasi bitta PDF ga joylanadi.
📝 PDF ni tahrirlash — PDF va kamchilik tavsifi qabul qilinadi, AI matnni
ko'rsatmaga muvofiq tuzatib qayta PDF qilib beradi. (Matn asosida qayta
tuziladi — original grafik dizayn saqlanmaydi.)
📖 Qo'llanma tayyorlash — savollar yuboriladi, har biriga AI javob yozib,
kichik harflarda savol-javob PDF qo'llanma tayyorlaydi.
Loyihaning tuzilishi
Code
O'rnatish (lokal)
Bash
Render'ga joylash (Free Web Service)
Repository'ni Render'ga ulang → New Web Service.
Build command: pip install -r requirements.txt
Start command: python bot.py
Environment bo'limida .env.example dagi barcha o'zgaruvchilarni qo'shing
(kamida TELEGRAM_TOKEN va GEMINI_API_KEY shart).
Bot ishga tushgach, /start yuboring.
Bot ichida oddiy HTTP health-check server ham ishlaydi (Render "Free Web
Service" uyquga ketmasligi/portni ko'rishi uchun) — qo'shimcha sozlash shart emas.
Har bir funksiya uchun alohida AI
Har bir funksiya .env orqali mustaqil sozlanadi: <FUNKSIYA>_PROVIDER,
<FUNKSIYA>_MODEL, <FUNKSIYA>_API_KEY (kerak bo'lsa <FUNKSIYA>_BASE_URL).
To'liq ro'yxat .env.example faylida. Masalan:
Code
Agar funksiyaga alohida qiymat berilmasa, standart GEMINI_API_KEY va
gemini-3.6-flash ishlatiladi.
Faqat bepul AI'lar ishlatiladi
Funksiya
Tavsiya etilgan provider/model
Nega
💬 Universal chat
Gemini 2.5 Flash
Umumiy suhbat va routing uchun sifat/tezlik muvozanati yaxshi
📘 Kurs ishi
Gemini 2.5 Flash
Uzun, tuzilgan matn va katta kontekst oynasi kerak
🌐 Tarjima
Gemini 2.5 Flash
Ko'p tilli tarjima sifati yuqori
📝 PDF tahrirlash
Gemini 2.5 Flash
Uzun hujjat matnini tushunish kerak
📖 Qo'llanma (savol-javob)
Groq — llama-3.3-70b-versatile
Qisqa javoblar uchun juda tez va bepul
Kalit olish (barchasi bepul, karta talab qilmaydi):
Gemini — https://aistudio.google.com/apikey (Google AI Studio, Flash/Flash-Lite modellar bepul tier'da)
Groq — https://console.groq.com/keys (llama-3.3-70b-versatile, llama-3.1-8b-instant, gemma2-9b-it va h.k. bepul)
Qo'shimcha zaxira: agar sozlangan provider ishlamasa (masalan limit tugasa),
bot avtomatik ravishda Groq'ga, undan keyin kalitsiz-bepul
Pollinations text API (text.pollinations.ai) ga o'tadi — hech qanday
sozlash talab qilinmaydi.
⚠️ Eslatma: AI provayderlarning bepul model nomlari va limitlari tez-tez
o'zgarib turadi. *_MODEL qiymatini istalgan vaqt .env orqali yangilashingiz
mumkin — kodni o'zgartirish shart emas. Joriy bepul modellar ro'yxatini
https://ai.google.dev/gemini-api/docs/pricing va https://console.groq.com/docs/models
sahifalaridan tekshiring.
Keyingi qadamlar
Loyiha kelgusida yangi funksiyalar (masalan rasm generatsiyasi, ovozli xabar)
bilan kengaytirilishi mumkin — har biri uchun handlers/ ichida yangi modul
va config.py da yangi _cfg(...) qatori qo'shish kifoya.