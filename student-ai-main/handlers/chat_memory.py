"""🧠 Aqil charxi — Telegram chat ichida ishlaydigan Memory o'yini.

Mini App'dagi Memory qoidalari saqlanadi, lekin doska inline tugmalar ko'rinishida
shu chat xabarining o'zida turadi. 2 kishilik rejimda navbat 1v1, 4 kishilik
rejimda esa ikki jamoa navbati: 1-jamoa-1, 2-jamoa-1, 1-jamoa-2, 2-jamoa-2 ...
Har bir jamoada nechta o'yinchi bo'lsa, o'sha navbatdagi indeks aylantirib boriladi.
"""
import json
import logging
import random
import threading
import time
import uuid

import config
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

DATA_FILE = "chat_memory_rooms.json"
DATA_KEY = "student_ai_chat_memory_rooms"
ROOM_TTL = 12 * 60 * 60
MAX_ROOMS = 300
LOCK = threading.RLock()

MEMORY_SIZES = {"4x4": (4, 4), "4x6": (4, 6), "6x6": (6, 6)}
MEMORY_SYMBOL_SETS = {
    "fruits": ["🍒","🍓","🍇","🍉","🍋","🍍","🍑","🍎","🍌","🥝","🍈","🍐","🥭","🍏","🫐","🥥","🍅","🥑"],
    "numbers": ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟","➕","➖","✖️","➗","🔢","💯","🔣","🆗"],
    "faces": ["🙃","😀","😂","😍","😎","🥳","🤩","😜","🤔","😴","🥶","🤯","😇","🤠","🥸","😱","🤗","😏"],
    "animals": ["🐇","🐶","🐱","🐼","🦊","🐸","🐵","🦁","🐯","🐨","🐷","🐮","🐔","🐙","🦄","🐢","🐝","🦉"],
    "space": ["🚀","🪐","🌟","☄️","🌙","🛸","👽","🌌","⭐","🌎","🔭","🛰️","☀️","🌠","🌑","🧑‍🚀","🌕","💫"],
    "sports": ["⚽","🏀","🏈","⚾","🎾","🏐","🏉","🎱","🏓","🏸","🥊","🥋","🎯","🏹","⛳","🛹","🏒","🥅"],
}
SIZE_LABELS = {"4x4": "4×4", "4x6": "4×6", "6x6": "6×6"}
SYMBOL_LABELS = {
    "fruits": "🍒 Mevalar", "numbers": "🔢 Raqamlar", "faces": "😀 Emoji",
    "animals": "🐼 Hayvonlar", "space": "🚀 Kosmos", "sports": "⚽ Sport",
}

raw, _ = config.persist_read(DATA_FILE, DATA_KEY)
try:
    ROOMS = json.loads(raw) if raw else {}
    if not isinstance(ROOMS, dict): ROOMS = {}
except Exception:
    ROOMS = {}


def _save():
    config.persist_write(DATA_FILE, DATA_KEY, json.dumps(ROOMS, ensure_ascii=False),
                         commit_message="🧠 Aqil charxi chat xonalari yangilandi")


def _purge():
    now = time.time()
    for rid in list(ROOMS):
        if now - ROOMS[rid].get("updated_at", now) > ROOM_TTL:
            ROOMS.pop(rid, None)


def create_room(creator_id: int) -> str:
    with LOCK:
        _purge()
        rid = uuid.uuid4().hex[:12]
        ROOMS[rid] = {
            "id": rid, "created_at": time.time(), "updated_at": time.time(),
            "status": "lobby", "mode": 2, "size": "4x4", "symbols": "fruits",
            "players": {}, "teams": {"1": [], "2": []},
            "board": [], "turn_team": 1, "turn_index": 0, "turn": None,
            "pending": [], "scores": {}, "winner": None, "message_id": None,
            "last_move": None, "resolving": False,
        }
        if len(ROOMS) > MAX_ROOMS:
            old = sorted(ROOMS, key=lambda x: ROOMS[x].get("updated_at", 0))[:len(ROOMS)-MAX_ROOMS]
            for x in old: ROOMS.pop(x, None)
        _save()
        return rid


def get_room(rid):
    with LOCK:
        _purge()
        return ROOMS.get(rid)


def _touch(room):
    room["updated_at"] = time.time()
    _save()


def _name(user):
    return (getattr(user, "first_name", None) or getattr(user, "username", None) or str(user.id))[:40]


def _players_text(room):
    if room["mode"] == 2:
        vals = list(room["players"].values())
        if not vals:
            return "👥 O'yinchilar: 0/2"
        return "👥 O'yinchilar: " + ", ".join(p["name"] for p in vals) + f" ({len(vals)}/2)"
    t1 = room["teams"]["1"]; t2 = room["teams"]["2"]
    def team_line(n, ids):
        names = [room["players"][x]["name"] for x in ids]
        return f"1-guruh: {', '.join(names) if names else '—'} ({len(ids)}/2)" if n == 1 else f"2-guruh: {', '.join(names) if names else '—'} ({len(ids)}/2)"
    return "👥 " + team_line(1, t1) + "\n👥 " + team_line(2, t2)


def _turn_player(room):
    if room["mode"] == 2:
        return room.get("turn")
    teams = room["teams"]
    team = str(room.get("turn_team", 1))
    ids = teams.get(team, [])
    if not ids:
        return None
    idx = int(room.get("turn_index", 0)) % len(ids)
    return ids[idx]


def _next_turn(room):
    if room["mode"] == 2:
        ids = list(room["players"])
        if ids:
            cur = room.get("turn")
            try: room["turn"] = ids[(ids.index(cur) + 1) % len(ids)]
            except ValueError: room["turn"] = ids[0]
        return
    # Team 1 -> Team 2 -> Team 1 -> ...; each team's own player index advances
    # only when that team gets its turn. Thus 2v1 becomes 1a, 2a, 1b, 2a...
    current = int(room.get("turn_team", 1))
    indices = room.setdefault("team_next_index", {"1": 0, "2": 0})
    current_ids = room["teams"].get(str(current), [])
    if current_ids:
        indices[str(current)] = (int(indices.get(str(current), 0)) + 1) % len(current_ids)
    room["turn_team"] = 2 if current == 1 else 1
    next_ids = room["teams"].get(str(room["turn_team"]), [])
    if next_ids:
        room["turn_index"] = int(indices.get(str(room["turn_team"]), 0)) % len(next_ids)


def _deal(room):
    rows, cols = MEMORY_SIZES[room["size"]]
    total = rows * cols
    pool = MEMORY_SYMBOL_SETS[room["symbols"]]
    chosen = (pool * ((total // 2 + len(pool) - 1) // len(pool)))[:total // 2]
    deck = chosen * 2
    random.shuffle(deck)
    room["board"] = [{"symbol": s, "state": "hidden"} for s in deck]
    room["pending"] = []
    room["scores"] = {uid: 0 for uid in room["players"]}


def _status_text(room):
    if room["status"] == "finished":
        scores = {}
        for team_no in ("1", "2"):
            scores[team_no] = sum(room["scores"].get(uid, 0) for uid in room["teams"][team_no])
        if room["mode"] == 2:
            vals = [(p["name"], room["scores"].get(uid, 0), uid) for uid,p in room["players"].items()]
            vals.sort(key=lambda x: x[1], reverse=True)
            return "🏆 O'yin tugadi!\n" + "\n".join(f"{n}: {s} juftlik" for n,s,_ in vals)
        return f"🏆 O'yin tugadi!\n1-guruh: {scores['1']} juftlik\n2-guruh: {scores['2']} juftlik"
    uid = _turn_player(room)
    if not uid: return "⏳ Navbat aniqlanmoqda..."
    return f"🎯 Navbat: {room['players'][uid]['name']}"


def render(room):
    lines = [
        "🧠 AQIL CHARXI",
        f"🔢 Katak: {SIZE_LABELS[room['size']]}   {SYMBOL_LABELS[room['symbols']]}",
        f"👥 Rejim: {room['mode']} kishi" if room["mode"] == 2 else "👥 Rejim: 4 kishi / 2 guruh",
        "",
        _players_text(room),
    ]
    if room["status"] == "lobby":
        lines += ["", "⚙️ O'yin sozlamalari va ishtirokchilarni tanlang.", "▶️ Boshlash uchun 2 kishilik rejimda 2 ta, 4 kishilik rejimda har bir guruhda kamida 1 ta ishtirokchi kerak."]
    else:
        lines += ["", _status_text(room)]
        if room["last_move"]:
            lm = room["last_move"]
            lines.append(f"{('✅ Juftlik topildi' if lm['matched'] else '❌ Juftlik topilmadi')}: {lm['symbols'][0]} {lm['symbols'][1]}")
    return "\n".join(lines)


def _keyboard(room):
    kb = []
    if room["status"] == "lobby":
        kb += [
            [InlineKeyboardButton("👥 2 kishi" + (" ✅" if room["mode"] == 2 else ""), callback_data=f"cm:mode:{room['id']}:2"),
             InlineKeyboardButton("👥 4 kishi" + (" ✅" if room["mode"] == 4 else ""), callback_data=f"cm:mode:{room['id']}:4")],
            [InlineKeyboardButton("🔢 4×4" + (" ✅" if room["size"] == "4x4" else ""), callback_data=f"cm:size:{room['id']}:4x4"),
             InlineKeyboardButton("🔢 4×6" + (" ✅" if room["size"] == "4x6" else ""), callback_data=f"cm:size:{room['id']}:4x6"),
             InlineKeyboardButton("🔢 6×6" + (" ✅" if room["size"] == "6x6" else ""), callback_data=f"cm:size:{room['id']}:6x6")],
            [InlineKeyboardButton("🍒 Mevalar" + (" ✅" if room["symbols"] == "fruits" else ""), callback_data=f"cm:sym:{room['id']}:fruits"),
             InlineKeyboardButton("🔢 Raqamlar" + (" ✅" if room["symbols"] == "numbers" else ""), callback_data=f"cm:sym:{room['id']}:numbers"),
             InlineKeyboardButton("😀 Emoji" + (" ✅" if room["symbols"] == "faces" else ""), callback_data=f"cm:sym:{room['id']}:faces")],
            [InlineKeyboardButton("🐼 Hayvonlar" + (" ✅" if room["symbols"] == "animals" else ""), callback_data=f"cm:sym:{room['id']}:animals"),
             InlineKeyboardButton("🚀 Kosmos" + (" ✅" if room["symbols"] == "space" else ""), callback_data=f"cm:sym:{room['id']}:space"),
             InlineKeyboardButton("⚽ Sport" + (" ✅" if room["symbols"] == "sports" else ""), callback_data=f"cm:sym:{room['id']}:sports")],
        ]
        if room["mode"] == 2:
            kb.append([InlineKeyboardButton("🙋‍♂️ Qo'shilish", callback_data=f"cm:join:{room['id']}:2"),
                       InlineKeyboardButton("▶️ Boshlash", callback_data=f"cm:start:{room['id']}")])
        else:
            kb.append([InlineKeyboardButton("1-guruhga qo'shilish", callback_data=f"cm:join:{room['id']}:1"),
                       InlineKeyboardButton("2-guruhga qo'shilish", callback_data=f"cm:join:{room['id']}:2")])
            kb.append([InlineKeyboardButton("▶️ Boshlash", callback_data=f"cm:start:{room['id']}")])
    else:
        board = room["board"]
        cols = MEMORY_SIZES[room["size"]][1]
        row = []
        for i, cell in enumerate(board):
            if cell["state"] in ("revealed", "matched"):
                label = cell["symbol"]
            else:
                label = "⬜"
            row.append(InlineKeyboardButton(label, callback_data=f"cm:flip:{room['id']}:{i}"))
            if len(row) == cols:
                kb.append(row); row=[]
        if row: kb.append(row)
        if room["status"] == "finished":
            kb.append([InlineKeyboardButton("🔄 Yangi o'yin", callback_data=f"cm:new:{room['id']}")])
    return InlineKeyboardMarkup(kb)


def message_payload(room):
    return render(room), _keyboard(room)


def configure(rid, kind, value):
    with LOCK:
        room = get_room(rid)
        if not room or room["status"] != "lobby": return False, "Bu xona endi sozlanmaydi."
        if kind == "mode":
            if int(value) not in (2,4): return False, "Rejim noto'g'ri."
            room["mode"] = int(value); room["players"] = {}; room["teams"] = {"1": [], "2": []}
        elif kind == "size":
            if value not in MEMORY_SIZES: return False, "Katak o'lchami noto'g'ri."
            room["size"] = value
        elif kind == "sym":
            if value not in MEMORY_SYMBOL_SETS: return False, "Belgi turi noto'g'ri."
            room["symbols"] = value
        else: return False, "Noma'lum sozlama."
        _touch(room)
        return True, None


def join(rid, user, team):
    with LOCK:
        room = get_room(rid)
        if not room or room["status"] != "lobby": return False, "O'yin allaqachon boshlangan."
        uid = str(user.id)
        if uid in room["players"]: return True, None
        if room["mode"] == 2:
            if len(room["players"]) >= 2: return False, "❌ 2 kishilik joy to'ldi."
            room["players"][uid] = {"name": _name(user), "team": "1"}
            room["teams"]["1"].append(uid)
        else:
            team = str(team)
            if team not in ("1","2"): return False, "Guruh noto'g'ri."
            if len(room["teams"][team]) >= 2: return False, f"❌ {team}-guruh to'ldi."
            room["players"][uid] = {"name": _name(user), "team": team}
            room["teams"][team].append(uid)
        _touch(room)
        return True, None


def start(rid):
    with LOCK:
        room = get_room(rid)
        if not room or room["status"] != "lobby": return False, "O'yin allaqachon boshlangan."
        if room["mode"] == 2:
            if len(room["players"]) != 2: return False, "❌ Avval 2 kishi qo'shilishi kerak."
            ids = list(room["players"])
            room["turn"] = ids[0]
        else:
            if not room["teams"]["1"] or not room["teams"]["2"]:
                return False, "❌ Boshlash uchun 1-guruh va 2-guruhda kamida bittadan o'yinchi bo'lishi kerak."
            room["turn_team"] = 1; room["turn_index"] = 0; room["team_next_index"] = {"1": 0, "2": 0}; room["turn"] = None
        _deal(room)
        room["status"] = "playing"
        room["winner"] = None
        room["last_move"] = None
        _touch(room)
        return True, None


def flip(rid, uid, index):
    with LOCK:
        room = get_room(rid)
        if not room: return False, "Xona topilmadi."
        uid = str(uid)
        if uid not in room["players"]: return False, "❌ Avval o'yinga qo'shiling."
        if room["status"] != "playing": return False, "O'yin hali boshlanmagan yoki tugagan."
        if room.get("resolving"):
            return False, "⏳ Juftlik natijasi aniqlanmoqda..."
        current = _turn_player(room)
        if current != uid: return False, f"⏳ Hozir {room['players'][current]['name']} yuradi."
        try: index = int(index)
        except (TypeError, ValueError): return False, "Katak noto'g'ri."
        board = room["board"]
        if not 0 <= index < len(board): return False, "Katak noto'g'ri."
        if board[index]["state"] != "hidden": return False, "Bu katak allaqachon ochilgan."
        pending = room.get("pending", [])
        if len(pending) >= 2:
            for i in pending: board[i]["state"] = "hidden"
            pending = []
        board[index]["state"] = "revealed"
        pending.append(index)
        room["pending"] = pending
        if len(pending) == 1:
            room["last_move"] = None
            _touch(room)
            return True, None
        i, j = pending
        matched = board[i]["symbol"] == board[j]["symbol"]
        si, sj = board[i]["symbol"], board[j]["symbol"]
        if matched:
            board[i]["state"] = board[j]["state"] = "matched"
            room["scores"][uid] = room["scores"].get(uid, 0) + 1
        room["last_move"] = {"symbols": [si, sj], "matched": matched, "by": uid}
        room["pending"] = []
        if not matched:
            room["resolving"] = True
        if all(c["state"] == "matched" for c in board):
            room["status"] = "finished"
            if room["mode"] == 2:
                ids = list(room["players"])
                a,b = ids[0], ids[1]
                sa,sb = room["scores"].get(a,0),room["scores"].get(b,0)
                room["winner"] = a if sa > sb else b if sb > sa else None
            else:
                s1 = sum(room["scores"].get(x,0) for x in room["teams"]["1"])
                s2 = sum(room["scores"].get(x,0) for x in room["teams"]["2"])
                room["winner"] = "1" if s1 > s2 else "2" if s2 > s1 else None
        elif not matched:
            _next_turn(room)
        _touch(room)
        return True, None


async def _edit(query, room):
    text, markup = message_payload(room)
    try:
        await query.edit_message_text(text=text, reply_markup=markup)
    except Exception as exc:
        logger.debug("Aqil charxi xabarini edit qilishda xato: %s", exc)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.data or not q.data.startswith("cm:"): return
    parts = q.data.split(":")
    if len(parts) < 3: return
    action, rid = parts[1], parts[2]
    room = get_room(rid)
    if not room: await q.answer("Xona muddati o'tgan.", show_alert=True); return

    if action == "mode" and len(parts) == 4:
        ok, err = configure(rid, "mode", parts[3])
        if not ok: await q.answer(err, show_alert=True)
        else:
            await q.answer("✅ Rejim o'zgartirildi")
            await _edit(q, get_room(rid))
        return
    if action in ("size", "sym") and len(parts) == 4:
        ok, err = configure(rid, action, parts[3])
        if not ok: await q.answer(err, show_alert=True)
        else:
            await q.answer("✅ Sozlama saqlandi")
            await _edit(q, get_room(rid))
        return
    if action == "join" and len(parts) == 4:
        ok, err = join(rid, q.from_user, parts[3])
        if not ok: await q.answer(err, show_alert=True)
        else:
            await q.answer("✅ Siz o'yinga qo'shildingiz")
            await _edit(q, get_room(rid))
        return
    if action == "start":
        ok, err = start(rid)
        if not ok: await q.answer(err, show_alert=True)
        else:
            await q.answer("▶️ O'yin boshlandi")
            await _edit(q, get_room(rid))
        return
    if action == "flip" and len(parts) == 4:
        ok, err = flip(rid, q.from_user.id, parts[3])
        if not ok:
            await q.answer(err, show_alert=True)
            return
        room = get_room(rid)
        await q.answer("🎴 Katak ochildi")
        await _edit(q, room)
        # Mos kelmagan juftlik Mini App'dagidek qisqa muddat ko'rinadi, keyin yopiladi.
        if room.get("last_move") and not room["last_move"]["matched"] and room["status"] == "playing":
            await asyncio_sleep(1.0)
            with LOCK:
                room = get_room(rid)
                if room and room.get("pending") == []:
                    for cell in room["board"]:
                        if cell["state"] == "revealed": cell["state"] = "hidden"
                    room["resolving"] = False
                    _touch(room)
            if room: await _edit(q, room)
        return
    if action == "new":
        new_rid = create_room(q.from_user.id)
        room = get_room(new_rid)
        await q.answer("🔄 Yangi o'yin tayyor")
        # Inline xabarni aynan shu yangi xonaga o'tkazish.
        await _edit(q, room)
        return


async def asyncio_sleep(seconds):
    import asyncio
    await asyncio.sleep(seconds)
