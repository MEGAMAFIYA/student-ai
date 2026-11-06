"""🧱 Quoridor — sof o'yin mantiqi (Telegram'ga bog'liq EMAS).

Bu modul faqat qoidalarni biladi: doska, yurish, devor, yo'l tekshiruvi (BFS),
g'olibni aniqlash va matnli doska chizish. Telegram bilan bog'lash
`chat_games.py` ichida (inline tugmalar orqali) amalga oshirilgan.

Koordinatalar: (r, c), r — qator (0 = eng yuqori), c — ustun (0 = eng chap).
Ekranda: qatorlar 1..9 (yuqoridan pastga), ustunlar a..i.

1-o'yinchi ("1", 🔵) pastdan (8, 4) boshlaydi va 0-qatorga yetishi kerak.
2-o'yinchi ("2", 🔴) tepadan (0, 4) boshlaydi va 8-qatorga yetishi kerak.

Devor — {"r": r, "c": c, "o": "h"|"v"}; (r, c) — 2×2 katak blokining
chap-yuqori katagi (0..7). "h" devor shu blokning yuqori va pastki qatorlari
orasidan, "v" devor chap va o'ng ustunlari orasidan o'tadi (2 katak uzunlikda).
"""
from collections import deque

SIZE = 9
WALL_ANCHORS = SIZE - 1          # devor langari 0..7
WALLS_PER_PLAYER = 10
START = {"1": (8, 4), "2": (0, 4)}
GOAL_ROW = {"1": 0, "2": 8}
DIRS = ((-1, 0), (1, 0), (0, -1), (0, 1))
COLS = "abcdefghi"


def other(pid):
    return "2" if str(pid) == "1" else "1"


def new_state():
    """Yangi o'yin uchun boshlang'ich holat (room lug'atiga qo'shiladigan kalitlar)."""
    return {
        "pawns": {"1": list(START["1"]), "2": list(START["2"])},
        "walls": [],
        "walls_left": {"1": WALLS_PER_PLAYER, "2": WALLS_PER_PLAYER},
        "wall_draft": {},
        "turn": "1",
        "winner": None,
        "reason": None,
        "last": None,
    }


def coord(r, c):
    """(7, 4) -> 'e8' — foydalanuvchiga ko'rsatiladigan nom."""
    return f"{COLS[c]}{r + 1}"


# ---------------------------------------------------------------- devorlar
def blocked_edges(walls):
    """Devorlar yopib qo'ygan katakdan-katakka o'tishlar (ikki yo'nalishda)."""
    edges = set()
    for w in walls:
        r, c, o = w["r"], w["c"], w["o"]
        if o == "h":
            for cc in (c, c + 1):
                edges.add(((r, cc), (r + 1, cc)))
                edges.add(((r + 1, cc), (r, cc)))
        else:
            for rr in (r, r + 1):
                edges.add(((rr, c), (rr, c + 1)))
                edges.add(((rr, c + 1), (rr, c)))
    return edges


def _in_board(r, c):
    return 0 <= r < SIZE and 0 <= c < SIZE


def has_path(edges, start, goal_row):
    """BFS: start katakdan goal_row qatoriga devorlarni aylanib o'tib yetish mumkinmi?
    (Rasmiy qoidaga ko'ra raqib piyodasi yo'lni to'smaydi.)"""
    start = tuple(start)
    seen = {start}
    q = deque([start])
    while q:
        r, c = q.popleft()
        if r == goal_row:
            return True
        for dr, dc in DIRS:
            nr, nc = r + dr, c + dc
            if _in_board(nr, nc) and (nr, nc) not in seen and ((r, c), (nr, nc)) not in edges:
                seen.add((nr, nc))
                q.append((nr, nc))
    return False


def wall_error(state, pid, r, c, o):
    """Devor qo'yish mumkin bo'lmasa — sababi (matn), mumkin bo'lsa — None."""
    pid = str(pid)
    if o not in ("h", "v"):
        return "Devor yo‘nalishi noto‘g‘ri."
    if not (isinstance(r, int) and isinstance(c, int)
            and 0 <= r < WALL_ANCHORS and 0 <= c < WALL_ANCHORS):
        return "Devor doska ichida bo‘lishi kerak."
    if state["walls_left"].get(pid, 0) <= 0:
        return "Devorlaringiz qolmagan."
    for w in state["walls"]:
        if w["r"] == r and w["c"] == c and w["o"] != o:
            return "Devorlar kesishmasligi kerak."
        if w["o"] == o:
            if o == "h" and w["r"] == r and abs(w["c"] - c) <= 1:
                return "Bu joyda devor allaqachon bor (ustma-ust tushadi)."
            if o == "v" and w["c"] == c and abs(w["r"] - r) <= 1:
                return "Bu joyda devor allaqachon bor (ustma-ust tushadi)."
    edges = blocked_edges(state["walls"] + [{"r": r, "c": c, "o": o}])
    for p in ("1", "2"):
        if not has_path(edges, state["pawns"][p], GOAL_ROW[p]):
            return "Bu devor o‘yinchining maqsad qatoriga boradigan yo‘lni butunlay to‘sib qo‘yadi."
    return None


# ----------------------------------------------------------------- yurishlar
def legal_moves(state, pid):
    """pid piyodasi shu navbatda bora oladigan barcha kataklar [(r, c), ...]
    (oddiy qadam, raqib ustidan sakrash, yon tomonga sakrash)."""
    pid = str(pid)
    edges = blocked_edges(state["walls"])
    pos = tuple(state["pawns"][pid])
    opp = tuple(state["pawns"][other(pid)])
    moves = []
    for dr, dc in DIRS:
        n = (pos[0] + dr, pos[1] + dc)
        if not _in_board(*n) or (pos, n) in edges:
            continue
        if n != opp:
            moves.append(n)
            continue
        # Raqib yonma-yon: to'g'ri sakrash
        j = (n[0] + dr, n[1] + dc)
        if _in_board(*j) and (n, j) not in edges:
            moves.append(j)
            continue
        # Orqasi devor yoki doska chegarasi — yon tomonlarga
        for pr, pc in ((dc, dr), (-dc, -dr)):  # perpendikulyar yo'nalishlar
            s = (n[0] + pr, n[1] + pc)
            if _in_board(*s) and (n, s) not in edges and s != pos:
                moves.append(s)
    seen, out = set(), []
    for m in moves:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _check_turn(state, pid):
    if state.get("winner"):
        return "O‘yin tugagan."
    if str(pid) != state.get("turn"):
        return "Hozir navbat sizda emas."
    return None


def apply_move(state, pid, r, c):
    """Piyodani (r, c) ga suradi. (ok, xato_matni) qaytaradi."""
    pid = str(pid)
    err = _check_turn(state, pid)
    if err:
        return False, err
    if (r, c) not in legal_moves(state, pid):
        return False, "Bu katakka yurib bo‘lmaydi."
    old = tuple(state["pawns"][pid])
    state["pawns"][pid] = [r, c]
    state["wall_draft"].pop(pid, None)
    state["last"] = f"{'🔵' if pid == '1' else '🔴'} {coord(*old)} → {coord(r, c)}"
    if r == GOAL_ROW[pid]:
        state["winner"] = pid
        state["reason"] = "goal"
    else:
        state["turn"] = other(pid)
    return True, None


def apply_wall(state, pid, r, c, o):
    """Devor qo'yadi. (ok, xato_matni) qaytaradi."""
    pid = str(pid)
    err = _check_turn(state, pid)
    if err:
        return False, err
    err = wall_error(state, pid, r, c, o)
    if err:
        return False, err
    state["walls"].append({"r": r, "c": c, "o": o})
    state["walls_left"][pid] -= 1
    state["wall_draft"].pop(pid, None)
    state["last"] = (f"{'🔵' if pid == '1' else '🔴'} "
                     f"{'gorizontal' if o == 'h' else 'vertikal'} devor ({coord(r, c)} bloki)")
    state["turn"] = other(pid)
    return True, None


def surrender(state, pid):
    pid = str(pid)
    if state.get("winner"):
        return False, "O‘yin tugagan."
    state["winner"] = other(pid)
    state["reason"] = "surrender"
    state["wall_draft"] = {}
    state["last"] = f"{'🔵' if pid == '1' else '🔴'} taslim bo‘ldi"
    return True, None


# --------------------------------------------------------------- chizish
def render_board(state, draft=None):
    """Doskani monospace matn (<pre> ichida ko'rsatish uchun) sifatida chizadi.

    1 / 2 — piyodalar, │ va ─ — devorlar, ║ va ═ — hali qo'yilmagan (ko'rib chiqilayotgan) devor.
    """
    W = SIZE * 2 - 1
    cell_rows = [[" "] * W for _ in range(SIZE)]
    gap_rows = [[" "] * W for _ in range(SIZE - 1)]
    for r in range(SIZE):
        for c in range(SIZE):
            cell_rows[r][2 * c] = "·"

    def put(walls, h_ch, v_ch):
        for w in walls:
            r, c, o = w["r"], w["c"], w["o"]
            if o == "h":
                for i in (2 * c, 2 * c + 1, 2 * c + 2):
                    gap_rows[r][i] = h_ch
            else:
                cell_rows[r][2 * c + 1] = v_ch
                cell_rows[r + 1][2 * c + 1] = v_ch
                gap_rows[r][2 * c + 1] = v_ch

    put(state["walls"], "─", "│")
    if draft:
        put([draft], "═", "║")
    for pid, pos in state["pawns"].items():
        cell_rows[pos[0]][2 * pos[1]] = pid

    lines = ["   " + " ".join(COLS)]
    for r in range(SIZE):
        lines.append(f"{r + 1}  " + "".join(cell_rows[r]))
        if r < SIZE - 1:
            lines.append("   " + "".join(gap_rows[r]))
    return "\n".join(lines)
