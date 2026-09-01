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
import tempfile

import yt_dlp

import config

logger = logging.getLogger(__name__)


# ============================================================
# 🎞️ FFmpeg'ni topish (Render'da apt/root YO'Q!)
# ============================================================
# Render'ning standart Python muhitida (bu loyihada Dockerfile/apt.txt/
# build.sh orqali hech qanday tizim paketi o'rnatilmagan) `ffmpeg` PATH'da
# UMUMAN MAVJUD EMAS va uni apt-get bilan o'rnatib bo'lmaydi (root yo'q,
# fayl tizimi ko'p joyda read-only). Shu sabab avvalgi
# `shutil.which("ffmpeg")` productionda doim `None` qaytargan — bu esa
# ikkita YASHIRIN muammoga olib kelgan:
#   1) /vid — "bestvideo+bestaudio" birlashtirib bo'lmagani uchun faqat
#      progressiv (video+audio bitta oqimda) "best" formatga tushib
#      qolgan, YouTube esa (ayniqsa Shorts'larda) ko'pincha BUNDAY format
#      umuman taklif qilmaydi -> "Requested format is not available".
#   2) /qo'shiq — MP3'ga o'tkazib bo'lmagani uchun har doim xato qaytgan.
#
# YECHIM: `imageio-ffmpeg` pip paketi orqali STATIK ffmpeg binary'sini
# ishlatamiz — bu oddiy `pip install` bilan o'rnatiladi, root/apt/tizim
# ruxsati SHART EMAS, shu sabab Render'da ham ishlaydi. PATH'da tizim
# ffmpeg'i topilsa ham (masalan lokal rivojlantirishda) shundan
# foydalaniladi — imageio-ffmpeg faqat ZAXIRA sifatida ishlatiladi.
def _detect_ffmpeg() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and os.path.isfile(bundled):
            return bundled
    except Exception as e:
        logger.warning(f"⚠️ imageio_ffmpeg orqali ham ffmpeg topilmadi: {e}")
    return ""


# /qo'shiq MP3'ga o'tkazish HAMDA /vid video+audio birlashtirish uchun
# ffmpeg SHART — modul yuklanganda bir marta tekshiramiz (har bir yuklab
# olishda qayta tekshirmaslik uchun).
FFMPEG_PATH = _detect_ffmpeg()
FFMPEG_AVAILABLE = bool(FFMPEG_PATH)
if not FFMPEG_AVAILABLE:
    logger.warning(
        "⚠️ ffmpeg topilmadi (tizimda ham, imageio-ffmpeg orqali ham) — "
        "/qo'shiq MP3'ga o'tkaza olmaydi va /vid faqat progressiv "
        "formatlar bilan cheklanadi. `requirements.txt`ga "
        "`imageio-ffmpeg` qo'shilganini va o'rnatilganini tekshiring."
    )

# ============================================================
# 🍪 YouTube bot-tekshiruvi ("Sign in to confirm you're not a bot")
# ============================================================
# Bulutli serverlarning (Render va h.k.) IP manzillari YouTube tomonidan
# tez-tez "shubhali" deb belgilanadi va so'rov rad etiladi. Ikkita
# yordamchi chora birga ishlatiladi:
#   1) "player_client" ro'yxatini kengaytirish — ba'zan android/ios/tv
#      kabi client orqali so'rov bu tekshiruvni chetlab o'tadi (kafolat
#      yo'q, YouTube buni tez-tez o'zgartiradi).
#   2) COOKIES FAYLI (eng ishonchli, lekin qo'lda sozlash talab qiladi):
#      cookies fayli topilsa (pastga, `_resolve_cookies_file()` ga
#      qarang), yt-dlp shu orqali "haqiqiy tizimga kirgan foydalanuvchi"
#      sifatida so'rov yuboradi.
#
# Cookies fayli QAYERDAN qidiriladi (birinchi topilgani ishlatiladi):
#   1) YOUTUBE_COOKIES_FILE muhit o'zgaruvchisi — aniq ko'rsatilgan yo'l
#      (masalan Render Secret File uchun: YOUTUBE_COOKIES_FILE=/etc/secrets/cookies.txt)
#   2) /etc/secrets/cookies.txt — Render "Secret Files" standart joyi
#      (fayl nomini aynan "cookies.txt" qilib yuklasangiz, muhit
#      o'zgaruvchisini sozlamasangiz ham AVTOMATIK topiladi)
#   3) loyiha root papkasidagi cookies.txt (joriy ishchi papka —
#      Render'da bu repo root, `python bot.py` shu yerdan ishga tushadi)
# Fayl TOPILMASA — bu FATAL XATO EMAS: yt-dlp cookiessiz, faqat
# player_client almashtirish orqali urinishda davom etadi (pastga
# qarang), va agar shunda ham bot-tekshiruvdan o'ta olmasa, foydalanuvchiga
# ANIQ "cookies kerak" xabari qaytariladi (_classify_ytdlp_error).
#
# Eksport qilish yo'li (mahalliy brauzerdan): brauzerga "Get cookies.txt
# LOCALLY" kengaytmasini o'rnatib, youtube.com'da tizimga kirgan holda
# (yopib qo'ymasdan, alohida oynada) eksport qiling, faylni Render
# "Secret Files" bo'limiga "cookies.txt" nomi bilan yuklang.
#
# XAVFSIZLIK: cookies fayli HECH QACHON kodga hardcode qilinmaydi va
# HECH QACHON logga (fayl mazmuni) yozilmaydi — faqat FAYL YO'LI
# borligi/yo'qligi haqida xabar beriladi.
def _copy_cookies_to_tmp(source_path: str) -> str:
    """Topilgan cookies faylini `/tmp/cookies.txt`ga nusxalaydi va shu
    YOZISH MUMKIN BO'LGAN nusxaning yo'lini qaytaradi.

    NEGA SHART: yt-dlp `YoutubeDL` obyekti yopilganda (`with` blokidan
    chiqishda) ichki `save_cookies()` chaqirib, cookiejar'ni **xuddi shu
    `cookiefile` yo'liga qayta yozishga urinadi** — cookies o'zgarmagan
    taqdirda ham. Render'da "Secret Files" (`/etc/secrets/...`) READ-ONLY
    fayl tizimida joylashgan, shu sabab asl faylni to'g'ridan-to'g'ri
    `cookiefile` sifatida bersak, HAR BIR yuklab olish oxirida
    `OSError: [Errno 30] Read-only file system` bilan yiqiladi — hatto
    yuklab olishning o'zi muvaffaqiyatli bo'lsa ham. Shu sabab asl fayl
    FAQAT shu yerda BIR MARTA o'qiladi, yt-dlp'ga esa doim `/tmp/`
    ichidagi (yoziladigan) nusxa beriladi."""
    tmp_path = os.path.join(tempfile.gettempdir(), "cookies.txt")
    try:
        shutil.copyfile(source_path, tmp_path)
        return tmp_path
    except OSError as e:
        logger.warning(
            f"⚠️ Cookies faylini /tmp/ ga nusxalab bo'lmadi ({source_path} -> {tmp_path}): {e} "
            "— cookies ISHLATILMAYDI (asl faylni to'g'ridan-to'g'ri berish "
            "read-only xatoga olib kelishi mumkin)."
        )
        return ""


def _resolve_cookies_file() -> str:
    explicit = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    project_root_cookies = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    candidates = [explicit, "/etc/secrets/cookies.txt", project_root_cookies]
    for path in candidates:
        if path and os.path.isfile(path):
            # Asl faylni HECH QACHON to'g'ridan-to'g'ri yt-dlp'ga
            # bermaymiz — faqat o'qish uchun ishlatib, yoziladigan
            # nusxasini /tmp/ ichida tayyorlaymiz (pastga qarang).
            return _copy_cookies_to_tmp(path)
    if explicit:
        logger.warning(f"⚠️ YOUTUBE_COOKIES_FILE ko'rsatilgan, lekin fayl topilmadi: {explicit}")
    return ""


YOUTUBE_COOKIES_FILE = _resolve_cookies_file()
logger.info(
    "🍪 Cookies fayli (YouTube + Instagram/TikTok va h.k. uchun): "
    + (f"topildi ({YOUTUBE_COOKIES_FILE})" if YOUTUBE_COOKIES_FILE
       else "topilmadi — YouTube uchun faqat player_client almashtirish orqali urinib ko'riladi, "
            "login talab qiladigan boshqa manbalar (masalan Instagram) cookies'siz ishlamasligi mumkin")
)


def _youtube_extra_opts(player_client: str | list[str] | None = None) -> dict:
    """download_video/download_audio/_search_one_source uchun YouTube
    bot-tekshiruvini yumshatishga urinadigan qo'shimcha sozlamalar.
    `player_client` berilmasa standart ro'yxat ishlatiladi; retry
    zanjirida esa har safar BITTA client beriladi (pastga qarang)."""
    opts = {
        "extractor_args": {"youtube": {"player_client": player_client or ["android", "web_safari", "ios"]}},
        # Cookies yo'q bo'lsa ham, oddiy brauzer kabi ko'rinish uchun —
        # ba'zi shubha darajasini pasaytiradi (kafolat emas).
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
        },
    }
    if YOUTUBE_COOKIES_FILE:
        opts["cookiefile"] = YOUTUBE_COOKIES_FILE
    return opts


# Cookies fayli bo'lmaganda YouTube "bot" tekshiruvi ko'pincha faqat
# BAZI client'larda uchraydi (barchasini bitta so'rovda birga ishlatish
# ba'zan aksincha ko'proq shubha uyg'otadi). Shu sabab, bot-tekshiruv
# xatosiga uchraganda, HAR BIR client'ni ALOHIDA-ALOHIDA sinab ko'ramiz —
# birortasi o'tsa, shu yetarli.
#
# MUHIM: "tv" client cookies bilan BIRGA ishlatilmaydi — tv client
# boshqacha autentifikatsiya oqimidan foydalanadi va cookies bilan
# aralashtirilsa, eksport qilingan brauzer sessiyasini serverda haqiqatan
# ham tizimdan chiqarib yuborishi mumkin (cookies faylini "yoqib
# qo'yadi"). Shu sabab ikkita ALOHIDA ro'yxat ishlatiladi.
_YOUTUBE_CLIENTS_NO_COOKIES = ["android", "ios", "tv", "mweb", "web_safari"]
_YOUTUBE_CLIENTS_WITH_COOKIES = ["mweb", "web_safari", "android", "ios"]


def _youtube_retry_clients() -> list[str]:
    return _YOUTUBE_CLIENTS_WITH_COOKIES if YOUTUBE_COOKIES_FILE else _YOUTUBE_CLIENTS_NO_COOKIES


def _run_youtube_with_retries(build_opts_fn, url: str) -> None:
    """`build_opts_fn(youtube_opts) -> ydl_opts` — chaqiruvchi o'z asosiy
    ydl_opts'ini (`format`, `outtmpl` va h.k.) shu funksiya orqali
    yig'adi, `youtube_opts` esa har urinishda boshqacha (bitta)
    player_client bilan almashtiriladi. Bot-tekshiruv xatosiga
    uchraganda keyingi client bilan qayta urinadi; bot-tekshiruv
    bo'lmagan xato (masalan private video) darhol ko'tariladi —
    urinishlarni behuda sarflamaslik uchun. Cookies fayli mavjud bo'lsa
    birinchi urinishning o'zi odatda yetarli bo'ladi."""
    last_exc: Exception | None = None
    for client in _youtube_retry_clients():
        ydl_opts = build_opts_fn(_youtube_extra_opts(player_client=[client]))
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            return
        except yt_dlp.utils.DownloadError as e:
            last_exc = e
            if not _is_bot_check_error(e):
                raise  # bot-tekshiruv bo'lmagan xato — qayta urinish foydasiz
            logger.warning(f"🎬 YouTube '{client}' client bilan bot-tekshiruvga uchradi, keyingisi sinaladi ({url}).")
            continue
    raise last_exc


class DownloadError(Exception):
    """Foydalanuvchiga ko'rsatsa bo'ladigan, tushunarli xabar bilan xato."""


def _is_bot_check_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "sign in to confirm" in msg or "not a bot" in msg


def _is_drm_error(exc: Exception) -> bool:
    return "drm protected" in str(exc).lower()


def _classify_ytdlp_error(exc: Exception) -> str:
    """yt-dlp'dan kelgan xom xato matnini foydalanuvchiga ko'rsatsa
    bo'ladigan ANIQ, qisqa o'zbekcha sababga aylantiradi. Har doim biror
    narsa qaytaradi (aniqlab bo'lmasa — xomashyo xabarning qisqartirilgani).
    Bu FAQAT foydalanuvchiga ko'rsatiladigan matn uchun — xatoning TO'LIQ
    asl matni har doim chaqiruvchida logger.error orqali alohida yoziladi."""
    msg = str(exc)
    low = msg.lower()
    if _is_bot_check_error(exc):
        return "YouTube bu so'rovni \"bot\" deb bloklamoqda (bulutli server IP'si shubhali deb belgilangan)"
    if _is_drm_error(exc):
        return "Bu kontent DRM (litsenziya) himoyasi bilan qulflangan"
    if "private video" in low:
        return "Video shaxsiy (private) — ochiq emas"
    if "video unavailable" in low or "this video is unavailable" in low:
        return "Video mavjud emas (o'chirilgan yoki yopilgan)"
    if "unsupported url" in low or "no video formats found" in low or "unable to extract" in low:
        return "Bu havola/manba qo'llab-quvvatlanmaydi yoki sahifa tuzilishi o'zgargan"
    if "login" in low or "log in" in low or "authentication" in low:
        return "Bu kontentni ko'rish uchun manba tizimga kirishni (login/cookies) talab qilmoqda"
    if "requested format is not available" in low:
        return (
            "Bu video uchun mos video/audio format topilmadi (manba "
            "tomonidan cheklangan yoki login/cookies talab qilinishi mumkin)"
        )
    if "age" in low and "restrict" in low:
        return "Video yosh chegarasi (age-restricted) bilan himoyalangan"
    if "copyright" in low:
        return "Video mualliflik huquqi da'vosi sababli bloklangan"
    if "geo" in low and ("block" in low or "restrict" in low) or "not available in your country" in low:
        return "Video geografik hudud bo'yicha bloklangan"
    if "http error 403" in low or "403: forbidden" in low:
        return "Manba so'rovni rad etdi (HTTP 403 — kirish taqiqlangan)"
    if "http error 404" in low:
        return "Sahifa topilmadi (HTTP 404) — havola noto'g'ri yoki o'chirilgan"
    if "http error 429" in low or "too many requests" in low:
        return "Manba so'rovlar sonini cheklab qo'ydi (429 — birozdan so'ng urinib ko'ring)"
    if "timed out" in low or "timeout" in low:
        return "Manbadan javob kutish vaqti tugadi (timeout)"
    if "connection" in low or "network" in low or "name or service not known" in low or "temporary failure in name resolution" in low:
        return "Tarmoq xatosi — manbaga ulanib bo'lmadi"
    if "premieres in" in low or "live event will begin" in low:
        return "Bu — hali boshlanmagan jonli efir (premyera)"
    if "ffmpeg" in low or "postprocessing" in low:
        return "Faylni qayta ishlashda (ffmpeg) xatolik"
    # Aniqlanmagan holat — yt-dlp xabarining o'zini (qisqartirib) ko'rsatamiz,
    # shunda ham foydalanuvchi, ham admin ANIQ nima yozilganini ko'radi.
    short = msg.strip().splitlines()[0][:200] if msg.strip() else "noma'lum xato"
    return f"Aniqlanmagan xato — {short}"


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
    is_youtube = "youtube.com" in url or "youtu.be" in url

    def build_opts(youtube_opts: dict) -> dict:
        opts = {
            "outtmpl": out_tmpl,
            # QATTIQ formatga ("best[ext=mp4]" kabi, faqat progressiv —
            # video+audio bitta faylda birlashtirilgan — formatlarga)
            # bog'lanmaymiz: YouTube ko'p videolarda BUNDAY progressiv
            # mp4 formatni umuman taklif qilmaydi (faqat alohida video-only
            # va audio-only oqimlar bor), shu sabab qattiq selector
            # "Requested format is not available" bilan yiqilardi.
            #
            # O'rniga: avval max_mb'dan pastroq bestvideo+bestaudio'ni
            # afzal ko'ramiz (ffmpeg orqali birlashtiriladi), keyin hajm
            # cheklovisiz bestvideo+bestaudio'ga, keyin hajm bilan
            # progressiv "best"ga, oxirida cheklovsiz "best"ga tushamiz —
            # shu orqali YouTube'da MAVJUD bo'lgan eng yaxshi format
            # AVTOMATIK topiladi.
            "format": (
                f"bestvideo*[filesize<{max_mb}M]+bestaudio[filesize<{max_mb}M]/"
                "bestvideo*+bestaudio/"
                f"best[filesize<{max_mb}M]/best"
                if FFMPEG_AVAILABLE
                # ffmpeg bo'lmasa video+audio'ni birlashtira olmaymiz —
                # faqat allaqachon birlashtirilgan (progressiv) formatni
                # so'raymiz, aks holda yuklab olingan fayl ovozsiz qolishi
                # mumkin.
                else f"best[filesize<{max_mb}M]/best"
            ),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "socket_timeout": timeout_sec,
        }
        # Cookies faylini FAQAT YouTube uchun emas, BARCHA manbalar
        # (Instagram, TikTok, Facebook, Twitter/X va h.k.) uchun ham
        # qo'llaymiz. Sabab: bu opsiya avval `youtube_opts` ichida bo'lib,
        # faqat `is_youtube=True` bo'lganda yuklab olinar edi — natijada
        # Instagram Reels kabi ko'pincha LOGIN talab qiladigan
        # manbalarga cookies UMUMAN yuborilmagan. Cookies bo'lmasa yt-dlp
        # bunday sahifalardan formatlar RO'YXATINI umuman ololmaydi va
        # aynan shu "Requested format is not available" xatosini beradi —
        # garchi bu holatda haqiqiy sabab "format mos kelmadi" emas,
        # "kirish rad etildi" bo'lsa ham. Fayl faqat mos domen uchun
        # ishlatiladi — boshqa saytlarga zarar yetkazmaydi.
        if YOUTUBE_COOKIES_FILE:
            opts["cookiefile"] = YOUTUBE_COOKIES_FILE
        opts.update(youtube_opts if is_youtube else {})
        if FFMPEG_AVAILABLE:
            # Faqat ffmpeg mavjud bo'lgandagina video+audio'ni birlashtirib,
            # natijani mp4'ga sozlaymiz (loyiha MP4 kutmoqda).
            opts["merge_output_format"] = "mp4"
            # yt-dlp'ga ANIQ qaysi ffmpeg binary'sini ishlatishni
            # ko'rsatamiz — Render'da ffmpeg PATH'da emas (yuqoridagi
            # `_detect_ffmpeg()` ga qarang), shu sabab buni bermasak
            # yt-dlp ffmpeg'ni "topolmadi" deb hisoblab, birlashtirmasdan
            # xato beradi.
            opts["ffmpeg_location"] = FFMPEG_PATH
        return opts

    try:
        if is_youtube:
            # YouTube'ning "bot" tekshiruvi ko'pincha bitta-ikkita
            # client'da uchraydi — HAR BIRINI alohida sinaymiz, biri
            # o'tsa yetarli (pastga qarang: _run_youtube_with_retries).
            _run_youtube_with_retries(build_opts, url)
        else:
            with yt_dlp.YoutubeDL(build_opts({})) as ydl:
                ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        reason = _classify_ytdlp_error(e)
        logger.error(f"🎬 /vid yuklab olishda xato ({url}) — sabab: {reason} | asl xato: {e}")
        raise DownloadError(f"❌ Video yuklab bo'lmadi.\n\nSabab: {reason}.") from e
    except Exception as e:
        logger.error(f"🎬 /vid kutilmagan xato ({url}): {type(e).__name__}: {e}", exc_info=True)
        raise DownloadError(f"❌ Video yuklab olishda kutilmagan xatolik yuz berdi.\n\nSabab: {type(e).__name__}: {e}") from e

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


def _search_one_source(prefix: str, query: str, count: int) -> tuple[list[dict], str | None]:
    """Bitta manbadan (masalan faqat YouTube yoki faqat SoundCloud)
    natijalarni yuklab olmasdan qaytaradi. Manba vaqtincha ishlamasa
    (tarmoq xatosi, bloklash va h.k.) BO'SH RO'YXAT qaytaradi — shu
    orqali bitta manbaning muammosi butun qidiruvni to'xtatib qo'ymaydi.
    Qaytaradi: (natijalar, xato_sababi_yoki_None) — sabab faqat xato
    bo'lganda (natija topilmagani uchun emas) beriladi, shunda
    search_tracks() barcha manbalar muvaffaqiyatsiz bo'lsa foydalanuvchiga
    ANIQ sababni ko'rsata oladi."""
    base_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "default_search": f"{prefix}{count}",
        "noplaylist": True,
    }
    is_youtube = prefix == "ytsearch"
    try:
        if is_youtube:
            info = None
            last_exc: Exception | None = None
            for client in _youtube_retry_clients():
                opts = {**base_opts, **_youtube_extra_opts(player_client=[client])}
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(query, download=False)
                    break
                except Exception as e:
                    last_exc = e
                    if not _is_bot_check_error(e):
                        raise
                    continue
            if info is None and last_exc is not None:
                raise last_exc
        else:
            with yt_dlp.YoutubeDL(base_opts) as ydl:
                info = ydl.extract_info(query, download=False)
    except Exception as e:
        reason = _classify_ytdlp_error(e)
        logger.error(f"🎵 /qo'shiq: '{prefix}' manbasida qidiruv muvaffaqiyatsiz ('{query}') — sabab: {reason} | asl xato: {e}")
        return [], reason
    return [e for e in ((info or {}).get("entries") or []) if e], None


def _soundcloud_track_blocked_reason(webpage_url: str) -> str | None:
    """SoundCloud trekini YUKLAMASDAN, DRM/litsenziya sababli
    bloklanganini oldindan tekshiradi (to'liq extract, lekin
    skip_download=True — fayl yuklab olinmaydi, faqat metadata so'raladi).
    Bloklangan bo'lsa sababni qaytaradi, aks holda `None` (ya'ni normal
    yuklab bo'ladi)."""
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(webpage_url, download=False)
    except Exception as e:
        if _is_drm_error(e):
            return _classify_ytdlp_error(e)
        # DRM'dan boshqa xato bo'lsa (masalan vaqtinchalik tarmoq xatosi) —
        # bu yerda rad etmaymiz, chunki noto'g'ri (yolg'on) filtrlash
        # yaxshi natijalarni ham ro'yxatdan olib tashlashi mumkin;
        # haqiqiy yuklab olishda baribir aniq sabab bilan xato chiqadi.
        return None
    return None


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
    errors: list[str] = []
    for src in SEARCH_SOURCES:
        raw_entries, error_reason = _search_one_source(src["prefix"], query, per_source)
        if error_reason:
            errors.append(f"{src['label']}: {error_reason}")
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

            if src["id"] == "soundcloud":
                # DRM bilan qulflangan treklar YUKLASHGA umuman qo'yilmaydi
                # — shuning uchun ularni ro'yxatga chiqarishdan OLDIN
                # tekshirib, topilsa butunlay o'tkazib yuboramiz (foydalanuvchi
                # keyin "yuklab bo'lmadi" xatosiga duch kelmasin).
                blocked_reason = _soundcloud_track_blocked_reason(webpage_url)
                if blocked_reason:
                    logger.info(f"🎵 SoundCloud trek ro'yxatdan chiqarib tashlandi (bloklangan): {webpage_url} — {blocked_reason}")
                    continue

            merged.append({
                "source_id": src["id"],
                "source_label": src["label"],
                "source_emoji": src["emoji"],
                "title": (e.get("title") or "Noma'lum").strip(),
                "duration": e.get("duration"),
                "uploader": (e.get("uploader") or "").strip(),
                "webpage_url": webpage_url,
            })

    # 📡 UCHINCHI (IXTIYORIY) manba — Telegram public kanallari (MTProto).
    # Faqat TO'LIQ sozlangan bo'lsa ishga tushadi (config.TG_SEARCH_ENABLED),
    # aks holda bu blok butunlay o'tkazib yuboriladi — YouTube+SoundCloud
    # natijalariga hech qanday ta'sir qilmaydi. Xato bo'lsa ham (masalan
    # 'telethon' o'rnatilmagan yoki sessiya eskirgan) qolgan ikki manba
    # natijalari BUZILMAYDI — qarang: telegram_search.py.
    if config.TG_SEARCH_ENABLED:
        try:
            import telegram_search
            merged.extend(telegram_search.search_public_audio(query, per_source))
        except Exception as e:
            errors.append(f"Telegram: {type(e).__name__}: {e}")
            logger.error(f"🎵 /qo'shiq: Telegram manbasida qidiruv muvaffaqiyatsiz ('{query}'): {type(e).__name__}: {e}", exc_info=True)

    if not merged:
        if errors:
            # Barcha manbalar XATO bilan muvaffaqiyatsiz bo'ldi (shunchaki
            # natija topilmagani emas) — foydalanuvchiga ANIQ sababini
            # ko'rsatamiz, "hech narsa topilmadi" deb chalg'itmaymiz.
            logger.error(f"🎵 /qo'shiq: '{query}' — barcha manbalar muvaffaqiyatsiz: {'; '.join(errors)}")
            raise DownloadError(
                f"❌ \"{query}\" bo'yicha qidiruv amalga oshmadi.\n\nSabab: {'; '.join(errors)}."
            )
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
    is_youtube = "youtube.com" in url or "youtu.be" in url

    def build_opts(youtube_opts: dict) -> dict:
        opts = {
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
            # Render'da ffmpeg PATH'da emas — aniq binary yo'lini
            # ko'rsatmasak, FFmpegExtractAudio postprocessor uni
            # "topolmadi" deb ishlamay qoladi (yuqoridagi
            # `_detect_ffmpeg()` izohiga qarang).
            "ffmpeg_location": FFMPEG_PATH,
        }
        # Cookies faylini FAQAT YouTube uchun emas, BARCHA manbalar uchun
        # qo'llaymiz (download_video()dagi bir xil izohga qarang).
        if YOUTUBE_COOKIES_FILE:
            opts["cookiefile"] = YOUTUBE_COOKIES_FILE
        opts.update(youtube_opts if is_youtube else {})
        return opts

    try:
        if is_youtube:
            _run_youtube_with_retries(build_opts, url)
        else:
            with yt_dlp.YoutubeDL(build_opts({})) as ydl:
                ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        reason = _classify_ytdlp_error(e)
        logger.error(f"🎵 /qo'shiq audio yuklab olishda xato ({url}) — sabab: {reason} | asl xato: {e}")
        raise DownloadError(f"❌ Bu qo'shiqni yuklab bo'lmadi.\n\nSabab: {reason}.") from e
    except Exception as e:
        logger.error(f"🎵 /qo'shiq audio yuklab olishda kutilmagan xato ({url}): {type(e).__name__}: {e}", exc_info=True)
        raise DownloadError(f"❌ Audio yuklab olishda kutilmagan xatolik yuz berdi.\n\nSabab: {type(e).__name__}: {e}") from e

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


def download_telegram_audio(track: dict, dest_dir: str, max_mb: int) -> str:
    """`search_tracks()` "telegram" manbasidan qaytargan natijani (aynan
    o'sha `tg_channel`/`tg_message_id` juftligi orqali) MTProto orqali
    yuklab, tayyor fayl yo'lini qaytaradi. Bu yerda ffmpeg/MP3'ga
    o'tkazish SHART EMAS — Telegram'dagi audio fayllar allaqachon
    to'g'ridan-to'g'ri jo'natishga yaroqli formatda."""
    import telegram_search
    out_path = os.path.join(dest_dir, f"tg_{track['tg_message_id']}")
    try:
        filepath = telegram_search.download_public_audio(track["tg_channel"], track["tg_message_id"], out_path)
    except telegram_search.TelegramSearchError as e:
        raise DownloadError(f"❌ Bu qo'shiqni Telegram'dan yuklab bo'lmadi.\n\nSabab: {e}.") from e
    except Exception as e:
        logger.error(f"📡 Telegram audio yuklab olishda kutilmagan xato ({track.get('webpage_url')}): {type(e).__name__}: {e}", exc_info=True)
        raise DownloadError(f"❌ Audio yuklab olishda kutilmagan xatolik yuz berdi.\n\nSabab: {type(e).__name__}: {e}") from e

    if not filepath or not os.path.isfile(filepath):
        raise DownloadError("❌ Qo'shiq yuklab olindi, lekin fayl topilmadi.")

    _enforce_size_limit(filepath, max_mb)
    return filepath
