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
from ai_clients import ask_gemini_multimodal

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

    # Asosiy Main Mini App allaqachon BotFather'da sozlangan bo'lsa, alohida
    # Direct Mini App short_name kerak emas. Bu ayniqsa eski deploylarda
    # `t.me/bot/rasim` oddiy bot/chatga qaytib qolishi muammosini bartaraf etadi.
    if not config.DRAWING_USE_DIRECT_APP_LINK:
        return f"https://t.me/{username}?startapp=draw_{room_id}&mode=fullscreen"

    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", short_name):
        raise ValueError("DRAWING_APP_SHORT_NAME noto'g'ri sozlangan.")

    # Alohida Direct Mini App deep-link.
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
        sub = room["submissions"].get(pid)
        players.append({
            "id": int(pid),
            "name": p.get("name") or pid,
            "submitted": pid in room["submissions"],
            # Faqat o'zingizning ballingiz — do'stingiznikini u ham
            # yuborguncha (yoki raund tugaguncha) ko'rsatmaymiz.
            "score": (sub.get("score") if sub else None) if pid == uid else None,
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
        "my_score": mine.get("score") if mine else None,
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

def _score_single(raw: str) -> dict:
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
        return {"score": None, "comment": "AI baholay olmadi. Qayta boshlang."}
    def pct(v):
        try:
            return max(0, min(100, int(round(float(v)))))
        except Exception:
            return None
    return {"score": pct(obj.get("score")), "comment": str(obj.get("comment") or "")[:300]}

async def _evaluate_single(prompt: str, img: bytes) -> dict:
    """Bitta rasmni — DO'STINI kutmasdan — mustaqil baholaydi. Har bir
    o'yinchi rasmni yuborishi bilanoq (birinchi bo'lsin, ikkinchi bo'lsin)
    shu natija darhol o'sha o'yinchining o'ziga (caption'da) ko'rsatiladi."""
    cfg = config.VISION_AI
    instruction = f"""
Siz faqat rasm chizish o'yinining hakamisiz. Boshqa hech qanday vazifani bajarmang.
Topshiriq: {prompt}

Rasmni MUSTAQIL baholang. Chiroy, rassomlik mahorati yoki chiroyli chizilganiga
emas, aynan topshiriqda so'ralgan obyektga SEMANTIK O'XSHASHLIKKA ball bering.

100 ballik mezon:
- 70 ball: topshiriqdagi asosiy obyektni to'g'ri tasvirlagani
- 20 ball: obyektning muhim ajratuvchi belgilarini ko'rsatgani
- 10 ball: rasm topshiriqqa aniq va tushunarli mosligi

Narsani juda oddiy chizish ham obyekt aniq bo'lsa yuqori ball olishi mumkin.
Qo'shimcha bezaklar asosiy obyektni almashtirmasa jarima bermang.

FAQAT quyidagi JSONni qaytaring, boshqa hech qanday matn yozmang:
{{"score": 0-100, "comment": "o'zbekcha 1 jumla"}}
"""
    try:
        raw, _status, _detail = await ask_gemini_multimodal(
            cfg, instruction, img, "image/jpeg", label="Drawing duel (1 rasm)"
        )
    except Exception as e:
        logger.error("🎨 Drawing single-eval xato: %s", e, exc_info=True)
        raw = None
    return _score_single(raw or "")

def _individual_caption(room: dict, uid: str) -> str:
    """Foydalanuvchi o'z rasmini do'stiga ulashganda, rasm ostiga
    chiqadigan matn — faqat O'ZINING topshirig'i va O'ZINING balli."""
    sub = room["submissions"].get(uid, {})
    score = sub.get("score")
    if score is None:
        return f"🎯 Berilgan shart: {room['prompt']}\n\n⚠️ AI baholay olmadi. «Qayta boshlash» bilan yangi raund boshlang."
    return f"🎯 Berilgan shart: {room['prompt']}\n\n📊 O'xshashi: {score}%"

def _summary_text(room: dict) -> str | None:
    """Ikkala o'yinchi ham yuborib bo'lgach — ikkalasining balli va
    g'olib bilan alohida qo'shimcha xabar (ikkala o'yinchining shaxsiy
    chatiga to'g'ridan-to'g'ri yuboriladi, qarang: bot.py)."""
    ids = list(room["players"].keys())
    if len(ids) != 2:
        return None
    s1, s2 = room["submissions"].get(ids[0]), room["submissions"].get(ids[1])
    if not s1 or not s2:
        return None
    n1 = s1.get("name") or "1-o'yinchi"
    n2 = s2.get("name") or "2-o'yinchi"
    p1, p2 = s1.get("score"), s2.get("score")
    if p1 is None or p2 is None:
        return (
            f"🎯 Topshiriq: {room['prompt']}\n\n"
            "⚠️ AI baholashi muvaffaqiyatsiz bo'ldi. «Qayta boshlash» bilan yangi raund boshlang."
        )
    winner_text = "🤝 Durang" if p1 == p2 else f"🏆 G'olib: {n1 if p1 > p2 else n2}"
    return f"🎯 Topshiriq: {room['prompt']}\n\n{n1}: {p1}%\n{n2}: {p2}%\n\n{winner_text}"

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
            "score": None,
            "comment": "",
        }
        room["updated_at"] = time.time()
        prompt = room["prompt"]
        other_uid = next((pid for pid in room["players"] if pid != uid), None)
    return {
        "state": public_state(room, uid),
        "image": jpeg_bytes,
        "query_id": user.get("_query_id"),
        "user_id": int(uid),
        "other_user_id": int(other_uid) if other_uid else None,
        "room_id": rid,
        "prompt": prompt,
        # Har DOIM (1-chi ham, 2-chi ham o'yinchi uchun) mustaqil
        # baholash kerak — Vision AI shu rasmni alohida baholaydi,
        # do'stining rasmini kutish shart emas.
        "single_eval": (prompt, jpeg_bytes),
    }, None

def finish_single_evaluation(rid: str, uid: str, score: dict):
    """Bitta o'yinchining AI ballini xonaga yozadi. Ikkalasi ham
    baholangan bo'lsa — g'olibni ham shu yerda aniqlaydi.
    Qaytaradi: (room_snapshot, shu_o'yinchi_uchun_caption, ikkalasi_ham_tugagan_bo'lsa_summary_yoki_None)."""
    with LOCK:
        room = _room(rid)
        if not room or uid not in room["submissions"]:
            return None, None, None
        room["submissions"][uid]["score"] = score.get("score")
        room["submissions"][uid]["comment"] = score.get("comment", "")
        room["updated_at"] = time.time()
        caption = _individual_caption(room, uid)
        summary = None
        if len(room["submissions"]) == 2 and all(
            room["submissions"][pid].get("score") is not None or room["submissions"][pid].get("comment")
            for pid in room["players"]
        ):
            ids = list(room["players"].keys())
            p1 = room["submissions"][ids[0]].get("score")
            p2 = room["submissions"][ids[1]].get("score")
            if p1 is None or p2 is None:
                winner = None
            elif p1 == p2:
                winner = "draw"
            else:
                winner = "player1" if p1 > p2 else "player2"
            room["result"] = {"player1": p1, "player2": p2, "winner": winner,
                               "comment": "Ikkala rasm ham topshiriqqa mustaqil baholandi."}
            room["status"] = "finished"
            summary = _summary_text(room)
        return dict(room), caption, summary

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
