"""
🎮 Student AI — 1v1 Games backend.
Authoritative server-side rules for Chess, Russian Draughts and the Memory
(juftlik topish) game. All moves are validated on the server; clients are
only a UI. Rooms are persisted through the project's existing persistence
layer.
"""
import copy, json, logging, os, random, re, threading, time, uuid
import config
import webapp_security

logger = logging.getLogger(__name__)

GAME_FILE = "game_rooms.json"
GAME_KEY = "student_ai_game_rooms"
ROOM_TTL = int(os.getenv("GAME_ROOM_TTL_SEC", str(12 * 60 * 60)))
MAX_ROOMS = 500
MAX_CHAT = 200
MAX_CHAT_CLIENT_KEYS = 500
MAX_SIGNAL_ITEMS = 60
LOCK = threading.RLock()

# ---------------- 🧠 Xotira o'yini (Memory / juftlik topish) sozlamalari ----------------
# Katak o'lchami -> (qatorlar, ustunlar). Har biri juft sonli katak beradi.
MEMORY_SIZES = {
    "4x4": (4, 4),   # 16 katak — 8 juft — tez o'yin
    "4x6": (4, 6),   # 24 katak — 12 juft — o'rtacha
    "6x6": (6, 6),   # 36 katak — 18 juft — uzun o'yin
}
MEMORY_DEFAULT_SIZE = "4x4"

# Har bir kategoriya kamida 18 ta belgi beradi (6x6 = 18 juft uchun yetarli).
MEMORY_SYMBOL_SETS = {
    "fruits": ["🍒","🍓","🍇","🍉","🍋","🍍","🍑","🍎","🍌","🥝","🍈","🍐","🥭","🍏","🫐","🥥","🍅","🥑"],
    "numbers": ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟","➕","➖","✖️","➗","🔢","💯","🔣","🆗"],
    "faces": ["🙃","😀","😂","😍","😎","🥳","🤩","😜","🤔","😴","🥶","🤯","😇","🤠","🥸","😱","🤗","😏"],
    "animals": ["🐇","🐶","🐱","🐼","🦊","🐸","🐵","🦁","🐯","🐨","🐷","🐮","🐔","🐙","🦄","🐢","🐝","🦉"],
    "space": ["🚀","🪐","🌟","☄️","🌙","🛸","👽","🌌","⭐","🌎","🔭","🛰️","☀️","🌠","🌑","🧑‍🚀","🌕","💫"],
    "sports": ["⚽","🏀","🏈","⚾","🎾","🏐","🏉","🎱","🏓","🏸","🥊","🥋","🎯","🏹","⛳","🛹","🏒","🥅"],
}
MEMORY_DEFAULT_SYMBOLS = "fruits"
MEMORY_SYMBOL_LABELS = {
    "fruits": "🍒 Mevalar",
    "numbers": "🔢 Raqamlar",
    "faces": "🙃 Emoji kallalar",
    "animals": "🐇 Hayvonlar",
    "space": "🚀 Kosmos",
    "sports": "⚽ Sport",
}

raw, _ = config.persist_read(GAME_FILE, GAME_KEY)
try:
    ROOMS = json.loads(raw) if raw else {}
    if not isinstance(ROOMS, dict): ROOMS = {}
except Exception:
    ROOMS = {}

def _save():
    config.persist_write(GAME_FILE, GAME_KEY, json.dumps(ROOMS, ensure_ascii=False),
                         commit_message="🎮 1v1 o'yin xonalari yangilandi")

def _purge():
    now=time.time()
    with LOCK:
        for rid in list(ROOMS):
            if now - ROOMS[rid].get("created_at", now) > ROOM_TTL:
                del ROOMS[rid]

def _initial_chess():
    return [
        list("rnbqkbnr"), list("pppppppp"), [None]*8, [None]*8,
        [None]*8, [None]*8, list("PPPPPPPP"), list("RNBQKBNR")
    ]

def _initial_checkers():
    b=[[None]*8 for _ in range(8)]
    # black moves toward increasing row index, white toward decreasing
    for r in range(3):
        for c in range(8):
            if (r+c)%2: b[r][c]="b"
    for r in range(5,8):
        for c in range(8):
            if (r+c)%2: b[r][c]="w"
    return b

def create_room(game, creator_id):
    if game not in ("chess", "checkers", "memory", "tictactoe"): return None
    _purge()
    rid=uuid.uuid4().hex[:24]
    room={
        "id":rid,"game":game,"created_at":time.time(),"updated_at":time.time(),
        "players":{}, "version":0,
        "selected":{}, "rematch_votes":{}, "signals":{}, "chat":[], "chat_keys":{},
    }
    if game=="memory":
        room.update({
            "board": [], "status":"lobby","winner":None,"reason":None,"turn":None,
            "memory_size": MEMORY_DEFAULT_SIZE, "memory_symbols": MEMORY_DEFAULT_SYMBOLS,
            "memory_ready": {}, "scores": {}, "last_reveal": None, "order": [],
        })
    elif game=="tictactoe":
        room.update({
            "board": [None]*9, "status":"lobby","winner":None,"reason":None,"turn":None,
            "win_line": None, "last_move": None,
        })
    else:
        room.update({
            "board": _initial_chess() if game=="chess" else _initial_checkers(),
            "turn":"w","status":"lobby","winner":None,"reason":None,
            "castling":"KQkq","ep":None,"halfmove":0,"fullmove":1,
            "forced_piece":None, "last_move":None,"history":[],
        })
    room["players"][str(int(creator_id))]={"side":None,"style":"classic","joined_at":time.time(),"name":str(creator_id)}
    with LOCK:
        ROOMS[rid]=room
        if len(ROOMS)>MAX_ROOMS:
            old=sorted(ROOMS,key=lambda x: ROOMS[x].get("updated_at",0))[:len(ROOMS)-MAX_ROOMS]
            for x in old: ROOMS.pop(x,None)
        _save()
    return rid

def room_url(game,rid):
    username=config.BOT_USERNAME_FALLBACK.lstrip("@")
    return f"https://t.me/{username}?startapp=game_{rid}&mode=fullscreen"

def _verify(init_data):
    return webapp_security.verify_telegram_init_data(init_data, config.TELEGRAM_TOKEN)

def _room(rid):
    _purge()
    return ROOMS.get(rid)

def _public(room, uid):
    players=[]
    for k,p in room["players"].items():
        players.append({"id":int(k),"side":p.get("side"),"style":p.get("style","classic"),"mark":p.get("mark"),"name":p.get("name") or str(k)})
    base = {
        "id":room["id"],"game":room["game"],"status":room["status"],"winner":room["winner"],
        "reason":room["reason"],"turn":room["turn"],"board":room["board"],
        "players":players,"me":int(uid),"version":room["version"],
    }
    if room["game"]=="memory":
        base.update({
            "memory_size": room.get("memory_size", MEMORY_DEFAULT_SIZE),
            "memory_symbols": room.get("memory_symbols", MEMORY_DEFAULT_SYMBOLS),
            "memory_ready": room.get("memory_ready", {}),
            "memory_sizes": list(MEMORY_SIZES),
            "memory_symbol_sets": MEMORY_SYMBOL_LABELS,
            "rows": room.get("rows"), "cols": room.get("cols"),
            "scores": room.get("scores", {}),
            "last_reveal": room.get("last_reveal"),
        })
    elif room["game"]=="tictactoe":
        base.update({
            "win_line": room.get("win_line"),
            "last_move": room.get("last_move"),
        })
    else:
        base.update({
            "last_move":room["last_move"],"forced_piece":room.get("forced_piece"),
            "castling":room["castling"],"ep":room["ep"],
            "halfmove":room["halfmove"],"fullmove":room["fullmove"],
            "legal_moves":_client_legal_moves(room),
        })
    return base

def join(rid, init_data):
    user=_verify(init_data)
    if not user: return None,"Mini App sessiyasi tasdiqlanmadi."
    uid=str(int(user["id"]))
    with LOCK:
        room=_room(rid)
        if not room: return None,"O'yin xonasi topilmadi yoki muddati o'tgan."
        if uid not in room["players"] and len(room["players"])>=2:
            return None,"Bu o'yin xonasi to'la. Faqat 2 kishi o'ynaydi."
        room["players"].setdefault(uid,{"side":None,"style":"classic","joined_at":time.time(),"name":user.get("first_name") or user.get("username") or uid})
        room["players"][uid]["name"] = user.get("first_name") or user.get("username") or room["players"][uid].get("name") or uid
        room["updated_at"]=time.time()
        _save()
        return _public(room,uid),None

def choose(rid, init_data, side, style):
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"])); side=(side or "").lower()
    if side not in ("w","b"): return None,"Rang noto'g'ri."
    style=(style or "classic").lower()
    if style not in ("classic","crystal","neon"): style="classic"
    with LOCK:
        room=_room(rid)
        if not room:return None,"Xona topilmadi."
        if room["game"] not in ("chess","checkers"):return None,"Bu o'yin turi uchun mos emas."
        if uid not in room["players"]:return None,"Avval xonaga kiring."
        taken={p.get("side") for k,p in room["players"].items() if k!=uid}
        if side in taken:return None,"Bu rangni do'stingiz tanlagan. Boshqa rangni tanlang."
        room["players"][uid]["side"]=side
        room["players"][uid]["style"]=style
        sides=[p.get("side") for p in room["players"].values()]
        if len(room["players"])==2 and all(s in ("w","b") for s in sides) and len(set(sides))==2:
            room["status"]="playing"
            room["turn"]="w"
        room["updated_at"]=time.time(); room["version"]+=1; _save()
        return _public(room,uid),None


# ---------------- ❌⭕ X-O (Tic-Tac-Toe) ----------------
TTT_LINES = ((0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6))

def _opp_mark(m): return "o" if m=="x" else "x"

def _ttt_winner(board):
    for a,b,c in TTT_LINES:
        if board[a] and board[a]==board[b]==board[c]:
            return board[a], (a,b,c)
    return None, None

def choose_mark(rid, init_data, mark):
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"])); mark=(mark or "").lower()
    if mark not in ("x","o"): return None,"Belgi noto'g'ri."
    with LOCK:
        room=_room(rid)
        if not room:return None,"Xona topilmadi."
        if room["game"]!="tictactoe":return None,"Bu o'yin turi uchun mos emas."
        if uid not in room["players"]:return None,"Avval xonaga kiring."
        if room["status"]!="lobby":return None,"O'yin allaqachon boshlangan."
        taken={p.get("mark") for k,p in room["players"].items() if k!=uid}
        if mark in taken:return None,"Bu belgini do'stingiz tanlagan. Boshqasini tanlang."
        room["players"][uid]["mark"]=mark
        marks=[p.get("mark") for p in room["players"].values()]
        if len(room["players"])==2 and all(m in ("x","o") for m in marks) and len(set(marks))==2:
            room["status"]="playing"
            room["turn"]="x"
        room["updated_at"]=time.time(); room["version"]+=1; _save()
        return _public(room,uid),None

def tictactoe_move(rid, init_data, index):
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"]))
    try: index=int(index)
    except (TypeError, ValueError): return None,"Katak raqami noto'g'ri."
    with LOCK:
        room=_room(rid)
        if not room:return None,"Xona topilmadi."
        if room["game"]!="tictactoe":return None,"Bu xona X-O o'yini emas."
        pl=room["players"].get(uid)
        if not pl:return None,"Siz bu xonada emassiz."
        mark=pl.get("mark")
        if room["status"]!="playing":return None,"O'yin hali boshlanmadi yoki tugagan."
        if mark not in ("x","o") or room["turn"]!=mark:return None,"Hozir yurish navbati sizda emas."
        if not (0<=index<9):return None,"Katak raqami noto'g'ri."
        board=room["board"]
        if board[index] is not None:return None,"Bu katak band."
        board[index]=mark
        room["last_move"]={"index":index,"mark":mark,"by":int(uid)}
        win_mark,line=_ttt_winner(board)
        if win_mark:
            room["status"]="finished"; room["reason"]="line"
            room["winner"]=uid; room["win_line"]=list(line)
        elif all(x is not None for x in board):
            room["status"]="finished"; room["reason"]="draw"; room["winner"]=None
        else:
            room["turn"]=_opp_mark(mark)
        room["updated_at"]=time.time(); room["version"]+=1; _save()
        return _public(room,uid),None


# ---------------- 🧠 Xotira o'yini (Memory / juftlik topish) ----------------

def memory_set_config(rid, init_data, size, symbols):
    """Lobbyda katak o'lchami va belgilar to'plamini tanlash (istalgan o'yinchi)."""
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"]))
    size=(size or MEMORY_DEFAULT_SIZE).strip()
    symbols=(symbols or MEMORY_DEFAULT_SYMBOLS).strip().lower()
    if size not in MEMORY_SIZES: return None,"Katak o'lchami noto'g'ri."
    if symbols not in MEMORY_SYMBOL_SETS: return None,"Belgilar turi noto'g'ri."
    with LOCK:
        room=_room(rid)
        if not room:return None,"Xona topilmadi."
        if room["game"]!="memory":return None,"Bu xona xotira o'yini emas."
        if uid not in room["players"]:return None,"Avval xonaga kiring."
        if room["status"]!="lobby":return None,"O'yin allaqachon boshlangan."
        room["memory_size"]=size
        room["memory_symbols"]=symbols
        # Sozlama o'zgarsa — hamma qaytadan "Tayyor" bosishi kerak.
        room["memory_ready"]={}
        room["updated_at"]=time.time(); room["version"]+=1; _save()
        return _public(room,uid),None

def _deal_memory(room):
    rows,cols=MEMORY_SIZES.get(room.get("memory_size") or MEMORY_DEFAULT_SIZE, MEMORY_SIZES[MEMORY_DEFAULT_SIZE])
    total=rows*cols
    pairs=total//2
    pool=MEMORY_SYMBOL_SETS.get(room.get("memory_symbols") or MEMORY_DEFAULT_SYMBOLS, MEMORY_SYMBOL_SETS[MEMORY_DEFAULT_SYMBOLS])
    if len(pool)>=pairs:
        chosen=pool[:pairs]
    else:
        chosen=(pool*((pairs//len(pool))+1))[:pairs]
    deck=chosen*2
    random.shuffle(deck)
    room["board"]=[{"symbol":s,"state":"hidden"} for s in deck]
    room["rows"]=rows; room["cols"]=cols

def memory_ready(rid, init_data):
    """O'yinchi joriy sozlamalar bilan tayyorligini bildiradi.

    Ikkala o'yinchi ham tayyor bo'lgach, katak/belgi tanloviga mos taxta
    tarqatiladi va o'yin boshlanadi.
    """
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"]))
    with LOCK:
        room=_room(rid)
        if not room:return None,"Xona topilmadi."
        if room["game"]!="memory":return None,"Bu xona xotira o'yini emas."
        if uid not in room["players"]:return None,"Avval xonaga kiring."
        if room["status"]!="lobby":
            return _public(room,uid),None
        room.setdefault("memory_ready",{})[uid]=True
        if len(room["players"])==2 and all(room["memory_ready"].get(k) for k in room["players"]):
            _deal_memory(room)
            order=list(room["players"].keys())
            room["order"]=order
            room["turn"]=order[0]
            room["scores"]={k:0 for k in order}
            room["last_reveal"]=None
            room["status"]="playing"
        room["updated_at"]=time.time(); room["version"]+=1; _save()
        return _public(room,uid),None

def memory_flip(rid, init_data, index):
    """Bitta katakni ochish. Ikkinchi katak ochilgach juftlik tekshiriladi."""
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"]))
    try: index=int(index)
    except (TypeError, ValueError): return None,"Katak raqami noto'g'ri."
    with LOCK:
        room=_room(rid)
        if not room:return None,"Xona topilmadi."
        if room["game"]!="memory":return None,"Bu xona xotira o'yini emas."
        if uid not in room["players"]:return None,"Siz bu xonada emassiz."
        if room["status"]!="playing":return None,"O'yin hali boshlanmadi yoki tugagan."
        if room["turn"]!=uid:return None,"Hozir yurish navbati sizda emas."
        board=room["board"]
        if not (0<=index<len(board)):return None,"Katak raqami noto'g'ri."
        if board[index]["state"]!="hidden":return None,"Bu katak allaqachon ochilgan."

        # Xavfsizlik: oldingi urinishdan ochiq qolgan (juftlashmagan) kataklar
        # bo'lsa — ular avtomatik yopiladi (bu holat normalda yuzaga kelmaydi).
        pending=[i for i,c in enumerate(board) if c["state"]=="revealed"]
        if len(pending)>=2:
            for i in pending: board[i]["state"]="hidden"
            pending=[]

        board[index]["state"]="revealed"
        pending.append(index)

        if len(pending)==1:
            room["last_reveal"]=None
            room["updated_at"]=time.time(); room["version"]+=1; _save()
            return _public(room,uid),None

        i,j=pending
        si,sj=board[i]["symbol"],board[j]["symbol"]
        matched=si==sj
        if matched:
            board[i]["state"]="matched"; board[j]["state"]="matched"
            scores=room.setdefault("scores",{})
            scores[uid]=scores.get(uid,0)+1
        else:
            board[i]["state"]="hidden"; board[j]["state"]="hidden"

        room["last_reveal"]={"indices":[i,j],"symbols":[si,sj],"matched":matched,"by":int(uid),"ts":time.time()}

        if all(c["state"]=="matched" for c in board):
            room["status"]="finished"; room["reason"]="completed"
            scores=room.get("scores",{})
            order=room.get("order") or list(room["players"].keys())
            if len(order)==2:
                a,b=order; sa,sb=scores.get(a,0),scores.get(b,0)
                room["winner"]=a if sa>sb else (b if sb>sa else None)
            else:
                room["winner"]=None
        elif not matched:
            # Juft topilmadi — navbat boshqa o'yinchiga o'tadi.
            others=[k for k in room.get("order") or room["players"] if k!=uid]
            if others: room["turn"]=others[0]
        # matched bo'lsa — navbat o'zgarmaydi, o'yinchi yana bir juft urinadi.

        room["updated_at"]=time.time(); room["version"]+=1; _save()
        return _public(room,uid),None

def _inside(r,c): return 0<=r<8 and 0<=c<8
def _color(piece):
    if not piece:return None
    return "w" if piece.isupper() else "b"
def _opp(side): return "b" if side=="w" else "w"
def _clone(b): return [row[:] for row in b]

# ---------------- Chess ----------------
KNIGHT=((2,1),(2,-1),(-2,1),(-2,-1),(1,2),(1,-2),(-1,2),(-1,-2))
KING=((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1))
def _ray_moves(board,r,c,dirs,side):
    out=[]
    for dr,dc in dirs:
        rr,cc=r+dr,c+dc
        while _inside(rr,cc):
            q=board[rr][cc]
            if q is None: out.append((rr,cc))
            else:
                if _color(q)!=side: out.append((rr,cc))
                break
            rr+=dr;cc+=dc
    return out

def _pseudo_chess(board,r,c,castling,ep):
    p=board[r][c]; side=_color(p)
    if not p:return []
    typ=p.lower(); out=[]
    if typ=="p":
        d=-1 if side=="w" else 1; start=6 if side=="w" else 1
        rr=r+d
        if _inside(rr,c) and board[rr][c] is None:
            out.append((rr,c))
            if r==start and board[r+2*d][c] is None:out.append((r+2*d,c))
        for dc in (-1,1):
            cc=c+dc
            if not _inside(rr,cc):continue
            if board[rr][cc] and _color(board[rr][cc])!=side:out.append((rr,cc))
            elif ep==[rr,cc]:out.append((rr,cc))
    elif typ=="n":
        out=[(r+dr,c+dc) for dr,dc in KNIGHT if _inside(r+dr,c+dc) and _color(board[r+dr][c+dc])!=side]
    elif typ=="b":out=_ray_moves(board,r,c,((-1,-1),(-1,1),(1,-1),(1,1)),side)
    elif typ=="r":out=_ray_moves(board,r,c,((-1,0),(1,0),(0,-1),(0,1)),side)
    elif typ=="q":out=_ray_moves(board,r,c,((-1,-1),(-1,1),(1,-1),(1,1),(-1,0),(1,0),(0,-1),(0,1)),side)
    elif typ=="k":
        out=[(r+dr,c+dc) for dr,dc in KING if _inside(r+dr,c+dc) and _color(board[r+dr][c+dc])!=side]
        home=7 if side=="w" else 0
        if r==home and c==4:
            rights="KQ" if side=="w" else "kq"
            if rights[0] in castling and board[home][5] is None and board[home][6] is None and board[home][7] and board[home][7].lower()=="r":
                out.append((home,6))
            if rights[1] in castling and board[home][1] is None and board[home][2] is None and board[home][3] is None and board[home][0] and board[home][0].lower()=="r":
                out.append((home,2))
    return out

def _attacked(board,r,c,by):
    # pawns
    pd=1 if by=="w" else -1
    for dc in (-1,1):
        rr,cc=r+pd,c+dc
        if _inside(rr,cc) and board[rr][cc]==("P" if by=="w" else "p"): return True
    for dr,dc in KNIGHT:
        rr,cc=r+dr,c+dc
        if _inside(rr,cc) and board[rr][cc]==("N" if by=="w" else "n"):return True
    for dirs,types in [(((-1,0),(1,0),(0,-1),(0,1)),("r","q")),(((-1,-1),(-1,1),(1,-1),(1,1)),("b","q"))]:
        for dr,dc in dirs:
            rr,cc=r+dr,c+dc
            while _inside(rr,cc):
                p=board[rr][cc]
                if p:
                    if _color(p)==by and p.lower() in types:return True
                    break
                rr+=dr;cc+=dc
    for dr,dc in KING:
        rr,cc=r+dr,c+dc
        if _inside(rr,cc) and board[rr][cc]==("K" if by=="w" else "k"):return True
    return False

def _king_pos(board,side):
    k="K" if side=="w" else "k"
    for r in range(8):
        for c in range(8):
            if board[r][c]==k:return (r,c)
    return None

def _in_check(board,side):
    k=_king_pos(board,side)
    return True if not k else _attacked(board,k[0],k[1],_opp(side))

def _apply_chess(board, move, castling, ep):
    r,c,nr,nc=move; p=board[r][c]; b=_clone(board); captured=b[nr][nc]
    b[r][c]=None
    if p.lower()=="p" and ep==[nr,nc] and captured is None:
        b[nr+(1 if _color(p)=="w" else -1)][nc]=None
    b[nr][nc]=p
    # castle
    if p.lower()=="k" and abs(nc-c)==2:
        if nc==6: b[nr][5]=b[nr][7];b[nr][7]=None
        else: b[nr][3]=b[nr][0];b[nr][0]=None
    # promote to queen by default; UI offers q/r/b/n
    return b,captured

def _legal_chess_moves(room, side):
    board=room["board"]; out=[]
    for r in range(8):
        for c in range(8):
            if _color(board[r][c])!=side:continue
            for nr,nc in _pseudo_chess(board,r,c,room["castling"],room["ep"]):
                # castle cannot cross check
                if board[r][c].lower()=="k" and abs(nc-c)==2:
                    if _in_check(board,side):continue
                    mid=(r,5 if nc==6 else 3)
                    mb,_=_apply_chess(board,(r,c,*mid),room["castling"],room["ep"])
                    if _in_check(mb,side):continue
                nb,_=_apply_chess(board,(r,c,nr,nc),room["castling"],room["ep"])
                if not _in_check(nb,side):out.append((r,c,nr,nc))
    return out

def _update_castling(room,p,r,c,nr,nc,captured):
    rights=room["castling"]
    # A king or its original rook moving permanently forfeits that side's
    # corresponding castling right. Capturing an original rook does the same.
    if p=="K" or (p=="R" and r==7 and c==7) or (captured=="R" and nr==7 and nc==7):
        rights=rights.replace("K","")
    if p=="K" or (p=="R" and r==7 and c==0) or (captured=="R" and nr==7 and nc==0):
        rights=rights.replace("Q","")
    if p=="k" or (p=="r" and r==0 and c==7) or (captured=="r" and nr==0 and nc==7):
        rights=rights.replace("k","")
    if p=="k" or (p=="r" and r==0 and c==0) or (captured=="r" and nr==0 and nc==0):
        rights=rights.replace("q","")
    room["castling"]=rights

def _insufficient_material(board):
    """K vs K, K+B vs K, K+N vs K, K+B vs K+B (same-colour bishops) — dead
    positions where checkmate is impossible for either side."""
    pieces=[p for row in board for p in row if p]
    if any(p.lower() in ("q","r","p") for p in pieces): return False
    minors=[p for p in pieces if p.lower() in ("b","n")]
    if len(minors)==0: return True                 # K vs K
    if len(minors)==1: return True                 # K+minor vs K
    if len(minors)==2 and all(p.lower()=="b" for p in minors):
        # K+B vs K+B is a draw only when both bishops sit on the same square colour.
        squares=[]
        for r in range(8):
            for c in range(8):
                if board[r][c] and board[r][c].lower()=="b": squares.append((r+c)%2)
        if len(squares)==2 and squares[0]==squares[1]: return True
    return False

def _chess_move(room, side, r,c,nr,nc,promo):
    legal=_legal_chess_moves(room,side)
    if (r,c,nr,nc) not in legal:return False,"Bu yurish mumkin emas."
    p=room["board"][r][c]
    old=room["board"]
    nb,captured=_apply_chess(old,(r,c,nr,nc),room["castling"],room["ep"])
    _update_castling(room,p,r,c,nr,nc,captured)
    if p.lower()=="p" and nr in (0,7):
        promo=(promo or "q").lower()
        if promo not in "qrbn":promo="q"
        nb[nr][nc]=promo.upper() if side=="w" else promo
    room["ep"]=[(r+nr)//2,c] if p.lower()=="p" and abs(nr-r)==2 else None
    room["halfmove"]=0 if p.lower()=="p" or captured else room["halfmove"]+1
    if side=="b":room["fullmove"]+=1
    room["board"]=nb; room["turn"]=_opp(side)
    room["last_move"]={"from":[r,c],"to":[nr,nc],"piece":p,"captured":captured}
    room["history"].append(room["last_move"])
    opp=room["turn"]; lm=_legal_chess_moves(room,opp)
    if not lm:
        room["status"]="finished";room["winner"]=side;room["reason"]="checkmate" if _in_check(room["board"],opp) else "stalemate"
    elif room["halfmove"]>=100:
        room["status"]="finished";room["winner"]=None;room["reason"]="50-move draw"
    elif _insufficient_material(room["board"]):
        room["status"]="finished";room["winner"]=None;room["reason"]="insufficient material"
    return True,None

def _client_legal_moves(room):
    """Authoritative move list for the side to move, in [r,c,nr,nc] form, so
    the client highlights exactly what the server rules engine allows —
    including castling, en passant, and check-safety — instead of
    re-implementing (and risking drifting from) the chess/draughts rules
    a second time in JavaScript."""
    if room.get("status")!="playing": return []
    side=room["turn"]
    if room["game"]=="chess":
        return [list(m) for m in _legal_chess_moves(room,side)]
    if room["game"]=="checkers":
        board=room["board"]; forced=room.get("forced_piece")
        if forced:
            r,c=forced
            return [[r,c,x["to"][0],x["to"][1]] for x in _checkers_captures(board,r,c)]
        return [[r,c,x["to"][0],x["to"][1]] for r,c,x in _all_checkers_moves(board,side)]
    return []

# ---------------- Russian Draughts ----------------
def _checkers_captures(board,r,c):
    p=board[r][c]
    if not p:return []
    side=p.lower(); king=p.isupper(); out=[]
    dirs=((-1,-1),(-1,1),(1,-1),(1,1))
    if not king:
        # Russian draughts men capture in all four directions.
        for dr,dc in dirs:
            mr,mc=r+dr,c+dc; lr,lc=r+2*dr,c+2*dc
            if _inside(lr,lc) and board[mr][mc] and board[mr][mc].lower()!=side and board[lr][lc] is None:
                out.append({"to":[lr,lc],"cap":[mr,mc]})
    else:
        for dr,dc in dirs:
            rr,cc=r+dr,c+dc; seen=False; cap=None
            while _inside(rr,cc):
                q=board[rr][cc]
                if q is None:
                    if seen: out.append({"to":[rr,cc],"cap":cap[:]})
                else:
                    if q.lower()==side or seen: break
                    seen=True;cap=[rr,cc]
                rr+=dr;cc+=dc
    return out

def _checkers_simple(board,r,c):
    p=board[r][c]; side=p.lower(); king=p.isupper()
    out=[]; dirs=((-1,-1),(-1,1),(1,-1),(1,1))
    if king:
        for dr,dc in dirs:
            rr,cc=r+dr,c+dc
            while _inside(rr,cc) and board[rr][cc] is None:
                out.append([rr,cc]);rr+=dr;cc+=dc
    else:
        d=-1 if side=="w" else 1
        for dc in (-1,1):
            rr,cc=r+d,c+dc
            if _inside(rr,cc) and board[rr][cc] is None:out.append([rr,cc])
    return out

def _all_checkers_captures(board,side):
    out=[]
    for r in range(8):
        for c in range(8):
            if board[r][c] and board[r][c].lower()==side:
                for x in _checkers_captures(board,r,c):out.append((r,c,x))
    return out

def _all_checkers_moves(board,side):
    caps=_all_checkers_captures(board,side)
    if caps:return caps
    out=[]
    for r in range(8):
        for c in range(8):
            if board[r][c] and board[r][c].lower()==side:
                for to in _checkers_simple(board,r,c):out.append((r,c,{"to":to,"cap":None}))
    return out

def _checkers_move(room,side,r,c,nr,nc):
    b=room["board"]; piece=b[r][c]
    legal=_all_checkers_moves(b,side)
    forced=room.get("forced_piece")
    if forced and [r,c] != forced:
        return False,"Urishni davom ettirish kerak — boshqa dona tanlab bo'lmaydi."
    chosen=None
    for a,cc,x in legal:
        if (a,cc)==(r,c) and x["to"]==[nr,nc]:chosen=x;break
    if not chosen:return False,"Bu yurish mumkin emas. Urish majburiy bo'lishi mumkin."
    b[r][c]=None; b[nr][nc]=piece
    cap=chosen["cap"]
    if cap:b[cap[0]][cap[1]]=None
    # Russian draughts: a man is crowned immediately on reaching the last row.
    # If another capture is available from the landing square, the same piece
    # must continue the capture sequence (including as a newly crowned king).
    if piece=="w" and nr==0:b[nr][nc]="W"
    if piece=="b" and nr==7:b[nr][nc]="B"
    captured_piece = _opp(side) if cap else None
    room["last_move"]={"from":[r,c],"to":[nr,nc],"piece":piece,"captured":captured_piece,"capture":bool(cap)}
    room["history"].append(room["last_move"])
    continuation=_checkers_captures(b,nr,nc) if cap else []
    if continuation:
        room["forced_piece"]=[nr,nc]
        room["turn"]=side
        return True,None
    room["forced_piece"]=None
    room["turn"]=_opp(side)
    # terminal: opponent has no pieces or no legal move
    opp=room["turn"]
    pieces=[p for row in b for p in row if p and p.lower()==opp]
    if not pieces or not _all_checkers_moves(b,opp):
        room["status"]="finished";room["winner"]=side;room["reason"]="no legal moves"
    return True,None

# ---------------- API ----------------
def move(rid,init_data,fr,to,promo="q"):
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"]))
    try:r,c=map(int,fr);nr,nc=map(int,to)
    except:return None,"Yurish koordinatasi noto'g'ri."
    with LOCK:
        room=_room(rid)
        if not room:return None,"Xona topilmadi."
        pl=room["players"].get(uid)
        if not pl:return None,"Siz bu xonada emassiz."
        side=pl.get("side")
        if room["status"]!="playing":return None,"O'yin hali boshlanmadi yoki tugagan."
        if side not in ("w","b") or room["turn"]!=side:return None,"Hozir yurish navbati sizda emas."
        ok,err=(_chess_move(room,side,r,c,nr,nc,promo) if room["game"]=="chess" else _checkers_move(room,side,r,c,nr,nc))
        if not ok:return None,err
        room["version"]+=1;room["updated_at"]=time.time();_save()
        return _public(room,uid),None

def set_style(rid,init_data,style):
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"]));style=(style or "classic").lower()
    if style not in ("classic","crystal","neon"):style="classic"
    with LOCK:
        room=_room(rid)
        if not room or uid not in room["players"]:return None,"Xona topilmadi."
        room["players"][uid]["style"]=style;room["version"]+=1;room["updated_at"]=time.time();_save()
        return _public(room,uid),None

def resign(rid,init_data):
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"]))
    with LOCK:
        room=_room(rid);p=room and room["players"].get(uid)
        if not p:return None,"Siz bu xonada emassiz."
        if room["status"]=="playing":
            room["status"]="finished";room["reason"]="resignation"
            if room["game"] in ("memory","tictactoe"):
                others=[k for k in room["players"] if k!=uid]
                room["winner"]=others[0] if others else None
            else:
                room["winner"]=_opp(p["side"])
            room["version"]+=1;room["updated_at"]=time.time();_save()
        return _public(room,uid),None


def add_chat(rid, init_data, text, client_id=""):
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    text=(text or "").strip()
    if not text:return None,"Xabar bo'sh."
    if len(text)>500:text=text[:500]
    uid=str(int(user["id"]))
    with LOCK:
        room=_room(rid)
        if not room or uid not in room["players"]:return None,"Siz bu xonada emassiz."
        client_id=(client_id or "").strip()[:100]
        if client_id:
            old_id=room.get("chat_keys",{}).get(client_id)
            if old_id:
                for item in room.get("chat",[]):
                    if item.get("id")==old_id:return item,None
        item={"id":uuid.uuid4().hex[:12],"user_id":int(uid),"name":user.get("first_name") or user.get("username") or uid,"text":text,"ts":time.time()}
        room.setdefault("chat",[]).append(item)
        room["chat"]=room["chat"][-MAX_CHAT:]
        if client_id:
            keys=room.setdefault("chat_keys",{});keys[client_id]=item["id"]
            if len(keys)>MAX_CHAT_CLIENT_KEYS:
                for key in list(keys)[:len(keys)-MAX_CHAT_CLIENT_KEYS]:keys.pop(key,None)
        room["version"]+=1;room["updated_at"]=time.time();_save()
        return item,None

def get_chat(rid, init_data, after_id=""):
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"]))
    with LOCK:
        room=_room(rid)
        if not room or uid not in room["players"]:return None,"Xona topilmadi."
        items=list(room.get("chat",[]))
    if after_id:
        for i,x in enumerate(items):
            if x.get("id")==after_id:
                items=items[i+1:];break
    return items[-100:],None

def signal(rid, init_data, target, payload):
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"])); target=str(int(target or 0))
    if not isinstance(payload,dict) or payload.get("type") not in ("offer","answer","ice","media"):
        return None,"Signal noto'g'ri."
    with LOCK:
        room=_room(rid)
        if not room or uid not in room["players"] or target not in room["players"] or target==uid:
            return None,"Signal qabul qiluvchisi noto'g'ri."
        box=room["signals"].setdefault(target,[])
        box.append({"from":int(user["id"]),"payload":payload,"ts":time.time()})
        room["signals"][target]=box[-MAX_SIGNAL_ITEMS:]
        room["updated_at"]=time.time(); _save()
    return True,None

def get_signals(rid, init_data):
    user=_verify(init_data)
    if not user:return None,"Sessiya xatosi."
    uid=str(int(user["id"]))
    with LOCK:
        room=_room(rid)
        if not room or uid not in room["players"]:return None,"Xona topilmadi."
        items=room["signals"].get(uid,[])
        room["signals"][uid]=[]
        return items,None

def rematch(rid,init_data):
    user=_verify(init_data)
    if not user:return None,"Sessiya tasdiqlanmadi."
    uid=str(int(user["id"]))
    with LOCK:
        room=_room(rid)
        if not room or uid not in room["players"]:return None,"Xona topilmadi."
        room["rematch_votes"][uid]=True
        if len(room["rematch_votes"])==2:
            gkey=room["game"]
            if gkey=="memory":
                room.update({
                    "status":"lobby","winner":None,"reason":None,"turn":None,"board":[],
                    "scores":{}, "last_reveal":None, "memory_ready":{}, "rematch_votes":{},
                })
            elif gkey=="tictactoe":
                room.update({
                    "status":"playing","winner":None,"reason":None,"turn":"x",
                    "board":[None]*9,"win_line":None,"last_move":None,"rematch_votes":{},
                })
            else:
                room["board"]=_initial_chess() if gkey=="chess" else _initial_checkers()
                room.update({"status":"playing","winner":None,"reason":None,"turn":"w","castling":"KQkq","ep":None,"halfmove":0,"fullmove":1,"history":[],"forced_piece":None,"last_move":None,"rematch_votes":{}})
        room["version"]+=1;room["updated_at"]=time.time();_save()
        return _public(room,uid),None

def handle_api(handler):
    from urllib.parse import parse_qs,urlsplit
    path=urlsplit(handler.path).path; qs=parse_qs(urlsplit(handler.path).query)
    init=handler.headers.get("X-Telegram-Init-Data",""); rid=qs.get("room",[""])[0]
    if path=="/api/game/join":
        d,e=join(rid,init)
    elif path=="/api/game/state":
        u=_verify(init); d,e=(None,"Sessiya xatosi.") if not u else ((lambda r: (_public(r,str(int(u["id"]))),None) if r and str(int(u["id"])) in r["players"] else (None,"Xona topilmadi yoki siz unda emassiz."))(_room(rid)))
    elif path=="/api/game/choose":
        d,e=choose(rid,init,qs.get("side",[""])[0],qs.get("style",["classic"])[0])
    elif path=="/api/game/signals":
        d,e=get_signals(rid,init)
    elif path=="/api/game/chat":
        d,e=get_chat(rid,init,qs.get("after",[""])[0])
    else:
        return _json(handler,404,{"ok":False,"error":"Not found"})
    return _json(handler,200 if not e else 400,{"ok":not bool(e),"data":d,"error":e})

def handle_post(handler):
    from urllib.parse import urlsplit
    try:
        n=int(handler.headers.get("Content-Length",0)); body=json.loads(handler.rfile.read(n).decode())
    except Exception:return _json(handler,400,{"ok":False,"error":"Noto'g'ri JSON."})
    init=handler.headers.get("X-Telegram-Init-Data","") or body.get("init_data","")
    p=urlsplit(handler.path).path; rid=str(body.get("room",""))
    if p=="/api/game/join":d,e=join(rid,init)
    elif p=="/api/game/move":d,e=move(rid,init,body.get("from"),body.get("to"),body.get("promotion","q"))
    elif p=="/api/game/style":d,e=set_style(rid,init,body.get("style","classic"))
    elif p=="/api/game/resign":d,e=resign(rid,init)
    elif p=="/api/game/rematch":d,e=rematch(rid,init)
    elif p=="/api/game/signal":d,e=signal(rid,init,body.get("target_user_id"),body.get("payload") or {})
    elif p=="/api/game/chat":d,e=add_chat(rid,init,body.get("text",""),body.get("client_id",""))
    elif p=="/api/game/memory/config":d,e=memory_set_config(rid,init,body.get("size",MEMORY_DEFAULT_SIZE),body.get("symbols",MEMORY_DEFAULT_SYMBOLS))
    elif p=="/api/game/memory/ready":d,e=memory_ready(rid,init)
    elif p=="/api/game/memory/flip":d,e=memory_flip(rid,init,body.get("index"))
    elif p=="/api/game/tictactoe/mark":d,e=choose_mark(rid,init,body.get("mark",""))
    elif p=="/api/game/tictactoe/move":d,e=tictactoe_move(rid,init,body.get("index"))
    else:return _json(handler,404,{"ok":False,"error":"Not found"})
    return _json(handler,200 if not e else 400,{"ok":not bool(e),"data":d,"error":e})

def _json(handler,status,data):
    body=json.dumps(data,ensure_ascii=False).encode()
    handler.send_response(status);handler.send_header("Content-Type","application/json; charset=utf-8")
    handler.send_header("Cache-Control","no-store");handler.send_header("Content-Length",str(len(body)));handler.end_headers();handler.wfile.write(body)

def serve_static(handler):
    from urllib.parse import urlsplit
    path=urlsplit(handler.path).path
    mapping={"/miniapp/game/":"index.html","/miniapp/game/index.html":"index.html","/miniapp/game/app.js":"app.js","/miniapp/game/style.css":"style.css"}
    name=mapping.get(path)
    if not name:return False
    fp=os.path.join(os.path.dirname(os.path.abspath(__file__)),"webapp","game",name)
    try: body=open(fp,"rb").read()
    except OSError:return False
    ctype={"html":"text/html","js":"application/javascript","css":"text/css"}[name.rsplit(".",1)[-1]]
    handler.send_response(200);handler.send_header("Content-Type",ctype+"; charset=utf-8")
    handler.send_header("Cache-Control","no-cache")
    if name=="index.html":
        handler.send_header("Permissions-Policy","camera=(self), microphone=(self)")
        text=body.decode("utf-8")
        turn_cfg=(f"<script>window.GAME_TURN_URL={json.dumps(config.GAME_TURN_URL)};"
                  f"window.GAME_TURN_USERNAME={json.dumps(config.GAME_TURN_USERNAME)};"
                  f"window.GAME_TURN_CREDENTIAL={json.dumps(config.GAME_TURN_CREDENTIAL)};</script>")
        text=text.replace("</head>",turn_cfg+"</head>",1)
        body=text.encode("utf-8")
    handler.send_header("Content-Length",str(len(body)));handler.end_headers();handler.wfile.write(body);return True
