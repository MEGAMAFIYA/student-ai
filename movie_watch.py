"""
🎬 KINO WATCH PARTY — Mini App backend.

Room:
- 2 ta foydalanuvchi
- play/pause/seek holati HTTP polling orqali sinxron
- ichki chat HTTP polling orqali
- WebRTC kamera/mikrofon signaling HTTP polling orqali
- video Telegram file_id'dan server-side proxy qilinadi; token browserga chiqmaydi.

Eslatma: WebRTC media P2P. NAT sabab ayrim tarmoqlarda TURN server talab qilinishi
mumkin. WATCH_TURN_* env o'zgaruvchilari orqali TURN berish mumkin.
"""

import json
import subprocess
import logging
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.request
import shutil
import tempfile
import uuid

import config
import storage
import webapp_security
import r2_storage

logger = logging.getLogger(__name__)

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp", "kino")
ROOMS = {}
ROOM_LOCK = threading.RLock()
MAX_CHAT = 200
MAX_SIGNAL_ITEMS = 30
MAX_CHAT_CLIENT_KEYS = 500
KINO_CACHE_DIR = os.path.join("/tmp", "student_ai_kino_cache")
KINO_CACHE_LOCK = threading.RLock()
os.makedirs(KINO_CACHE_DIR, exist_ok=True)


def _purge_rooms():
    now = time.time()
    with ROOM_LOCK:
        for rid in list(ROOMS):
            if now - ROOMS[rid]["created_at"] > config.KINO_ROOM_TTL_SEC:
                del ROOMS[rid]


def create_room(movie_id: str, creator_id: int) -> str | None:
    if not storage.get_movie(movie_id):
        return None
    _purge_rooms()
    rid = uuid.uuid4().hex[:24]
    with ROOM_LOCK:
        ROOMS[rid] = {
            "movie_id": movie_id,
            "created_at": time.time(),
            "participants": {str(int(creator_id)): {"joined_at": time.time()}},
            "state": {"playing": False, "position": 0.0, "version": 0, "updated_at": time.time(), "actor_id": int(creator_id)},
            "chat": [],
            "chat_client_keys": {},
            "signals": {},
        }
    return rid


def find_or_create_room(movie_id: str, creator_id: int) -> str | None:
    """Inline qidiruvda har bir klaviatura bosilishida yangi xona yaratib
    yubormaslik uchun shu foydalanuvchining hali bo'sh xonasini qayta ishlatadi."""
    _purge_rooms()
    with ROOM_LOCK:
        for rid, room in reversed(list(ROOMS.items())):
            if room.get("movie_id") == movie_id and set(room.get("participants", {})) == {str(int(creator_id))}:
                return rid
    return create_room(movie_id, creator_id)


def room_url(movie_id: str, room_id: str) -> str:
    # Main Mini App direct-link form. BotFather'da Main Mini App shu
    # /miniapp/kino/ URL'ga o'rnatilganda Telegram foydalanuvchiga
    # initData + startapp=room_<id> beradi va link 1:1 chatda ham ishlaydi.
    username = config.BOT_USERNAME_FALLBACK.lstrip("@")
    if getattr(config, "KINO_APP_SHORT_NAME", ""):
        # Named Direct Mini App (agar alohida app short name berilgan bo'lsa).
        return f"https://t.me/{username}/{config.KINO_APP_SHORT_NAME}?startapp=room_{room_id}&mode=fullscreen"
    # Asosiy Mini App Direct Link. BotFather'da Main Mini App URL sifatida
    # /miniapp/ yoki loyihaning root URL'i berilgan bo'lishi kerak.
    # startapp qiymati Mini App ichida tgWebAppStartParam orqali olinadi.
    return f"https://t.me/{username}?startapp=room_{room_id}&mode=fullscreen"


def _get_room(rid):
    _purge_rooms()
    with ROOM_LOCK:
        return ROOMS.get(rid)


def _verify(init_data):
    return webapp_security.verify_telegram_init_data(init_data, config.TELEGRAM_TOKEN)


def join_room(rid: str, init_data: str):
    user = _verify(init_data)
    if not user:
        return None, "Mini App sessiyasi tasdiqlanmadi."
    room = _get_room(rid)
    if not room:
        return None, "Kino xonasi topilmadi yoki muddati o'tgan."
    uid = str(user["id"])
    with ROOM_LOCK:
        if uid not in room["participants"] and len(room["participants"]) >= 2:
            return None, "Bu xona to'la. Faqat 2 kishi birga tomosha qilishi mumkin."
        room["participants"].setdefault(uid, {"joined_at": time.time()})
        movie = storage.get_movie(room["movie_id"])
        return {
            "user_id": int(uid),
            "movie": movie,
            "participants": [int(x) for x in room["participants"]],
            "state": dict(room["state"]),
            "stream_path": f"/api/kino/stream/{rid}/{movie['id']}",
            "share_url": room_url(movie["id"], rid),
        }, None


def _room_movie_payload(room: dict, rid: str):
    movie = storage.get_movie(room.get("movie_id"))
    return {
        "movie": movie,
        "stream_path": f"/api/kino/stream/{rid}/{movie['id']}" if movie else "",
        "media_r2": bool(movie and movie.get("r2_key") and r2_storage.enabled()),
        "media_url_path": f"/api/kino/media-url/{rid}/{movie['id']}" if movie else "",
    }


def change_movie(rid: str, init_data: str, movie_id: str):
    """Xonadagi kinoni barcha qatnashchilar uchun almashtiradi.
    Xona va WebRTC ulanishi saqlanadi; faqat video holati boshidan boshlanadi."""
    user = _verify(init_data)
    movie = storage.get_movie(str(movie_id))
    if not user:
        return None, "Tasdiqlash xatosi."
    if not movie:
        return None, "Kino topilmadi."
    room = _get_room(rid)
    if not room:
        return None, "Xona topilmadi."
    uid = str(user["id"])
    with ROOM_LOCK:
        if uid not in room["participants"]:
            return None, "Siz bu xonaga qo'shilmagansiz."
        room["movie_id"] = str(movie["id"])
        room["state"].update({
            "playing": False,
            "position": 0.0,
            "updated_at": time.time(),
            "actor_id": int(user["id"]),
        })
        room["state"]["version"] += 1
        room["signals"] = {}
        room["updated_at"] = time.time()
        payload = {
            **room["state"],
            "participants": [int(x) for x in room["participants"]],
            "server_now": time.time(),
            **_room_movie_payload(room, rid),
        }
        return payload, None


def list_movies(rid: str, init_data: str):
    user = _verify(init_data)
    room = _get_room(rid)
    if not user or not room:
        return None, "Xona topilmadi yoki tasdiqlash xatosi."
    if str(user["id"]) not in room["participants"]:
        return None, "Siz bu xonaga qo'shilmagansiz."
    movies = storage.search_movies("")
    return [{"id": str(m["id"]), "title": m["title"]} for m in movies[:50]], None


def room_state(rid: str, init_data: str, playing=None, position=None):
    user = _verify(init_data)
    if not user:
        return None, "Tasdiqlash xatosi."
    room = _get_room(rid)
    if not room:
        return None, "Xona topilmadi."
    uid = str(user["id"])
    with ROOM_LOCK:
        if uid not in room["participants"]:
            return None, "Siz bu xonaga qo'shilmagansiz."
        if playing is not None or position is not None:
            # Only an explicit client action changes the authoritative room state.
            # Store the actor so clients can distinguish their own echo from a
            # genuine remote event; this prevents a polling response from
            # rewinding the local player to an older position.
            if playing is not None:
                room["state"]["playing"] = bool(playing)
            if position is not None:
                room["state"]["position"] = max(0.0, float(position))
            room["state"]["version"] += 1
            room["state"]["updated_at"] = time.time()
            room["state"]["actor_id"] = int(user["id"])
        return {
            **room["state"],
            "participants": [int(x) for x in room["participants"]],
            "server_now": time.time(),
            **_room_movie_payload(room, rid),
        }, None


def add_chat(rid: str, init_data: str, text: str, client_id: str = ""):
    user = _verify(init_data)
    if not user:
        return None, "Tasdiqlash xatosi."
    room = _get_room(rid)
    text = (text or "").strip()
    if not room or not text:
        return None, "Xona yoki xabar noto'g'ri."
    if len(text) > 500:
        text = text[:500]
    uid = str(user["id"])
    with ROOM_LOCK:
        if uid not in room["participants"]:
            return None, "Siz bu xonaga qo'shilmagansiz."
        client_id = (client_id or "").strip()[:100]
        if client_id:
            old_id = room.get("chat_client_keys", {}).get(client_id)
            if old_id:
                for old_item in room["chat"]:
                    if old_item.get("id") == old_id:
                        return old_item, None
        item = {
            "id": uuid.uuid4().hex[:12],
            "user_id": int(user["id"]),
            "name": user.get("first_name") or user.get("username") or str(user["id"]),
            "text": text,
            "ts": time.time(),
        }
        room["chat"].append(item)
        room["chat"] = room["chat"][-MAX_CHAT:]
        if client_id:
            keys = room.setdefault("chat_client_keys", {})
            keys[client_id] = item["id"]
            if len(keys) > MAX_CHAT_CLIENT_KEYS:
                for old_key in list(keys)[:len(keys) - MAX_CHAT_CLIENT_KEYS]:
                    keys.pop(old_key, None)
        return item, None


def get_chat(rid: str, init_data: str, after_id: str = ""):
    user = _verify(init_data)
    room = _get_room(rid)
    if not user or not room:
        return None, "Xona topilmadi yoki tasdiqlash xatosi."
    if str(user["id"]) not in room["participants"]:
        return None, "Siz bu xonaga qo'shilmagansiz."
    with ROOM_LOCK:
        items = list(room["chat"])
    if after_id:
        for i, x in enumerate(items):
            if x["id"] == after_id:
                items = items[i + 1:]
                break
    return items[-100:], None


def put_signal(rid: str, init_data: str, target_user_id: int, payload: dict):
    user = _verify(init_data)
    room = _get_room(rid)
    if not user or not room:
        return False, "Tasdiqlash xatosi."
    uid = str(user["id"])
    target = str(int(target_user_id))
    if uid not in room["participants"] or target not in room["participants"] or uid == target:
        return False, "Signal qabul qiluvchisi noto'g'ri."
    with ROOM_LOCK:
        box = room["signals"].setdefault(target, [])
        box.append({"from": int(user["id"]), "payload": payload})
        room["signals"][target] = box[-MAX_SIGNAL_ITEMS:]
    return True, None


def get_signals(rid: str, init_data: str):
    user = _verify(init_data)
    room = _get_room(rid)
    if not user or not room:
        return None, "Tasdiqlash xatosi."
    uid = str(user["id"])
    if uid not in room["participants"]:
        return None, "Siz bu xonaga qo'shilmagansiz."
    with ROOM_LOCK:
        items = room["signals"].get(uid, [])
        room["signals"][uid] = []
    return items, None


def archive_movie_to_r2(movie: dict) -> tuple[bool, str, str]:
    """Telegram media -> R2 one-time import. Returns (ok, key, error).

    This runs only when R2 is configured. The temporary file is deleted after
    upload, so Render is not used as a permanent movie CDN.
    """
    if not r2_storage.enabled():
        return False, "", "R2 sozlanmagan"
    movie_id = str(movie.get("id", ""))
    key = movie.get("r2_key") or r2_storage.make_key(movie_id, movie.get("file_name", ""))
    if movie.get("r2_key") and r2_storage.exists(key):
        return True, key, ""
    fd, tmp = tempfile.mkstemp(prefix="student_ai_r2_", suffix=".media")
    os.close(fd)
    try:
        remote = _telegram_file_url(movie["file_id"])
        max_bytes = max(1, int(config.KINO_MAX_UPLOAD_MB)) * 1024 * 1024
        total = 0
        with urllib.request.urlopen(
            urllib.request.Request(remote, headers={"User-Agent": "StudentAI-Kino/3.0"}),
            timeout=180,
        ) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"Kino {total / 1024 / 1024:.1f} MB — KINO_MAX_UPLOAD_MB oshib ketdi")
                out.write(chunk)
        r2_storage.upload_file(tmp, key, movie.get("mime_type") or "video/mp4")
        return True, key, ""
    except Exception as e:
        logger.exception("☁️ R2 kino upload xatosi: %s", e)
        return False, "", str(e)[:240]
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def movie_media_url(rid: str, movie_id: str, init_data: str) -> tuple[str | None, str | None]:
    """Returns a direct R2/CDN URL when available, otherwise legacy stream path."""
    user = _verify(init_data)
    if not user:
        return None, "Tasdiqlash xatosi."
    room = _get_room(rid)
    if not room or str(movie_id) != str(room.get("movie_id")):
        return None, "Kino xonasi topilmadi."
    if str(user["id"]) not in room.get("participants", {}):
        return None, "Siz bu xonaga qo'shilmagansiz."
    movie = storage.get_movie(movie_id)
    if not movie:
        return None, "Kino topilmadi."
    key = movie.get("r2_key")
    if key and r2_storage.enabled():
        try:
            return r2_storage.media_url(key), None
        except Exception as e:
            logger.warning("☁️ R2 URL yaratilmadi, fallback ishlaydi: %s", e)
    return f"/api/kino/stream/{rid}/{movie['id']}", None


def _telegram_file_url(file_id: str):
    """Bot API getFile orqali file_path oladi. Token faqat server ichida qoladi."""
    token = config.TELEGRAM_TOKEN
    url = f"https://api.telegram.org/bot{token}/getFile?file_id={urllib.parse.quote(file_id)}"
    req = urllib.request.Request(url, headers={"User-Agent": "StudentAI-Kino/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok") or not data.get("result", {}).get("file_path"):
        raise RuntimeError("Telegram fayl yo'li olinmadi.")
    path = data["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{token}/{path}"


def _movie_cache_path(movie_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(movie_id))
    return os.path.join(KINO_CACHE_DIR, safe + ".media")


def _download_movie_to_cache(movie: dict) -> str:
    """Telegramdan kinoni bir marta olib, lokal runtime cache'ga yozadi.
    Keyingi ijrolarda Telegramga qayta murojaat qilinmaydi.
    Cloud Bot API getFile cheklovi sabab bu yo'l faqat KINO_MAX_UPLOAD_MB ichidagi
    fayllar uchun ishlaydi."""
    path = _movie_cache_path(movie["id"])
    with KINO_CACHE_LOCK:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        remote = _telegram_file_url(movie["file_id"])
        tmp = path + ".part"
        try:
            with urllib.request.urlopen(
                urllib.request.Request(remote, headers={"User-Agent": "StudentAI-Kino/2.0"}),
                timeout=120,
            ) as resp, open(tmp, "wb") as out:
                total = 0
                max_bytes = max(1, int(config.KINO_MAX_UPLOAD_MB)) * 1024 * 1024
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise RuntimeError(
                            f"Kino cache uchun juda katta: {total / 1024 / 1024:.1f} MB > {config.KINO_MAX_UPLOAD_MB} MB"
                        )
                    out.write(chunk)
            os.replace(tmp, path)
            logger.info("🎬 Kino cache yaratildi: %s (%d bytes)", movie["id"], os.path.getsize(path))
            return path
        finally:
            try:
                if os.path.exists(tmp): os.remove(tmp)
            except OSError:
                pass


def _mime_for_movie(movie: dict) -> str:
    mime = (movie.get("mime_type") or "").lower().split(";", 1)[0].strip()
    if mime == "video/quicktime":
        return "video/mp4"
    if mime.startswith("video/"):
        return mime
    guessed = mimetypes.guess_type(movie.get("file_name", ""))[0]
    return guessed or "video/mp4"


def _ensure_browser_mp4(movie: dict, source_path: str) -> str:
    """Browser uchun mos MP4 qaytaradi.

    MP4 konteynerining o'zi yetarli emas: ayrim kinolar HEVC/H.265, AC-3 va
    boshqa kodeklarda bo'lishi mumkin. Avval ffprobe bilan tekshiramiz va faqat
    mos kelmaydigan faylni H.264/AAC ga transcode qilamiz. Shu bilan odatiy
    H.264 MP4 lar ortiqcha CPU ishlatmaydi, Telegram/Android WebView mosligi esa
    ancha yuqori bo'ladi.
    """
    name = (movie.get("file_name") or "").lower()
    mime = (movie.get("mime_type") or "").lower()
    force = os.getenv("KINO_FORCE_TRANSCODE", "0") == "1"
    needs_transcode = force or not (name.endswith(".mp4") or mime == "video/mp4")

    if not needs_transcode:
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            try:
                probe = subprocess.run(
                    [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                     "-of", "json", source_path],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, timeout=15, check=True,
                )
                streams = json.loads(probe.stdout or "{}").get("streams", [])
                vcodecs = {x.get("codec_name") for x in streams if x.get("codec_type") == "video"}
                acodecs = {x.get("codec_name") for x in streams if x.get("codec_type") == "audio"}
                needs_transcode = not bool(vcodecs & {"h264", "avc1"}) or bool(acodecs - {"aac"})
            except Exception as e:
                # Probe ishlamasa mavjud MP4 ni buzmasdan serve qilamiz.
                logger.debug("🎬 ffprobe tekshiruvi o'tmadi: %s", e)
                needs_transcode = False

    if not needs_transcode:
        return source_path

    out = os.path.splitext(source_path)[0] + "_h264.mp4"
    with KINO_CACHE_LOCK:
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("🎬 ffmpeg topilmadi; original video serve qilinadi: %s", movie.get("title"))
            return source_path
        tmp = out + ".part.mp4"
        cmd = [ffmpeg, "-y", "-i", source_path, "-map", "0:v:0", "-map", "0:a:0?",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               "-ar", "48000", "-movflags", "+faststart", tmp]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300, check=True)
            if os.path.getsize(tmp) <= 0:
                raise RuntimeError("ffmpeg bo'sh fayl yaratdi")
            os.replace(tmp, out)
            logger.info("🎬 Browser MP4 tayyor: %s", movie.get("title"))
            return out
        except Exception as e:
            logger.error("🎬 MP4 transcode xatosi: %s", e, exc_info=True)
            try:
                if os.path.exists(tmp): os.remove(tmp)
            except OSError:
                pass
            return source_path


def _serve_local_range(handler, path: str, content_type: str):
    size = os.path.getsize(path)
    range_header = handler.headers.get("Range", "").strip()
    start, end = 0, size - 1
    status = 200
    if range_header:
        m = re.match(r"^bytes=(\d*)-(\d*)$", range_header)
        if not m:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.end_headers()
            return
        a, b = m.groups()
        if a:
            start = int(a)
            end = int(b) if b else size - 1
        else:
            suffix = int(b or 0)
            start = max(0, size - suffix)
            end = size - 1
        if start >= size or start > end:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.end_headers()
            return
        end = min(end, size - 1)
        status = 206

    length = end - start + 1
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(length))
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Cache-Control", "private, max-age=3600")
    handler.end_headers()
    with open(path, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining:
            chunk = f.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)


def serve_movie(handler, room_id: str, movie_id: str):
    """Browserga ishonchli Range video stream beradi.
    Avval runtime cache'dan foydalanadi; cache yo'q bo'lsa Telegramdan bir marta oladi.
    Shu bilan bitta tomoshabin ham kinoni mustaqil ko'ra oladi."""
    room = _get_room(room_id)
    if not room or str(room.get("movie_id")) != str(movie_id):
        _send_text(handler, 404, "Kino xonasi topilmadi.")
        return
    movie = storage.get_movie(movie_id)
    if not movie:
        _send_text(handler, 404, "Kino topilmadi.")
        return
    try:
        cache = _download_movie_to_cache(movie)
        playable = _ensure_browser_mp4(movie, cache)
        _serve_local_range(handler, playable, "video/mp4" if playable.endswith(".mp4") else _mime_for_movie(movie))
    except urllib.error.HTTPError as e:
        logger.error("🎬 Kino yuklash HTTPError: %s", e)
        _send_text(handler, e.code if e.code in (404, 416) else 502, "Telegramdan kino faylini olishda xatolik.")
    except Exception as e:
        logger.error("🎬 Kino stream xatosi: %s: %s", type(e).__name__, e, exc_info=True)
        _send_text(handler, 502, "Kino stream tayyorlanmadi: " + str(e)[:180])

def _send_text(handler, status, text):
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _json(handler, status, data):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_api(handler):
    from urllib.parse import parse_qs, urlsplit
    parsed = urlsplit(handler.path)
    path = parsed.path
    init_data = handler.headers.get("X-Telegram-Init-Data", "")
    if path == "/api/kino/join":
        qs = parse_qs(parsed.query)
        data, err = join_room(qs.get("room", [""])[0], init_data)
        return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": data, "error": err})
    if path.startswith("/api/kino/media-url/"):
        parts = path.split("/api/kino/media-url/", 1)[1].split("/")
        if len(parts) == 2:
            url, err = movie_media_url(parts[0], parts[1], init_data)
            return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": {"url": url} if url else None, "error": err})
        return _json(handler, 404, {"ok": False, "error": "Not found."})
    if path == "/api/kino/state":
        qs = parse_qs(parsed.query)
        data, err = room_state(qs.get("room", [""])[0], init_data)
        return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": data, "error": err})
    if path == "/api/kino/chat":
        qs = parse_qs(parsed.query)
        data, err = get_chat(qs.get("room", [""])[0], init_data, qs.get("after", [""])[0])
        return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": data, "error": err})
    if path == "/api/kino/movies":
        qs = parse_qs(parsed.query)
        data, err = list_movies(qs.get("room", [""])[0], init_data)
        return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": data, "error": err})
    if path == "/api/kino/signals":
        qs = parse_qs(parsed.query)
        data, err = get_signals(qs.get("room", [""])[0], init_data)
        return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": data, "error": err})
    return _json(handler, 404, {"ok": False, "error": "Not found."})

def handle_post(handler):
    from urllib.parse import parse_qs, urlsplit
    try:
        length = int(handler.headers.get("Content-Length", 0))
        raw = handler.rfile.read(length)
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        return _json(handler, 400, {"ok": False, "error": "Noto'g'ri JSON."})
    path = urlsplit(handler.path).path
    init_data = handler.headers.get("X-Telegram-Init-Data", "") or body.get("init_data", "")
    if path == "/api/kino/create":
        user = _verify(init_data)
        movie_id = body.get("movie", "")
        if not user or not storage.get_movie(movie_id):
            return _json(handler, 400, {"ok": False, "error": "Kino yoki sessiya noto'g'ri."})
        rid = create_room(movie_id, int(user["id"]))
        return _json(handler, 200, {"ok": True, "data": {"room": rid, "url": room_url(movie_id, rid)}})
    if path == "/api/kino/state":
        data, err = room_state(body.get("room",""), init_data, body.get("playing"), body.get("position"))
        return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": data, "error": err})
    if path == "/api/kino/chat":
        data, err = add_chat(body.get("room",""), init_data, body.get("text",""), body.get("client_id", ""))
        return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": data, "error": err})
    if path == "/api/kino/signal":
        data, err = put_signal(body.get("room",""), init_data, int(body.get("target_user_id", 0)), body.get("payload") or {})
        return _json(handler, 200 if not err else 400, {"ok": bool(data), "data": data, "error": err})
    return _json(handler, 404, {"ok": False, "error": "Not found."})


def serve_static(handler):
    from urllib.parse import urlsplit
    path = urlsplit(handler.path).path
    mapping = {
        "/miniapp/kino/": "index.html",
        "/miniapp/kino/index.html": "index.html",
        "/miniapp/kino/app.js": "app.js",
        "/miniapp/kino/style.css": "style.css",
    }
    name = mapping.get(path)
    if not name:
        return False
    fp = os.path.join(WEBAPP_DIR, name)
    try:
        with open(fp, "rb") as f:
            body = f.read()
    except OSError:
        return False
    ctype = mimetypes.guess_type(name)[0] or "text/plain"
    # TURN credentials faqat HTML ichidagi runtime config sifatida beriladi;
    # ular URL orqali oshkor qilinmaydi. TURN ishlatilsa credential browserga
    # chiqishi tabiiy (WebRTC client credentiali), shuning uchun qisqa muddatli
    # TURN credentiallardan foydalanish tavsiya etiladi.
    if name == "index.html":
        text = body.decode("utf-8")
        turn_urls = list(getattr(config, "KINO_TURN_URLS", ()) or ())
        if not turn_urls and config.KINO_TURN_URL:
            turn_urls = [config.KINO_TURN_URL]
        turn_cfg = (f"<script>window.KINO_TURN_URLS={json.dumps(turn_urls)};"
                    f"window.KINO_TURN_URL={json.dumps(config.KINO_TURN_URL)};"
                    f"window.KINO_TURN_USERNAME={json.dumps(config.KINO_TURN_USERNAME)};"
                    f"window.KINO_TURN_CREDENTIAL={json.dumps(config.KINO_TURN_CREDENTIAL)};</script>")
        text = text.replace("</head>", turn_cfg + "</head>", 1)
        body = text.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or name.endswith(".js") else ""))
    handler.send_header("Cache-Control", "no-cache")
    if name == "index.html":
        handler.send_header("Permissions-Policy", "camera=(self), microphone=(self)")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return True


# urllib.parse import is intentionally local in the normal request paths.
import urllib.parse
