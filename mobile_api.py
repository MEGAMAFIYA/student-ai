"""REST API used only by the native Student AI Android application."""
from __future__ import annotations

import base64
import io
import ipaddress
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from urllib.parse import urlparse, parse_qs, quote

import config
import mobile_auth
import storage
import wallet
from ai_clients import ask_ai, ask_gemini_multimodal
from handlers import quiz as quiz_handler
from handlers import course_work, essay, pptx_gen
from handlers import managed_tests
from pdf_tools import make_pdf, extract_pdf_text, images_to_pdf

logger = logging.getLogger(__name__)

_QUIZ_SESSIONS: dict[int, dict] = {}
_APPLICATION = None

# Per-process abuse protection. For multiple workers/instances, use a shared
# gateway or Redis-backed limiter as well.
_RATE_LIMITS: dict[tuple[str, str], deque] = {}
_RATE_LOCK = __import__("threading").Lock()
_RATE_WINDOW_SECONDS = 60
_RATE_LIMITS_PER_WINDOW = {"GET": 90, "POST": 30}


def _client_ip(handler) -> str:
    """Haqiqiy mijoz IP'si.

    Render kabi hostinglarda bot reverse-proxy ortida ishlaydi: to'g'ridan-to'g'ri
    peer (client_address) HAR DOIM proksining ichki IP'si bo'ladi, shuning uchun
    barcha foydalanuvchilar BITTA rate-limit chelagiga tushib, login/poll 429
    qaytarib qolardi. Peer ichki (private/loopback) bo'lgandagina, proksi
    qo'shgan X-Forwarded-For'ning ENG O'NG (ishonchli) yozuvi olinadi — chap
    tomondagi qiymatlarni mijoz soxtalashtirishi mumkin.
    """
    try:
        peer = str(handler.client_address[0])
    except (AttributeError, IndexError, TypeError):
        return "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not (peer_ip.is_private or peer_ip.is_loopback):
        return peer
    headers = getattr(handler, "headers", None)
    forwarded = (headers.get("X-Forwarded-For", "") if headers else "") or ""
    for part in reversed([p.strip() for p in forwarded.split(",") if p.strip()]):
        try:
            ip = ipaddress.ip_address(part)
        except ValueError:
            continue
        if not (ip.is_private or ip.is_loopback):
            return str(ip)
    return peer


def _rate_limited(handler, method: str) -> bool:
    limit = _RATE_LIMITS_PER_WINDOW.get(method, 30)
    now = time.monotonic()
    key = (method, _client_ip(handler))
    with _RATE_LOCK:
        # Periodically prune old client keys to prevent unbounded memory growth.
        for old_key, timestamps in list(_RATE_LIMITS.items()):
            while timestamps and now - timestamps[0] >= _RATE_WINDOW_SECONDS:
                timestamps.popleft()
            if not timestamps:
                _RATE_LIMITS.pop(old_key, None)
        timestamps = _RATE_LIMITS.setdefault(key, deque())
        while timestamps and now - timestamps[0] >= _RATE_WINDOW_SECONDS:
            timestamps.popleft()
        if len(timestamps) >= limit:
            _json(handler, 429, {"error": "rate_limited", "retry_after_seconds": _RATE_WINDOW_SECONDS})
            return True
        timestamps.append(now)
        return False



_MAIN_LOOP = None


def set_application(application, loop=None) -> None:
    global _APPLICATION, _MAIN_LOOP
    _APPLICATION = application
    if loop is not None:
        _MAIN_LOOP = loop


def _run_async(coro, timeout: float = 170):
    """HTTP thread'dan coroutine'ni botning ASOSIY event loop'ida bajaradi.

    ai_clients'dagi asyncio.Lock va keshlar shu loop'ga bog'langan, shuning
    uchun har so'rovda asyncio.run (yangi loop) ishlatish xato beradi.
    """
    import asyncio
    loop = _MAIN_LOOP
    if loop is not None and loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)
    return asyncio.run(coro)

_TASKS = {
    "universal": (config.UNIVERSAL_CHAT_AI, "Siz Talaba AI universal yordamchisisiz. O'zbek tilida foydali va aniq javob bering."),
    "course_work": (config.COURSE_WORK_AI, "Kurs ishi uchun akademik, tartibli va faktlarga ehtiyotkor matn yozing."),
    "essay": (config.ESSAY_AI, "Referat yoki insho uchun ravon, akademik o'zbek tilida matn yozing."),
    "translate": (config.TRANSLATE_AI, "Professional tarjimon sifatida faqat aniq va tabiiy tarjimani qaytaring."),
    "solve": (config.SOLVE_AI, "Masalani bosqichma-bosqich yeching va yakunda aniq javobni ko'rsating."),
    "summarize": (config.SUMMARY_AI, "Berilgan matnni mazmunini yo'qotmasdan qisqa va tushunarli konspekt qiling."),
    "grammar": (config.GRAMMAR_AI, "O'zbek matnidagi imlo va grammatik xatolarni tuzating va yakuniy matnni qaytaring."),
    "citation": (config.CITATION_AI, "Berilgan manba ma'lumotidan so'ralgan uslubda bibliografik iqtibos yarating."),
    "guide": (config.GUIDE_AI, "O'qituvchi sifatida savollarga aniq, qisqa va tushunarli javoblar bering."),
    "tabrik": (config.UNIVERSAL_CHAT_AI, "Chiroyli, samimiy va tabiiy o'zbekcha tabrik yozing."),
    "pro": (config.UNIVERSAL_CHAT_AI, "Chiroyli, romantik va estetik tabriknoma matni yozing."),
}


# ── Uzoq davom etadigan vazifalar (kurs ishi, referat, taqdimot) ──────────────
# Generatsiya daqiqalar oladi, HTTP so'rov esa proksi/ilova timeout'iga uchraydi.
# Shuning uchun vazifa fonda (botning asosiy loop'ida) bajariladi, ilova esa
# /api/mobile/job?id=... orqali natijani so'rab turadi.
import threading as _threading

_LONG_FEATURES = {"course_work", "essay", "pptx"}
_JOBS: dict = {}
_JOBS_LOCK = _threading.Lock()
_JOB_TTL = 60 * 60


def _jobs_cleanup(now: float) -> None:
    for jid in [k for k, v in _JOBS.items() if now - v["created"] > _JOB_TTL]:
        _JOBS.pop(jid, None)


def _start_job(user: dict, body: dict) -> dict:
    import asyncio
    now = time.time()
    with _JOBS_LOCK:
        _jobs_cleanup(now)
        for jid, j in _JOBS.items():
            if j["user_id"] == user["id"] and j["status"] == "pending":
                return {"job_id": jid, "status": "pending", "reply": "⏳ Oldingi vazifangiz hali tayyorlanmoqda..."}
        job_id = uuid.uuid4().hex
        _JOBS[job_id] = {"user_id": user["id"], "status": "pending", "created": now, "result": None}

    async def _runner():
        try:
            result = await asyncio.wait_for(_task(user, body), timeout=25 * 60)
            status = "done"
        except asyncio.TimeoutError:
            result, status = {"reply": "❌ Vaqt tugadi. Kichikroq hajm bilan qayta urinib ko'ring."}, "error"
        except Exception as e:
            logger.exception("mobile job xatosi")
            result, status = {"reply": f"❌ Xatolik: {type(e).__name__}"}, "error"
        with _JOBS_LOCK:
            if job_id in _JOBS:
                _JOBS[job_id].update(status=status, result=result)

    loop = _MAIN_LOOP
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(_runner(), loop)
    else:
        _threading.Thread(target=lambda: asyncio.run(_runner()), daemon=True).start()
    return {"job_id": job_id, "status": "pending", "reply": "⏳ Tayyorlanmoqda..."}


def _job_status(user: dict, job_id: str) -> tuple[int, dict]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job or job["user_id"] != user["id"]:
            return 404, {"error": "job_not_found", "status": "expired"}
        if job["status"] == "pending":
            return 200, {"status": "pending"}
        result = dict(job["result"] or {})
    result["status"] = job["status"]
    return 200, result


def _json(handler, status: int, payload: dict):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?$")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "10.0.2.2")


def _public_base_url(handler) -> str:
    """Return a validated public base URL.

    Production URLs must come from explicit configuration or Render's trusted
    environment variable. Request Host/X-Forwarded-* headers are not trusted
    to build links because clients can spoof them.
    """
    for raw in (getattr(config, "PUBLIC_BASE_URL", ""), os.getenv("RENDER_EXTERNAL_URL", "")):
        raw = (raw or "").strip().rstrip("/")
        if not raw:
            continue
        if not re.match(r"^https?://", raw, re.I):
            raw = "https://" + raw
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            logger.warning("PUBLIC_BASE_URL noto'g'ri formatda; URL qaytarilmadi")
            continue
        if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
            logger.warning("Ochiq PUBLIC_BASE_URL uchun HTTPS talab qilinadi")
            continue
        return f"{parsed.scheme}://{parsed.netloc}"

    # Allow only explicit local development hosts; never derive a public URL
    # from an arbitrary Host or X-Forwarded-* header.
    headers = getattr(handler, "headers", None)
    host = (headers.get("Host", "") if headers else "").split(",")[0].strip()
    if host and _HOST_RE.fullmatch(host) and host.split(":")[0] in _LOCAL_HOSTS:
        return f"http://{host}"
    return ""


def _read_json(handler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_content_length") from exc
    if length <= 0:
        raise ValueError("empty_request_body")
    if length > 15 * 1024 * 1024:
        raise ValueError("request_body_too_large")
    raw = handler.rfile.read(length)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_json") from exc
    if not isinstance(body, dict):
        raise ValueError("json_object_required")
    return body


def _auth(handler) -> dict | None:
    header = handler.headers.get("Authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    user = mobile_auth.get_user(token)
    if not user:
        _json(handler, 401, {"error": "unauthorized"})
        return None
    return user


def _flatten_sections(sections: dict) -> str:
    out = [sections.get("kirish", "")]
    for bob in sections.get("bobs", []):
        out.append(bob.get("content", ""))
    out.extend([sections.get("xulosa", ""), sections.get("adabiyotlar", "")])
    return "\n\n".join(x for x in out if x)


def _flatten_essay(sections: dict) -> str:
    out = [sections.get("kirish", "")]
    for p in sections.get("asosiy_qism", []):
        out.append(f"{p.get('title','')}\n{p.get('content','')}")
    out.extend([sections.get("xulosa", ""), sections.get("adabiyotlar", "")])
    return "\n\n".join(x for x in out if x)


async def _task(user: dict, body: dict) -> dict:
    feature = str(body.get("feature", "universal"))
    text = str(body.get("text") or body.get("message") or "").strip()
    history = []
    for h in (body.get("history") or [])[-20:]:
        if isinstance(h, dict) and str(h.get("content") or "").strip():
            role = "assistant" if h.get("role") in ("assistant", "model", "bot") else "user"
            history.append({"role": role, "content": str(h["content"])})
    if feature == "universal" and not text:
        return {"reply": "❌ Xabar bo'sh."}
    if feature == "my":
        return {"reply": f"👤 {user.get('first_name') or 'Foydalanuvchi'}\n🆔 {user['id']}\n💰 Balans: {wallet.get_balance(user['id'])}"}
    if feature == "wallet_balance":
        return {"reply": f"💰 Balansingiz: {wallet.get_balance(user['id'])}"}
    if feature == "wallet_history":
        rows = wallet.list_payments(user_id=user["id"])[-20:]
        if not rows:
            return {"reply": "🧾 To'lovlar tarixi bo'sh."}
        return {"reply": "\n".join(f"• {r.get('amount',0)} — {r.get('status','')} — {r.get('created_at','')}" for r in rows)}
    if feature == "myfiles":
        files = storage.get_user_files(user["id"])
        if not files:
            return {"reply": "🗂 Hozircha saqlangan fayllar yo'q."}
        return {"reply": "\n".join(f"• {f.get('title','')} ({f.get('type','')})" for f in files[:30])}
    if feature == "remind":
        import datetime as _dt
        try:
            when, reminder_text = [x.strip() for x in text.split("|", 1)]
            due = _dt.datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=_dt.timezone.utc).timestamp()
            if due <= time.time():
                return {"reply": "❌ Vaqt kelajakdagi sana/vaqt bo'lishi kerak."}
            reminder = storage.add_reminder(user["id"], user["id"], reminder_text, due)
            if _APPLICATION is not None:
                try:
                    from handlers.reminders import schedule_reminder
                    schedule_reminder(_APPLICATION, reminder)
                except Exception:
                    logger.exception("Mobile reminder scheduling failed")
            return {"reply": f"✅ Eslatma saqlandi: {when}\n📝 {reminder_text}"}
        except Exception:
            return {"reply": "❗ Format: YYYY-MM-DD HH:MM | eslatma matni"}
    if feature == "qoshiq":
        from video_tools import search_tracks
        results = await __import__('asyncio').to_thread(search_tracks, text, config.QOSHIQ_SEARCH_COUNT)
        return {"reply": "\n".join(f"{i+1}. {r.get('title','')} — {r.get('url','')}" for i, r in enumerate(results[:20])) or "Natija topilmadi."}
    if feature == "vid":
        from video_tools import download_video, DownloadError
        import tempfile, os, shutil
        dest = tempfile.mkdtemp(prefix="mobile_vid_")
        try:
            path = await __import__('asyncio').to_thread(download_video, text, dest, config.VID_MAX_MB, config.VID_DOWNLOAD_TIMEOUT_SEC)
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            return {"reply": "✅ Video tayyor.", "file_base64": data, "filename": os.path.basename(path), "mime": "video/mp4"}
        except DownloadError as e:
            return {"reply": str(e)}
        finally:
            shutil.rmtree(dest, ignore_errors=True)

    if feature == "pptx":
        topic = text or "Talaba AI"
        count = max(3, min(int(body.get("count", 8) or 8), 20))
        slides = await pptx_gen._generate_slides(topic, count)
        if not slides:
            return {"reply": "❌ Taqdimot yaratilmadi."}
        from pptx_tools import build_presentation
        buf = await __import__('asyncio').to_thread(build_presentation, topic, slides, "Talaba AI")
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        storage.record_usage("pptx", user["id"])
        return {"reply": f"📊 {len(slides)} slayd tayyor.", "file_base64": data, "filename": f"{topic[:40]}.pptx", "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}

    if feature == "course_work":
        topic = text or "Sun'iy intellekt"
        pages = max(1, min(int(body.get("pages") or 3), 15))
        result = await course_work.generate_course_work(topic, pages)
        if not result:
            return {"reply": "❌ Kurs ishini yaratib bo'lmadi."}
        sections, pdf_buf, actual_pages = result
        storage.record_usage("course_work", user["id"])
        data = base64.b64encode(pdf_buf.getvalue()).decode("ascii")
        return {"reply": f"📘 Kurs ishi tayyor — {actual_pages} bet.", "file_base64": data, "filename": f"kurs_ishi_{topic[:30]}.pdf", "mime": "application/pdf", "text": _flatten_sections(sections)}

    if feature == "essay":
        topic = text or "Ta'lim"
        pages = max(1, min(int(body.get("pages") or 3), 15))
        work_type = body.get("work_type", "referat")
        result = await essay.generate_essay(topic, pages, work_type)
        if not result:
            return {"reply": "❌ Yaratib bo'lmadi. Qayta urinib ko'ring."}
        sections, pdf_buf, actual_pages = result
        storage.record_usage("essay", user["id"])
        data = base64.b64encode(pdf_buf.getvalue()).decode("ascii")
        return {"reply": f"🗒 {work_type.title()} tayyor — {actual_pages} bet.", "file_base64": data, "filename": f"{work_type}_{topic[:30]}.pdf", "mime": "application/pdf", "text": _flatten_essay(sections)}

    if feature == "rasim":
        base = (config.PUBLIC_BASE_URL or "").rstrip("/")
        return {"reply": f"🎨 Rasm chizish oynasini oching:\n{base}/miniapp/rasim/" if base else "🎨 Rasm chizish uchun botdagi /rasim funksiyasidan foydalaning."}

    cfg_system = _TASKS.get(feature)
    if not cfg_system:
        return {"reply": "❌ Noma'lum funksiya."}
    cfg, system = cfg_system
    prompt = text
    if feature == "translate":
        target = body.get("target_lang", "Inglizcha")
        prompt = f"Matnni {target} tiliga tarjima qiling:\n\n{text}"
    elif feature == "citation":
        style = body.get("style", "GOST")
        prompt = f"Uslub: {style}\nManba ma'lumotlari:\n{text}"
    elif feature == "guide":
        prompt = "Quyidagi savollarga alohida, aniq javob bering:\n" + text
    result = await ask_ai(cfg, prompt, system, history=history)
    if not result:
        return {"reply": "❌ AI hozir javob bermadi. Birozdan so'ng qayta urinib ko'ring."}
    storage.record_usage(feature if feature in storage._FUNCTION_LABELS_FOR_STATS else "universal_chat", user["id"])
    return {"reply": result}


def _send_telegram_text(chat_id: int, text: str) -> tuple[str, str]:
    """Bot API'ga to'g'ridan-to'g'ri (sinxron) sendMessage yuboradi.

    Eski kod HTTP thread ichida asyncio.run(application.bot.send_message(...))
    chaqirardi: PTB Bot'ining httpx klienti asosiy event loop'ga bog'langan,
    yangi loop'dan chaqirilganda "Event loop is closed" xatosi chiqib, kod HECH
    QACHON yetib bormasdi. Bu yerda esa event loop'ga bog'liq bo'lmagan oddiy
    HTTPS so'rov ishlatiladi.

    Qaytaradi: ("ok"|"blocked"|"error", tafsilot). "blocked" — foydalanuvchi
    botni hali ishga tushirmagan/bloklagan yoki ID noto'g'ri.
    """
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": int(chat_id), "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        return ("ok", "") if data.get("ok") else ("error", str(data)[:200])
    except urllib.error.HTTPError as exc:
        try:
            desc = json.loads(exc.read().decode("utf-8")).get("description", "")
        except Exception:
            desc = ""
        if exc.code in (400, 403):
            return "blocked", desc or str(exc.code)
        return "error", f"HTTP {exc.code}: {desc}"
    except Exception as exc:  # tarmoq/timeout
        return "error", f"{type(exc).__name__}: {exc}"


def handle_get(handler) -> bool:
    path = urlparse(handler.path).path
    if not path.startswith("/api/mobile/"):
        return False
    if _rate_limited(handler, "GET"):
        return True
    if path == "/api/mobile/auth/poll":
        token = parse_qs(urlparse(handler.path).query).get("token", [""])[0]
        _json(handler, 200, mobile_auth.poll(token))
        return True
    user = _auth(handler)
    if user is None:
        return True
    if path == "/api/mobile/job":
        jid = parse_qs(urlparse(handler.path).query).get("id", [""])[0]
        code, payload = _job_status(user, jid)
        _json(handler, code, payload)
        return True
    if path == "/api/mobile/my/files":
        _json(handler, 200, {"files": storage.get_user_files(user["id"])})
        return True
    if path == "/api/mobile/my/reminders":
        _json(handler, 200, {"reminders": storage.get_user_reminders(user["id"])})
        return True
    if path == "/api/mobile/kino/list":
        q = parse_qs(urlparse(handler.path).query).get("q", [""])[0].strip().lower()
        movies = storage.search_movies(q)[:50]
        _json(handler, 200, {"movies": [{"id": str(m["id"]), "title": m["title"], "size": int(m.get("size") or 0)} for m in movies]})
        return True
    if path.startswith("/api/mobile/kino/watch/"):
        movie_id = path.rsplit("/", 1)[-1]
        movie = storage.get_movie(movie_id)
        if not movie:
            _json(handler, 404, {"error": "movie_not_found"}); return True
        import movie_watch
        rid = movie_watch.find_or_create_room(movie_id, user["id"])
        if not rid:
            _json(handler, 404, {"error": "room_create_failed"}); return True
        if int(movie.get("size") or 0) <= 0:
            _json(handler, 422, {"error": "movie_size_unknown"}); return True
        base = _public_base_url(handler)
        if not base:
            # 200 + nisbiy URL qaytarish o'rniga aniq xato: ilova sababni ko'rsata oladi.
            _json(handler, 503, {"error": "server_url_not_configured"}); return True
        token = movie_watch._make_stream_token(rid, movie_id, user["id"])
        stream = f"{base}/api/kino/stream/{quote(str(rid), safe='')}/{quote(str(movie_id), safe='')}?token={quote(token, safe='')}"
        _json(handler, 200, {"stream_url": stream})
        return True
    return False


def handle_post(handler) -> bool:
    path = urlparse(handler.path).path
    if not path.startswith("/api/mobile/"):
        return False
    if _rate_limited(handler, "POST"):
        return True
    if path == "/api/mobile/auth/logout":
        header = handler.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        revoked = mobile_auth.revoke_session(token)
        _json(handler, 200, {"revoked": bool(revoked)})
        return True
    if path == "/api/mobile/auth/start":
        start = mobile_auth.start_login()
        _json(handler, 200, {"login_token": start.login_token, "deep_link": start.deep_link, "expires_in": start.expires_in})
        return True
    if path == "/api/mobile/auth/code/request":
        try:
            body = _read_json(handler)
            telegram_id = int(str(body.get("telegram_id", "")).strip())
        except (ValueError, TypeError):
            _json(handler, 400, {"error": "invalid_telegram_id", "message": "Telegram ID raqamini to‘g‘ri kiriting."})
            return True
        if telegram_id <= 0 or telegram_id > 10**13:
            _json(handler, 400, {"error": "invalid_telegram_id", "message": "Telegram ID raqamini to‘g‘ri kiriting."})
            return True
        if not config.TELEGRAM_TOKEN:
            _json(handler, 503, {"error": "telegram_bot_unavailable", "message": "Bot sozlanmagan. Keyinroq urinib ko‘ring."})
            return True
        try:
            code = mobile_auth.create_code_challenge(telegram_id)
        except mobile_auth.CodeCooldown as exc:
            _json(handler, 429, {
                "error": "code_cooldown",
                "retry_after_seconds": exc.retry_after,
                "message": f"Kod yaqinda yuborilgan. {exc.retry_after} soniyadan so‘ng qayta urinib ko‘ring.",
            })
            return True
        except Exception:
            logger.exception("Mobile login code creation failed")
            _json(handler, 500, {"error": "code_request_failed", "message": "Server xatosi. Qayta urinib ko‘ring."})
            return True
        status, detail = _send_telegram_text(
            telegram_id,
            f"🔐 Student AI ilovasiga kirish kodi: {code}\n\n"
            "Kod 5 daqiqa amal qiladi. Agar bu so‘rov sizniki bo‘lmasa, e’tiborsiz qoldiring.",
        )
        if status == "ok":
            _json(handler, 200, {"sent": True, "expires_in": 300})
        else:
            # Kod yetib bormadi — qayta urinish cooldown'ga tushmasligi uchun bekor qilamiz.
            mobile_auth.discard_code_challenge(telegram_id)
            if status == "blocked":
                _json(handler, 400, {
                    "error": "cannot_message_user",
                    "message": "Bot sizga yoza olmadi. Avval Telegramda botni oching va /start bosing, ID ni tekshiring.",
                })
            else:
                logger.warning("Mobile login code delivery failed for %s: %s", telegram_id, detail)
                _json(handler, 502, {"error": "telegram_unreachable", "message": "Telegram bilan bog‘lanib bo‘lmadi. Birozdan so‘ng qayta urinib ko‘ring."})
        return True
    if path == "/api/mobile/auth/code/verify":
        try:
            body = _read_json(handler)
            telegram_id = int(str(body.get("telegram_id", "")).strip())
        except (ValueError, TypeError):
            _json(handler, 400, {"error": "invalid_request"})
            return True
        try:
            profile = {}
            try:
                profile = storage.get_user_profile(telegram_id)
            except Exception:
                logger.debug("Profil o'qilmadi", exc_info=True)
            result = mobile_auth.verify_code(telegram_id, str(body.get("code", "")), profile=profile)
        except Exception:
            logger.exception("Mobile login code verification failed")
            _json(handler, 500, {"error": "verify_failed"})
            return True
        if result is None:
            _json(handler, 401, {"error": "invalid_or_expired_code"})
        else:
            _json(handler, 200, result)
        return True
    user = _auth(handler)
    if user is None:
        return True
    try:
        body = _read_json(handler)
        if path == "/api/mobile/chat":
            body["feature"] = "universal"
        if path == "/api/mobile/task" and str(body.get("feature", "")) in _LONG_FEATURES:
            _json(handler, 200, _start_job(user, body))
            return True
        if path in ("/api/mobile/task", "/api/mobile/chat"):
            _json(handler, 200, _run_async(_task(user, body)))
            return True
        if path == "/api/mobile/file-task":
            _json(handler, 200, _run_async(_file_task(user, body)))
            return True
        if path == "/api/mobile/quiz/start":
            return _quiz_start(handler, user, body)
        if path == "/api/mobile/quiz/answer":
            return _quiz_answer(handler, user, body)
    except ValueError as e:
        _json(handler, 400, {"error": str(e) or "invalid_request"})
        return True
    except Exception as e:
        logger.exception("mobile API xatosi")
        _json(handler, 500, {"error": f"server_error: {type(e).__name__}"})
        return True
    return False


def _quiz_start(handler, user: dict, body: dict):
    import asyncio
    count = max(1, min(int(body.get("n") or 10), 30))
    # Avval developer orqali qo'shilgan faol testlar ishlatiladi.
    active = managed_tests.get_questions(active_only=True)
    questions = []
    if active:
        rows = active[:count]
        for _, topic, q, opts, correct in rows:
            options = list(opts)
            if body.get("jas"):
                options.reverse()
                correct = len(options) - 1 - correct
            questions.append({"topic": topic, "savol": q, "variantlar": options, "togri": correct, "pro": bool(body.get("pro"))})
    else:
        topic = str(body.get("topic") or "Umumiy bilimlar")
        generated = asyncio.run(quiz_handler._generate_questions(topic, count)) or []
        for q in generated:
            options = list(q["variantlar"])
            correct = q["togri"]
            if body.get("jas"):
                options.reverse(); correct = len(options) - 1 - correct
            questions.append({"topic": topic, **q, "variantlar": options, "togri": correct, "pro": bool(body.get("pro"))})
    if not questions:
        _json(handler, 200, {"finished": True, "question": None, "error": "active_tests_empty"})
        return True
    uid = user["id"]
    _QUIZ_SESSIONS[uid] = {"questions": questions, "idx": 0, "answers": []}
    q = questions[0]
    _json(handler, 200, {"finished": False, "question": _quiz_dto(q, 0, len(questions))})
    return True


def _quiz_dto(q, idx, total):
    return {"index": idx, "total": total, "topic": q.get("topic", ""), "question": q["savol"], "options": None if q.get("pro") else q["variantlar"]}


def _quiz_answer(handler, user: dict, body: dict):
    sess = _QUIZ_SESSIONS.get(user["id"])
    if not sess:
        _json(handler, 400, {"error": "quiz_session_missing"}); return True
    idx = sess["idx"]
    q = sess["questions"][idx]
    if body.get("text") is not None:
        answer_text = str(body.get("text"))
        correct = answer_text.strip().lower() == str(q["variantlar"][q["togri"]]).strip().lower()
    else:
        chosen = int(body.get("index", -1))
        correct = chosen == q["togri"]
    sess["answers"].append(1 if correct else 0)
    sess["idx"] += 1
    finished = sess["idx"] >= len(sess["questions"])
    next_q = None if finished else _quiz_dto(sess["questions"][sess["idx"]], sess["idx"], len(sess["questions"]))
    if finished:
        total = len(sess["questions"]); ok = sum(sess["answers"]); bad = total - ok
        _QUIZ_SESSIONS.pop(user["id"], None)
        _json(handler, 200, {"correct": correct, "correct_answer": q["variantlar"][q["togri"]], "finished": True, "next": None, "stats": {"total": total, "ok": ok, "bad": bad}})
    else:
        _json(handler, 200, {"correct": correct, "correct_answer": q["variantlar"][q["togri"]], "finished": False, "next": next_q, "stats": None})
    return True


async def _file_task(user: dict, body: dict) -> dict:
    feature = str(body.get("feature", ""))
    files = body.get("files") or []
    decoded = []
    for item in files:
        try:
            decoded.append((str(item.get("name", "file")), str(item.get("mime", "application/octet-stream")), base64.b64decode(item.get("base64", ""))))
        except Exception:
            continue
    if feature == "images_pdf":
        if not decoded:
            return {"reply": "❗ Rasm tanlang."}
        buf = await __import__('asyncio').to_thread(images_to_pdf, [b for _, _, b in decoded])
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        storage.record_usage("images_pdf", user["id"])
        return {"reply": f"📄 {len(decoded)} ta rasmdan PDF tayyor.", "file_base64": data, "filename": "suratlar.pdf", "mime": "application/pdf"}
    if feature == "edit_pdf":
        if not decoded:
            return {"reply": "❗ PDF tanlang."}
        name, _, raw = decoded[0]
        text = await __import__('asyncio').to_thread(extract_pdf_text, raw)
        if not text:
            return {"reply": "❌ PDF dan matn o'qilmadi."}
        instruction = str(body.get("instruction", "")).strip()
        if not instruction:
            return {"reply": "❗ Tahrirlash ko'rsatmasini yozing."}
        edited = await ask_ai(config.EDIT_PDF_AI, f"Hujjat:\n{text}\n\nKo'rsatma:\n{instruction}", "Hujjatni ko'rsatmaga muvofiq tahrirlang va to'liq yakuniy matnni qaytaring.")
        if not edited:
            return {"reply": "❌ PDF tahrirlanmadi."}
        buf = await __import__('asyncio').to_thread(make_pdf, name.rsplit('.',1)[0].title(), edited)
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        storage.record_usage("edit_pdf", user["id"])
        return {"reply": "📝 PDF tahrirlandi.", "file_base64": data, "filename": name.rsplit('.',1)[0] + "_tahrirlangan.pdf", "mime": "application/pdf"}
    if feature in ("translate", "summarize") and decoded:
        _, _, raw = decoded[0]
        text = await __import__('asyncio').to_thread(extract_pdf_text, raw)
        body["text"] = text
        return await _task(user, body)
    if feature == "solve" and decoded:
        _, mime, raw = decoded[0]
        result, status, detail = await ask_gemini_multimodal(config.SOLVE_AI, "Rasmdagi masalani bosqichma-bosqich o'zbek tilida yeching.", raw, mime, label="Mobile solve")
        return {"reply": result or f"❌ Rasmli masalani yechib bo'lmadi: {detail}"}
    return {"reply": "❌ Bu fayl turi uchun mobil amal topilmadi."}
