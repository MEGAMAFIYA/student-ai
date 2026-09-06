"""
🎨 1v1 RASM CHIZISH O'YINI

Ikki foydalanuvchi bir xil topshiriqni oladi, alohida rasm chizadi va yuboradi.
Ikkala rasm kelmaguncha Vision AI umuman chaqirilmaydi. Ikkinchi rasm kelgach,
AI faqat baholaydi va natija qaytariladi.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import random
import re
import threading
import time
import uuid
from typing import Any

from PIL import Image

import config
import webapp_security
from ai_clients import ask_gemini_images

logger = logging.getLogger(__name__)

LOCK = threading.RLock()
ROOMS: dict[str, dict[str, Any]] = {}
ROOM_TTL = 60 * 60
MAX_ROOMS = 500

# Telegram tasdiqlagan haqiqiy bot username. /post_init() da get_me()
# orqali o'rnatiladi; env fallback faqat bot API vaqtincha ishlamasa qoladi.
_RUNTIME_BOT_USERNAME = ""

def set_bot_username(username: str | None) -> None:
    """Bot ishga tushganda Telegram bergan haqiqiy username'ni saqlaydi."""
    global _RUNTIME_BOT_USERNAME
    value = str(username or "").strip().lstrip("@")
    if value:
        _RUNTIME_BOT_USERNAME = value

PROMPTS = [
    "🐄 Sigir", "🏠 Uy", "👤 Odam", "🐱 Mushuk", "🐶 It", "🐰 Quyon",
    "🦁 Sher", "🐘 Fil", "🐢 Toshbaqa", "🐟 Baliq", "🦋 Kapalak", "🐝 Ari",
    "🐔 Xo'roz", "🐴 Ot", "🦒 Jirafa", "🐧 Pingvin", "🦉 Boyqush",
    "🐍 Ilon", "🐸 Qurbaqa", "🚗 Mashina", "✈️ Samolyot", "🌳 Daraxt",
    "🌸 Gul", "☀️ Quyosh", "🌙 Oy", "⭐ Yulduz", "☂️ Soyabon",
    "📚 Kitob", "⌚ Soat", "🎈 Shar", "🎂 Tort", "🍎 Olma", "🍉 Tarvuz",
    "⚽ Futbol to'pi", "🚲 Velosiped", "⛰️ Tog'", "🌈 Kamalak",
    "🎁 Sovg'a", "🎸 Gitara", "🚀 Raketa",
]

def _purge() -> None:
    now = time.time()
    for rid, room in list(ROOMS.items()):
        if now - room["updated_at"] > ROOM_TTL:
            ROOMS.pop(rid, None)

def _new_prompt(previous: str = "") -> str:
    choices = [p for p in PROMPTS if p != previous] or PROMPTS
    return random.choice(choices)

def create_room(creator_id: int) -> str:
    with LOCK:
        _purge()
        rid = uuid.uuid4().hex[:24]
        prompt = _new_prompt()
        ROOMS[rid] = {
            "id": rid,
            "created_at": time.time(),
            "updated_at": time.time(),
            "prompt": prompt,
            "round": 1,
            "players": {},
            "submissions": {},
            "restart_votes": set(),
            "status": "waiting",
            "result": None,
        }
        ROOMS[rid]["players"][str(int(creator_id))] = {
            "name": str(creator_id),
            "joined_at": time.time(),
        }
        if len(ROOMS) > MAX_ROOMS:
            old = sorted(ROOMS, key=lambda x: ROOMS[x]["updated_at"])[:len(ROOMS)-MAX_ROOMS]
            for x in old:
                ROOMS.pop(x, None)
        return rid

def room_url(rid: str) -> str:
    """1v1 xona uchun Telegram Direct Mini App havolasini yaratadi.

    Inline xabardagi URL tugmasi aynan shu formatdan foydalanadi:
    https://t.me/<bot_username>/<short_name>?startapp=draw_<room>

    Muhim: username Telegram API'dan olingan haqiqiy qiymat bo'lsa, env'dagi
    eski/stale BOT_USERNAME sababli tugma bot profiliga tushib qolmaydi.
    """
    room_id = str(rid or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", room_id):
        raise ValueError("Noto'g'ri rasm xonasi ID.")

    username = (_RUNTIME_BOT_USERNAME or config.BOT_USERNAME_FALLBACK).strip().lstrip("@")
    short_name = config.DRAWING_APP_SHORT_NAME.strip().strip("/") or "rasim"

    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", username):
        raise ValueError("BOT_USERNAME noto'g'ri sozlangan.")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", short_name):
        raise ValueError("DRAWING_APP_SHORT_NAME noto'g'ri sozlangan.")

    # Direct Mini App deep-link. startapp qiymatini URL-encode qilish shart
    # emas (rid faqat xavfsiz belgilar), lekin formatni Telegram talabi bilan
    # aniq saqlaymiz.
    return f"https://t.me/{username}/{short_name}?startapp=draw_{room_id}&mode=fullscreen"

def _verify(init_data: str) -> dict | None:
    return webapp_security.verify_telegram_init_data(init_data, config.TELEGRAM_TOKEN)

def _room(rid: str) -> dict | None:
    _purge()
    return ROOMS.get(rid)

def join(rid: str, init_data: str):
    user = _verify(init_data)
    if not user:
        return None, "Mini App sessiyasi tasdiqlanmadi."
    uid = str(int(user["id"]))
    with LOCK:
        room = _room(rid)
        if not room:
            return None, "Rasm chizish xonasi topilmadi yoki muddati o'tgan."
        if uid not in room["players"] and len(room["players"]) >= 2:
            return None, "Bu xona to'la. Faqat siz va do'stingiz qatnashadi."
        room["players"].setdefault(uid, {
            "name": user.get("first_name") or user.get("username") or uid,
            "joined_at": time.time(),
        })
        room["players"][uid]["name"] = user.get("first_name") or user.get("username") or room["players"][uid]["name"]
        room["updated_at"] = time.time()
        if len(room["players"]) == 2 and room["status"] == "waiting":
            room["status"] = "drawing"
        return public_state(room, uid), None

def public_state(room: dict, uid: str) -> dict:
    players = []
    for pid, p in room["players"].items():
        players.append({
            "id": int(pid),
            "name": p.get("name") or pid,
            "submitted": pid in room["submissions"],
        })
    mine = room["submissions"].get(uid)
    return {
        "id": room["id"],
        "prompt": room["prompt"],
        "round": room["round"],
        "status": room["status"],
        "players": players,
        "me": int(uid),
        "submitted": bool(mine),
        "both_submitted": len(room["submissions"]) == 2,
        "result": room.get("result"),
    }

def _normalize_image(image_bytes: bytes) -> tuple[bytes, str]:
    if len(image_bytes) > 5 * 1024 * 1024:
        raise ValueError("Rasm 5 MB dan katta.")
    with Image.open(io.BytesIO(image_bytes)) as img:
        if img.width < 160 or img.height < 160:
            raise ValueError("Rasm o'lchami juda kichik.")
        if img.width > 2048 or img.height > 2048:
            img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        # AI uchun PNG saqlaymiz, Telegramga yuborish uchun JPEG alohida olinadi.
        rgb = Image.new("RGB", img.size, "white")
        if img.mode in ("RGBA", "LA"):
            rgb.paste(img.convert("RGBA"), mask=img.convert("RGBA").getchannel("A"))
        else:
            rgb.paste(img.convert("RGB"))
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=92, optimize=True)
        return out.getvalue(), "image/jpeg"

def _score_result(raw: str, prompt: str) -> dict:
    # Modeldan JSON talab qilamiz, ammo noto'g'ri JSON bo'lsa ham foydalanuvchiga
    # hech qachon modelning xom javobini chiqarib yubormaymiz.
    raw = (raw or "").strip()
    obj = None
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            obj = json.loads(m.group(0))
    except Exception:
        obj = None
    if not isinstance(obj, dict):
        return {
            "player1": None, "player2": None, "winner": None,
            "comment": "AI baholash formati noto'g'ri qaytdi. Qayta boshlang."
        }
    def pct(v):
        try:
            return max(0, min(100, int(round(float(v)))))
        except Exception:
            return None
    p1, p2 = pct(obj.get("player1")), pct(obj.get("player2"))
    winner = obj.get("winner")
    if winner not in ("player1", "player2", "draw"):
        winner = "draw" if p1 is not None and p2 is not None and p1 == p2 else ("player1" if (p1 or 0) > (p2 or 0) else "player2")
    comment = str(obj.get("comment") or "Rasmlar topshiriqqa o'xshashligi bo'yicha baholandi.")[:500]
    return {"player1": p1, "player2": p2, "winner": winner, "comment": comment}

async def _evaluate(prompt: str, img1: bytes, img2: bytes) -> dict:
    cfg = config.VISION_AI
    instruction = f"""
Siz faqat 1v1 rasm chizish o'yinining hakamisiz. Boshqa hech qanday vazifani bajarmang.
Topshiriq: {prompt}

Ikki rasmni MUSTAQIL baholang. Chiroy, rassomlik mahorati, ranglarning qimmatligi yoki
kim chiroyli chizganiga emas, aynan topshiriqda so'ralgan obyektga SEMANTIK O'XSHASHLIKKA
ball bering.

100 ballik mezon:
- 70 ball: topshiriqdagi asosiy obyektni to'g'ri tasvirlagani
- 20 ball: obyektning muhim ajratuvchi belgilarini ko'rsatgani
- 10 ball: rasm topshiriqqa aniq va tushunarli mosligi

Ikkala rasmni bir xil mezon bilan baholang. Narsani juda oddiy chizish ham obyekt aniq bo'lsa
yuqori ball olishi mumkin. Qo'shimcha bezaklar asosiy obyektni almashtirmasa jarima bermang.

FAQAT quyidagi JSONni qaytaring:
{{"player1": 0-100, "player2": 0-100, "winner": "player1"|"player2"|"draw",
"comment": "o'zbekcha 1-2 jumla"}}
"""
    try:
        raw = await ask_gemini_images(cfg, instruction, img1, img2, "image/jpeg")
    except Exception as e:
        logger.error("🎨 Drawing AI xato: %s", e, exc_info=True)
        raw = None
    return _score_result(raw or "", prompt)

def _telegram_caption(result: dict, room: dict) -> str:
    p1, p2 = result.get("player1"), result.get("player2")
    winner = result.get("winner")
    names = list(room["players"].values())
    n1 = names[0].get("name", "1-o'yinchi") if len(names) > 0 else "1-o'yinchi"
    n2 = names[1].get("name", "2-o'yinchi") if len(names) > 1 else "2-o'yinchi"
    if p1 is None or p2 is None:
        return "🎨 AI baholashi muvaffaqiyatsiz bo'ldi. «Qayta boshlash» bilan yangi raund boshlang."
    winner_text = "🤝 Durang" if winner == "draw" else f"🏆 G'olib: {n1 if winner == 'player1' else n2}"
    return (
        f"🎯 Topshiriq: {room['prompt']}\n\n"
        f"📊 {n1}: {p1}%\n"
        f"📊 {n2}: {p2}%\n"
        f"{winner_text}\n\n"
        f"🧠 {result.get('comment','')}"
    )

def submit(rid: str, init_data: str, image_bytes: bytes):
    user = _verify(init_data)
    if not user:
        return None, "Sessiya tasdiqlanmadi."
    uid = str(int(user["id"]))
    try:
        jpeg_bytes, _ = _normalize_image(image_bytes)
    except Exception as e:
        return None, str(e)
    with LOCK:
        room = _room(rid)
        if not room or uid not in room["players"]:
            return None, "Siz bu xonada emassiz."
        if len(room["players"]) < 2:
            return None, "Do'stingiz ham Mini App'ga kirishini kuting."
        if room["status"] not in ("drawing",):
            return None, "Bu raund allaqachon yakunlangan. Yangi raundni boshlang."
        if uid in room["submissions"]:
            return None, "Siz rasmni allaqachon yuborgansiz. Do'stingizni kuting."
        # JPEG AI uchun yetarli va Telegram photo URL uchun mos.
        room["submissions"][uid] = {
            "image": jpeg_bytes,
            "submitted_at": time.time(),
            "name": user.get("first_name") or user.get("username") or uid,
            "query_id": user.get("_query_id"),
        }
        room["updated_at"] = time.time()
        if len(room["submissions"]) < 2:
            return {
                "state": public_state(room, uid),
                "image": jpeg_bytes,
                "caption": f"🎨 Sizning rasmingiz yuborildi.\n🎯 Topshiriq: {room['prompt']}\n⏳ Do'stingiz rasm yuborishini kutyapmiz.",
                "query_id": user.get("_query_id"),
                "room_id": rid,
            }, None

        # Ikkala rasm kelgan yagona nuqta: AI shu yerda ishga tushadi.
        ids = list(room["players"].keys())
        img1 = room["submissions"][ids[0]]["image"]
        img2 = room["submissions"][ids[1]]["image"]
        prompt = room["prompt"]
    return {
        "state": public_state(room, uid),
        "image": jpeg_bytes,
        "caption": "",
        "query_id": user.get("_query_id"),
        "room_id": rid,
        "evaluate": (prompt, img1, img2),
    }, None

def finish_evaluation(rid: str, result: dict):
    with LOCK:
        room = _room(rid)
        if not room:
            return None
        room["result"] = result
        room["status"] = "finished"
        room["updated_at"] = time.time()
        return dict(room)

def restart(rid: str, init_data: str):
    user = _verify(init_data)
    if not user:
        return None, "Sessiya tasdiqlanmadi."
    uid = str(int(user["id"]))
    with LOCK:
        room = _room(rid)
        if not room or uid not in room["players"]:
            return None, "Xona topilmadi."
        if room["status"] != "finished":
            return None, "Avval joriy raund tugasin."
        room["restart_votes"].add(uid)
        if len(room["restart_votes"]) < 2:
            room["updated_at"] = time.time()
            return public_state(room, uid), None
        previous = room["prompt"]
        room["prompt"] = _new_prompt(previous)
        room["round"] += 1
        room["submissions"] = {}
        room["restart_votes"] = set()
        room["result"] = None
        room["status"] = "drawing"
        room["updated_at"] = time.time()
        return public_state(room, uid), None

def status(rid: str, init_data: str):
    user = _verify(init_data)
    if not user:
        return None, "Sessiya tasdiqlanmadi."
    uid = str(int(user["id"]))
    with LOCK:
        room = _room(rid)
        if not room or uid not in room["players"]:
            return None, "Xona topilmadi."
        return public_state(room, uid), None
