"""🧱 Quoridor Mini App backend (game.py) testlari: qo'shilish, yurish, devor, navbat, g'alaba, qayta o'ynash, bir vaqtlilik."""
import json
import threading
import unittest
from unittest.mock import patch

from _stub_telegram import install_stubs
install_stubs()
import game
import quoridor as Q


def fake_verify(init):
    # init_data o'rniga "uid:ism:username" matni ishlatiladi
    if not init: return None
    uid, name, un = (init.split(":") + ["", ""])[:3]
    return {"id": int(uid), "first_name": name, "username": un}


class MiniAppQuoridor(unittest.TestCase):
    def setUp(self):
        self.p = patch.object(game, "_verify", fake_verify); self.p.start()
        self.rid = game.create_room("quoridor", 111)
        self.A, self.B, self.C = "111:Ali:ali", "222:Vali:vali", "333:Sami:sami"

    def tearDown(self):
        self.p.stop(); game.ROOMS.pop(self.rid, None)

    def start(self):
        d, e = game.join(self.rid, self.A); self.assertIsNone(e)
        d, e = game.join(self.rid, self.B); self.assertIsNone(e)
        return d

    def test_join_creator_then_second_starts_game(self):
        d, e = game.join(self.rid, self.A)
        self.assertEqual(d["status"], "lobby"); self.assertEqual(d["q"]["my_pid"], "1")
        d, e = game.join(self.rid, self.B)
        self.assertEqual(d["status"], "playing"); self.assertEqual(d["q"]["my_pid"], "2")
        self.assertEqual(d["turn"], "1")
        self.assertEqual(d["q"]["p1"]["username"], "ali"); self.assertEqual(d["q"]["p2"]["username"], "vali")

    def test_third_player_rejected(self):
        self.start()
        d, e = game.join(self.rid, self.C)
        self.assertIsNone(d); self.assertIn("to'la", e)

    def test_unverified_session_rejected(self):
        d, e = game.join(self.rid, "")
        self.assertIsNone(d); self.assertTrue(e)

    def test_move_turn_order_and_wrong_player(self):
        self.start()
        d, e = game.quoridor_move(self.rid, self.B, 1, 4)
        self.assertIsNone(d); self.assertIn("navbat", e)
        d, e = game.quoridor_move(self.rid, self.C, 7, 4)
        self.assertIsNone(d)
        d, e = game.quoridor_move(self.rid, self.A, 7, 4)
        self.assertIsNone(e); self.assertEqual(d["q"]["pawns"]["1"], [7, 4]); self.assertEqual(d["turn"], "2")
        d, e = game.quoridor_move(self.rid, self.A, 6, 4)
        self.assertIsNone(d)

    def test_illegal_moves_rejected(self):
        self.start()
        for rc in [(7, 3), (6, 4), (9, 4), (8, 4), (-1, 4)]:
            d, e = game.quoridor_move(self.rid, self.A, *rc); self.assertIsNone(d, rc)
        d, e = game.quoridor_move(self.rid, self.A, "7", 4); self.assertIsNone(d)
        d, e = game.quoridor_move(self.rid, self.A, True, 4); self.assertIsNone(d)

    def test_wall_placement_count_and_turn(self):
        self.start()
        d, e = game.quoridor_wall(self.rid, self.A, 3, 3, "h")
        self.assertIsNone(e); self.assertEqual(d["q"]["walls_left"]["1"], 9); self.assertEqual(d["turn"], "2")
        self.assertEqual(len(d["q"]["walls"]), 1)
        d, e = game.quoridor_wall(self.rid, self.A, 4, 4, "v"); self.assertIsNone(d)   # navbat 2-da
        d, e = game.quoridor_wall(self.rid, self.B, 3, 3, "v"); self.assertIsNone(d)   # kesishadi
        d, e = game.quoridor_wall(self.rid, self.B, 3, 4, "h"); self.assertIsNone(d)   # ustma-ust
        d, e = game.quoridor_wall(self.rid, self.B, 8, 0, "h"); self.assertIsNone(d)   # doskadan tashqari
        d, e = game.quoridor_wall(self.rid, self.B, 0, 0, "x"); self.assertIsNone(d)

    def test_wall_blocking_all_paths_rejected(self):
        self.start()
        room = game.ROOMS[self.rid]
        # 2-o'yinchi burchakda (a1): o'ng tomoni vertikal devor bilan yopilgan; ostiga gorizontal devor
        # qo'yilsa (a1-b1) cho'ntak butunlay yopilib qoladi -> rad etilishi kerak.
        room["pawns"]["2"] = [0, 0]
        room["walls"] = [{"r": 0, "c": 1, "o": "v"}]
        d, e = game.quoridor_wall(self.rid, self.A, 0, 0, "h")
        self.assertIsNone(d); self.assertIn("to‘sib", e)
        self.assertEqual(game.ROOMS[self.rid]["walls_left"]["1"], 10)

    def test_ten_wall_limit(self):
        self.start()
        room = game.ROOMS[self.rid]; room["walls_left"]["1"] = 0
        d, e = game.quoridor_wall(self.rid, self.A, 3, 3, "h")
        self.assertIsNone(d); self.assertIn("qolmagan", e)

    def test_win_stops_game_and_old_buttons_dead(self):
        self.start()
        room = game.ROOMS[self.rid]
        room["pawns"]["1"] = [1, 4]; room["pawns"]["2"] = [0, 0]
        d, e = game.quoridor_move(self.rid, self.A, 0, 4)
        self.assertIsNone(e); self.assertEqual(d["status"], "finished"); self.assertEqual(d["winner"], "111")
        self.assertEqual(d["q"]["winner_pid"], "1")
        d, e = game.quoridor_move(self.rid, self.B, 1, 0); self.assertIsNone(d)
        d, e = game.quoridor_wall(self.rid, self.B, 3, 3, "h"); self.assertIsNone(d)

    def test_player2_wins_by_reaching_bottom(self):
        self.start()
        room = game.ROOMS[self.rid]
        room["turn"] = "2"; room["pawns"]["2"] = [7, 2]; room["pawns"]["1"] = [8, 8]
        d, e = game.quoridor_move(self.rid, self.B, 8, 2)
        self.assertIsNone(e); self.assertEqual(d["winner"], "222")

    def test_resign_and_rematch_swaps_sides(self):
        self.start()
        d, e = game.resign(self.rid, self.B)
        self.assertEqual(d["status"], "finished"); self.assertEqual(d["winner"], "111")
        d, e = game.rematch(self.rid, self.A); self.assertEqual(d["status"], "finished")  # 2-chi rozilik kutiladi
        d, e = game.rematch(self.rid, self.B)
        self.assertEqual(d["status"], "playing"); self.assertEqual(d["q"]["my_pid"], "1")  # B endi 1-o'yinchi
        self.assertEqual(d["q"]["walls_left"], {"1": 10, "2": 10}); self.assertEqual(d["q"]["walls"], [])
        self.assertEqual(d["turn"], "1")

    def test_rematch_before_finish_rejected(self):
        self.start()
        d, e = game.rematch(self.rid, self.A); self.assertIsNone(d)

    def test_legal_moves_and_walls_only_for_mover(self):
        self.start()
        dA = game._public(game.ROOMS[self.rid], "111"); dB = game._public(game.ROOMS[self.rid], "222")
        self.assertEqual(len(dA["q"]["legal_moves"]), 3); self.assertGreater(len(dA["q"]["legal_walls"]), 100)
        self.assertEqual(dB["q"]["legal_moves"], []); self.assertEqual(dB["q"]["legal_walls"], [])

    def test_legal_walls_match_engine(self):
        self.start()
        game.quoridor_wall(self.rid, self.A, 3, 3, "h")
        room = game.ROOMS[self.rid]
        got = set(game._public(room, "222")["q"]["legal_walls"])
        want = {f"{o}:{r}:{c}" for o in "hv" for r in range(8) for c in range(8) if Q.wall_error(room, "2", r, c, o) is None}
        self.assertEqual(got, want)

    def test_json_persistence_roundtrip(self):
        self.start(); game.quoridor_move(self.rid, self.A, 7, 4); game.quoridor_wall(self.rid, self.B, 3, 3, "h")
        restored = json.loads(json.dumps(game.ROOMS[self.rid]))
        game.ROOMS[self.rid] = restored
        d, e = game.quoridor_move(self.rid, self.A, 6, 4)
        self.assertIsNone(e); self.assertEqual(d["q"]["pawns"]["1"], [6, 4]); self.assertEqual(len(d["q"]["walls"]), 1)

    def test_independent_rooms(self):
        rid2 = game.create_room("quoridor", 111)
        try:
            self.start(); game.join(rid2, self.A); game.join(rid2, self.C)
            game.quoridor_move(self.rid, self.A, 7, 4)
            self.assertEqual(game.ROOMS[rid2]["pawns"]["1"], [8, 4])
        finally:
            game.ROOMS.pop(rid2, None)

    def test_concurrent_same_move_only_once(self):
        self.start(); results = []
        def go():
            d, e = game.quoridor_move(self.rid, self.A, 7, 4); results.append(e is None)
        ts = [threading.Thread(target=go) for _ in range(12)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sum(results), 1)
        self.assertEqual(game.ROOMS[self.rid]["pawns"]["1"], [7, 4]); self.assertEqual(game.ROOMS[self.rid]["turn"], "2")

    def test_concurrent_joins_only_one_second_player(self):
        game.join(self.rid, self.A); out = []
        def go(n):
            d, e = game.join(self.rid, f"{500+n}:U{n}:u{n}"); out.append(e is None)
        ts = [threading.Thread(target=go, args=(i,)) for i in range(10)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sum(out), 1)

    def test_other_games_still_blocked_from_quoridor_endpoints(self):
        rid = game.create_room("tictactoe", 111)
        try:
            d, e = game.quoridor_move(rid, self.A, 7, 4); self.assertIsNone(d)
        finally:
            game.ROOMS.pop(rid, None)

    def test_full_random_game_terminates(self):
        import random
        random.seed(7); self.start(); room = game.ROOMS[self.rid]
        ids = {"1": self.A, "2": self.B}
        for _ in range(600):
            if room["status"] == "finished": break
            pid = room["turn"]
            if random.random() < .3 and room["walls_left"][pid]:
                r, c, o = random.randrange(8), random.randrange(8), random.choice("hv")
                game.quoridor_wall(self.rid, ids[pid], r, c, o)
            else:
                mv = random.choice(Q.legal_moves(room, pid)); game.quoridor_move(self.rid, ids[pid], *mv)
            for p in "12":
                self.assertTrue(Q.has_path(Q.blocked_edges(room["walls"]), room["pawns"][p], Q.GOAL_ROW[p]))
        # o'yin davomida yo'llar har doim ochiq qolgan bo'lishi kerak (yuqoridagi assert)


class RoutesAndServing(unittest.TestCase):
    def test_static_files_exist_and_reference_quoridor(self):
        import os
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webapp", "game")
        js = open(os.path.join(base, "app.js"), encoding="utf-8").read()
        html = open(os.path.join(base, "index.html"), encoding="utf-8").read()
        for needle in ("/api/game/quoridor/move", "/api/game/quoridor/wall", "/api/game/rematch"):
            self.assertIn(needle, js)
        for el in ("qBoard", "qWallOk", "rematchBtn", "lobbyQuoridor"):
            self.assertIn(f'id="{el}"', html)

    def test_post_routes_registered(self):
        src = open(game.__file__, encoding="utf-8").read()
        self.assertIn('"/api/game/quoridor/move"', src); self.assertIn('"/api/game/quoridor/wall"', src)

    def test_inline_result_uses_miniapp_url_button(self):
        import os
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "handlers", "inline_query.py"), encoding="utf-8").read()
        self.assertIn("game.room_url(gkey, rid)", src); self.assertIn("🧱 <b>QUORIDOR</b>", src)

    def test_callback_data_unaffected_deeplink_format(self):
        url = game.room_url("quoridor", "abc")
        self.assertIn("startapp=game_abc", url)


if __name__ == "__main__":
    unittest.main()
