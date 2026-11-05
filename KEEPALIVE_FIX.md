# 😴 Render "Uxlamaslik" (keep-alive) — tuzatishlar

**Asosiy kamchilik:** `render_keepalive.py` ishlardi, lekin ro'yxatga servis qo'shadigan tugma
(`/developer > RENDER > servis`) kodda umuman yo'q edi → ro'yxat doim bo'sh → hech qanday ping ketmasdi.

Tuzatildi:
- `/developer > ☁️ RENDER > servis` ekranida **😴 Uxlamaslikni yoqish/o'chirish** tugmasi va holat qatori
  (yoqilgan/o'chiq, oxirgi ping natijasi). Yoqilganda birinchi ping darhol fonda yuboriladi.
- Har servis alohida vazifada pinglanadi (bittasi sekin bo'lsa boshqasi kechikmaydi), oldingi ping tugamasa
  ustiga yangisi yig'ilmaydi; qat'iy 5 daqiqalik jadval (ping davomiyligi intervalga qo'shilib siljimaydi).
- Birinchi ping 20 soniya kechiktiriladi (HTTP server ko'tarilib olishi uchun).
- O'chirilgan servislar natijasi xotiradan tozalanadi; OK loglari faqat birinchi marta/tiklanganda INFO
  (kuniga 288 qator spam yo'q).
- `storage.get_keepalive/get_all_keepalive` lock ostida; `set_keepalive` URL'ni tekshiradi (faqat http/https).
- Ixtiyoriy: `KEEPALIVE_SELF=1` — ro'yxat bo'sh bo'lsa `RENDER_EXTERNAL_URL` (botning o'z manzili) ga ping
  (RENDER_API_KEY bo'lmasa ham ishlaydi). Standart holatda o'chiq.
