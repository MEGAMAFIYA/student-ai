"""Telegram MTProto client — kino uchun PRIMARY Telegram media resolver.

Bu modul fayl baytlarini diskka saqlamaydi. U Telegramdagi aniq message'ni
Telethon orqali topadi va undan document metadata'sini beradi. Streaming
adapter keyingi bosqichda shu metadata asosida Telegramdan chunklarni oladi.

Bot API tokeni bu modulga umuman berilmaydi: MTProto uchun alohida user
session (StringSession) ishlatiladi.
"""

import base64
import asyncio
import logging
import threading
import time
from typing import Any, Callable

import config

logger = logging.getLogger(__name__)

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    TELETHON_AVAILABLE = True
except ImportError:  # pragma: no cover - requirements.txt Telethonni o'z ichiga oladi
    TELETHON_AVAILABLE = False

_client = None
_stream_loop = None
_stream_thread = None
_stream_client = None
_stream_ready = threading.Event()
_stream_lock = threading.RLock()


def is_configured() -> bool:
    """MTProto uchun barcha zarur secretlar mavjudligini tekshiradi."""
    return bool(
        TELETHON_AVAILABLE
        and config.TG_API_ID
        and config.TG_API_HASH
        and config.TG_SESSION
    )


def _new_client():
    if not is_configured():
        raise RuntimeError("Telegram MTProto sozlanmagan (API_ID/API_HASH/SESSION).")
    return TelegramClient(
        StringSession(config.TG_SESSION),
        int(config.TG_API_ID),
        config.TG_API_HASH,
        connection_retries=1,
        retry_delay=1,
        request_retries=1,
        timeout=getattr(config, "TG_SEARCH_TIMEOUT_SEC", 15),
    )


async def get_client():
    """Bitta async event-loop ichida qayta ishlatiladigan MTProto client."""
    global _client
    if _client is None:
        _client = _new_client()
    if not _client.is_connected():
        await _client.connect()
    if not await _client.is_user_authorized():
        raise RuntimeError("Telegram MTProto session avtorizatsiyadan o'tmagan.")
    return _client


def _b64(data: bytes | bytearray | None) -> str:
    if not data:
        return ""
    return base64.urlsafe_b64encode(bytes(data)).decode("ascii").rstrip("=")


def _document_metadata(message: Any) -> dict:
    """Telethon message'dan DB uchun faqat identifier/metadata chiqaradi."""
    document = getattr(message, "document", None)
    if not document:
        raise ValueError("Telegram message ichida document topilmadi.")

    file_obj = getattr(message, "file", None)
    mime_type = getattr(document, "mime_type", None) or getattr(file_obj, "mime_type", None) or ""
    file_name = getattr(file_obj, "name", None) or ""
    size = int(getattr(document, "size", 0) or getattr(file_obj, "size", 0) or 0)

    return {
        "telegram_chat_id": int(getattr(message, "chat_id", 0) or 0),
        "telegram_message_id": int(getattr(message, "id", 0) or 0),
        "telegram_document_id": int(getattr(document, "id", 0) or 0),
        # Bot API file_unique_id va MTProto document.id bir xil identifikator emas.
        # Shuning uchun MTProto bu maydonni o'zi uydirmaydi; handler Bot API unique_id
        # ni alohida saqlab beradi.
        "telegram_file_unique_id": "",
        "telegram_access_hash": (
            int(document.access_hash) if getattr(document, "access_hash", None) is not None else None
        ),
        "telegram_file_reference": _b64(getattr(document, "file_reference", None)),
        "mime_type": mime_type,
        "file_name": file_name[:200],
        "size": size,
    }


class MTProtoStorageError(RuntimeError):
    """Kino saqlash kanali/xabari MTProto orqali ochilmaganda (aniq sabab bilan)."""


# StringSession entity (access_hash) keshini SAQLAMAYDI. Yangi ishga tushgan jarayonda
# `get_messages(-100...)` "Could not find the input entity" bilan yiqiladi, toki
# kanal dialoglar ro'yxati orqali bir marta ko'rilmaguncha. Shu sababli har client
# uchun keshni bir marta "isitamiz" va kerak bo'lsa public username'ga tushamiz.
_DIALOGS_REWARM_SEC = 300


async def _resolve_input_chat(client, chat_id: int):
    chat_id = int(chat_id)
    try:
        return await client.get_input_entity(chat_id)
    except ValueError:
        pass

    now = time.monotonic()
    last_warm = getattr(client, "_kino_dialogs_warm_at", None)
    if last_warm is None or now - last_warm > _DIALOGS_REWARM_SEC:
        # Barcha dialoglarni bir marta yuklaymiz (kesh to'ladi). Muvaffaqiyatsiz
        # urinishdan keyin ham vaqt belgilanadi, shunda har so'rovda qayta yuklanmaydi.
        client._kino_dialogs_warm_at = now
        try:
            await client.get_dialogs()
        except Exception as exc:
            logger.warning("MTProto get_dialogs xato: %s: %s", type(exc).__name__, exc)
        try:
            return await client.get_input_entity(chat_id)
        except ValueError:
            pass

    username = (getattr(config, "KINO_STORAGE_CHANNEL_USERNAME", "") or "").strip().lstrip("@")
    if username and chat_id == int(getattr(config, "KINO_STORAGE_CHANNEL_ID", 0) or 0):
        try:
            return await client.get_input_entity(username)
        except Exception as exc:
            logger.warning("MTProto username orqali kanal topilmadi (%s): %s: %s", username, type(exc).__name__, exc)

    raise MTProtoStorageError(
        f"Kanal ({chat_id}) MTProto sessiyasi uchun ochilmadi. Session akkaunti shu kanalga "
        "a'zo bo'lishi (yoki kanal public username'ga ega bo'lishi) kerak."
    )


async def _fetch_message(client, chat_id: int, message_id: int):
    entity = await _resolve_input_chat(client, chat_id)
    message = await client.get_messages(entity, ids=int(message_id))
    if not message:
        raise MTProtoStorageError(f"Xabar topilmadi: chat={chat_id} message={message_id}.")
    if not getattr(message, "media", None):
        raise MTProtoStorageError(f"Xabarda media yo'q: chat={chat_id} message={message_id}.")
    return message


async def resolve_message(chat_id: int, message_id: int) -> dict:
    """Telegramdagi aniq message'ni MTProto orqali resolve qiladi.

    Muhim: media download qilinmaydi. Faqat metadata olinadi.
    """
    client = await get_client()
    message = await _fetch_message(client, chat_id, message_id)
    return _document_metadata(message)


async def check_storage(chat_id: int, message_id: int | None = None) -> tuple[bool, str]:
    """Admin diagnostikasi: session kanalni va (ixtiyoriy) xabarni ocha oladimi?
    Hech qanday secret yoki media baytlari qaytarilmaydi."""
    try:
        client = await get_client()
        await _resolve_input_chat(client, chat_id)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if message_id is None:
        return True, "kanal ochildi"
    try:
        meta = _document_metadata(await _fetch_message(client, chat_id, message_id))
    except Exception as exc:
        return False, f"kanal ochildi, lekin xabar #{message_id} o'qilmadi — {type(exc).__name__}: {exc}"
    return True, f"xabar #{message_id} OK, hajm {int(meta.get('size') or 0) / (1024 * 1024):.1f} MB"


def _stream_loop_worker():
    """Separate event loop for synchronous HTTP streaming threads.

    The bot itself already owns its asyncio loop. Telethon clients are not
    shared across loops, so the streaming adapter uses a second StringSession
    client in this dedicated loop. No media bytes are persisted.
    """
    global _stream_loop, _stream_client
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _stream_loop = loop
    try:
        _stream_client = _new_client()
        loop.run_until_complete(_stream_client.connect())
        if not loop.run_until_complete(_stream_client.is_user_authorized()):
            raise RuntimeError("Telegram MTProto stream session avtorizatsiyadan o'tmagan.")
        _stream_ready.set()
        loop.run_forever()
    except Exception as exc:
        logger.error("MTProto stream loop ishga tushmadi: %s", exc, exc_info=True)
        _stream_ready.set()
    finally:
        try:
            if _stream_client is not None and _stream_client.is_connected():
                loop.run_until_complete(_stream_client.disconnect())
        except Exception:
            pass
        loop.close()


def _ensure_stream_client():
    global _stream_thread
    if not is_configured():
        raise RuntimeError("Telegram MTProto sozlanmagan.")
    with _stream_lock:
        if _stream_thread is None or not _stream_thread.is_alive():
            _stream_ready.clear()
            _stream_thread = threading.Thread(target=_stream_loop_worker, name="kino-mtproto", daemon=True)
            _stream_thread.start()
    if not _stream_ready.wait(timeout=20):
        raise RuntimeError("MTProto stream client ishga tushmadi.")
    if _stream_loop is None or _stream_client is None or not _stream_client.is_connected():
        raise RuntimeError("MTProto stream client ulanmagan.")
    return _stream_loop, _stream_client


# Stream loop ichida ishlatiladi (bitta thread) — qulf kerak emas. Har 1 MiB chunk uchun
# `get_messages` chaqirish minglab ortiqcha so'rov va FloodWait xavfini tug'dirardi.
# file_reference soatlar davomida yaroqli; xato bo'lsa keshni tashlab, bir marta qayta olamiz.
_MSG_CACHE: dict[tuple[int, int], tuple[float, Any]] = {}
_MSG_CACHE_TTL_SEC = 600
_MSG_CACHE_MAX = 64


async def _get_stream_message(chat_id: int, message_id: int, fresh: bool = False):
    key = (int(chat_id), int(message_id))
    now = time.monotonic()
    if not fresh:
        hit = _MSG_CACHE.get(key)
        if hit and now - hit[0] < _MSG_CACHE_TTL_SEC:
            return hit[1]
    message = await _fetch_message(_stream_client, chat_id, message_id)
    if len(_MSG_CACHE) >= _MSG_CACHE_MAX:
        oldest = min(_MSG_CACHE, key=lambda k: _MSG_CACHE[k][0])
        _MSG_CACHE.pop(oldest, None)
    _MSG_CACHE[key] = (now, message)
    return message


async def _read_range(media, offset: int, limit: int) -> bytes:
    chunks = []
    remaining = int(limit)
    request_size = min(
        int(getattr(config, "KINO_STREAM_CHUNK_SIZE", 1024 * 1024)),
        max(64 * 1024, remaining),
    )
    it = _stream_client.iter_download(
        media, offset=max(0, int(offset)), limit=remaining,
        request_size=request_size,
    )
    try:
        async for chunk in it:
            if not chunk:
                break
            chunks.append(bytes(chunk))
            remaining -= len(chunk)
            if remaining <= 0:
                break
    finally:
        # Erta break qilinganda Telethon boshqa DC uchun ochgan "exported sender"ni
        # o'zi yopmaydi; yopmasak har Range so'rovida ulanish sizib chiqadi.
        close = getattr(it, "close", None)
        if close is not None:
            try:
                res = close()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass
    # Telegram `request_size` ga karrali qaytaradi; HTTP Content-Length'dan ortiq
    # bayt yozilmasligi uchun so'ralgan uzunlikkacha kesamiz.
    return b"".join(chunks)[: int(limit)]


async def _download_range_async(chat_id: int, message_id: int, offset: int, limit: int) -> bytes:
    # This coroutine is executed inside the dedicated stream loop.
    if int(limit) <= 0:
        return b""
    message = await _get_stream_message(chat_id, message_id)
    try:
        return await _read_range(message.media, offset, limit)
    except Exception as exc:
        if "FileReference" not in type(exc).__name__:
            raise
        logger.info("MTProto file_reference eskirdi, yangilanmoqda: chat=%s message=%s", chat_id, message_id)
        _MSG_CACHE.pop((int(chat_id), int(message_id)), None)
        message = await _get_stream_message(chat_id, message_id, fresh=True)
        return await _read_range(message.media, offset, limit)


def download_range(chat_id: int, message_id: int, offset: int, limit: int, timeout: float = 30.0) -> bytes:
    """Synchronously fetch at most ``limit`` bytes from Telegram via MTProto.

    Only the current HTTP chunk is held in RAM; no file is written to disk.
    """
    loop, _ = _ensure_stream_client()
    future = asyncio.run_coroutine_threadsafe(
        _download_range_async(int(chat_id), int(message_id), int(offset), int(limit)),
        loop,
    )
    return future.result(timeout=max(1.0, float(timeout)))


# ---------------------------------------------------------------------------
# Pipelined streaming (kino lag fix)
#
# Eski usul: har 1 MiB uchun alohida `download_range()` -> Telegramdan olish va
# brauzerga yozish NAVBATMA-NAVBAT edi, har chunkda yangi iter_download ochilardi.
# Yangi usul: bitta iter_download butun Range bo'yicha ishlaydi va chunklarni
# cheklangan navbatga (asyncio.Queue) oldindan yuklab turadi. HTTP thread esa
# navbatdan olib brauzerga yozadi - ikkalasi PARALLEL ishlaydi.
# ---------------------------------------------------------------------------
_MIN_REQUEST = 4096
_MAX_REQUEST = 1024 * 1024


def _pick_request_size(value: int | None = None) -> int:
    """Telegram talabi: 4096 ga karrali, 1 MiB dan oshmaydi."""
    size = int(value or getattr(config, "KINO_STREAM_CHUNK_SIZE", _MAX_REQUEST))
    size = max(_MIN_REQUEST, min(_MAX_REQUEST, size))
    return size - (size % _MIN_REQUEST)


async def _close_iter(it) -> None:
    close = getattr(it, "close", None)
    if close is None:
        return
    try:
        res = close()
        if asyncio.iscoroutine(res):
            await res
    except Exception:
        pass


async def _stream_producer(q: "asyncio.Queue", chat_id: int, message_id: int,
                           offset: int, limit: int, req: int) -> None:
    """Stream loop ichida ishlaydi. Chunklarni `q` ga qo'yadi (backpressure: maxsize)."""
    sent = 0
    refreshed = False
    fresh = False
    try:
        while sent < limit:
            message = await _get_stream_message(chat_id, message_id, fresh=fresh)
            fresh = False
            pos = offset + sent
            aligned = pos - (pos % req)       # Telegram offset'i chunkka karrali bo'lishi kerak
            skip = pos - aligned
            chunk_count = (skip + (limit - sent) + req - 1) // req
            it = _stream_client.iter_download(
                message.media, offset=aligned, limit=chunk_count, request_size=req,
            )
            try:
                async for chunk in it:
                    data = bytes(chunk)
                    if not data:
                        break
                    if skip:
                        if len(data) <= skip:
                            skip -= len(data)
                            continue
                        data = data[skip:]
                        skip = 0
                    data = data[: limit - sent]
                    if not data:
                        break
                    await q.put(("data", data))
                    sent += len(data)
                    if sent >= limit:
                        break
                break                          # oqim normal tugadi
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # file_reference eskirsa: keshni tashlab, QOLGAN joydan davom etamiz.
                if "FileReference" in type(exc).__name__ and not refreshed:
                    refreshed = True
                    fresh = True
                    _MSG_CACHE.pop((int(chat_id), int(message_id)), None)
                    logger.info("MTProto file_reference eskirdi (stream): chat=%s message=%s", chat_id, message_id)
                    continue
                raise
            finally:
                await _close_iter(it)
        await q.put(("end", None))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await q.put(("error", exc))


async def _queue_get(q: "asyncio.Queue"):
    return await q.get()


def stream_range(chat_id: int, message_id: int, offset: int, limit: int,
                 timeout: float = 30.0, prefetch: int | None = None):
    """Blocking generator: Telegramdan [offset, offset+limit) baytlarni bo'laklab beradi.

    * Yuklash fonda (stream loop) oldinga ketadi, `prefetch` ta chunkgacha.
    * Generator yopilganda (`.close()` yoki client uzilganda) fon vazifa bekor qilinadi.
    * Hech narsa diskka yozilmaydi; RAM'da eng ko'pi bilan prefetch * chunk_size.
    """
    offset = max(0, int(offset))
    limit = int(limit)
    if limit <= 0:
        return
    loop, _ = _ensure_stream_client()
    prefetch = max(1, int(prefetch or getattr(config, "KINO_STREAM_PREFETCH", 4)))
    req = _pick_request_size()
    timeout = max(1.0, float(timeout))

    async def _setup():
        q = asyncio.Queue(maxsize=prefetch)
        task = asyncio.ensure_future(
            _stream_producer(q, int(chat_id), int(message_id), offset, limit, req)
        )
        return q, task

    q, task = asyncio.run_coroutine_threadsafe(_setup(), loop).result(timeout=timeout)
    got = 0
    try:
        while True:
            fut = asyncio.run_coroutine_threadsafe(_queue_get(q), loop)
            try:
                kind, payload = fut.result(timeout=timeout)
            except Exception:
                fut.cancel()
                raise
            if kind == "data":
                got += len(payload)
                yield payload
            elif kind == "end":
                break
            else:
                raise payload
        if got < limit:
            raise RuntimeError("Telegram MTProto oqimi erta tugadi.")
    finally:
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            pass  # loop yopilgan


async def check_connection() -> tuple[bool, str]:
    """Admin/debug uchun secretlarni oshkor qilmasdan ulanish holatini tekshiradi."""
    try:
        client = await get_client()
        me = await client.get_me()
        label = getattr(me, "username", None) or getattr(me, "id", "unknown")
        return True, f"MTProto OK: {label}"
    except Exception as exc:
        logger.warning("MTProto connection check failed: %s", exc)
        return False, f"MTProto xato: {type(exc).__name__}"


async def close_client() -> None:
    """Toza shutdown: asosiy va kino streaming MTProto clientlarini yopadi."""
    global _client, _stream_client, _stream_loop, _stream_thread

    if _client is not None:
        try:
            await _client.disconnect()
        finally:
            _client = None

    # Streaming client alohida event-loop/thread'da ishlaydi. Uni o'sha
    # loop ichidan to'xtatish kerak; boshqa loop'dan disconnect qilish
    # Telethon obyektini noto'g'ri loopga bog'lab qo'yishi mumkin.
    loop = _stream_loop
    client = _stream_client
    if loop is not None and client is not None and loop.is_running():
        async def _shutdown_stream():
            try:
                if client.is_connected():
                    await client.disconnect()
            finally:
                loop.stop()
        try:
            future = asyncio.run_coroutine_threadsafe(_shutdown_stream(), loop)
            future.result(timeout=10)
        except Exception as exc:
            logger.warning("MTProto stream shutdown failed: %s", exc)

    thread = _stream_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=10)
    _stream_client = None
    _stream_loop = None
    _stream_thread = None
