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


MEMORY_SYMBOLS = game.MEMORY_SYMBOL_SETS
MEMORY_SYMBOL_LABELS = game.MEMORY_SYMBOL_LABELS

MEMORY_SIZES = {"4x4": (4,4), "4x6": (4,6), "6x6": (6,6)}

def _memory_text(room):
    symbol_key=room.get("memory_symbols", "fruits")
    symbol_label=MEMORY_SYMBOL_LABELS.get(symbol_key, symbol_key)
    lines=["🧠 <b>Aqil charxi</b>", "", f"📐 {room.get('memory_size','4x4')}  •  🎨 {symbol_label}"]
    if room.get("memory_mode") == "teams":
        teams = room.get("teams", {"1": [], "2": []})
        for team_id in ("1", "2"):
            names = [_name(room.get("players", {}).get(uid, {})) for uid in teams.get(team_id, [])]
            lines.append(f"🟦 {team_id}-guruh ({len(names)}/2): " + (", ".join(names) if names else "hali bo'sh"))
    else:
        for uid,p in _players(room):
            ready="✅" if room.get("memory_ready",{}).get(uid) else "⏳"
            lines.append(f"{ready} {_name(p)}")
    if room.get("status")=="lobby":
        if room.get("memory_mode") == "teams":
            lines += ["", "Har bir guruhga 1–2 kishi qo‘shilsin. Boshlash uchun ikkala guruhda ham kamida bittadan o‘yinchi bo‘lishi kerak."]
        else:
            lines += ["", "2 kishilik rejim: ikki o‘yinchi qo‘shilgach ▶️ Boshlash tugmasini bosing."]
    elif room.get("status")=="playing":
        uid=str(room.get("turn")); p=room.get("players",{}).get(uid)
        scores=room.get("scores",{})
        lines += ["", f"🎯 Navbat: {_name(p) if p else uid}"]
        if room.get("memory_mode") == "teams":
            lines.append("🏆 " + "  |  ".join(f"{team}-guruh: {scores.get(team, 0)}" for team in ("1", "2")))
        else:
            lines.append("🏆 " + "  |  ".join(f"{_name(room['players'].get(k,{}))}: {v}" for k, v in scores.items()))
    elif room.get("status")=="finished":
        win=room.get("winner"); p=room.get("players",{}).get(str(win)) if win else None
        if room.get("memory_mode") == "teams":
            lines += ["", f"🏆 Natija: {('1-guruh g‘olib' if win == '1' else '2-guruh g‘olib') if win in ('1','2') else 'Durrang'}"]
        else:
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
                elif cell.get("state") == "matched": label=cell.get("symbol", "⬜")
            row.append(InlineKeyboardButton(label, callback_data=f"cg:mf:{room['id']}:{i}"))
        kb.append(row)
    if room.get("status")=="lobby":
        kb.append([InlineKeyboardButton("4×4",callback_data=f"cg:msize:{room['id']}:4x4"),InlineKeyboardButton("4×6",callback_data=f"cg:msize:{room['id']}:4x6"),InlineKeyboardButton("6×6",callback_data=f"cg:msize:{room['id']}:6x6")])
        kb.append([InlineKeyboardButton("🍒 Mevalar",callback_data=f"cg:msym:{room['id']}:fruits"),InlineKeyboardButton("🔢 Raqamlar",callback_data=f"cg:msym:{room['id']}:numbers"),InlineKeyboardButton("🙃 Emoji",callback_data=f"cg:msym:{room['id']}:faces")])
        kb.append([InlineKeyboardButton("🐇 Hayvonlar",callback_data=f"cg:msym:{room['id']}:animals"),InlineKeyboardButton("🚀 Kosmos",callback_data=f"cg:msym:{room['id']}:space"),InlineKeyboardButton("⚽ Sport",callback_data=f"cg:msym:{room['id']}:sports")])
        kb.append([InlineKeyboardButton("👤 2 kishilik",callback_data=f"cg:mmode:{room['id']}:duel"),InlineKeyboardButton("👥 Guruh bo‘lib",callback_data=f"cg:mmode:{room['id']}:teams")])
        if room.get("memory_mode") == "teams":
            kb.append([InlineKeyboardButton("1-guruh",callback_data=f"cg:mteam:{room['id']}:1"),InlineKeyboardButton("2-guruh",callback_data=f"cg:mteam:{room['id']}:2")])
        elif len(room.get("players",{}))<2:
            kb.append([InlineKeyboardButton("👥 Qo‘shilish",callback_data=f"cg:join:{room['id']}")])
        if room.get("players"):
            kb.append([InlineKeyboardButton("▶️ Boshlash",callback_data=f"cg:mready:{room['id']}")])
    if room.get("status")=="finished":
        kb.append([InlineKeyboardButton("🔄 Qayta boshlash",callback_data=f"cg:restart:{room['id']}")])
    kb.append([InlineKeyboardButton("🔄 Yangilash",callback_data=f"cg:refresh:{room['id']}")])
    return InlineKeyboardMarkup(kb)


def memory_set_mode(room, uid, mode):
    with game.LOCK:
        uid=str(uid)
        if room.get("status") != "lobby": return False, "O‘yin allaqachon boshlangan."
        if uid not in room.get("players", {}): return False, "Avval o‘yinga qo‘shiling."
        if mode not in ("duel", "teams"): return False, "Rejim noto‘g‘ri."
        if mode == "duel" and len(room.get("players", {})) > 2:
            return False, "2 kishilik rejim uchun xonada 2 tadan ortiq o‘yinchi bor. Yangi xona oching."
        room["memory_mode"] = mode
        room["teams"] = {"1": [], "2": []}
        if mode == "teams":
            # Xona yaratilganida birinchi o'yinchi allaqachon ro'yxatda bo'ladi;
            # rejim tanlangach u guruhni tugma orqali aniq tanlaydi.
            for p in room["players"].values(): p.pop("team", None)
        else:
            for p in room["players"].values(): p.pop("team", None)
        room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
    return True, None


def memory_join_team(room, uid, user, team_id):
    uid=str(int(uid)); team_id=str(team_id)
    with game.LOCK:
        if room.get("status") != "lobby": return False, "O‘yin allaqachon boshlangan."
        if room.get("memory_mode") != "teams": return False, "Avval guruh bo‘lib rejimini tanlang."
        if team_id not in ("1", "2"): return False, "Guruh noto‘g‘ri."
        teams=room.setdefault("teams", {"1": [], "2": []})
        existing=room.get("players", {}).get(uid)
        if existing and existing.get("team") == team_id:
            return True, None
        if len(teams.get(team_id, [])) >= 2:
            return False, "Bu guruh to‘la (2 kishi)."
        if existing and existing.get("team") in teams:
            teams[existing["team"]]=[x for x in teams[existing["team"]] if x != uid]
        room.setdefault("players", {})[uid] = {"name": user.full_name or user.username or uid,
            "side": None, "mark": None, "team": team_id, "joined_at": game.time.time()}
        teams.setdefault(team_id, []).append(uid)
        room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
    return True, None


def _memory_turn_order(room):
    teams=room.get("teams", {"1": [], "2": []})
    a=list(teams.get("1", [])); b=list(teams.get("2", []))
    if not a or not b: return []
    import math
    rounds=math.lcm(len(a), len(b))
    order=[]
    for i in range(rounds):
        order.append(a[i % len(a)])
        order.append(b[i % len(b)])
    return order


def restart_room(room, uid):
    uid=str(uid)
    with game.LOCK:
        if uid not in room.get("players", {}): return False, "Faqat o‘yin ishtirokchilari qayta boshlashi mumkin."
        if room.get("status") != "finished": return False, "O‘yin hali tugamagan."
        if room.get("game") == "memory":
            room["board"]=[]; room["scores"]={}; room["winner"]=None; room["reason"]=None
            room["memory_pending"]=[]; room["memory_waiting"]=False; room["last_mismatch"]=None
            room["memory_ready"]={}; room["turn_order_index"]=0
            if room.get("memory_mode") == "teams":
                room["scores"]={"1":0,"2":0}; order=_memory_turn_order(room)
                if not order: return False, "Har ikki guruhga kamida bitta o‘yinchi kerak."
                room["turn_order"]=order; room["turn"]=order[0]
            else:
                ids=list(room.get("players", {})); room["scores"]={k:0 for k in ids}
                room["turn"]=ids[0] if ids else None
            room["status"]="lobby"
        elif room.get("game") == "tictactoe":
            room.update(board=[None]*9, status="playing", winner=None, reason=None, turn="x", win_line=None, last_move=None)
        elif room.get("game") == "checkers":
            room.update(board=game._initial_checkers(), status="playing", winner=None, reason=None, turn="w", forced_piece=None, last_move=None, history=[], chat_selected={})
        elif room.get("game") == "quoridor":
            room.update(status="playing", winner=None, reason=None, turn="1", pawns={"1":[8,4],"2":[0,4]}, walls_left={"1":10,"2":10}, walls=[], wall_draft={})
        else:
            return False, "Bu o‘yin uchun qayta boshlash mavjud emas."
        room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
    return True, None

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
        if uid not in room.get("players", {}): return False,"Avval o‘yinga qo‘shiling."
        if room.get("status")!="lobby": return False,"O‘yin allaqachon boshlangan."
        mode=room.get("memory_mode", "duel")
        if mode == "teams":
            order=_memory_turn_order(room)
            if not order: return False, "Boshlash uchun 1-guruh va 2-guruhga kamida bittadan o‘yinchi kerak."
            room["turn_order"]=order; room["turn_order_index"]=0; room["turn"]=order[0]
            room["scores"]={"1":0,"2":0}
        else:
            players=list(room.get("players", {}))
            if len(players) < 2: return False, "2 kishilik o‘yinni boshlash uchun yana bir o‘yinchi qo‘shilsin."
            room["turn_order"]=players; room["turn_order_index"]=0; room["turn"]=players[0]
            room["scores"]={k:0 for k in players}
        rows,cols=MEMORY_SIZES.get(room.get("memory_size") or "4x4", MEMORY_SIZES["4x4"])
        pairs=rows*cols//2
        pool=MEMORY_SYMBOLS.get(room.get("memory_symbols") or "fruits", MEMORY_SYMBOLS["fruits"])
        chosen=(pool*((pairs//len(pool))+1))[:pairs]
        deck=chosen*2; random.shuffle(deck)
        room["board"]=[{"symbol":x,"state":"hidden"} for x in deck]
        room["status"]="playing"; room["memory_pending"]=[]
        room["memory_waiting"]=False; room["last_mismatch"]=None
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
                scorer = room.get("players", {}).get(uid, {}).get("team") if room.get("memory_mode") == "teams" else uid
                room["scores"][str(scorer)]=room["scores"].get(str(scorer),0)+1
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
                room["winner"] = max(vals,key=lambda x:x[1])[0] if vals and sum(1 for _,v in vals if v == max(x[1] for x in vals)) == 1 else None
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
        stored_order=room.get("turn_order")
        order=list(stored_order or room.get("players", {}).keys())
        if order:
            if stored_order:
                idx=int(room.get("turn_order_index", 0))
            else:
                current=str(room.get("turn"))
                idx=order.index(current) if current in order else -1
            room["turn_order_index"]=(idx+1) % len(order)
            room["turn"]=str(order[room["turn_order_index"]])
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
    if room.get("status") == "finished":
        kb.append([InlineKeyboardButton("🔄 Qayta boshlash", callback_data=f"cg:restart:{room['id']}")])
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
    if room.get("status")=="finished":
        kb.append([InlineKeyboardButton("🔄 Qayta boshlash", callback_data=f"cg:restart:{room['id']}")])
    kb.append([InlineKeyboardButton("🔄 Yangilash", callback_data=f"cg:refresh:{room['id']}")])
    return InlineKeyboardMarkup(kb)


def render(room):
    if room["game"]=="quoridor": return _q_text(room), _q_kb(room)
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

# ---------------- 🧱 Quoridor — 2 kishilik chat o'yini ----------------
def _q_blocked(room, r1, c1, r2, c2):
    for w in room.get("walls", []):
        r,c,o=w["r"],w["c"],w["o"]
        if o == "h" and c <= min(c1,c2) <= c+1 and c1==c2 and {r1,r2}=={r,r+1}:
            return True
        if o == "v" and r <= min(r1,r2) <= r+1 and r1==r2 and {c1,c2}=={c,c+1}:
            return True
    return False

def _q_path_exists(room, start, player):
    from collections import deque
    goal = 0 if player == "1" else 8
    q=deque([tuple(start)]); seen={tuple(start)}
    while q:
        r,c=q.popleft()
        if r==goal: return True
        for dr,dc in ((1,0),(-1,0),(0,1),(0,-1)):
            nr,nc=r+dr,c+dc
            if 0<=nr<9 and 0<=nc<9 and (nr,nc) not in seen and not _q_blocked(room,r,c,nr,nc):
                seen.add((nr,nc)); q.append((nr,nc))
    return False

def _q_text(room):
    names={uid:_name(p) for uid,p in room.get("players",{}).items()}
    lines=["🧱 <b>QUORIDOR</b>", "", f"🔵 {names.get(room.get('p1'), '1-o‘yinchi')}  — devor: {room.get('walls_left',{}).get('1',10)}",
           f"🔴 {names.get(room.get('p2'), '2-o‘yinchi')}  — devor: {room.get('walls_left',{}).get('2',10)}", ""]
    board=[['·' for _ in range(9)] for _ in range(9)]
    for w in room.get('walls',[]):
        r,c,o=w['r'],w['c'],w['o']
        if o=='h':
            board[r][c]='━'; board[r][c+1]='━'; board[r+1][c]='━'; board[r+1][c+1]='━'
        else:
            board[r][c]='┃'; board[r+1][c]='┃'; board[r][c+1]='┃'; board[r+1][c+1]='┃'
    for pid,pos in room.get('pawns',{}).items(): board[pos[0]][pos[1]]='🔵' if pid=='1' else '🔴'
    lines += [' '.join(row) for row in board]
    if room.get('status')=='lobby': lines += ['', 'Ikkinchi o‘yinchi qo‘shilishini kuting.']
    elif room.get('status')=='playing':
        p=room.get('turn'); lines += ['', f"🎯 Navbat: {names.get(room.get('p1' if p=='1' else 'p2'), p)}"]
        if room.get('wall_draft',{}).get(p):
            d=room['wall_draft'][p]; lines.append(f"🧱 Devor: {'gorizontal' if d.get('o')=='h' else 'vertikal'} • qator {('-' if d.get('r') is None else d.get('r')+1)} • ustun {('-' if d.get('c') is None else d.get('c')+1)}")
            lines.append('Qator va ustunni tanlab, devorni tasdiqlang.')
        else: lines.append('Yurish yoki devor qo‘yishni tanlang.')
    elif room.get('status')=='finished': lines += ['', f"🏆 G‘olib: {names.get(room.get('p1' if room.get('winner')=='1' else 'p2'), 'o‘yinchi')}"]
    return '\n'.join(lines)

def _q_kb(room):
    rid=room['id']; kb=[]
    if room.get('status')=='lobby':
        if len(room.get('players',{}))<2: kb.append([InlineKeyboardButton('👥 Qo‘shilish',callback_data=f'cg:join:{rid}')])
    elif room.get('status')=='playing':
        pid=room.get('turn'); draft=room.get('wall_draft',{}).get(pid)
        if draft:
            kb.append([InlineKeyboardButton('↔️ Gorizontal',callback_data=f'cg:qo:{rid}:h'),InlineKeyboardButton('↕️ Vertikal',callback_data=f'cg:qo:{rid}:v')])
            kb.append([InlineKeyboardButton(f'Qator {i+1}',callback_data=f'cg:qr:{rid}:{i}') for i in range(4)])
            kb.append([InlineKeyboardButton(f'Qator {i+5}',callback_data=f'cg:qr:{rid}:{i+4}') for i in range(4)])
            kb.append([InlineKeyboardButton(f'Ustun {i+1}',callback_data=f'cg:qc:{rid}:{i}') for i in range(4)])
            kb.append([InlineKeyboardButton(f'Ustun {i+5}',callback_data=f'cg:qc:{rid}:{i+4}') for i in range(4)])
            if draft.get('r') is not None and draft.get('c') is not None: kb.append([InlineKeyboardButton('🧱 Devorni qo‘yish',callback_data=f'cg:qp:{rid}')])
            kb.append([InlineKeyboardButton('❌ Bekor qilish',callback_data=f'cg:qx:{rid}')])
        else:
            kb += [[InlineKeyboardButton('⬆️',callback_data=f'cg:qm:{rid}:U')],
                   [InlineKeyboardButton('⬅️',callback_data=f'cg:qm:{rid}:L'),InlineKeyboardButton('⬇️',callback_data=f'cg:qm:{rid}:D'),InlineKeyboardButton('➡️',callback_data=f'cg:qm:{rid}:R')],
                   [InlineKeyboardButton('🧱 Devor qo‘yish',callback_data=f'cg:qd:{rid}')]]
    if room.get('status')=='finished': kb.append([InlineKeyboardButton('🔄 Qayta boshlash',callback_data=f'cg:restart:{rid}')])
    kb.append([InlineKeyboardButton('🔄 Yangilash',callback_data=f'cg:refresh:{rid}')])
    return InlineKeyboardMarkup(kb)

def quoridor_join(room, uid, user):
    uid=str(int(uid))
    with game.LOCK:
        if uid not in room['players'] and len(room['players'])>=2: return False,'O‘yin xonasi to‘la.'
        room['players'].setdefault(uid, {'name':user.full_name or user.username or uid,'joined_at':game.time.time()})
        room['players'][uid]['name']=user.full_name or user.username or uid
        ids=list(room['players'])
        if len(ids)==2:
            room['p1'],room['p2']=ids[0],ids[1]; room['status']='playing'; room['turn']='1'
        room['version']+=1; room['updated_at']=game.time.time(); game._save()
    return True,None

def quoridor_action(room, uid, action, value=None):
    uid=str(uid)
    with game.LOCK:
        if uid not in room.get('players',{}): return False,'Siz bu o‘yinda emassiz.'
        if room.get('status')!='playing': return False,'O‘yin hali boshlanmagan yoki tugagan.'
        pid='1' if uid==room.get('p1') else '2'
        if pid!=room.get('turn'): return False,'Hozir navbat sizda emas.'
        pos=room['pawns'][pid]
        if action=='move':
            delta={'U':(-1,0),'D':(1,0),'L':(0,-1),'R':(0,1)}.get(value)
            if not delta: return False,'Yurish noto‘g‘ri.'
            nr,nc=pos[0]+delta[0],pos[1]+delta[1]
            if not(0<=nr<9 and 0<=nc<9) or _q_blocked(room,*pos,nr,nc): return False,'Bu tomonga yurib bo‘lmaydi.'
            other='2' if pid=='1' else '1'; opos=room['pawns'][other]
            if [nr,nc]==opos:
                jr,jc=nr+delta[0],nc+delta[1]
                if 0<=jr<9 and 0<=jc<9 and not _q_blocked(room,nr,nc,jr,jc): nr,nc=jr,jc
                else: return False,'Raqib ortidan sakrash yo‘li yopiq.'
            room['pawns'][pid]=[nr,nc]
            if nr==(0 if pid=='1' else 8): room['status']='finished'; room['winner']=pid
            else: room['turn']=other
            room['wall_draft'].pop(pid,None)
        elif action=='draft': room.setdefault('wall_draft',{})[pid]={'o':'h','r':None,'c':None}
        elif action in ('orient','row','col'):
            d=room.setdefault('wall_draft',{}).setdefault(pid,{'o':'h','r':None,'c':None})
            if action=='orient' and value in ('h','v'): d['o']=value
            elif action=='row' and 0<=int(value)<=7: d['r']=int(value)
            elif action=='col' and 0<=int(value)<=7: d['c']=int(value)
        elif action=='cancel': room.get('wall_draft',{}).pop(pid,None)
        elif action=='place':
            d=room.get('wall_draft',{}).get(pid)
            if not d or d.get('r') is None or d.get('c') is None: return False,'Devor yo‘nalishi, qatori va ustunini tanlang.'
            if room['walls_left'].get(pid,0)<=0: return False,'Devorlaringiz qolmagan.'
            w={'r':d['r'],'c':d['c'],'o':d['o']}
            walls=room.get('walls',[])
            if w in walls or any(x['r']==w['r'] and x['c']==w['c'] and x['o']!=w['o'] for x in walls): return False,'Bu joy band.'
            # Parallel devorlar bir-birining ustiga tushmasin.
            if any(x['o']==w['o'] and x['r']==w['r'] and abs(x['c']-w['c'])<2 for x in walls) if w['o']=='h' else any(x['o']==w['o'] and x['c']==w['c'] and abs(x['r']-w['r'])<2 for x in walls): return False,'Devorlar ustma-ust tushmasligi kerak.'
            walls.append(w)
            if not _q_path_exists(room,room['pawns']['1'],'1') or not _q_path_exists(room,room['pawns']['2'],'2'):
                walls.pop(); return False,'Bu devor yo‘lni butunlay to‘sib qo‘yadi.'
            room['walls_left'][pid]-=1; room['wall_draft'].pop(pid,None); room['turn']='2' if pid=='1' else '1'
        else: return False,'Noma’lum amal.'
        room['version']+=1; room['updated_at']=game.time.time(); game._save()
    return True,None
