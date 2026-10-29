# Qotish va database-lock tahlili (student-ai)

## Topilgan sabablar
1. O'yin xonalari, statistika, wallet va boshqalar SQLite'da emas — bitta katta JSON sifatida Neon Postgres'ga (`student_ai_kv`) yoziladi. Faqat `mobile_auth` SQLite ishlatadi.
2. `game._save()` har yurish/chat/**WebRTC signal (ICE)** da BUTUN xonalar JSON'ini Neon'ga sinxron yozardi, har safar yangi TCP+TLS ulanish bilan va global `LOCK` ichida. Mini App esa har 180/650 ms da polling qiladi, polling `_purge()` orqali shu LOCK'ni kutardi → qotish.
3. `storage.record_user/record_usage` ham event loop ichida sinxron Neon yozuvi qilardi.
4. `mobile_auth.get_user` har so'rovda 3 ta DELETE (yozuv-tranzaksiya) + global lock bajarardi.

## Qilingan o'zgarishlar (yangi servis yo'q, ma'lumot formati o'zgarmagan)
- `config.py`: Neon uchun qayta ishlatiladigan ulanish (+keepalive, uzilsa 1 marta avto-reconnect); `persist_write_deferred` — debounce/coalesce qiluvchi fon yozuvchi, chiqishda (`atexit`) flush. `PERSIST_DEFERRED=0` — eski sinxron rejim.
- `game.py`: signal bazaga yozilmaydi; xonalar kechiktirib yoziladi (JSON faqat qisqa LOCK ostida).
- `storage.py`: kechiktirilgan yozuv. Wallet/Pro obuna (pul) oldingidek darhol yoziladi (faqat tezroq ulanish bilan).
- `mobile_auth.py`: WAL bir marta, `synchronous=NORMAL`, cleanup daqiqada 1 marta, `get_user` keshli va global lock'siz, `BEGIN IMMEDIATE` read→write joyda.
- `cache.py`: ixtiyoriy Redis (`REDIS_URL`), yo'q/uzilgan bo'lsa xotira keshi (30 s circuit-breaker).
- `webapp_security.py`: initData tekshiruvi 60 s eslab qolinadi.
- `backup_data.py`: Postgres + SQLite + JSON zaxira (faqat o'qiydi).

## PostgreSQL baholash
- Loyiha allaqachon Postgres (Neon free) ishlatadi — migratsiya shart emas, muammo ishlatish usulida edi.
- Desktop Postgres tavsiya etilmaydi: kompyuter doim yoqiq bo'lishi, Render'ga ochiq port/tunnel (xavfsizlik) kerak, internet uzilsa bot to'xtaydi.
- SQLite (`mobile_auth`) yozuvlari kam; Render Free diski vaqtinchalik — deployda sessiyalar yo'qoladi (persistent disk pullik). Kerak bo'lsa keyingi qadam: sessiyalarni ham Neon'ga ko'chirish.

## Cheklov
Kechiktirilgan yozuvda kutilmagan o'chishda (SIGKILL) so'nggi ~1.5 s o'zgarish yo'qolishi mumkin (o'yin xonalari 12 soatlik vaqtinchalik ma'lumot). SIGTERM/deployda flush qilinadi.

## Xavfsizlik
Zip ichidagi `.env` da Neon DATABASE_URL paroli ochiq edi — Neon'da parolni almashtiring.
