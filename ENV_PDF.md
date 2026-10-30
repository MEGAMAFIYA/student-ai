# ENV PDF — eksport / import

`/developer → RENDER → servis → ⚙️ Sozlamalar` ekranida ikki yangi tugma:

- **📄 ENV'larni PDF qilish** — servisdagi BARCHA environment variable'lar (KEY + maxfiy qiymat)
  bitta PDF qilib yuboriladi.
- **📥 ENV PDF yuklash** — bot PDF so'raydi; yuborilgan PDF'dagi hamma ENV tanlangan servisga
  o'rnatiladi (mavjudining qiymati almashadi, PDF'da yo'qlari o'chirilmaydi). Keyin 🚀 Deploy qiling.

Boshqa Render hisobiga ko'chirish: eski servisda PDF oling → yangi hisobda botni zipdan ishga
tushiring → yangi servisda «ENV PDF yuklash». PDF'da `RENDER_API_KEY*` / `RENDER_OWNER_ID*` /
`RENDER_SERVICE_ID*` bo'lsa, bot so'raydi: hammasini o'rnatish yoki ularsiz o'rnatish
(yangi servis eski hisobni boshqarib qolmasligi uchun).

PDF tuzilishi: ko'rinadigan KEY/qiymat ro'yxati + ichiga biriktirilgan `render_env_data.json` +
zaxira sifatida sahifa oxirida siqilgan matn (`ENVPDF:xxxxx:...`). SHA-256 nazorat summasi bor.
PDF'ni tahrirlamang. Fayllar: `render_env_pdf.py`, `render_api.list_env_vars_all`,
`handlers/developer.py` (renvpdf / renvpdfup / renvpdfgo), `bot.py` (DEV_WAIT_ENVPDF).
