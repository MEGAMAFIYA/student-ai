"""
📡 "/qo'shiq" uchun QO'SHIMCHA (uchinchi) manba — Telegram PUBLIC
kanal/guruhlaridagi audio kontentni MTProto orqali (Telethon
kutubxonasi bilan) qidirish.

MUHIM CHEKLOV (kodda tuzatib bo'lmaydigan Telegram platforma cheklovi):
  - Oddiy Telegram Bot API bilan (bot tokeni orqali) boshqa
    kanal/guruhlarni "qidirish" MUMKIN EMAS — bot faqat o'zi A'ZO
    bo'lgan chatlarni ko'radi. Shuning uchun bu yerda ALOHIDA, ODDIY
    FOYDALANUVCHI hisobi (MTProto/Telethon) ishlatiladi.
  - Telegram'da HECH QANDAY usul (Bot API ham, MTProto ham) orqali
    "barcha ochiq kanallarni" GLOBAL qidirib bo'lmaydi — faqat ANIQ
    nomi/ID'si ma'lum bo'lgan (yoki hisob allaqachon a'zo bo'lgan)
    kanal/guruh ICHIDA qidirish mumkin. Shu sabab qidiriladigan
    kanallar ro'yxati ANIQ ko'rsatiladi: `config.TG_SEARCH_CHANNELS`.
  - Bu modul FAQAT public (hisob a'zo bo'lishi shart bo'lmagan, ya'ni
    Telegram tomonidan ochiq deb belgilangan) kanal/guruhlar bilan
    ishlaydi. Yopiq/xususiy guruhlarga ruxsatsiz kirish yoki Telegram
    cheklovlarini chetlab o'tish uchun HECH QANDAY logika QASDDAN
    qo'shilmagan — agar ko'rsatilgan kanal mavjud bo'lmasa yoki hisob
    unga kira olmasa, o'sha kanal shunchaki o'tkazib yuboriladi (xato
    butun qidiruvni to'xtatmaydi).

YOQISH: 4 ta environment variable TO'LIQ to'ldirilishi kerak — qarang
config.py > "Telegram public kontentidan qidirish" bo'limi:
  TG_API_ID, TG_API_HASH, TG_SESSION, TG_SEARCH_CHANNELS
Birortasi bo'lmasa `config.TG_SEARCH_ENABLED = False` bo'ladi va bu
modul UMUMAN chaqirilmaydi (video_tools.py orqali) — "/qo'shiq" avvalgidek
faqat YouTube+SoundCloud bilan ishlayveradi.

TG_SESSION QANDAY OLINADI (bir marta, LOKAL kompyuterda qilinadi):
    pip install telethon
    python -c "
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
with TelegramClient(StringSession(), API_ID, 'API_HASH') as c:
    print(c.session.save())
"
  (bu sizni telefon raqamingiz + Telegram'dan kelgan kod bilan
  tizimga kiritadi va bosilgan StringSession qiymatini chop etadi —
  shu qiymatni Render Environment Variable TG_SESSION sifatida
  saqlang, HECH QACHON repoga commit qilmang).
"""

import asyncio
import logging

import config

logger = logging.getLogger(__name__)

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False


class TelegramSearchError(Exception):
    """Foydalanuvchiga emas, faqat video_tools.py'ga (loglash/errors
    ro'yxati uchun) ko'rsatiladigan ichki xato."""


def _new_client() -> "TelegramClient":
    return TelegramClient(
        StringSession(config.TG_SESSION),
        int(config.TG_API_ID),
        config.TG_API_HASH,
        connection_retries=1,
        timeout=config.TG_SEARCH_TIMEOUT_SEC,
    )


def _format_title(msg) -> str:
    f = msg.file
    if f and (f.title or f.performer):
        parts = [p for p in (f.performer, f.title) if p]
        return " - ".join(parts)
    if f and f.name:
        return f.name
    return (msg.text or "Noma'lum audio")[:80]


async def _channel_display_title(client, channel: str) -> str | None:
    """Kanalning HAQIQIY ko'rsatiladigan nomini (masalan "Muzikalar
    UzMuz") oladi — natijalar ro'yxatida "Artist — Qo'shiq — Kanal"
    formatidagi "Kanal" qismi shu (username emas, chunki username
    ko'pincha o'qib bo'lmaydigan/notanish bo'ladi). Topib bo'lmasa (kanal
    o'chirilgan, kirish yo'q va h.k.) `None` qaytaradi — bunday holda
    natija kanal nomisiz (faqat sarlavha bilan) ko'rsatiladi, hech qanday
    "None"/generik qiymat QO'YILMAYDI."""
    try:
        entity = await client.get_entity(channel)
        title = (getattr(entity, "title", "") or "").strip()
        return title or None
    except Exception as e:
        logger.warning(f"📡 Telegram: '{channel}' kanal nomini olib bo'lmadi: {type(e).__name__}: {e}")
        return None


async def _search_channel(client, channel: str, query: str, limit: int, channel_title: str | None) -> list[dict]:
    """Bitta public kanal ICHIDA qidiradi. Kanal topilmasa/kira
    bo'lmasa yoki boshqa xato bo'lsa — BO'SH RO'YXAT qaytaradi (bitta
    kanalning muammosi qolganlarini to'xtatmasin)."""
    out: list[dict] = []
    try:
        async for msg in client.iter_messages(channel, search=query, limit=limit):
            if not (msg.audio or msg.voice):
                continue  # faqat audio/ovozli xabarlar — hujjat/video o'tkazib yuboriladi
            out.append({
                "source_id": "telegram",
                "source_label": "Telegram",
                "source_emoji": "📡",
                "title": _format_title(msg)[:120],
                "duration": msg.file.duration if msg.file else None,
                "uploader": (msg.file.performer if msg.file else "") or "",
                # "channel" — ro'yxatda ko'rsatiladigan HAQIQIY kanal nomi
                # (masalan "Muzikalar UzMuz"), performer/ijrochi EMAS —
                # ular allaqachon "title" ichida bor bo'lishi mumkin
                # (qarang: _format_title). Topilmasa — None (qarang:
                # video_tools.display_channel()).
                "channel": channel_title,
                # Foydalanuvchiga ko'rsatish/log uchun — YUKLASH uchun
                # emas (yuklash aynan tg_channel+tg_message_id orqali,
                # qarang: download_public_audio()).
                "webpage_url": f"https://t.me/{channel}/{msg.id}",
                "tg_channel": channel,
                "tg_message_id": msg.id,
            })
    except Exception as e:
        logger.warning(f"📡 Telegram qidiruv: '{channel}' kanalida muammo (o'tkazib yuborildi): {type(e).__name__}: {e}")
    return out


async def _search_public_audio_async(query: str, count: int) -> list[dict]:
    if not config.TG_SEARCH_ENABLED:
        return []
    if not TELETHON_AVAILABLE:
        logger.warning("📡 Telegram qidiruv YOQILGAN, lekin 'telethon' kutubxonasi o'rnatilmagan (requirements.txt'ni tekshiring).")
        return []

    client = _new_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            logger.error("📡 Telegram MTProto sessiyasi tasdiqlanmagan — TG_SESSION eskirgan/yaroqsiz bo'lishi mumkin.")
            return []
        per_channel = max(1, -(-count // max(1, len(config.TG_SEARCH_CHANNELS))))
        results: list[dict] = []
        for channel in config.TG_SEARCH_CHANNELS:
            channel_title = await _channel_display_title(client, channel)
            results.extend(await _search_channel(client, channel, query, per_channel, channel_title))
        return results[:count]
    finally:
        await client.disconnect()


async def _download_public_audio_async(tg_channel: str, tg_message_id: int, dest_path: str) -> str:
    client = _new_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise TelegramSearchError("Telegram MTProto sessiyasi tasdiqlanmagan (TG_SESSION eskirgan/yaroqsiz).")
        msg = await client.get_messages(tg_channel, ids=tg_message_id)
        if not msg or not (msg.audio or msg.voice):
            raise TelegramSearchError("Xabar endi mavjud emas yoki audio o'chirilgan.")
        saved_path = await client.download_media(msg, file=dest_path)
        if not saved_path:
            raise TelegramSearchError("Faylni yuklab bo'lmadi (download_media bo'sh natija qaytardi).")
        return saved_path
    finally:
        await client.disconnect()


# ============================================================
# SINXRON WRAPPER'LAR — video_tools.py bu ikkalasini
# `asyncio.to_thread(...)` orqali (boshqa yt-dlp funksiyalari bilan bir
# xil naqshda) chaqiradi, shuning uchun ular ODDIY (blocking) funksiya
# ko'rinishida bo'lishi kerak. Har biri o'zining ALOHIDA event loop'ida
# (`asyncio.run`) ishlaydi — worker thread'da (to_thread ichida) allaqachon
# faol loop bo'lmagani uchun bu xavfsiz.
# ============================================================

def search_public_audio(query: str, count: int) -> list[dict]:
    try:
        return asyncio.run(_search_public_audio_async(query, count))
    except Exception as e:
        logger.error(f"📡 Telegram qidiruvida kutilmagan xato ('{query}'): {type(e).__name__}: {e}", exc_info=True)
        return []


def download_public_audio(tg_channel: str, tg_message_id: int, dest_path: str) -> str:
    return asyncio.run(_download_public_audio_async(tg_channel, tg_message_id, dest_path))


# ============================================================
# 🔍 /developer > 🎵 Qo'shiq qidirish > "Tekshirish" uchun.
# ============================================================

async def _check_telegram_async() -> dict:
    """Haqiqiy MTProto ulanishini ochib, sessiya tasdiqlanganini va HAR
    BIR sozlangan kanalga kirish mumkinligini (get_entity — yengil,
    xabarlarni yuklab olmaydi) tekshiradi. Bitta kanal muammosi
    qolganlarini to'xtatmaydi."""
    client = _new_client()
    await client.connect()
    try:
        authorized = await client.is_user_authorized()
        if not authorized:
            return {"authorized": False, "ok_channels": 0, "bad_channels": []}
        ok_channels = 0
        bad_channels: list[tuple[str, str]] = []
        for channel in config.TG_SEARCH_CHANNELS:
            try:
                await client.get_entity(channel)
                ok_channels += 1
            except Exception as e:
                bad_channels.append((channel, f"{type(e).__name__}: {e}"))
        return {"authorized": True, "ok_channels": ok_channels, "bad_channels": bad_channels}
    finally:
        await client.disconnect()


def check_telegram_music_search() -> dict:
    """Qaytaradi: {"status": "off"|"ok"|"partial"|"error", "lines": [...]}.
    MAXFIY qiymatlarni (TG_SESSION, TG_API_HASH) HECH QACHON to'liq
    ko'rsatmaydi — faqat mavjud/mavjud emasligini ("✅ Mavjud" / "❌ Mavjud
    emas")."""
    if not config.is_music_source_enabled("telegram"):
        return {"status": "off", "lines": ["Holati: OFF (admin tomonidan o'chirilgan)"]}

    lines = ["Holati: ON"]
    missing = []
    if not config.TG_API_ID:
        missing.append("TG_API_ID")
    if not config.TG_API_HASH:
        missing.append("TG_API_HASH")
    if not config.TG_SESSION:
        missing.append("TG_SESSION")
    if not config.TG_SEARCH_CHANNELS:
        missing.append("TG_SEARCH_CHANNELS")

    lines.append(f"TG_API_ID: {'✅ Mavjud' if config.TG_API_ID else '❌ Mavjud emas'}")
    lines.append(f"TG_API_HASH: {'✅ Mavjud' if config.TG_API_HASH else '❌ Mavjud emas'}")
    lines.append(f"TG_SESSION: {'✅ Mavjud' if config.TG_SESSION else '❌ Mavjud emas'}")
    lines.append(
        f"TG_SEARCH_CHANNELS: {len(config.TG_SEARCH_CHANNELS)} ta kanal sozlangan"
        if config.TG_SEARCH_CHANNELS else "TG_SEARCH_CHANNELS: ❌ Mavjud emas"
    )

    if missing:
        lines.append(f"Sabab: {', '.join(missing)} sozlanmagan")
        return {"status": "error", "lines": lines}

    if not TELETHON_AVAILABLE:
        lines.append("Telethon: ❌ o'rnatilmagan")
        lines.append("Sabab: 'telethon' kutubxonasi o'rnatilmagan (requirements.txt'ni tekshiring, keyin qayta deploy qiling)")
        return {"status": "error", "lines": lines}

    lines.append("Telethon: ✅ o'rnatilgan")

    try:
        result = asyncio.run(_check_telegram_async())
    except Exception as e:
        logger.error(f"📡 Telegram tekshiruvida kutilmagan xato: {type(e).__name__}: {e}", exc_info=True)
        lines.append(f"Sabab: {type(e).__name__}: {e}")
        return {"status": "error", "lines": lines}

    if not result["authorized"]:
        lines.append("Telegram API ulanishi: ❌\nSabab: sessiya tasdiqlanmagan (TG_SESSION eskirgan/yaroqsiz)")
        return {"status": "error", "lines": lines}

    lines.append("Telegram API ulanishi: ✅ OK")
    ok_channels, bad_channels = result["ok_channels"], result["bad_channels"]
    if bad_channels:
        lines.append(f"Kanallarga qidiruv: 🟡 ({ok_channels} ta kanal OK, {len(bad_channels)} ta muammoli)")
        for ch, reason in bad_channels:
            lines.append(f"  ⚠️ @{ch}: {reason}")
        return {"status": "partial", "lines": lines}

    lines.append(f"Kanallarga qidiruv: ✅ OK ({ok_channels} ta kanal)")
    return {"status": "ok", "lines": lines}