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
import math
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

# Mijoz (app.js) shu xabarlarni solishtirib "sessiya eskirdi" holatini ajratadi;
# server esa ularni 401 + {"code": "auth"} bilan qaytaradi.
# Telegram Bot API getFile bot uchun faqat shu hajmgacha fayl beradi.
BOT_API_DOWNLOAD_LIMIT = 20 * 1024 * 1024

ERR_AUTH = "Mini App sessiyasi tasdiqlanmadi yoki muddati tugagan. Mini App'ni Telegram ichidan qayta oching."
ERR_ROOM = "Kino xonasi topilmadi yoki muddati o'tgan."
ERR_NOT_MEMBER = "Siz bu xonaga qo'shilmagansiz."
ERR_BAD_REQUEST = "So'rov ma'lumotlari noto'g'ri."

MAX_BODY_BYTES = 64 * 1024            # POST tanasi chegarasi (autentifikatsiyadan OLDIN o'qiladi)
MAX_POSITION_SEC = 48 * 60 * 60       # video pozitsiyasi uchun oqilona yuqori chegara
_RID_RE = re.compile(r"^[a-f0-9]{24}$")
_CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


def _public_movie(movie: dict | None) -> dict | None:
    """Brauzerga faqat kerakli maydonlar beriladi. Katalog yozuvida Telegram
    file_id, access_hash, file_reference va yuklovchi ID'si ham bor — ular
    serverda qoladi."""
    if not movie:
        return None
    return {
        "id": str(movie.get("id")),
        "title": movie.get("title") or "",
        "size": int(movie.get("size") or 0),
        "mime_type": _mime_for_movie(movie),
    }


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
    """Xonani faqat FAOL BO'LMAY qolganda (KINO_ROOM_TTL_SEC) yoki mutlaq
    maksimal umridan (KINO_ROOM_MAX_AGE_SEC) oshganda o'chiradi. Avval umr
    yaratilgan paytdan sanalgani uchun, xona 5 soat oldin yaratilgan bo'lsa,
    film o'rtasida uziladi."""
    now = time.time()
    idle_limit = config.KINO_ROOM_TTL_SEC
    max_age = getattr(config, "KINO_ROOM_MAX_AGE_SEC", idle_limit)
    with ROOM_LOCK:
        for rid in list(ROOMS):
            room = ROOMS[rid]
            last = room.get("last_active", room["created_at"])
            if now - last > idle_limit or now - room["created_at"] > max_age:
                del ROOMS[rid]
            elif not room.get("joined") and now - room["created_at"] > getattr(config, "KINO_UNUSED_ROOM_TTL_SEC", 1800):
                # Inline natijalar uchun yaratilgan, lekin hech kim ochmagan xonalar
                # xotirani to'ldirmasin. Havola ochilsa xona movie id bo'yicha qayta tiklanadi.
                del ROOMS[rid]


def create_room(movie_id: str, creator_id: int, rid: str | None = None) -> str | None:
    """Yangi xona yaratadi. `rid` berilsa (server qayta ishga tushgandan keyin tiklash)
    shu ID bilan yaratiladi; shu ID'li xona allaqachon bo'lsa, uni o'zgartirmaydi."""
    if not storage.get_movie(movie_id):
        return None
    _purge_rooms()
    if rid is not None and not _RID_RE.match(str(rid)):
        return None
    with ROOM_LOCK:
        if rid is not None and rid in ROOMS:
            return rid
        if len(ROOMS) >= getattr(config, "KINO_MAX_ROOMS", 500):
            logger.warning("🎬 Xonalar limiti to'ldi (%s)", len(ROOMS))
            return None
        rid = rid or uuid.uuid4().hex[:24]
        ROOMS[rid] = {
            "movie_id": movie_id,
            "created_at": time.time(),
            "last_active": time.time(),
            "joined": False,
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
        return f"https://t.me/{username}/{config.KINO_APP_SHORT_NAME}?startapp=room_{room_id}_{movie_id}&mode=fullscreen"
    # Asosiy Mini App Direct Link. BotFather'da Main Mini App URL sifatida
    # /miniapp/ yoki loyihaning root URL'i berilgan bo'lishi kerak.
    # startapp qiymati Mini App ichida tgWebAppStartParam orqali olinadi.
    # `room_<xona>_<kino>`: kino ID havolaga qo'shilgani uchun server qayta ishga tushsa ham
    # (xotiradagi xonalar yo'qolsa) havola ochilganda xona o'sha kino bilan qayta tiklanadi.
    return f"https://t.me/{username}?startapp=room_{room_id}_{movie_id}&mode=fullscreen"


def _get_room(rid):
    _purge_rooms()
    with ROOM_LOCK:
        room = ROOMS.get(rid)
        if room is not None:
            room["last_active"] = time.time()
        return room


def _verify(init_data):
    # Telegram Mini App ochiq turganda initData'ni yangilamaydi. Umumiy 1 soatlik
    # limit (webapp_security.MAX_INIT_DATA_AGE_SECONDS) film o'rtasida (60-daqiqada)
    # barcha so'rovlarni rad etardi. Kino uchun alohida, uzunroq oyna ishlatamiz.
    return webapp_security.verify_telegram_init_data(
        init_data,
        config.TELEGRAM_TOKEN,
        max_age_seconds=int(getattr(config, "KINO_INIT_DATA_MAX_AGE_SEC", 24 * 60 * 60)),
    )


def _respond(handler, data, err):
    """Barcha /api/kino/* javoblari uchun yagona format. Sessiya xatosi 401 +
    code="auth" bilan ajratiladi, shunda mijoz foydalanuvchiga to'g'ri xabar beradi."""
    if err:
        if err == ERR_AUTH:
            status, code = 401, "auth"
        elif err == ERR_ROOM:
            status, code = 404, "room"
        elif err == ERR_NOT_MEMBER:
            # Xona qayta tiklangan bo'lishi mumkin (server restart): mijoz join orqali qayta qo'shiladi.
            status, code = 403, "member"
        else:
            status, code = 400, "error"
        return _json(handler, status, {"ok": False, "data": None, "error": err, "code": code})
    return _json(handler, 200, {"ok": True, "data": data, "error": None})


def join_room(rid: str, init_data: str, movie_hint: str = ""):
    user = _verify(init_data)
    if not user:
        return None, ERR_AUTH
    rid = rid if isinstance(rid, str) else ""
    room = _get_room(rid)
    if not room and movie_hint and isinstance(movie_hint, str):
        # Xotiradagi xonalar server qayta ishga tushganda yo'qoladi. Mijoz kino ID'ni
        # bilgani uchun xonani SHU ID bilan qayta tiklaymiz (xona ID'si 96 bitlik tasodifiy
        # kalit, havolani bilish uning o'zi xonaga kirish huquqi bilan teng).
        if create_room(movie_hint, int(user["id"]), rid=rid):
            room = _get_room(rid)
    if not room:
        return None, ERR_ROOM
    uid = str(user["id"])
    with ROOM_LOCK:
        if uid not in room["participants"] and len(room["participants"]) >= 2:
            return None, "Bu xona to'la. Faqat 2 kishi birga tomosha qilishi mumkin."
        movie = storage.get_movie(room["movie_id"])
        if not movie:
            return None, "Bu kino katalogdan o'chirilgan."
        room["participants"].setdefault(uid, {"joined_at": time.time()})
        room["joined"] = True
        token = urllib.parse.quote(_make_stream_token(rid, movie["id"], int(uid)))
        return {
            "user_id": int(uid),
            "movie": _public_movie(movie),
            "participants": [int(x) for x in room["participants"]],
            "state": dict(room["state"]),
            "server_now": time.time(),
            "stream_path": f"/api/kino/stream/{rid}/{movie['id']}?token={token}",
            "share_url": room_url(movie["id"], rid),
        }, None


def _room_movie_payload(room: dict, rid: str, user_id: int | None = None):
    movie = storage.get_movie(room.get("movie_id"))
    token = _make_stream_token(rid, movie["id"], int(user_id or 0)) if movie and user_id else ""
    stream = f"/api/kino/stream/{rid}/{movie['id']}?token={urllib.parse.quote(token)}" if movie and token else ""
    return {
        "movie": _public_movie(movie),
        "stream_path": stream,
    }


def change_movie(rid: str, init_data: str, movie_id: str):
    """Xonadagi kinoni barcha qatnashchilar uchun almashtiradi.
    Xona va WebRTC ulanishi saqlanadi; faqat video holati boshidan boshlanadi.
    Boshqa ishtirokchi yangi kinoni keyingi /state so'rovida (polling) oladi."""
    user = _verify(init_data)
    if not user:
        return None, ERR_AUTH
    room = _get_room(rid)
    if not room:
        return None, ERR_ROOM
    movie = storage.get_movie(str(movie_id or ""))
    if not movie:
        return None, "Kino topilmadi."
    if int(movie.get("size") or 0) <= 0:
        return None, "Bu kinoning hajmi aniqlanmagan, uni oqimlab bo'lmaydi."
    uid = str(user["id"])
    with ROOM_LOCK:
        if uid not in room["participants"]:
            return None, ERR_NOT_MEMBER
        if str(room.get("movie_id")) != str(movie["id"]):
            room["movie_id"] = str(movie["id"])
            room["state"].update({
                "playing": False,
                "position": 0.0,
                "updated_at": time.time(),
                "actor_id": int(user["id"]),
            })
            room["state"]["version"] += 1
            # Diqqat: room["signals"] ATAYLAB tozalanmaydi. Kino almashishi
            # kamera/mikrofon (WebRTC) ulanishiga tegmasligi kerak; navbatdagi
            # offer/answer/ice xabarlarini o'chirish ulanishni buzardi.
            room["updated_at"] = time.time()
        return {
            **room["state"],
            "participants": [int(x) for x in room["participants"]],
            "server_now": time.time(),
            **_room_movie_payload(room, rid, int(user["id"])),
        }, None


def list_movies(rid: str, init_data: str):
    user = _verify(init_data)
    if not user:
        return None, ERR_AUTH
    room = _get_room(rid)
    if not room:
        return None, ERR_ROOM
    if str(user["id"]) not in room["participants"]:
        return None, ERR_NOT_MEMBER
    movies = storage.search_movies("")
    return [{"id": str(m["id"]), "title": m["title"]} for m in movies[:50]], None


def _clean_position(value):
    """None | sonli qiymat -> chekli float [0, MAX_POSITION_SEC]. Aks holda ValueError.
    NaN/Infinity ruxsat etilmaydi: ular JSON'ni hamma uchun yaroqsiz qilib qo'yardi."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("bool")
    number = float(value)          # str/None/list -> ValueError/TypeError
    if not math.isfinite(number):
        raise ValueError("not finite")
    return min(max(0.0, number), float(MAX_POSITION_SEC))


def room_state(rid: str, init_data: str, playing=None, position=None):
    user = _verify(init_data)
    if not user:
        return None, ERR_AUTH
    room = _get_room(rid)
    if not room:
        return None, ERR_ROOM
    uid = str(user["id"])
    try:
        position = _clean_position(position)
    except (TypeError, ValueError):
        return None, "Pozitsiya noto'g'ri."
    if playing is not None and not isinstance(playing, bool):
        return None, "Ijro holati noto'g'ri."
    with ROOM_LOCK:
        if uid not in room["participants"]:
            return None, ERR_NOT_MEMBER
        if playing is not None or position is not None:
            # Only an explicit client action changes the authoritative room state.
            # Store the actor so clients can distinguish their own echo from a
            # genuine remote event; this prevents a polling response from
            # rewinding the local player to an older position.
            if playing is not None:
                room["state"]["playing"] = bool(playing)
            if position is not None:
                room["state"]["position"] = position
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
        return None, ERR_AUTH
    room = _get_room(rid)
    text = (text or "").strip()
    if not room:
        return None, ERR_ROOM
    if not text:
        return None, "Xabar bo'sh."
    if len(text) > 500:
        text = text[:500]
    uid = str(user["id"])
    with ROOM_LOCK:
        if uid not in room["participants"]:
            return None, ERR_NOT_MEMBER
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
    if not user:
        return None, ERR_AUTH
    room = _get_room(rid)
    if not room:
        return None, ERR_ROOM
    if str(user["id"]) not in room["participants"]:
        return None, ERR_NOT_MEMBER
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
    if not user:
        return False, ERR_AUTH
    room = _get_room(rid)
    if not room:
        return False, ERR_ROOM
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
    if not user:
        return None, ERR_AUTH
    room = _get_room(rid)
    if not room:
        return None, ERR_ROOM
    uid = str(user["id"])
    if uid not in room["participants"]:
        return None, ERR_NOT_MEMBER
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
    # Bot tokenini HMAC kaliti sifatida to'g'ridan-to'g'ri ishlatmaymiz: maqsadga xos kalit.
    return hmac.new(value.encode("utf-8"), b"kino-stream-token-v1", hashlib.sha256).digest()


def _make_stream_token(room_id: str, movie_id: str, user_id: int, ttl: int | None = None) -> str:
    # Brauzer bitta stream URL'ni butun film davomida ishlatadi (Range so'rovlari
    # shu URL bilan keladi). 1 soatlik token filmni 60-daqiqada uzib qo'yardi.
    # Haqiqiy ruxsat nazorati _authorize_stream'dagi xona/ishtirokchi tekshiruvi.
    if ttl is None:
        ttl = getattr(config, "KINO_STREAM_TOKEN_TTL_SEC", 12 * 60 * 60)
    payload = f"{room_id}|{movie_id}|{int(user_id)}|{int(time.time()) + max(60, int(ttl))}"
    raw = payload.encode("utf-8")
    sig = hmac.new(_stream_secret(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + b"." + sig).decode("ascii").rstrip("=")


def _verify_stream_token(token: str, room_id: str, movie_id: str) -> int | None:
    try:
        pad = "=" * (-len(token) % 4)
        blob = base64.urlsafe_b64decode((token + pad).encode("ascii"))
        # Format: raw + b"." + sha256-imzo (32 bayt). Imzo ixtiyoriy baytlardan iborat va
        # ichida "." (0x2E) bo'lishi mumkin, shuning uchun rsplit(b".") ishlatib bo'lmaydi
        # (avval ~8 tadan 1 token tasodifiy "yaroqsiz" deb rad etilardi). Imzo oxiridan kesiladi.
        digest_len = hashlib.sha256().digest_size
        if len(blob) <= digest_len + 1 or blob[-(digest_len + 1):-digest_len] != b".":
            return None
        raw, sig = blob[:-(digest_len + 1)], blob[-digest_len:]
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


def _effective_range(handler, size: int):
    """Range sarlavhasini tahlil qiladi va javob uzunligini cheklaydi -> (start, end, status) | None.

    * Range bor bo'lsa javob HAR DOIM 206. Avval butun faylni so'raydigan `bytes=0-`
      ga 200 qaytarilardi, iOS Safari esa Range so'roviga 206 kutadi.
    * Javob KINO_STREAM_MAX_RESPONSE_BYTES dan oshmaydi; brauzer qolganini keyingi Range
      bilan oladi. Shunda bitta ulanish butun filmni ushlab turmaydi: seek qilinganda eski
      ulanish soniyalarda yopiladi va umumiy oqim limiti (STREAM_SLOT) band bo'lib qolmaydi.
    """
    parsed = _parse_range(handler.headers.get("Range", ""), size)
    if not parsed:
        return None
    start, end, status = parsed
    if status == 206:
        cap = max(64 * 1024, int(getattr(config, "KINO_STREAM_MAX_RESPONSE_BYTES", 8 * 1024 * 1024)))
        end = min(end, start + cap - 1)
    return start, end, status


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


def _serve_mtproto_range(handler, movie: dict, start: int, end: int, content_type: str, status: int = 206):
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
    _send_stream_headers(handler, status, content_type, size, start, end)
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


def _bot_api_stream(handler, movie: dict, start: int, end: int, content_type: str, status: int = 206):
    """Legacy/secondary fallback: Telegram Bot API CDN through this Render process."""
    if not movie.get("file_id"):
        raise RuntimeError("Fallback uchun file_id mavjud emas.")
    size = int(movie.get("size") or 0)
    if size > BOT_API_DOWNLOAD_LIMIT:
        # Bot API getFile 20 MB'dan katta faylni umuman bermaydi — befoyda so'rov
        # yubormay, sababni aniq aytamiz.
        raise RuntimeError(
            f"Bot API zaxirasi {BOT_API_DOWNLOAD_LIMIT // (1024 * 1024)} MB dan katta faylni bera olmaydi "
            f"(kino hajmi {size // (1024 * 1024)} MB). MTProto tuzatilishi kerak: /kino_check."
        )
    remote = _telegram_file_url(movie["file_id"])
    req = urllib.request.Request(remote, headers={
        "User-Agent": "StudentAI-Kino/3.0",
        "Range": f"bytes={start}-{end}",
    })
    with urllib.request.urlopen(req, timeout=35) as resp:
        up_status = getattr(resp, "status", 200)
        if up_status == 200 and start:
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
            size = actual_size + (start if up_status == 200 else 0)
        _send_stream_headers(handler, status, content_type, size, start, end)
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
    # Mobil ilova xonaga join_room orqali kirmaydi, faqat stream qiladi: shu ham "ishlatilmoqda".
    room["joined"] = True
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
    parsed_range = _effective_range(handler, size)
    if not parsed_range:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    start, end, status = parsed_range
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
    parsed_range = _effective_range(handler, size)
    if not parsed_range:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    start, end, status = parsed_range
    content_type = _mime_for_movie(movie)
    handler._kino_stream_headers_sent = False
    acquired = STREAM_SLOT.acquire(timeout=float(getattr(config, "KINO_STREAM_TIMEOUT_SEC", 35)))
    if not acquired:
        _send_text(handler, 503, "Kino oqimi band. Bir necha soniyadan keyin qayta urinib ko'ring.")
        return
    try:
        try:
            logger.info("🎬 STREAM MTProto TRY movie=%s bytes=%s-%s", movie_id, start, end)
            _serve_mtproto_range(handler, movie, start, end, content_type, status)
            logger.info("🎬 STREAM MTProto OK movie=%s bytes=%s-%s", movie_id, start, end)
            return
        except _CLIENT_GONE:
            # Brauzer ulanishni yopdi (seek, sahifa yopildi). Bu xato emas: zaxiraga o'tmaymiz,
            # slot esa finally'da darhol bo'shaydi.
            logger.info("🎬 STREAM client uzildi movie=%s bytes=%s-%s", movie_id, start, end)
            handler.close_connection = True
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
            _bot_api_stream(handler, movie, start, end, content_type, status)
            logger.info("🎬 STREAM FALLBACK OK movie=%s bytes=%s-%s", movie_id, start, end)
        except _CLIENT_GONE:
            logger.info("🎬 STREAM client uzildi (fallback) movie=%s bytes=%s-%s", movie_id, start, end)
            handler.close_connection = True
        except Exception as fallback_exc:
            logger.error("🎬 STREAM FALLBACK FAILED movie=%s room=%s file_id=%s bytes=%s-%s error=%s: %s", movie_id, room_id, bool(movie.get("file_id")), start, end, type(fallback_exc).__name__, fallback_exc, exc_info=True)
            # If headers were already sent by the fallback, the socket may be
            # partially written; otherwise provide a clean HTTP error.
            try:
                if size > BOT_API_DOWNLOAD_LIMIT:
                    _send_text(handler, 502, "Telegram MTProto oqimi ulanmagan va katta fayl uchun zaxira yo'q. Admin /kino_check ni ishga tushirsin.")
                else:
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


def _str_arg(value, limit: int = 200) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _to_int(value):
    """int | None. bool, float, matn va boshqalarni qat'iy rad etadi (matn faqat raqamlardan iborat bo'lsa)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,18}", value.strip()):
        return int(value.strip())
    return None


def handle_api(handler):
    from urllib.parse import parse_qs, urlsplit
    try:
        parsed = urlsplit(handler.path)
        path = parsed.path
        init_data = handler.headers.get("X-Telegram-Init-Data", "")
        qs = parse_qs(parsed.query)
        room = _str_arg(qs.get("room", [""])[0])
        if path == "/api/kino/join":
            data, err = join_room(room, init_data, _str_arg(qs.get("movie", [""])[0], 64))
            return _respond(handler, data, err)
        if path == "/api/kino/state":
            data, err = room_state(room, init_data)
            return _respond(handler, data, err)
        if path == "/api/kino/chat":
            data, err = get_chat(room, init_data, _str_arg(qs.get("after", [""])[0]))
            return _respond(handler, data, err)
        if path == "/api/kino/movies":
            data, err = list_movies(room, init_data)
            return _respond(handler, data, err)
        if path == "/api/kino/signals":
            data, err = get_signals(room, init_data)
            return _respond(handler, data, err)
        return _json(handler, 404, {"ok": False, "error": "Not found."})
    except _CLIENT_GONE:
        raise
    except Exception:
        logger.exception("🎬 /api/kino GET xatosi: %s", getattr(handler, "path", ""))
        return _json(handler, 500, {"ok": False, "data": None, "error": "Server xatosi.", "code": "error"})


def _reject_json_constant(name):
    raise ValueError(f"JSON konstantasi ruxsat etilmaydi: {name}")


def _read_json_body(handler):
    """-> (dict, None) yoki (None, (status, xabar)). Tana autentifikatsiyadan OLDIN o'qiladi,
    shuning uchun uning hajmi cheklanadi va NaN/Infinity qabul qilinmaydi."""
    try:
        length = int(handler.headers.get("Content-Length", 0) or 0)
    except (TypeError, ValueError):
        return None, (400, "Content-Length noto'g'ri.")
    if length < 0:
        return None, (400, "Content-Length noto'g'ri.")
    if length > MAX_BODY_BYTES:
        # Tanani o'qimaymiz; keep-alive ulanishni yopamiz, aks holda o'qilmagan bayt keyingi so'rovni buzadi.
        handler.close_connection = True
        return None, (413, "So'rov juda katta.")
    try:
        raw = handler.rfile.read(length) if length else b""
        body = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant) if raw else {}
    except Exception:
        return None, (400, "Noto'g'ri JSON.")
    if not isinstance(body, dict):
        return None, (400, "JSON obyekt bo'lishi kerak.")
    return body, None


def handle_post(handler):
    from urllib.parse import urlsplit
    try:
        body, bad = _read_json_body(handler)
        if bad:
            return _json(handler, bad[0], {"ok": False, "data": None, "error": bad[1], "code": "error"})
        path = urlsplit(handler.path).path
        init_data = handler.headers.get("X-Telegram-Init-Data", "") or _str_arg(body.get("init_data"), 4096)
        room = _str_arg(body.get("room"))
        if path == "/api/kino/create":
            user = _verify(init_data)
            movie_id = _str_arg(body.get("movie"), 64)
            if not user:
                return _respond(handler, None, ERR_AUTH)
            if not movie_id or not storage.get_movie(movie_id):
                return _respond(handler, None, "Kino topilmadi.")
            rid = create_room(movie_id, int(user["id"]))
            if not rid:
                return _json(handler, 503, {"ok": False, "data": None, "code": "error",
                                            "error": "Hozir faol xonalar juda ko'p. Birozdan keyin urinib ko'ring."})
            return _json(handler, 200, {"ok": True, "data": {"room": rid, "url": room_url(movie_id, rid)}})
        if path == "/api/kino/state":
            data, err = room_state(room, init_data, body.get("playing"), body.get("position"))
            return _respond(handler, data, err)
        if path == "/api/kino/change_movie":
            data, err = change_movie(room, init_data, _str_arg(body.get("movie_id"), 64))
            return _respond(handler, data, err)
        if path == "/api/kino/chat":
            text = body.get("text")
            if not isinstance(text, str):
                return _respond(handler, None, ERR_BAD_REQUEST)
            data, err = add_chat(room, init_data, text, _str_arg(body.get("client_id"), 100))
            return _respond(handler, data, err)
        if path == "/api/kino/signal":
            target = _to_int(body.get("target_user_id"))
            payload = body.get("payload")
            if target is None or not isinstance(payload, dict):
                return _respond(handler, None, ERR_BAD_REQUEST)
            data, err = put_signal(room, init_data, target, payload)
            return _respond(handler, data, err)
        return _json(handler, 404, {"ok": False, "error": "Not found."})
    except _CLIENT_GONE:
        raise
    except Exception:
        logger.exception("🎬 /api/kino POST xatosi: %s", getattr(handler, "path", ""))
        return _json(handler, 500, {"ok": False, "data": None, "error": "Server xatosi.", "code": "error"})


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
