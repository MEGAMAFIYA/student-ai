"""Telegram-chat board games for Student AI.

Shashka and X-O are rendered entirely with inline keyboard buttons; no Mini App.
"""
import game
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import random


def _room(rid):
    with game.LOCK:
        return game.ROOMS.get(rid)


def _name(p):
    return (p.get("name") or str(p.get("id") or "o'yinchi"))[:32]


def _players(room):
    return list(room.get("players", {}).items())


MEMORY_SYMBOLS = {
    "fruits": ["🍎","🍐","🍊","🍋","🍉","🍇","🍓","🍒","🥝","🍌","🍑","🥭","🍍","🥥","🥕","🍅","🍏","🫐"],
    "emoji": ["😀","😂","😍","😎","🤩","🥳","🤖","👻","🐼","🦊","🐯","🐸","🐵","🦁","🐨","🐰","🐱","🐶"],
    "numbers": ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟","🔢","0️⃣","🔣","🔠","🔡","➕","➖","✖️"],
}
MEMORY_SIZES = {"4x4": (4,4), "4x6": (4,6), "6x6": (6,6)}

def _memory_text(room):
    lines=["🧠 <b>Aqil charxi</b>", "", f"📐 {room.get('memory_size','4x4')}  •  🎨 {room.get('memory_symbols','fruits')}"]
    for uid,p in _players(room):
        ready="✅" if room.get("memory_ready",{}).get(uid) else "⏳"
        lines.append(f"{ready} {_name(p)}")
    if room.get("status")=="lobby":
        lines += ["", "Birinchi o‘yinchi ▶️ Boshlash tugmasini bir marta bosadi. Ikkinchi o‘yinchi qo‘shilishi shart emas."]
    elif room.get("status")=="playing":
        uid=str(room.get("turn")); p=room.get("players",{}).get(uid)
        scores=room.get("scores",{})
        lines += ["", f"🎯 Navbat: {_name(p) if p else uid}", "🏆 " + "  |  ".join(f"{_name(room['players'].get(k,{}))}: {v}" for k,v in scores.items())]
    elif room.get("status")=="finished":
        win=room.get("winner"); p=room.get("players",{}).get(str(win)) if win else None
        lines += ["", f"🏆 G‘olib: {_name(p) if p else 'Durrang'}"]
    return "\n".join(lines)

def _memory_kb(room):
    rows,cols=MEMORY_SIZES.get(room.get("memory_size","4x4"),(4,4))
    board=room.get("board",[])
    kb=[]
    for r in range(rows):
        row=[]
        for c in range(cols):
            i=r*cols+c; cell=board[i] if i<len(board) else None
            label="⬜"
            if cell:
                if cell.get("state") in ("revealed","pending"): label=cell.get("symbol","⬜")
                elif cell.get("state") == "matched": label="⠀"
            row.append(InlineKeyboardButton(label, callback_data=f"cg:mf:{room['id']}:{i}"))
        kb.append(row)
    if room.get("status")=="lobby":
        kb.append([InlineKeyboardButton("4×4",callback_data=f"cg:msize:{room['id']}:4x4"),InlineKeyboardButton("4×6",callback_data=f"cg:msize:{room['id']}:4x6"),InlineKeyboardButton("6×6",callback_data=f"cg:msize:{room['id']}:6x6")])
        kb.append([InlineKeyboardButton("🍎 Meva",callback_data=f"cg:msym:{room['id']}:fruits"),InlineKeyboardButton("😀 Emoji",callback_data=f"cg:msym:{room['id']}:emoji"),InlineKeyboardButton("🔢 Raqam",callback_data=f"cg:msym:{room['id']}:numbers")])
        if len(room.get("players",{}))<2: kb.append([InlineKeyboardButton("👥 Qo‘shilish",callback_data=f"cg:join:{room['id']}")])
        if room.get("players"): kb.append([InlineKeyboardButton("▶️ Boshlash",callback_data=f"cg:mready:{room['id']}")])
    kb.append([InlineKeyboardButton("🔄 Yangilash",callback_data=f"cg:refresh:{room['id']}")])
    return InlineKeyboardMarkup(kb)

def memory_join(room, uid, user):
    return add_player(room,uid,user)

def memory_config(room, uid, key, value):
    with game.LOCK:
        if room.get("status")!="lobby": return False,"O‘yin allaqachon boshlangan."
        if str(uid) not in room.get("players",{}): return False,"Avval o‘yinga qo‘shiling."
        if key=="size" and value not in MEMORY_SIZES: return False,"O‘lcham noto‘g‘ri."
        if key=="symbols" and value not in MEMORY_SYMBOLS: return False,"Belgilar turi noto‘g‘ri."
        room["memory_"+key]=value; room["memory_ready"]={}; room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
    return True,None

def memory_ready(room, uid):
    with game.LOCK:
        uid=str(uid)
        if uid not in room.get("players",{}): return False,"Avval o‘yinga qo‘shiling."
        if room.get("status")!="lobby": return False,"O‘yin allaqachon boshlangan."
        rows,cols=MEMORY_SIZES.get(room.get("memory_size") or "4x4", MEMORY_SIZES["4x4"])
        pairs=rows*cols//2
        pool=MEMORY_SYMBOLS.get(room.get("memory_symbols") or "fruits", MEMORY_SYMBOLS["fruits"])
        chosen=(pool*((pairs//len(pool))+1))[:pairs]
        deck=chosen*2
        random.shuffle(deck)
        room["board"]=[{"symbol":x,"state":"hidden"} for x in deck]
        room["turn"]=uid
        room["scores"]={k:0 for k in room["players"]}
        room["status"]="playing"
        room["memory_pending"]=[]
        room["memory_waiting"]=False
        room["last_mismatch"]=None
        room["memory_started_by"]=uid
        room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
    return True,None

def memory_flip(room, uid, index):
    with game.LOCK:
        uid=str(uid)
        if uid not in room.get("players",{}): return False,"Siz bu o‘yinda emassiz."
        if room.get("status")!="playing": return False,"O‘yin hali boshlanmadi yoki tugagan."
        room.setdefault("memory_waiting", False)
        room.setdefault("memory_pending", [])
        if room.get("memory_waiting"): return False,"Kartalar yopilishini kuting."
        if room.get("turn")!=uid: return False,"Hozir navbat sizda emas."
        b=room.get("board",[])
        if not isinstance(index, int) or index<0 or index>=len(b) or b[index]["state"]!="hidden":
            return False,"Bu katakni bosib bo‘lmaydi."
        pending=room.setdefault("memory_pending",[])
        b[index]["state"]="revealed"; pending.append(index)
        room["last_mismatch"] = None
        if len(pending)==2:
            a,c=pending
            if b[a]["symbol"]==b[c]["symbol"]:
                b[a]["state"]=b[c]["state"]="matched"
                room["scores"][uid]=room["scores"].get(uid,0)+1
                pending.clear()
            else:
                # Juft bo'lmagan ikki karta qisqa vaqt ochiq turadi. Shu
                # vaqt ichida boshqa yurish qabul qilinmaydi; handler keyin
                # memory_hide_mismatch() chaqirib navbatni almashtiradi.
                b[a]["state"]=b[c]["state"]="pending"
                room["last_mismatch"]=[a,c]
                room["memory_waiting"] = True
            if all(x["state"]=="matched" for x in b):
                room["status"]="finished"
                vals=list(room["scores"].items())
                room["winner"] = max(vals,key=lambda x:x[1])[0] if vals and (len(vals)==1 or vals[0][1]!=vals[1][1]) else None
        room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
    return True,None


def memory_hide_mismatch(room):
    """Yopilmagan juft bo'lmagan kartalarni yopib, navbatni o'tkazadi."""
    with game.LOCK:
        if not room or not room.get("memory_waiting"):
            return False
        pending=list(room.get("memory_pending", []))
        for idx in pending:
            if 0 <= idx < len(room.get("board", [])) and room["board"][idx].get("state") == "pending":
                room["board"][idx]["state"] = "hidden"
        room["memory_pending"]=[]
        room["memory_waiting"]=False
        room["last_mismatch"]=None
        players=list(room.get("players", {}))
        current=str(room.get("turn"))
        others=[x for x in players if x != current]
        if others: room["turn"]=others[0]
        room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
        return True


def _checkers_text(room):
    ps = _players(room)
    lines = ["⚪ <b>Rus shashkasi</b>", ""]
    for uid, p in ps:
        side = {"w":"⚪ Oq", "b":"⚫ Qora"}.get(p.get("side"), "tanlanmagan")
        lines.append(f"{side}: {_name(p)}")
    if room.get("status") == "lobby":
        lines += ["", "Rang tanlang va sherigingizni kuting."]
    elif room.get("status") == "playing":
        side = room.get("turn")
        who = next((p for _,p in ps if p.get("side") == side), None)
        lines += ["", f"🎯 Navbat: {_name(who) if who else side}"]
    elif room.get("status") == "finished":
        win = room.get("winner")
        # Shashka qoidalari g'olibni foydalanuvchi ID'si emas, w/b tomoni
        # sifatida qaytaradi.
        wp = next((p for _, p in ps if p.get("side") == win), None) if win in ("w", "b") else None
        lines += ["", f"🏆 G‘olib: {_name(wp) if wp else 'Durrang'}"]
    return "\n".join(lines)


def _checkers_kb(room, selected=None):
    board = room["board"]
    kb=[]
    for r in range(8):
        row=[]
        for c in range(8):
            p=board[r][c]
            # Bo'sh kataklar ko'rinadigan belgisiz qoladi. Telegram inline
            # tugmasi matni bo'sh bo'lishi mumkin emas, shuning uchun U+2800
            # (ko'rinmas Braille blank) ishlatiladi.
            label = "⠀"
            if p == "w": label="⚪"
            elif p == "b": label="⚫"
            elif p == "W": label="👑"
            elif p == "B": label="♛"
            # Tanlangan dona o'z belgisi bilan qoladi; bo'sh katakda ishora yo'q.
            row.append(InlineKeyboardButton(label, callback_data=f"cg:c:{room['id']}:{r}:{c}"))
        kb.append(row)
    if room.get("status") == "lobby":
        kb.append([InlineKeyboardButton("⚪ Oq", callback_data=f"cg:side:{room['id']}:w"), InlineKeyboardButton("⚫ Qora", callback_data=f"cg:side:{room['id']}:b")])
    kb.append([InlineKeyboardButton("🔄 Yangilash", callback_data=f"cg:refresh:{room['id']}")])
    return InlineKeyboardMarkup(kb)


def _ttt_text(room):
    lines=["❌⭕ <b>X-O o‘yini</b>", ""]
    for uid,p in _players(room):
        mark={"x":"❌ X","o":"⭕ O"}.get(p.get("mark"),"tanlanmagan")
        lines.append(f"{mark}: {_name(p)}")
    if room.get("status")=="lobby": lines += ["", "Belgi tanlang va 2-o‘yinchini kuting."]
    elif room.get("status")=="playing":
        turn=room.get("turn"); p=next((p for _,p in _players(room) if p.get("mark")==turn),None)
        lines += ["", f"🎯 Navbat: {_name(p) if p else turn}"]
    elif room.get("status")=="finished":
        win=room.get("winner"); p=room.get("players",{}).get(str(win)) if win else None
        lines += ["", f"🏆 G‘olib: {_name(p) if p else 'Durrang'}"]
    return "\n".join(lines)


def _ttt_kb(room):
    icons={"x":"❌","o":"⭕",None:"⬜"}
    kb=[]
    for r in range(3):
        kb.append([InlineKeyboardButton(icons[room["board"][r*3+c]], callback_data=f"cg:t:{room['id']}:{r*3+c}") for c in range(3)])
    if room.get("status")=="lobby":
        kb.append([InlineKeyboardButton("❌ X", callback_data=f"cg:mark:{room['id']}:x"), InlineKeyboardButton("⭕ O", callback_data=f"cg:mark:{room['id']}:o")])
    kb.append([InlineKeyboardButton("🔄 Yangilash", callback_data=f"cg:refresh:{room['id']}")])
    return InlineKeyboardMarkup(kb)


def render(room):
    if room["game"]=="checkers": return _checkers_text(room), _checkers_kb(room)
    if room["game"]=="memory": return _memory_text(room), _memory_kb(room)
    return _ttt_text(room), _ttt_kb(room)


def add_player(room, uid, user):
    uid=str(int(uid))
    with game.LOCK:
        is_new = uid not in room["players"]
        if is_new and len(room["players"])>=2:
            return False, "O‘yin 2 kishilik va xona to‘la."
        room["players"].setdefault(uid,{"side":None,"mark":None,"style":"classic","joined_at":game.time.time(),"name":str(uid)})
        room["players"][uid]["name"] = user.full_name or user.username or str(uid)
        # Birinchi o'yinchi rang/belgi tanlab bo'lganidan keyin ikkinchi
        # o'yinchi shunchaki "Qo'shilish"ni bossa ham qarama-qarshi tanlov
        # avtomatik beriladi va o'yin boshlanadi.
        if is_new and room.get("status") == "lobby" and len(room["players"]) == 2:
            other = next((v for k,v in room["players"].items() if k != uid), None)
            me = room["players"][uid]
            if room.get("game") == "checkers" and other and other.get("side") in ("w", "b"):
                me["side"] = "b" if other["side"] == "w" else "w"
                room["status"] = "playing"; room["turn"] = "w"
            elif room.get("game") == "tictactoe" and other and other.get("mark") in ("x", "o"):
                me["mark"] = "o" if other["mark"] == "x" else "x"
                room["status"] = "playing"; room["turn"] = "x"
        room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
    return True, None


def choose_side(room, uid, side):
    uid = str(uid)
    with game.LOCK:
        p=room["players"].get(uid)
        if not p:return False,"Avval o‘yinga qo‘shiling."
        if room["status"]!="lobby":return False,"O‘yin allaqachon boshlangan."
        if side not in ("w","b"):return False,"Rang noto‘g‘ri."
        others=[(k,v) for k,v in room["players"].items() if k != uid]
        if any(v.get("side")==side for _,v in others):
            # Ikkinchi o'yinchi birinchi tanlagan rangni bossa, avtomatik
            # qarama-qarshi rangni oladi.
            side = "b" if side == "w" else "w"
        p["side"]=side
        if len(room["players"])==2:
            other = next((v for k,v in room["players"].items() if k != uid), None)
            if other and other.get("side") is None:
                other["side"] = "b" if side == "w" else "w"
            if other and {v.get("side") for v in room["players"].values()}=={"w","b"}:
                room["status"]="playing"; room["turn"]="w"
        room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
    return True,None


def choose_mark(room, uid, mark):
    uid = str(uid)
    with game.LOCK:
        p=room["players"].get(uid)
        if not p:return False,"Avval o‘yinga qo‘shiling."
        if room["status"]!="lobby":return False,"O‘yin allaqachon boshlangan."
        if mark not in ("x","o"):return False,"Belgi noto‘g‘ri."
        others=[(k,v) for k,v in room["players"].items() if k != uid]
        if any(v.get("mark")==mark for _,v in others):
            mark = "o" if mark == "x" else "x"
        p["mark"]=mark
        if len(room["players"])==2:
            other = next((v for k,v in room["players"].items() if k != uid), None)
            if other and other.get("mark") is None:
                other["mark"] = "o" if mark == "x" else "x"
            if other and {v.get("mark") for v in room["players"].values()}=={"x","o"}:
                room["status"]="playing"; room["turn"]="x"
        room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
    return True,None


def checkers_move(room, uid, r, c):
    with game.LOCK:
        p=room["players"].get(str(uid))
        if not p:return False,"Siz bu o‘yinda emassiz."
        if room["status"]!="playing":return False,"O‘yin hali boshlanmadi yoki tugagan."
        side=p.get("side")
        if side != room.get("turn"):return False,"Hozir navbat sizda emas."
        sel=room.setdefault("chat_selected",{}).get(str(uid))
        board=room["board"]
        if not (0<=r<8 and 0<=c<8):
            return False,"Katak koordinatasi noto‘g‘ri."
        if sel is None:
            if not board[r][c] or board[r][c].lower()!=side:
                return False,"Avval o‘zingizning donangizni tanlang."
            forced=room.get("forced_piece")
            if forced and [r,c] != forced:
                return False,"Urishni davom ettirish uchun aynan belgilangan donani tanlang."
            room["chat_selected"][str(uid)]=[r,c]
            room["version"]+=1;room["updated_at"]=game.time.time();game._save()
            return True,None
        fr=sel
        # O'yinchi boshqa o'z donasini bossa, yurish o'rniga tanlovni almashtir.
        if board[r][c] and board[r][c].lower()==side and [r,c] != fr and not room.get("forced_piece"):
            room["chat_selected"][str(uid)]=[r,c]
            room["version"]+=1;room["updated_at"]=game.time.time();game._save()
            return True,None
        ok,err=game._checkers_move(room,side,fr[0],fr[1],r,c)
        if not ok:
            return False,err
        room["chat_selected"].pop(str(uid),None)
        room["version"]+=1;room["updated_at"]=game.time.time();game._save()
        return True,None


def ttt_move(room, uid, index):
    with game.LOCK:
        p=room["players"].get(str(uid))
        if not p:return False,"Siz bu o‘yinda emassiz."
        if room["status"]!="playing":return False,"O‘yin hali boshlanmadi yoki tugagan."
        mark=p.get("mark")
        if mark!=room.get("turn"):return False,"Hozir navbat sizda emas."
        if not (0<=index<9) or room["board"][index] is not None:return False,"Bu katak band yoki noto‘g‘ri."
        b=room["board"]; b[index]=mark; room["last_move"]={"index":index,"mark":mark,"by":int(uid)}
        win,line=game._ttt_winner(b)
        if win:
            room["status"]="finished";room["reason"]="line";room["winner"]=str(uid);room["win_line"]=list(line)
        elif all(x is not None for x in b): room["status"]="finished";room["reason"]="draw";room["winner"]=None
        else: room["turn"]=game._opp_mark(mark)
        room["version"]+=1;room["updated_at"]=game.time.time();game._save()
    return True,None
