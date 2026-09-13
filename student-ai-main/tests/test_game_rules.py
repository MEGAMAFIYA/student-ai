import unittest
import game

class GameRulesTest(unittest.TestCase):
    def test_chess_basic_and_rook_castling_right(self):
        room={"board":game._initial_chess(),"castling":"KQkq","ep":None,
              "halfmove":0,"fullmove":1,"turn":"w","status":"playing",
              "winner":None,"reason":None,"last_move":None,"history":[]}
        ok,err=game._chess_move(room,"w",6,4,4,4,"q")
        self.assertTrue(ok,err)
        self.assertTrue(game._chess_move(room,"b",1,4,3,4,"q")[0])
        # A rook leaving h1 permanently removes White's K-side castling right.
        room["board"][6][7]=None
        ok,err=game._chess_move(room,"w",7,7,6,7,"q")
        self.assertTrue(ok,err)
        self.assertNotIn("K",room["castling"])
        self.assertIn("Q",room["castling"])

    def test_checkers_mandatory_and_multi_capture(self):
        room={"board":[[None]*8 for _ in range(8)],"turn":"w","status":"playing",
              "winner":None,"reason":None,"last_move":None,"history":[],"forced_piece":None}
        room["board"][5][0]="w"; room["board"][4][1]="b"; room["board"][2][3]="b"
        ok,err=game._checkers_move(room,"w",5,0,3,2)
        self.assertTrue(ok,err); self.assertEqual(room["turn"],"w")
        self.assertEqual(room["forced_piece"],[3,2])
        ok,err=game._checkers_move(room,"w",3,2,1,4)
        self.assertTrue(ok,err); self.assertEqual(room["turn"],"b")

if __name__=="__main__":
    unittest.main()
