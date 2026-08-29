"""
🎬🎵 /vid va /qo'shiq funksiyalari uchun yt-dlp asosidagi yordamchi
modul — HAR IKKALA handler (handlers/vid.py, handlers/qoshiq.py) shu
yerdagi funksiyalarni chaqiradi, yt-dlp bilan bevosita ishlash mantig'i
FAQAT shu yerda (duplicate kod yo'q).

MUHIM: bu yerdagi barcha funksiyalar SINXRON (blocking, tarmoq so'rovi +
disk yozish) — chaqiruvchi handler ularni albatta
`asyncio.to_thread(...)` orqali chaqirishi kerak, aks holda bitta
yuklab olish butun botni (event loop'ni) to'xtatib qo'yadi.

Har bir chaqiruv o'zining ALOHIDA vaqtinchalik papkasida ishlaydi
(chaqiruvchi tomonidan `tempfile.mkdtemp()` bilan yaratiladi va
ishlatilgandan keyin o'chiriladi) — shu orqali parallel
foydalanuvchilarning yuklab olishlari fayl nomi darajasida
ARALASHMAYDI.

CHEKLOV (mas'uliyat bilan ishlatish): bu modul faqat ochiq/ruxsat
etilgan manbalardan (yt-dlp qo'llab-quvvatlaydigan ommaviy platformalar)
YUKLAB OLISH uchun; hech qanday autentifikatsiya/pullik kontentni
chetlab o'tish, cookie o'g'irlash yoki geo-blocking'ni buzish logikasi
QASDDAN qo'shilmagan.
"""

import logging
import os
import shutil

import yt_dlp

logger = logging.getLogger(__name__)

# /qo'shiq MP3'ga o'tkazish uchun ffmpeg SHART — modul yuklanganda bir
# marta tekshiramiz (har bir yuklab olishda qayta tekshirmaslik uchun).
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
if not FFMPEG_AVAILABLE:
    logger.warning(
        "⚠️ ffmpeg topilmadi — /qo'shiq MP3'ga o'tkaza olmaydi. "
        "Serverga (Render/VPS) ffmpeg o'rnating: apt-get install -y ffmpeg."
    )


class DownloadError(Exception):
    """Foydalanuvchiga ko'rsatsa bo'ladigan, tushunarli xabar bilan xato."""


def _largest_file_in(dest_dir: str) -> str | None:
    candidates = [
        os.path.join(dest_dir, f) for f in os.listdir(dest_dir)
        if os.path.isfile(os.path.join(dest_dir, f))
    ]
    if not candidates:
        return None
    return max(candidates, key=os.path.getsize)


def _enforce_size_limit(filepath: str, max_mb: int) -> None:
    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    if size_mb > max_mb:
        try:
            os.remove(filepath)
        except OSError:
            pass
        raise DownloadError(
            f"❌ Fayl juda katta ({size_mb:.0f} MB). Telegram bot orqali "
            f"yuborish uchun {max_mb} MB dan kichik bo'lishi kerak."
        )


# ============================================================
# 🎬 /vid — video yuklab olish
# ============================================================

def download_video(url: str, dest_dir: str, max_mb: int, timeout_sec: int) -> str:
    """`url`dan videoni `dest_dir` ichiga yuklab, tayyor fayl yo'lini
    qaytaradi. Xatolikda `DownloadError` (foydalanuvchiga ko'rsatiladigan
    matn bilan) ko'taradi."""
    out_tmpl = os.path.join(dest_dir, "%(id)s.%(ext)s")
    ydl_opts = {
        "outtmpl": out_tmpl,
        # max_mb'dan sal pastroq formatlarni afzal ko'ramiz — shunda katta
        # fayl umuman yuklab olinmaydi (vaqt/trafik behuda ketmaydi),
        # topilmasa eng past sifatli mavjud formatga tushamiz.
        "format": (
            f"best[filesize<{max_mb}M][ext=mp4]/best[filesize<{max_mb}M]/"
            "best[ext=mp4]/best"
        ),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "socket_timeout": timeout_sec,
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        logger.warning(f"🎬 /vid yuklab olishda xato ({url}): {e}")
        raise DownloadError(
            "❌ Bu havoladan video yuklab bo'lmadi. Havola noto'g'ri, video "
            "o'chirilgan/yopiq (private) bo'lishi yoki manba hozircha "
            "qo'llab-quvvatlanmasligi mumkin."
        ) from e
    except Exception as e:
        logger.error(f"🎬 /vid kutilmagan xato ({url}): {type(e).__name__}: {e}", exc_info=True)
        raise DownloadError("❌ Video yuklab olishda kutilmagan xatolik yuz berdi.") from e

    filepath = _largest_file_in(dest_dir)
    if not filepath:
        raise DownloadError("❌ Video yuklab olindi, lekin fayl topilmadi.")

    _enforce_size_limit(filepath, max_mb)
    return filepath


# ============================================================
# 🎵 /qo'shiq — qidirish (yuklamasdan) + audio yuklab olish
# ============================================================
# Qidiriladigan MANBALAR: har biri yt-dlp tomonidan RASMAN
# qo'llab-quvvatlanadigan, ochiq/qonuniy platforma (o'z "qidiruv"
# prefiksi bilan — xuddi "ytsearch" kabi). Ro'yxatga faqat shu tarzda
# ISHONCHLI qidiriladigan manbalar qo'shiladi — nomlari yt-dlp
# hujjatlarida rasman tasdiqlangan bo'lishi kerak; "oqayiq.uz" kabi
# litsenziyasiz mp3-agregator saytlar BUNGA KIRMAYDI va qo'shilmaydi
# (ular mualliflik huquqi egalaridan ruxsatsiz ishlaydi).
SEARCH_SOURCES = [
    {"id": "youtube", "label": "YouTube", "emoji": "▶️", "prefix": "ytsearch"},
    {"id": "soundcloud", "label": "SoundCloud", "emoji": "☁️", "prefix": "scsearch"},
]


def _search_one_source(prefix: str, query: str, count: int) -> list[dict]:
    """Bitta manbadan (masalan faqat YouTube yoki faqat SoundCloud)
    natijalarni yuklab olmasdan qaytaradi. Manba vaqtincha ishlamasa
    (tarmoq xatosi, bloklash va h.k.) BO'SH RO'YXAT qaytaradi — shu
    orqali bitta manbaning muammosi butun qidiruvni to'xtatib qo'ymaydi."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "default_search": f"{prefix}{count}",
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception as e:
        logger.warning(f"🎵 /qo'shiq: '{prefix}' manbasida qidiruv muvaffaqiyatsiz ('{query}'): {type(e).__name__}: {e}")
        return []
    return [e for e in ((info or {}).get("entries") or []) if e]


def search_tracks(query: str, count: int) -> list[dict]:
    """`query` bo'yicha BARCHA `SEARCH_SOURCES`'dan (hozircha YouTube va
    SoundCloud) qidirib, manbalar orasida taqsimlangan (har biridan
    taxminan teng ulush) `count` tagacha natijani qaytaradi:
    [{"source_id","source_label","source_emoji","title","duration",
    "uploader","webpage_url"}]. Har bir natija o'zining TO'LIQ
    havolasi (webpage_url) bilan qaytadi — shu orqali download_audio()
    qaysi manbadan yuklashni bilish uchun manba turini bilishi shart
    emas, faqat havolani ochadi."""
    per_source = max(1, -(-count // len(SEARCH_SOURCES)))  # yumaloq yuqoriga

    merged: list[dict] = []
    for src in SEARCH_SOURCES:
        raw_entries = _search_one_source(src["prefix"], query, per_source)
        for e in raw_entries:
            raw_ref = e.get("webpage_url") or e.get("url")
            if not raw_ref and src["id"] == "youtube":
                # YouTube'ning extract_flat natijalarida ko'pincha "url"
                # umuman bo'lmaydi, faqat "id" keladi — undan watch
                # havolasini o'zimiz quramiz.
                raw_ref = e.get("id")
            if not raw_ref:
                continue
            if raw_ref.startswith("http"):
                webpage_url = raw_ref
            elif src["id"] == "youtube":
                webpage_url = f"https://www.youtube.com/watch?v={raw_ref}"
            else:
                continue  # boshqa manba uchun ID'dan ishonchli havola qura olmaymiz
            merged.append({
                "source_id": src["id"],
                "source_label": src["label"],
                "source_emoji": src["emoji"],
                "title": (e.get("title") or "Noma'lum").strip(),
                "duration": e.get("duration"),
                "uploader": (e.get("uploader") or "").strip(),
                "webpage_url": webpage_url,
            })

    if not merged:
        raise DownloadError(f"❌ \"{query}\" bo'yicha hech qanday manbada natija topilmadi.")

    return merged[:count]


def download_audio(url: str, dest_dir: str, max_mb: int, timeout_sec: int) -> str:
    """Berilgan (to'liq, `search_tracks()` qaytargan) havoladan audio
    yuklab, uni MP3'ga o'tkazadi va tayyor .mp3 fayl yo'lini qaytaradi.
    Manba YouTube, SoundCloud yoki yt-dlp qo'llab-quvvatlaydigan boshqa
    har qanday sayt bo'lishi mumkin — bu funksiya manba turidan mustaqil
    ishlaydi.

    MUHIM: MP3'ga o'tkazish uchun serverda `ffmpeg` o'rnatilgan bo'lishi
    SHART. Agar topilmasa — jimgina boshqa formatga (webm/m4a) tushib
    qolish o'rniga ANIQ, tushunarli xato ko'taramiz (foydalanuvchi
    "MP3 kelmadi" deb administratorga murojaat qilib chalkashmasligi
    uchun)."""
    if not FFMPEG_AVAILABLE:
        raise DownloadError(
            "❌ Qo'shiqni MP3'ga o'tkazib bo'lmadi — serverda ffmpeg "
            "o'rnatilmagan. Administrator serverga ffmpeg o'rnatishi kerak "
            "(masalan: apt-get install -y ffmpeg)."
        )

    out_tmpl = os.path.join(dest_dir, "%(id)s.%(ext)s")
    ydl_opts = {
        "outtmpl": out_tmpl,
        "format": f"bestaudio[filesize<{max_mb}M]/bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "socket_timeout": timeout_sec,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        logger.warning(f"🎵 /qo'shiq audio yuklab olishda xato ({url}): {e}")
        raise DownloadError("❌ Bu qo'shiqni yuklab bo'lmadi. Birozdan so'ng qayta urinib ko'ring.") from e
    except Exception as e:
        logger.error(f"🎵 /qo'shiq audio yuklab olishda kutilmagan xato ({url}): {type(e).__name__}: {e}", exc_info=True)
        raise DownloadError("❌ Audio yuklab olishda kutilmagan xatolik yuz berdi.") from e

    # FFmpegExtractAudio ishlagach original (webm/m4a) fayl o'chiriladi va
    # faqat .mp3 qoladi — shu sabab eng katta fayl emas, ANIQ .mp3
    # kengaytmali faylni qidiramiz (papkada boshqa chalkash qoldiq
    # bo'lmasin).
    mp3_files = [
        os.path.join(dest_dir, f) for f in os.listdir(dest_dir)
        if f.lower().endswith(".mp3") and os.path.isfile(os.path.join(dest_dir, f))
    ]
    if not mp3_files:
        raise DownloadError("❌ Qo'shiq yuklab olindi, lekin MP3'ga o'tkazishda xatolik yuz berdi.")
    filepath = max(mp3_files, key=os.path.getsize)

    _enforce_size_limit(filepath, max_mb)
    return filepath
