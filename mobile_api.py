"""REST API used only by the native Student AI Android application."""
from __future__ import annotations

import base64
import io
import json
import logging
import time
import uuid
from urllib.parse import urlparse, parse_qs

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


def set_application(application) -> None:
    global _APPLICATION
    _APPLICATION = application

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


def _json(handler, status: int, payload: dict):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _read_json(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if length <= 0 or length > 15 * 1024 * 1024:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


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
    text = str(body.get("text", "")).strip()
    history = body.get("history") or []
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
        pages = max(1, min(int(body.get("pages", 3) or 3), 12))
        result = await course_work.generate_course_work(topic, pages, user_id=user["id"], source_tracker={})
        if not result:
            return {"reply": "❌ Kurs ishini yaratib bo'lmadi."}
        sections, pdf_buf, actual_pages = result
        storage.record_usage("course_work", user["id"])
        data = base64.b64encode(pdf_buf.getvalue()).decode("ascii")
        return {"reply": f"📘 Kurs ishi tayyor — {actual_pages} bet.", "file_base64": data, "filename": f"kurs_ishi_{topic[:30]}.pdf", "mime": "application/pdf", "text": _flatten_sections(sections)}

    if feature == "essay":
        topic = text or "Ta'lim"
        pages = max(1, min(int(body.get("pages", 3) or 3), 12))
        work_type = body.get("work_type", "referat")
        result = await essay.generate_essay(topic, pages, work_type)
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


def handle_get(handler) -> bool:
    path = urlparse(handler.path).path
    if not path.startswith("/api/mobile/"):
        return False
    if path == "/api/mobile/auth/poll":
        token = parse_qs(urlparse(handler.path).query).get("token", [""])[0]
        _json(handler, 200, mobile_auth.poll(token))
        return True
    user = _auth(handler)
    if user is None:
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
        token = movie_watch._make_stream_token(rid, movie_id, user["id"])
        base = (config.PUBLIC_BASE_URL or "").rstrip("/")
        stream = f"{base}/api/kino/stream/{rid}/{movie_id}?token={token}"
        _json(handler, 200, {"stream_url": stream})
        return True
    return False


def handle_post(handler) -> bool:
    path = urlparse(handler.path).path
    if not path.startswith("/api/mobile/"):
        return False
    if path == "/api/mobile/auth/start":
        start = mobile_auth.start_login()
        _json(handler, 200, {"login_token": start.login_token, "deep_link": start.deep_link, "expires_in": start.expires_in})
        return True
    user = _auth(handler)
    if user is None:
        return True
    try:
        body = _read_json(handler)
        if path == "/api/mobile/chat":
            body["feature"] = "universal"
        if path == "/api/mobile/task":
            import asyncio
            _json(handler, 200, asyncio.run(_task(user, body)))
            return True
        if path == "/api/mobile/file-task":
            import asyncio
            _json(handler, 200, asyncio.run(_file_task(user, body)))
            return True
        if path == "/api/mobile/quiz/start":
            return _quiz_start(handler, user, body)
        if path == "/api/mobile/quiz/answer":
            return _quiz_answer(handler, user, body)
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
