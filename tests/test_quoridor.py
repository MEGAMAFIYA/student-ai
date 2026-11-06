import json
import threading
import unittest
from unittest.mock import patch

from _stub_telegram import install_stubs
install_stubs()
import game
import chat_games
import quoridor as Q


class U:
    def __init__(self, uid, name, username=None):
        self.id = uid
        self.full_name = name
        self.username = username


def fresh_state():
    s = Q.new_state()
    return s


def wall(r, c, o):
    return {"r": r, "c": c, "o": o}


class EngineTests(unittest.TestCase):
    def test_initial_state(self):
        s = fresh_state()
        self.assertEqual(s["pawns"], {"1": [8, 4], "2": [0, 4]})
        self.assertEqual(s["walls_left"], {"1": 10, "2": 10})
        self.assertEqual(s["turn"], "1")
        self.assertEqual(len(Q.legal_moves(s, "1")), 3)  # chetda emas: yuqori, chap, o'ng (pastda doska chegarasi)
        self.assertEqual(len(Q.legal_moves(s, "2")), 3)

    def test_simple_move_and_turn_switch(self):
        s = fresh_state()
        ok, err = Q.apply_move(s, "1", 7, 4)
        self.assertTrue(ok, err)
        self.assertEqual(s["pawns"]["1"], [7, 4])
        self.assertEqual(s["turn"], "2")

    def test_wrong_turn_rejected(self):
        s = fresh_state()
        ok, err = Q.apply_move(s, "2", 1, 4)
        self.assertFalse(ok)
        self.assertEqual(s["pawns"]["2"], [0, 4])
        self.assertEqual(s["turn"], "1")

    def test_diagonal_and_far_moves_rejected(self):
        s = fresh_state()
        self.assertFalse(Q.apply_move(s, "1", 7, 3)[0])
        self.assertFalse(Q.apply_move(s, "1", 6, 4)[0])
        self.assertFalse(Q.apply_move(s, "1", 9, 4)[0])

    def test_cannot_leave_board(self):
        s = fresh_state()
        s["pawns"]["1"] = [8, 0]
        self.assertNotIn((8, -1), Q.legal_moves(s, "1"))
        self.assertNotIn((9, 0), Q.legal_moves(s, "1"))

    def test_wall_blocks_movement(self):
        s = fresh_state()
        s["walls"].append(wall(7, 3, "h"))  # (7,3),(7,4) bilan (8,3),(8,4) orasi
        self.assertNotIn((7, 4), Q.legal_moves(s, "1"))
        s["walls"] = [wall(7, 4, "h")]       # (7,4)-(8,4) va (7,5)-(8,5)
        self.assertNotIn((7, 4), Q.legal_moves(s, "1"))
        s["walls"] = [wall(7, 5, "h")]       # (7,5)-(8,5) va (7,6)-(8,6): (8,4) ga ta'sir qilmaydi
        self.assertIn((7, 4), Q.legal_moves(s, "1"))

    def test_vertical_wall_blocks_sideways(self):
        s = fresh_state()
        s["walls"].append(wall(7, 3, "v"))  # (7,3)|(7,4) va (8,3)|(8,4)
        self.assertNotIn((8, 3), Q.legal_moves(s, "1"))
        self.assertIn((8, 5), Q.legal_moves(s, "1"))

    def test_straight_jump(self):
        s = fresh_state()
        s["pawns"] = {"1": [5, 4], "2": [4, 4]}
        moves = Q.legal_moves(s, "1")
        self.assertIn((3, 4), moves)          # sakrash
        self.assertNotIn((4, 4), moves)       # raqib katagiga turib bo'lmaydi
        ok, err = Q.apply_move(s, "1", 3, 4)
        self.assertTrue(ok, err)

    def test_jump_blocked_by_wall_allows_side_steps(self):
        s = fresh_state()
        s["pawns"] = {"1": [5, 4], "2": [4, 4]}
        s["walls"].append(wall(3, 4, "h"))   # (3,4) bilan (4,4) orasi yopiq
        moves = Q.legal_moves(s, "1")
        self.assertNotIn((3, 4), moves)
        self.assertIn((4, 3), moves)
        self.assertIn((4, 5), moves)

    def test_jump_blocked_by_board_edge_allows_side_steps(self):
        s = fresh_state()
        s["pawns"] = {"1": [1, 4], "2": [0, 4]}
        moves = Q.legal_moves(s, "1")
        self.assertNotIn((-1, 4), moves)
        self.assertIn((0, 3), moves)
        self.assertIn((0, 5), moves)

    def test_side_step_respects_walls(self):
        s = fresh_state()
        s["pawns"] = {"1": [5, 4], "2": [4, 4]}
        s["walls"] += [wall(3, 4, "h"), wall(3, 3, "v")]  # orqa yopiq; (4,3)|(4,4) oralig'i ham yopiq emas?
        # vertikal devor (3,3): (3,3)|(3,4) va (4,3)|(4,4) -> raqib chap tomoniga o'tish yopiq
        moves = Q.legal_moves(s, "1")
        self.assertNotIn((4, 3), moves)  # raqib (4,4) dan (4,3) ga devor bor
        self.assertIn((4, 5), moves)

    def test_win_bottom_player_reaches_top(self):
        s = fresh_state()
        s["pawns"]["1"] = [1, 4]
        s["pawns"]["2"] = [4, 0]
        ok, err = Q.apply_move(s, "1", 0, 4)
        self.assertTrue(ok, err)
        self.assertEqual(s["winner"], "1")
        # g'alabadan keyin hech kim yura olmaydi
        self.assertFalse(Q.apply_move(s, "2", 5, 0)[0])
        self.assertFalse(Q.apply_wall(s, "2", 0, 0, "h")[0])

    def test_win_top_player_reaches_bottom(self):
        s = fresh_state()
        s["turn"] = "2"
        s["pawns"] = {"1": [4, 0], "2": [7, 4]}
        ok, err = Q.apply_move(s, "2", 8, 4)
        self.assertTrue(ok, err)
        self.assertEqual(s["winner"], "2")

    def test_win_by_jumping_onto_goal_row(self):
        s = fresh_state()
        s["pawns"] = {"1": [2, 4], "2": [1, 4]}
        ok, err = Q.apply_move(s, "1", 0, 4)
        self.assertTrue(ok, err)
        self.assertEqual(s["winner"], "1")

    # ----- devorlar
    def test_wall_placement_counts_and_turn(self):
        s = fresh_state()
        ok, err = Q.apply_wall(s, "1", 3, 3, "h")
        self.assertTrue(ok, err)
        self.assertEqual(s["walls_left"], {"1": 9, "2": 10})
        self.assertEqual(s["turn"], "2")
        self.assertEqual(len(s["walls"]), 1)

    def test_wall_wrong_turn(self):
        s = fresh_state()
        self.assertFalse(Q.apply_wall(s, "2", 3, 3, "h")[0])
        self.assertEqual(s["walls"], [])
        self.assertEqual(s["walls_left"], {"1": 10, "2": 10})

    def test_wall_out_of_bounds(self):
        s = fresh_state()
        for r, c in [(8, 0), (0, 8), (-1, 0), (0, -1), (9, 9)]:
            self.assertIsNotNone(Q.wall_error(s, "1", r, c, "h"), (r, c))
            self.assertIsNotNone(Q.wall_error(s, "1", r, c, "v"), (r, c))
        self.assertIsNone(Q.wall_error(s, "1", 7, 7, "h"))
        self.assertIsNotNone(Q.wall_error(s, "1", 0, 0, "x"))

    def test_wall_overlap_same_and_half(self):
        s = fresh_state()
        s["walls"].append(wall(4, 4, "h"))
        self.assertIsNotNone(Q.wall_error(s, "1", 4, 4, "h"))   # aynan bir xil
        self.assertIsNotNone(Q.wall_error(s, "1", 4, 3, "h"))   # yarmi ustma-ust
        self.assertIsNotNone(Q.wall_error(s, "1", 4, 5, "h"))
        self.assertIsNone(Q.wall_error(s, "1", 4, 6, "h"))      # yonma-yon — mumkin
        self.assertIsNone(Q.wall_error(s, "1", 4, 2, "h"))
        self.assertIsNone(Q.wall_error(s, "1", 3, 4, "h"))      # boshqa chiziq
        s["walls"] = [wall(4, 4, "v")]
        self.assertIsNotNone(Q.wall_error(s, "1", 3, 4, "v"))
        self.assertIsNotNone(Q.wall_error(s, "1", 5, 4, "v"))
        self.assertIsNone(Q.wall_error(s, "1", 6, 4, "v"))

    def test_walls_cannot_cross(self):
        s = fresh_state()
        s["walls"].append(wall(4, 4, "h"))
        self.assertIsNotNone(Q.wall_error(s, "1", 4, 4, "v"))
        # T-shaklida tegish (kesishmasdan) mumkin
        self.assertIsNone(Q.wall_error(s, "1", 3, 4, "v"))
        self.assertIsNone(Q.wall_error(s, "1", 4, 5, "v"))

    def test_no_walls_left(self):
        s = fresh_state()
        s["walls_left"]["1"] = 0
        self.assertIsNotNone(Q.wall_error(s, "1", 3, 3, "h"))
        ok, err = Q.apply_wall(s, "1", 3, 3, "h")
        self.assertFalse(ok)
        self.assertEqual(s["walls"], [])

    def test_ten_walls_then_blocked(self):
        s = fresh_state()
        placed = 0
        for i in range(10):
            s["turn"] = "1"
            ok, err = Q.apply_wall(s, "1", 1 + (i % 2) * 3, i % 7, "h") if False else (None, None)
        # 10 ta ruxsat etilgan devor: turli qatorlarda yonma-yon
        spots = [(1, 0), (1, 2), (1, 4), (1, 6), (3, 0), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2)]
        for (r, c) in spots:
            s["turn"] = "1"
            ok, err = Q.apply_wall(s, "1", r, c, "h")
            self.assertTrue(ok, f"{(r, c)} {err}")
            placed += 1
        self.assertEqual(placed, 10)
        self.assertEqual(s["walls_left"]["1"], 0)
        s["turn"] = "1"
        self.assertFalse(Q.apply_wall(s, "1", 5, 4, "h")[0])

    def test_wall_cannot_fully_block_path(self):
        s = fresh_state()
        # Qator 0|1 chizig'i 0..7 ustunlar bo'ylab yopiq; 8-ustunda yagona yo'l qoldi (2-o'yinchi tepada).
        s["walls"] = [wall(0, 0, "h"), wall(0, 2, "h"), wall(0, 4, "h"), wall(0, 6, "h")]
        # 8-ustunga kirish yo'lini (0,7)|(0,8) vertikal devor bilan yopish — butunlay to'sadi -> rad
        err = Q.wall_error(s, "1", 0, 7, "v")
        self.assertIsNotNone(err)
        self.assertIn("yo‘lni", err)
        s["turn"] = "1"
        ok, err = Q.apply_wall(s, "1", 0, 7, "v")
        self.assertFalse(ok)
        self.assertEqual(len(s["walls"]), 4)
        self.assertEqual(s["walls_left"]["1"], 10)
        self.assertEqual(s["turn"], "1")  # rad etilgan devor navbatni o'zgartirmaydi
        # shu qatorda boshqa, yo'lni yopmaydigan devor esa mumkin
        self.assertIsNone(Q.wall_error(s, "1", 4, 4, "h"))

    def test_wall_cannot_enclose_pawn_in_pocket(self):
        s = fresh_state()
        s["pawns"] = {"1": [8, 0], "2": [0, 4]}
        s["walls"] = [wall(7, 0, "h")]                       # (8,0) va (8,1) tepasi yopiq
        err = Q.wall_error(s, "2", 7, 1, "v")                # (8,1)|(8,2) yopiladi -> cho'ntak
        self.assertIsNotNone(err)
        self.assertIn("yo‘lni", err)
        self.assertIsNone(Q.wall_error(s, "2", 5, 1, "v"))   # boshqa joy mumkin

    def test_blocking_checks_both_players(self):
        s = fresh_state()
        # 1-o'yinchi (8,4) atrofini yopib, o'zini qamab qo'yadigan devorni ham rad etadi
        s["walls"] = [wall(7, 3, "h")]                        # (8,3),(8,4) tepasi yopiq
        s["walls"].append(wall(7, 5, "h"))                    # (8,5),(8,6) tepasi yopiq
        err = Q.wall_error(s, "2", 7, 2, "v")                 # (7,2)|(7,3),(8,2)|(8,3): chap yo'l
        self.assertIsNone(err)                                # hali (8,7),(8,8) orqali chiqish bor
        s["walls"].append(wall(7, 2, "v"))
        err = Q.wall_error(s, "2", 7, 6, "v")                 # (8,6)|(8,7) yopilsa — butunlay qamaladi
        self.assertIsNotNone(err)

    def test_has_path_basic(self):
        self.assertTrue(Q.has_path(set(), (8, 4), 0))
        # to'liq gorizontal to'siq (8 ta ustun yopiq, bitta ochiq)
        walls = [wall(3, 0, "h"), wall(3, 2, "h"), wall(3, 4, "h"), wall(3, 6, "h")]
        e = Q.blocked_edges(walls)
        self.assertTrue(Q.has_path(e, (8, 4), 0))  # 9-ustun (index 8) ochiq
        walls.append(wall(3, 7, "h")) if False else None

    def test_has_path_fully_blocked(self):
        # Qator 3 va 4 orasi 9 ta ustun bo'ylab yopiq (4 ta devor = 8 ustun + 1 ta qolgan ustun)
        edges = Q.blocked_edges([wall(3, 0, "h"), wall(3, 2, "h"), wall(3, 4, "h"), wall(3, 6, "h")])
        for c in range(8):
            self.assertIn(((3, c), (4, c)), edges)
        self.assertNotIn(((3, 8), (4, 8)), edges)
        edges |= {((3, 8), (4, 8)), ((4, 8), (3, 8))}
        self.assertFalse(Q.has_path(edges, (8, 4), 0))

    def test_surrender(self):
        s = fresh_state()
        ok, err = Q.surrender(s, "1")
        self.assertTrue(ok)
        self.assertEqual(s["winner"], "2")
        self.assertFalse(Q.surrender(s, "2")[0])

    def test_render_board_shape(self):
        s = fresh_state()
        s["walls"].append(wall(3, 3, "h"))
        s["walls"].append(wall(5, 5, "v"))
        txt = Q.render_board(s)
        lines = txt.split("\n")
        self.assertEqual(len(lines), 1 + 9 + 8)
        self.assertIn("a b c d e f g h i", lines[0])
        self.assertIn("1", lines[1 + 16])   # 1-o'yinchi pastki qatorda
        self.assertIn("2", lines[1])        # 2-o'yinchi yuqorida
        self.assertIn("─", txt)
        self.assertIn("│", txt)
        self.assertNotIn("═", txt)
        prev = Q.render_board(s, draft=wall(0, 0, "h"))
        self.assertIn("═", prev)


class FullGameFlowTests(unittest.TestCase):
    """chat_games orqali Telegram tugmalari ssenariysi (xotirada, tarmoqsiz)."""

    def setUp(self):
        p = patch.object(game, "_save", lambda: None)
        p.start()
        self.addCleanup(p.stop)
        self.ali = U(101, "Ali Valiyev", "ali")
        self.vali = U(202, "Vali", None)
        self.guest = U(303, "Mehmon", "guest")
        self.rid = game.create_room("quoridor", self.ali.id)
        self.addCleanup(lambda: game.ROOMS.pop(self.rid, None))
        self.room = game.ROOMS[self.rid]
        chat_games.q_set_profile(self.room, self.ali)

    def act(self, user, action, *args):
        return chat_games.quoridor_dispatch(self.room, user, action, list(args))

    def start(self):
        ok, err = chat_games.quoridor_join(self.room, self.vali.id, self.vali)
        self.assertTrue(ok, err)

    def kb_texts(self):
        _, kb = chat_games.render(self.room)
        return [b.text for row in kb.inline_keyboard for b in row]

    def kb_data(self):
        _, kb = chat_games.render(self.room)
        return [b.callback_data for row in kb.inline_keyboard for b in row]

    def test_lobby_render_and_join(self):
        text, kb = chat_games.render(self.room)
        self.assertIn("QUORIDOR", text)
        self.assertIn("@ali", text)
        self.assertIn("kutilmoqda", text)
        self.assertIn("👥 Qo‘shilish", self.kb_texts())
        # yaratuvchi o'zi bilan o'ynay olmaydi
        ok, err = chat_games.quoridor_join(self.room, self.ali.id, self.ali)
        self.assertFalse(ok)
        self.assertEqual(self.room["status"], "lobby")
        self.start()
        self.assertEqual(self.room["status"], "playing")
        self.assertEqual(self.room["turn"], "1")
        # uchinchi kishi qo'shila olmaydi
        ok, err = chat_games.quoridor_join(self.room, self.guest.id, self.guest)
        self.assertFalse(ok)
        self.assertNotIn(str(self.guest.id), self.room["players"])
        text, _ = chat_games.render(self.room)
        self.assertIn("Navbat: 1-o‘yinchi", text)

    def test_moves_before_second_player_rejected(self):
        ok, err = self.act(self.ali, "qm", "74")
        self.assertFalse(ok)
        self.assertEqual(self.room["pawns"]["1"], [8, 4])

    def test_turns_alternate_and_outsiders_blocked(self):
        self.start()
        ok, err = self.act(self.vali, "qm", "14")          # navbat 1-o'yinchida
        self.assertFalse(ok)
        ok, err = self.act(self.guest, "qm", "74")         # begona
        self.assertFalse(ok)
        self.assertIn("emassiz", err)
        self.assertTrue(self.act(self.ali, "qm", "74")[0])
        self.assertEqual(self.room["turn"], "2")
        self.assertFalse(self.act(self.ali, "qm", "64")[0])  # navbat Vali'da
        self.assertTrue(self.act(self.vali, "qm", "14")[0])
        self.assertEqual(self.room["pawns"], {"1": [7, 4], "2": [1, 4]})
        self.assertEqual(self.room["turn"], "1")

    def test_illegal_and_malformed_moves(self):
        self.start()
        for bad in ["73", "64", "99", "ab", "", "-1"]:
            ok, err = self.act(self.ali, "qm", bad)
            self.assertFalse(ok, bad)
        ok, err = chat_games.quoridor_dispatch(self.room, self.ali, "qm", [])
        self.assertFalse(ok)
        self.assertEqual(self.room["pawns"]["1"], [8, 4])
        self.assertEqual(self.room["turn"], "1")

    def test_wall_flow_with_buttons_and_counts(self):
        self.start()
        self.assertIn("🧱 Devor qo‘yish (10)", self.kb_texts())
        self.assertTrue(self.act(self.ali, "qd")[0])
        self.assertIn("cg:qp:" + self.rid, self.kb_data()) if False else None
        # tanlovlar
        self.assertTrue(self.act(self.ali, "qo", "v")[0])
        self.assertTrue(self.act(self.ali, "qr", "3")[0])
        self.assertTrue(self.act(self.ali, "qc", "4")[0])
        self.assertIn(f"cg:qp:{self.rid}", self.kb_data())   # tasdiqlash tugmasi paydo bo'ladi
        text, _ = chat_games.render(self.room)
        self.assertIn("║", text)                              # ko'rib chiqilayotgan devor doskada
        self.assertTrue(self.act(self.ali, "qp")[0])
        self.assertEqual(self.room["walls"], [{"r": 3, "c": 4, "o": "v"}])
        self.assertEqual(self.room["walls_left"], {"1": 9, "2": 10})
        self.assertEqual(self.room["turn"], "2")
        text, _ = chat_games.render(self.room)
        self.assertIn("│", text)
        self.assertIn("🧱 9", text)
        # yana joylashtirish — navbat 2-o'yinchida bo'lgani uchun Ali qila olmaydi
        self.assertFalse(self.act(self.ali, "qd")[0])

    def test_wall_cancel_and_wrong_player_draft(self):
        self.start()
        self.assertTrue(self.act(self.ali, "qd")[0])
        self.assertFalse(self.act(self.vali, "qo", "h")[0])
        self.assertTrue(self.act(self.ali, "qx")[0])
        self.assertEqual(self.room["wall_draft"], {})
        self.assertEqual(self.room["turn"], "1")
        self.assertFalse(self.act(self.ali, "qp")[0])         # draft yo'q
        self.assertEqual(self.room["walls"], [])

    def test_wall_place_requires_row_and_col(self):
        self.start()
        self.act(self.ali, "qd")
        ok, err = self.act(self.ali, "qp")
        self.assertFalse(ok)
        self.act(self.ali, "qr", "2")
        self.assertFalse(self.act(self.ali, "qp")[0])
        self.assertFalse(self.act(self.ali, "qc", "8")[0])   # 0..7 dan tashqari
        self.assertFalse(self.act(self.ali, "qc", "x")[0])
        self.assertTrue(self.act(self.ali, "qc", "2")[0])
        self.assertTrue(self.act(self.ali, "qp")[0])

    def test_blocking_wall_rejected_in_flow(self):
        self.start()
        # 2-o'yinchi yo'lini 4 ta devor bilan 8 ustunga yopamiz (navbatni qo'lda 1 ga qaytarib)
        for c in (0, 2, 4, 6):
            self.room["turn"] = "1"
            self.act(self.ali, "qd"); self.act(self.ali, "qo", "h")
            self.act(self.ali, "qr", "0"); self.act(self.ali, "qc", str(c))
            ok, err = self.act(self.ali, "qp")
            self.assertTrue(ok, err)
        self.room["turn"] = "1"
        self.act(self.ali, "qd"); self.act(self.ali, "qo", "h")
        self.act(self.ali, "qr", "0"); self.act(self.ali, "qc", "7")
        text, _ = chat_games.render(self.room)
        self.assertIn("⛔", text)                            # oldindan ogohlantiradi
        self.assertNotIn(f"cg:qp:{self.rid}", self.kb_data()) # tasdiqlash tugmasi yo'q
        ok, err = self.act(self.ali, "qp")
        self.assertFalse(ok)
        self.assertEqual(len(self.room["walls"]), 4)
        self.assertEqual(self.room["walls_left"]["1"], 6)
        self.assertEqual(self.room["turn"], "1")

    def test_wall_counter_reaches_zero(self):
        self.start()
        spots = [(1, 0), (1, 2), (1, 4), (1, 6), (3, 0), (3, 2), (3, 4), (3, 6), (5, 0), (5, 2)]
        for (r, c) in spots:
            self.room["turn"] = "1"
            self.act(self.ali, "qd"); self.act(self.ali, "qr", str(r)); self.act(self.ali, "qc", str(c))
            ok, err = self.act(self.ali, "qp")
            self.assertTrue(ok, f"{(r, c)}: {err}")
        self.assertEqual(self.room["walls_left"]["1"], 0)
        self.room["turn"] = "1"
        ok, err = self.act(self.ali, "qd")
        self.assertFalse(ok)
        self.assertNotIn("🧱 Devor qo‘yish (0)", self.kb_texts())
        self.assertEqual(self.room["walls_left"]["2"], 10)

    def test_win_stops_game_and_old_buttons_dead(self):
        self.start()
        self.room["pawns"] = {"1": [1, 4], "2": [4, 0]}
        ok, err = self.act(self.ali, "qm", "04")
        self.assertTrue(ok, err)
        self.assertEqual(self.room["status"], "finished")
        self.assertEqual(self.room["winner"], "1")
        text, kb = chat_games.render(self.room)
        self.assertIn("G‘olib", text)
        self.assertIn("@ali", text)
        self.assertIn("🔄 Qayta o‘ynash", [b.text for row in kb.inline_keyboard for b in row])
        # eski tugmalar ishlamaydi
        self.assertFalse(self.act(self.vali, "qm", "50")[0])
        self.assertFalse(self.act(self.ali, "qd")[0])
        self.assertFalse(self.act(self.ali, "qs")[0])
        self.assertEqual(self.room["pawns"]["1"], [0, 4])

    def test_restart_resets_and_swaps_sides(self):
        self.start()
        self.act(self.ali, "qd"); self.act(self.ali, "qr", "3"); self.act(self.ali, "qc", "3"); self.act(self.ali, "qp")
        self.room["pawns"] = {"1": [5, 0], "2": [7, 4]}
        self.room["turn"] = "2"
        self.assertTrue(self.act(self.vali, "qm", "84")[0])
        self.assertEqual(self.room["status"], "finished")
        self.assertEqual(self.room["winner"], "2")
        # o'yin tugamaguncha restart yo'q; begona qayta boshlay olmaydi
        ok, err = chat_games.restart_room(self.room, self.guest.id)
        self.assertFalse(ok)
        ok, err = chat_games.restart_room(self.room, self.vali.id)
        self.assertTrue(ok, err)
        self.assertEqual(self.room["status"], "playing")
        self.assertEqual(self.room["walls"], [])
        self.assertEqual(self.room["walls_left"], {"1": 10, "2": 10})
        self.assertEqual(self.room["pawns"], {"1": [8, 4], "2": [0, 4]})
        self.assertIsNone(self.room["winner"])
        self.assertEqual(self.room["turn"], "1")
        self.assertEqual(self.room["p1"], str(self.vali.id))   # ranglar almashdi
        self.assertEqual(self.room["p2"], str(self.ali.id))
        self.assertTrue(self.act(self.vali, "qm", "74")[0])    # endi Vali birinchi yuradi
        self.assertFalse(chat_games.restart_room(self.room, self.vali.id)[0])  # tugamagan o'yin

    def test_surrender_requires_confirmation(self):
        self.start()
        ok, msg = self.act(self.vali, "qs")                   # navbat Alida bo'lsa ham taslim bo'lish mumkin
        self.assertFalse(ok)
        self.assertEqual(self.room["status"], "playing")
        self.assertIn("tasdiqlash", msg.lower())
        ok, err = self.act(self.vali, "qs")
        self.assertTrue(ok, err)
        self.assertEqual(self.room["status"], "finished")
        self.assertEqual(self.room["winner"], "1")
        text, _ = chat_games.render(self.room)
        self.assertIn("taslim", text.lower())

    def test_surrender_confirmation_expires(self):
        self.start()
        self.act(self.ali, "qs")
        self.room["surrender_ask"]["1"] -= chat_games.Q_SURRENDER_WINDOW + 1
        ok, msg = self.act(self.ali, "qs")
        self.assertFalse(ok)
        self.assertEqual(self.room["status"], "playing")

    def test_rules_button_for_anyone(self):
        ok, msg = self.act(self.guest, "qh")
        self.assertFalse(ok)
        self.assertIn("Maqsad", msg)
        self.assertLessEqual(len(msg), 200)

    def test_actions_rejected_on_other_games_room(self):
        rid = game.create_room("tictactoe", self.ali.id)
        self.addCleanup(lambda: game.ROOMS.pop(rid, None))
        before = json.dumps(game.ROOMS[rid], sort_keys=True)
        ok, err = chat_games.quoridor_dispatch(game.ROOMS[rid], self.ali, "qm", ["74"])
        self.assertFalse(ok)
        self.assertEqual(json.dumps(game.ROOMS[rid], sort_keys=True), before)

    def test_callback_data_within_telegram_limit_everywhere(self):
        def check():
            _, kb = chat_games.render(self.room)
            for row in kb.inline_keyboard:
                self.assertLessEqual(len(row), 8)
                for b in row:
                    self.assertLessEqual(len(b.callback_data.encode()), 64, b.callback_data)
        check()
        self.start(); check()
        self.act(self.ali, "qd"); check()
        self.act(self.ali, "qo", "v"); self.act(self.ali, "qr", "7"); self.act(self.ali, "qc", "7"); check()
        self.act(self.ali, "qp"); check()
        self.room["pawns"]["2"] = [7, 4]; self.room["turn"] = "2"
        self.act(self.vali, "qm", "84"); check()

    def test_jump_buttons_shown_and_work(self):
        self.start()
        self.room["pawns"] = {"1": [5, 4], "2": [4, 4]}
        data = self.kb_data()
        self.assertIn(f"cg:qm:{self.rid}:34", data)           # sakrab o'tish tugmasi
        self.assertNotIn(f"cg:qm:{self.rid}:44", data)
        self.assertTrue(self.act(self.ali, "qm", "34")[0])

    def test_state_is_json_serializable_and_survives_restart(self):
        self.start()
        self.act(self.ali, "qm", "74")
        self.act(self.vali, "qd"); self.act(self.vali, "qr", "4"); self.act(self.vali, "qc", "4"); self.act(self.vali, "qp")
        dumped = game._snapshot()
        restored = json.loads(dumped)[self.rid]
        text_before, _ = chat_games.render(self.room)
        text_after, kb = chat_games.render(restored)
        self.assertEqual(text_before, text_after)
        # tiklangan xonada o'yin davom etadi
        self.assertTrue(chat_games.quoridor_dispatch(restored, self.ali, "qm", ["64"])[0])
        self.assertEqual(restored["pawns"]["1"], [6, 4])

    def test_legacy_room_from_previous_version_is_normalized(self):
        legacy = {"id": "old", "game": "quoridor", "status": "playing", "turn": "1",
                  "players": {"1": {"name": "A"}, "2": {"name": "B"}},
                  "p1": "1", "p2": "2", "pawns": {"1": [8, 4], "2": [0, 4]},
                  "walls_left": {"1": 10, "2": 10}, "walls": [], "wall_draft": {}, "version": 0}
        text, kb = chat_games.render(legacy)
        self.assertIn("QUORIDOR", text)

    def test_independent_rooms(self):
        rid2 = game.create_room("quoridor", self.vali.id)
        self.addCleanup(lambda: game.ROOMS.pop(rid2, None))
        room2 = game.ROOMS[rid2]
        self.start()
        self.assertTrue(chat_games.quoridor_join(room2, self.guest.id, self.guest)[0])
        self.assertTrue(self.act(self.ali, "qm", "74")[0])
        self.assertEqual(room2["pawns"]["1"], [8, 4])
        self.assertEqual(room2["turn"], "1")
        self.assertNotEqual(self.rid, rid2)
        self.assertTrue(chat_games.quoridor_dispatch(room2, self.vali, "qm", ["74"])[0])
        self.assertEqual(self.room["pawns"]["1"], [7, 4])
        self.assertEqual(self.room["turn"], "2")

    def test_concurrent_duplicate_callbacks_apply_once(self):
        self.start()
        results = []
        barrier = threading.Barrier(12)

        def worker():
            barrier.wait()
            results.append(chat_games.quoridor_dispatch(self.room, self.ali, "qm", ["74"])[0])

        ts = [threading.Thread(target=worker) for _ in range(12)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.room["pawns"]["1"], [7, 4])
        self.assertEqual(self.room["turn"], "2")

    def test_concurrent_joins_only_one_second_player(self):
        users = [U(1000 + i, f"U{i}") for i in range(10)]
        results = []
        barrier = threading.Barrier(len(users))

        def worker(u):
            barrier.wait()
            results.append(chat_games.quoridor_join(self.room, u.id, u)[0])

        ts = [threading.Thread(target=worker, args=(u,)) for u in users]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(results.count(True), 1)
        self.assertEqual(len(self.room["players"]), 2)

    def test_random_playthrough_invariants(self):
        """Tasodifiy to'liq o'yin: har qadamda qoidalar buzilmasligi shart."""
        import random
        rnd = random.Random(7)
        for _ in range(40):
            s = fresh_state()
            for _step in range(200):
                if s["winner"]:
                    break
                pid = s["turn"]
                if rnd.random() < 0.35 and s["walls_left"][pid] > 0:
                    r, c, o = rnd.randrange(8), rnd.randrange(8), rnd.choice("hv")
                    ok, _ = Q.apply_wall(s, pid, r, c, o)
                else:
                    mv = rnd.choice(Q.legal_moves(s, pid))
                    ok, err = Q.apply_move(s, pid, *mv)
                    self.assertTrue(ok, err)
                e = Q.blocked_edges(s["walls"])
                self.assertTrue(Q.has_path(e, s["pawns"]["1"], 0))
                self.assertTrue(Q.has_path(e, s["pawns"]["2"], 8))
                self.assertEqual(len(s["walls"]) + s["walls_left"]["1"] + s["walls_left"]["2"], 20)
                self.assertNotEqual(s["pawns"]["1"], s["pawns"]["2"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Telegram qatlami (haqiqiy python-telegram-bot o'rnatilmagan sandbox uchun):
# bot.py dagi `_chat_game_callback` funksiyasi manba koddan ajratib olinib, soxta
# CallbackQuery bilan ishga tushiriladi — marshrutlash, answer() va
# edit_message_text() chaqiruvlari tekshiriladi.
# ---------------------------------------------------------------------------
import ast
import asyncio
import logging
import os
import re


def _load_callback():
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_chat_game_callback")
    mod = ast.Module(body=[fn], type_ignores=[])
    ns = {"chat_games": chat_games, "asyncio": asyncio, "logger": logging.getLogger("t")}
    exec(compile(mod, "bot.py", "exec"), ns)
    return ns["_chat_game_callback"]


class FakeQuery:
    def __init__(self, user, data):
        self.from_user = user
        self.data = data
        self.answers = []
        self.edits = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
        self.edits.append((text, reply_markup, parse_mode))


class FakeUpdate:
    def __init__(self, q):
        self.callback_query = q


class TelegramLayerTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(game, "_save", lambda: None)
        p.start(); self.addCleanup(p.stop)
        self.cb = _load_callback()
        self.ali, self.vali, self.guest = U(11, "Ali", "ali"), U(22, "Vali", "vali"), U(33, "G", "g")
        self.rid = game.create_room("quoridor", self.ali.id)
        self.addCleanup(lambda: game.ROOMS.pop(self.rid, None))
        chat_games.q_set_profile(game.ROOMS[self.rid], self.ali)

    def press(self, user, data):
        q = FakeQuery(user, data)
        asyncio.run(self.cb(FakeUpdate(q), None))
        return q

    def test_full_game_through_callbacks(self):
        r = self.rid
        q = self.press(self.vali, f"cg:join:{r}")
        self.assertEqual(q.answers[-1], ("✅", False))
        self.assertEqual(len(q.edits), 1)
        self.assertIn("@vali", q.edits[0][0])
        self.assertEqual(q.edits[0][2], "HTML")
        # begona tugmani bosadi
        q = self.press(self.guest, f"cg:qm:{r}:74")
        self.assertTrue(q.answers[-1][1]); self.assertEqual(q.edits, [])
        # noto'g'ri navbat
        q = self.press(self.vali, f"cg:qm:{r}:14")
        self.assertTrue(q.answers[-1][1]); self.assertEqual(q.edits, [])
        # to'g'ri yurish -> doska yangilanadi
        q = self.press(self.ali, f"cg:qm:{r}:74")
        self.assertEqual(q.answers[-1], ("✅", False)); self.assertEqual(len(q.edits), 1)
        self.assertIn("Navbat: 2-o‘yinchi", q.edits[0][0])
        # qoidalar — alert, doska qayta chizilmaydi
        q = self.press(self.guest, f"cg:qh:{r}")
        self.assertTrue(q.answers[-1][1]); self.assertIn("Maqsad", q.answers[-1][0]); self.assertEqual(q.edits, [])
        # eskirgan/noma'lum xona va buzuq tugma
        q = self.press(self.ali, "cg:qm:nonexistent:74")
        self.assertTrue(q.answers[-1][1])
        q = self.press(self.ali, "cg:qm")
        self.assertTrue(q.answers[-1][1])
        # g'alaba -> doska yopiladi, eski tugma ishlamaydi, qayta o'ynash ishlaydi
        room = game.ROOMS[r]; room["pawns"] = {"1": [1, 0], "2": [0, 8]}; room["turn"] = "1"
        q = self.press(self.ali, f"cg:qm:{r}:00")
        self.assertIn("G‘olib", q.edits[0][0])
        q = self.press(self.vali, f"cg:qm:{r}:10")
        self.assertTrue(q.answers[-1][1]); self.assertEqual(q.edits, [])
        q = self.press(self.vali, f"cg:restart:{r}")
        self.assertEqual(q.answers[-1], ("✅", False)); self.assertEqual(game.ROOMS[r]["status"], "playing")

    def test_quoridor_buttons_do_not_touch_other_games(self):
        rid = game.create_room("tictactoe", self.ali.id)
        self.addCleanup(lambda: game.ROOMS.pop(rid, None))
        before = json.dumps(game.ROOMS[rid], sort_keys=True)
        q = self.press(self.ali, f"cg:qm:{rid}:74")
        self.assertTrue(q.answers[-1][1])
        self.assertEqual(json.dumps(game.ROOMS[rid], sort_keys=True), before)


class InlineTriggerTests(unittest.TestCase):
    def test_trigger_regexes(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "handlers", "inline_query.py"), encoding="utf-8").read()
        ns = {"re": re}
        for name in ("GAME_RE", "QUORIDOR_RE"):
            m = re.search(rf"^{name} = (re\.compile\(.*\))$", src, re.M)
            self.assertIsNotNone(m, name)
            ns[name] = eval(m.group(1), ns)
        for q in ("game", "GAME", "games", "o'yin", "oyin", "o‘yinlar", "oyinlar"):
            self.assertTrue(ns["GAME_RE"].match(q), q)
        for q in ("quoridor", "Quoridor", "kvoridor", "koridor"):
            self.assertTrue(ns["QUORIDOR_RE"].match(q), q)
        for q in ("gamex", "quoridorx", "salom", "game over"):
            self.assertFalse(ns["GAME_RE"].match(q) or ns["QUORIDOR_RE"].match(q), q)
