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


async def resolve_message(chat_id: int, message_id: int) -> dict:
    """Telegramdagi aniq message'ni MTProto orqali resolve qiladi.

    Muhim: media download qilinmaydi. Faqat metadata olinadi.
    """
    client = await get_client()
    message = await client.get_messages(int(chat_id), ids=int(message_id))
    if not message:
        raise RuntimeError("Telegram message topilmadi.")
    return _document_metadata(message)



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


async def _download_range_async(chat_id: int, message_id: int, offset: int, limit: int) -> bytes:
    # This coroutine is executed inside the dedicated stream loop.
    # Message'ni har Range so'roviga qayta resolve qilish file_reference eskirgan
    # holatda ham yangi Telegram media reference olish imkonini beradi.
    message = await _stream_client.get_messages(int(chat_id), ids=int(message_id))
    if not message or not getattr(message, "media", None):
        raise RuntimeError("Telegram media message topilmadi.")
    chunks = []
    remaining = int(limit)
    request_size = min(
        int(getattr(config, "KINO_STREAM_CHUNK_SIZE", 1024 * 1024)),
        max(64 * 1024, remaining),
    )
    async for chunk in _stream_client.iter_download(
        message.media, offset=max(0, int(offset)), limit=remaining,
        request_size=request_size,
    ):
        if not chunk:
            break
        chunks.append(bytes(chunk))
        remaining -= len(chunk)
        if remaining <= 0:
            break
    return b"".join(chunks)


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
