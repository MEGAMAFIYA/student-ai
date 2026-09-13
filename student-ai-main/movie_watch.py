"""
🎬 KINO WATCH PARTY — Mini App backend.

Room:
- 2 ta foydalanuvchi
- play/pause/seek holati HTTP polling orqali sinxron
- ichki chat HTTP polling orqali
- WebRTC kamera/mikrofon signaling HTTP polling orqali
- video Telegram MTProto'dan server-side Range stream qilinadi; qisqa stream token browserga beriladi, Telegram credentiallari esa serverda qoladi.

Eslatma: WebRTC media P2P. NAT sabab ayrim tarmoqlarda TURN server talab qilinishi
mumkin. WATCH_TURN_* env o'zgaruvchilari orqali TURN berish mumkin.
"""

import json
import logging
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
import hmac
import hashlib
import base64
import urllib.parse

import config
import storage
import webapp_security
import telegram_mtproto

logger = logging.getLogger(__name__)

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp", "kino")
ROOMS = {}
ROOM_LOCK = threading.RLock()
MAX_CHAT = 200
MAX_SIGNAL_ITEMS = 30
MAX_CHAT_CLIENT_KEYS = 500
STREAM_SLOT = threading.BoundedSemaphore(getattr(config, "KINO_STREAM_MAX_CONCURRENT", 4))


def _mime_for_movie(movie: dict) -> str:
    """Katalog yozuvidan video MIME type aniqlaydi."""
    if not isinstance(movie, dict):
        return "application/octet-stream"
    mime = str(movie.get("mime_type") or movie.get("mime") or "").strip()
    if mime:
        return mime
    name = str(movie.get("file_name") or movie.get("filename") or "").lower()
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "video/mp4"


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
            "stream_path": f"/api/kino/stream/{rid}/{movie['id']}?token={urllib.parse.quote(_make_stream_token(rid, movie['id'], int(user["id"]))) }",
            "share_url": room_url(movie["id"], rid),
        }, None


def _room_movie_payload(room: dict, rid: str, user_id: int | None = None):
    movie = storage.get_movie(room.get("movie_id"))
    token = _make_stream_token(rid, movie["id"], int(user_id or 0)) if movie and user_id else ""
    stream = f"/api/kino/stream/{rid}/{movie['id']}?token={urllib.parse.quote(token)}" if movie and token else ""
    return {
        "movie": movie,
        "stream_path": stream,
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
            **_room_movie_payload(room, rid, int(user["id"])),
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
            **_room_movie_payload(room, rid, int(user["id"])),
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


def _stream_secret() -> bytes:
    value = getattr(config, "KINO_STREAM_TOKEN_SECRET", "") or config.TELEGRAM_TOKEN
    return value.encode("utf-8")


def _make_stream_token(room_id: str, movie_id: str, user_id: int, ttl: int = 3600) -> str:
    payload = f"{room_id}|{movie_id}|{int(user_id)}|{int(time.time()) + max(60, int(ttl))}"
    raw = payload.encode("utf-8")
    sig = hmac.new(_stream_secret(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + b"." + sig).decode("ascii").rstrip("=")


def _verify_stream_token(token: str, room_id: str, movie_id: str) -> int | None:
    try:
        pad = "=" * (-len(token) % 4)
        blob = base64.urlsafe_b64decode((token + pad).encode("ascii"))
        raw, sig = blob.rsplit(b".", 1)
        if not hmac.compare_digest(hmac.new(_stream_secret(), raw, hashlib.sha256).digest(), sig):
            return None
        r, m, uid, exp = raw.decode("utf-8").split("|", 3)
        if r != str(room_id) or m != str(movie_id) or int(exp) < int(time.time()):
            return None
        return int(uid)
    except Exception:
        return None


def _parse_range(range_header: str, size: int):
    if not range_header:
        return 0, size - 1, 200
    m = re.match(r"^bytes=(\d*)-(\d*)$", range_header.strip())
    if not m:
        return None
    a, b = m.groups()
    if not a and not b:
        return None
    if a:
        start = int(a)
        end = int(b) if b else size - 1
    else:
        suffix = int(b)
        if suffix <= 0:
            return None
        start = max(0, size - suffix)
        end = size - 1
    if start >= size or start > end:
        return None
    return start, min(end, size - 1), 206


def _send_stream_headers(handler, status: int, content_type: str, size: int, start: int, end: int):
    length = max(0, end - start + 1)
    # HTTP handler flag lets the fallback layer know whether headers are already on the wire.
    handler._kino_stream_headers_sent = True
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(length))
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Cache-Control", "private, no-store")
    handler.end_headers()


def _serve_mtproto_range(handler, movie: dict, start: int, end: int, content_type: str):
    chat_id = movie.get("telegram_chat_id")
    message_id = movie.get("telegram_message_id")
    size = int(movie.get("size") or 0)
    if not chat_id or not message_id or size <= 0:
        raise RuntimeError("MTProto metadata to'liq emas.")
    # First chunk is fetched before headers so a primary failure can cleanly
    # switch to the fallback before the HTTP response has started.
    chunk_size = int(getattr(config, "KINO_STREAM_CHUNK_SIZE", 1024 * 1024))
    timeout = float(getattr(config, "KINO_STREAM_TIMEOUT_SEC", 35))
    first_limit = min(chunk_size, end - start + 1)
    first = telegram_mtproto.download_range(chat_id, message_id, start, first_limit, timeout=timeout)
    if not first:
        raise RuntimeError("Telegram MTProto bo'sh chunk qaytardi.")
    _send_stream_headers(handler, 206 if (start > 0 or end < size - 1) else 200, content_type, size, start, end)
    handler.wfile.write(first)
    sent = len(first)
    pos = start + sent
    while pos <= end:
        want = min(chunk_size, end - pos + 1)
        chunk = telegram_mtproto.download_range(chat_id, message_id, pos, want, timeout=timeout)
        if not chunk:
            raise RuntimeError("Telegram MTProto oqimi erta tugadi.")
        handler.wfile.write(chunk)
        pos += len(chunk)
        if len(chunk) < want:
            raise RuntimeError("Telegram MTProto oqimi kutilmaganda qisqardi.")


def _bot_api_stream(handler, movie: dict, start: int, end: int, content_type: str):
    """Legacy/secondary fallback: Telegram Bot API CDN through this Render process."""
    if not movie.get("file_id"):
        raise RuntimeError("Fallback uchun file_id mavjud emas.")
    remote = _telegram_file_url(movie["file_id"])
    size = int(movie.get("size") or 0)
    req = urllib.request.Request(remote, headers={
        "User-Agent": "StudentAI-Kino/3.0",
        "Range": f"bytes={start}-{end}",
    })
    with urllib.request.urlopen(req, timeout=35) as resp:
        status = getattr(resp, "status", 200)
        if status == 200 and start:
            # Some Telegram/CDN responses ignore Range. Avoid pretending the
            # response is the requested range; discard only the requested prefix.
            remaining = start
            while remaining:
                data = resp.read(min(1024 * 1024, remaining))
                if not data:
                    raise RuntimeError("Fallback stream Range'ni bajara olmadi.")
                remaining -= len(data)
        actual_size = int(resp.headers.get("Content-Length", size or 0))
        if not size:
            size = actual_size + (start if status == 200 else 0)
        if status == 200 and start == 0 and end == size - 1:
            _send_stream_headers(handler, 200, content_type, size, 0, end)
        else:
            _send_stream_headers(handler, 206, content_type, size, start, end)
        remaining = end - start + 1
        while remaining:
            chunk = resp.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)
        if remaining:
            raise RuntimeError("Fallback Telegram oqimi erta tugadi.")


def _authorize_stream(handler, room_id: str, movie_id: str):
    from urllib.parse import parse_qs, urlsplit
    parsed = urlsplit(handler.path)
    token = parse_qs(parsed.query).get("token", [""])[0]
    uid = _verify_stream_token(token, room_id, movie_id)
    if uid is None:
        _send_text(handler, 403, "Stream ruxsati yaroqsiz yoki muddati o'tgan.")
        return None, None
    room = _get_room(room_id)
    if not room or str(room.get("movie_id")) != str(movie_id) or str(uid) not in room.get("participants", {}):
        _send_text(handler, 403, "Bu kino xonasiga kirish huquqi yo'q.")
        return None, None
    movie = storage.get_movie(movie_id)
    if not movie:
        _send_text(handler, 404, "Kino topilmadi.")
        return None, None
    return uid, movie


def serve_movie_head(handler, room_id: str, movie_id: str):
    """Browser/Telegram HEAD so'roviga faqat metadata bilan javob beradi."""
    uid, movie = _authorize_stream(handler, room_id, movie_id)
    if not movie:
        logger.error("🎬 STREAM REJECTED room=%s movie=%s path=%s", room_id, movie_id, getattr(handler, "path", ""))
        return
    logger.info("🎬 STREAM START room=%s movie=%s user=%s chat=%s message=%s size=%s range=%s", room_id, movie_id, uid, movie.get("telegram_chat_id"), movie.get("telegram_message_id"), movie.get("size"), handler.headers.get("Range", ""))
    size = int(movie.get("size") or 0)
    if size <= 0:
        _send_text(handler, 502, "Kino hajmi aniqlanmadi.")
        return
    parsed_range = _parse_range(handler.headers.get("Range", ""), size)
    if not parsed_range:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.end_headers()
        return
    start, end, _ = parsed_range
    status = 206 if (start > 0 or end < size - 1) else 200
    _send_stream_headers(handler, status, _mime_for_movie(movie), size, start, end)


def serve_movie(handler, room_id: str, movie_id: str):
    """Primary MTProto stream with automatic Bot API/Render fallback.

    No movie bytes are persisted to disk/R2. The browser receives one stable
    stream URL and never needs to know which Telegram transport was selected.
    """
    uid, movie = _authorize_stream(handler, room_id, movie_id)
    if not movie:
        return
    size = int(movie.get("size") or 0)
    if size <= 0:
        _send_text(handler, 502, "Kino hajmi aniqlanmadi.")
        return
    parsed_range = _parse_range(handler.headers.get("Range", ""), size)
    if not parsed_range:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.end_headers()
        return
    start, end, _ = parsed_range
    content_type = _mime_for_movie(movie)
    handler._kino_stream_headers_sent = False
    acquired = STREAM_SLOT.acquire(timeout=float(getattr(config, "KINO_STREAM_TIMEOUT_SEC", 35)))
    if not acquired:
        _send_text(handler, 503, "Kino oqimi band. Bir necha soniyadan keyin qayta urinib ko'ring.")
        return
    try:
        try:
            logger.info("🎬 STREAM MTProto TRY movie=%s bytes=%s-%s", movie_id, start, end)
            _serve_mtproto_range(handler, movie, start, end, content_type)
            logger.info("🎬 STREAM MTProto OK movie=%s bytes=%s-%s", movie_id, start, end)
            return
        except Exception as primary_exc:
            logger.error("🎬 STREAM MTProto FAILED movie=%s room=%s chat=%s message=%s bytes=%s-%s error=%s: %s", movie_id, room_id, movie.get("telegram_chat_id"), movie.get("telegram_message_id"), start, end, type(primary_exc).__name__, primary_exc, exc_info=True)
            logger.warning("🎬 STREAM FALLBACK TRY movie=%s", movie_id)
            # Fallback can only replace the primary transport before HTTP headers
            # have been sent. Once bytes are on the wire, sending a second set of
            # headers would corrupt the response. The browser will retry the next
            # Range request and get a fresh primary/fallback attempt.
            if getattr(handler, "_kino_stream_headers_sent", False):
                try:
                    handler.close_connection = True
                except Exception:
                    pass
                return
        try:
            _bot_api_stream(handler, movie, start, end, content_type)
            logger.info("🎬 STREAM FALLBACK OK movie=%s bytes=%s-%s", movie_id, start, end)
        except Exception as fallback_exc:
            logger.error("🎬 STREAM FALLBACK FAILED movie=%s room=%s file_id=%s bytes=%s-%s error=%s: %s", movie_id, room_id, bool(movie.get("file_id")), start, end, type(fallback_exc).__name__, fallback_exc, exc_info=True)
            # If headers were already sent by the fallback, the socket may be
            # partially written; otherwise provide a clean HTTP error.
            try:
                _send_text(handler, 502, "Telegram kino oqimi vaqtincha mavjud emas.")
            except Exception:
                pass
    finally:
        STREAM_SLOT.release()

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
