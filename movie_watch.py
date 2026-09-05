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
import logging
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

import config
import storage
import webapp_security

logger = logging.getLogger(__name__)

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp", "kino")
ROOMS = {}
ROOM_LOCK = threading.RLock()
MAX_CHAT = 200
MAX_SIGNAL_ITEMS = 30


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
            "state": {"playing": False, "position": 0.0, "version": 0, "updated_at": time.time()},
            "chat": [],
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
            if playing is not None:
                room["state"]["playing"] = bool(playing)
            if position is not None:
                room["state"]["position"] = max(0.0, float(position))
            room["state"]["version"] += 1
            room["state"]["updated_at"] = time.time()
        return {
            **room["state"],
            "participants": [int(x) for x in room["participants"]],
        }, None


def add_chat(rid: str, init_data: str, text: str):
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
        item = {
            "id": uuid.uuid4().hex[:12],
            "user_id": int(user["id"]),
            "name": user.get("first_name") or user.get("username") or str(user["id"]),
            "text": text,
            "ts": time.time(),
        }
        room["chat"].append(item)
        room["chat"] = room["chat"][-MAX_CHAT:]
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


def serve_movie(handler, room_id: str, movie_id: str):
    """Range-aware Telegram media proxy. Faqat mavjud watch-room ichidagi
    mos kino stream qilinadi; Bot API token browserga chiqmaydi."""
    room = _get_room(room_id)
    if not room or str(room.get("movie_id")) != str(movie_id):
        _send_text(handler, 404, "Kino xonasi topilmadi.")
        return
    movie = storage.get_movie(movie_id)
    if not movie:
        _send_text(handler, 404, "Kino topilmadi.")
        return
    try:
        remote = _telegram_file_url(movie["file_id"])
        headers = {"User-Agent": "StudentAI-Kino/1.0"}
        incoming_range = handler.headers.get("Range")
        if incoming_range:
            headers["Range"] = incoming_range
        req = urllib.request.Request(remote, headers=headers)
        resp = urllib.request.urlopen(req, timeout=60)
        status = getattr(resp, "status", 200) or 200
        content_type = movie.get("mime_type") or mimetypes.guess_type(movie.get("file_name", ""))[0] or "video/mp4"
        length = resp.headers.get("Content-Length")
        content_range = resp.headers.get("Content-Range")
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        if length:
            handler.send_header("Content-Length", length)
        if content_range:
            handler.send_header("Content-Range", content_range)
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Cache-Control", "private, max-age=60")
        handler.end_headers()
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            handler.wfile.write(chunk)
    except urllib.error.HTTPError as e:
        logger.error("🎬 Kino stream Telegram HTTPError: %s", e)
        try:
            _send_text(handler, e.code if e.code in (404, 416) else 502, "Video oqimini olishda xatolik.")
        except Exception:
            pass
    except Exception as e:
        logger.error("🎬 Kino stream xatosi: %s: %s", type(e).__name__, e, exc_info=True)
        try:
            _send_text(handler, 502, "Video oqimini olishda xatolik.")
        except Exception:
            pass


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
    if path == "/api/kino/join":
        qs = parse_qs(parsed.query)
        data, err = join_room(qs.get("room", [""])[0], handler.headers.get("X-Telegram-Init-Data", ""))
        return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": data, "error": err})
    if path == "/api/kino/create":
        user = _verify(init_data)
        movie_id = body.get("movie", "")
        if not user or not storage.get_movie(movie_id):
            return _json(handler, 400, {"ok": False, "error": "Kino yoki sessiya noto'g'ri."})
        rid = create_room(movie_id, int(user["id"]))
        return _json(handler, 200, {"ok": True, "data": {"room": rid, "url": room_url(movie_id, rid)}})
    if path == "/api/kino/state":
        qs = parse_qs(parsed.query)
        data, err = room_state(qs.get("room", [""])[0], handler.headers.get("X-Telegram-Init-Data", ""))
        return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": data, "error": err})
    if path == "/api/kino/chat":
        qs = parse_qs(parsed.query)
        data, err = get_chat(qs.get("room", [""])[0], handler.headers.get("X-Telegram-Init-Data", ""), qs.get("after", [""])[0])
        return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": data, "error": err})
    if path == "/api/kino/signals":
        qs = parse_qs(parsed.query)
        data, err = get_signals(qs.get("room", [""])[0], handler.headers.get("X-Telegram-Init-Data", ""))
        return _json(handler, 200 if not err else 400, {"ok": not bool(err), "data": data, "error": err})
    _send_text(handler, 404, "Not found.")


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
        data, err = add_chat(body.get("room",""), init_data, body.get("text",""))
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
    handler.send_response(200)
    handler.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or name.endswith(".js") else ""))
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return True


# urllib.parse import is intentionally local in the normal request paths.
import urllib.parse
