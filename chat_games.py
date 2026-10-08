"""Telegram-chat board games for Student AI.

Shashka and X-O are rendered entirely with inline keyboard buttons; no Mini App.
"""
import game
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _room(rid):
    with game.LOCK:
        return game.ROOMS.get(rid)


def _name(p):
    return (p.get("name") or str(p.get("id") or "o'yinchi"))[:32]


def _players(room):
    return list(room.get("players", {}).items())


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
        wp = room.get("players", {}).get(str(win)) if win else None
        lines += ["", f"🏆 G‘olib: {_name(wp) if wp else 'Durrang'}"]
    return "\n".join(lines)


def _checkers_kb(room, selected=None):
    board = room["board"]
    kb=[]
    for r in range(8):
        row=[]
        for c in range(8):
            p=board[r][c]
            label = "⬛" if (r+c)%2==0 else "▫️"
            if p == "w": label="⚪"
            elif p == "b": label="⚫"
            elif p == "W": label="👑"
            elif p == "B": label="♛"
            if selected == [r,c]: label="🔵"
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
    return _ttt_text(room), _ttt_kb(room)


def add_player(room, uid, user):
    uid=str(int(uid))
    with game.LOCK:
        if uid not in room["players"] and len(room["players"])>=2:
            return False, "O‘yin 2 kishilik va xona to‘la."
        room["players"].setdefault(uid,{"side":None,"mark":None,"style":"classic","joined_at":game.time.time(),"name":str(uid)})
        room["players"][uid]["name"] = user.full_name or user.username or str(uid)
        room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
    return True, None


def choose_side(room, uid, side):
    with game.LOCK:
        p=room["players"].get(str(uid))
        if not p:return False,"Avval o‘yinga qo‘shiling."
        if room["status"]!="lobby":return False,"O‘yin allaqachon boshlangan."
        if side not in ("w","b"):return False,"Rang noto‘g‘ri."
        if any(k!=str(uid) and v.get("side")==side for k,v in room["players"].items()):return False,"Bu rang tanlangan."
        p["side"]=side
        if len(room["players"])==2 and {v.get("side") for v in room["players"].values()}=={"w","b"}:
            room["status"]="playing"; room["turn"]="w"
        room["updated_at"]=game.time.time(); room["version"]+=1; game._save()
    return True,None


def choose_mark(room, uid, mark):
    with game.LOCK:
        p=room["players"].get(str(uid))
        if not p:return False,"Avval o‘yinga qo‘shiling."
        if room["status"]!="lobby":return False,"O‘yin allaqachon boshlangan."
        if mark not in ("x","o"):return False,"Belgi noto‘g‘ri."
        if any(k!=str(uid) and v.get("mark")==mark for k,v in room["players"].items()):return False,"Bu belgi tanlangan."
        p["mark"]=mark
        if len(room["players"])==2 and {v.get("mark") for v in room["players"].values()}=={"x","o"}:
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
        if sel is None:
            if not (0<=r<8 and 0<=c<8) or not board[r][c] or board[r][c].lower()!=side:
                return False,"Avval o‘zingizning donangizni tanlang."
            room["chat_selected"][str(uid)]=[r,c]
            return True,None
        fr=sel
        ok,err=game._checkers_move(room,side,fr[0],fr[1],r,c)
        room["chat_selected"].pop(str(uid),None)
        if not ok:return False,err
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
