import unittest
from unittest.mock import patch
from _stub_telegram import install_stubs
install_stubs()
import game
import chat_games

class FakeUser:
    def __init__(self, uid, name):
        self.id = uid
        self.full_name = name
        self.username = name.lower()


def make_room(kind='checkers'):
    board = game._initial_checkers() if kind == 'checkers' else ([None] * 9 if kind == 'tictactoe' else [])
    return {
        'id': 'testroom', 'game': kind, 'status': 'lobby', 'players': {
            '1': {'name':'Ali','side':None,'mark':None},
        }, 'board': board, 'turn': None, 'winner':None, 'version':0,
        'updated_at':0, 'chat_selected':{}, 'forced_piece':None, 'history':[],
    }

class ChatGamesAuditTests(unittest.TestCase):
    def setUp(self):
        self.save_patch = patch.object(game, '_save', lambda: None)
        self.save_patch.start()
        self.addCleanup(self.save_patch.stop)

    def test_checkers_second_player_gets_opposite_even_if_same_button(self):
        room=make_room('checkers')
        self.assertTrue(chat_games.choose_side(room, 1, 'w')[0])
        self.assertTrue(chat_games.add_player(room, 2, FakeUser(2,'Vali'))[0])
        # Ikkinchi o'yinchi shunchaki qo'shilganda ham qarama-qarshi rang beriladi.
        self.assertEqual(room['players']['1']['side'], 'w')
        self.assertEqual(room['players']['2']['side'], 'b')
        self.assertEqual(room['status'], 'playing')
        self.assertEqual(room['turn'], 'w')

    def test_ttt_second_player_gets_opposite_and_x_starts(self):
        room=make_room('tictactoe')
        self.assertTrue(chat_games.choose_mark(room, 1, 'x')[0])
        self.assertTrue(chat_games.add_player(room, 2, FakeUser(2,'Vali'))[0])
        self.assertEqual(room['players']['1']['mark'], 'x')
        self.assertEqual(room['players']['2']['mark'], 'o')
        self.assertEqual(room['status'], 'playing')
        self.assertEqual(room['turn'], 'x')

    def test_checkers_empty_cells_are_visually_blank_and_pieces_render(self):
        room=make_room('checkers')
        _, markup=chat_games.render(room)
        board=markup.inline_keyboard[:8]
        for r, row in enumerate(board):
            for c, button in enumerate(row):
                piece=room['board'][r][c]
                if piece is None:
                    self.assertEqual(button.text, '⠀', (r,c))
                else:
                    self.assertNotEqual(button.text, '⠀', (r,c))
        self.assertIn('⚫', [button.text for row in board for button in row])
        self.assertIn('⚪', [button.text for row in board for button in row])
        self.assertNotIn('▫️', [button.text for row in board for button in row])
        self.assertNotIn('⬛', [button.text for row in board for button in row])

    def test_memory_mismatch_closes_and_changes_turn(self):
        room={
            'id':'memorytest','game':'memory','status':'playing',
            'players':{'1':{'name':'Ali'},'2':{'name':'Vali'}},
            'board':[{'symbol':'🍎','state':'hidden'},{'symbol':'🍐','state':'hidden'}],
            'turn':'1','scores':{'1':0,'2':0},'memory_pending':[],
            'memory_waiting':False,'last_mismatch':None,'version':0,'updated_at':0,
        }
        self.assertTrue(chat_games.memory_flip(room, '1', 0)[0])
        self.assertTrue(chat_games.memory_flip(room, '1', 1)[0])
        self.assertTrue(room['memory_waiting'])
        self.assertEqual([x['state'] for x in room['board']], ['pending','pending'])
        ok,err=chat_games.memory_flip(room, '2', 0)
        self.assertFalse(ok)
        self.assertIn('kut', err.lower())
        self.assertTrue(chat_games.memory_hide_mismatch(room))
        self.assertEqual([x['state'] for x in room['board']], ['hidden','hidden'])
        self.assertEqual(room['turn'], '2')
        self.assertFalse(room['memory_waiting'])

    def test_memory_match_scores_and_keeps_turn(self):
        room={
            'id':'memorytest','game':'memory','status':'playing',
            'players':{'1':{'name':'Ali'},'2':{'name':'Vali'}},
            'board':[{'symbol':'🍎','state':'hidden'},{'symbol':'🍎','state':'hidden'}],
            'turn':'1','scores':{'1':0,'2':0},'memory_pending':[],
            'memory_waiting':False,'last_mismatch':None,'version':0,'updated_at':0,
        }
        self.assertTrue(chat_games.memory_flip(room, '1', 0)[0])
        self.assertTrue(chat_games.memory_flip(room, '1', 1)[0])
        self.assertEqual([x['state'] for x in room['board']], ['matched','matched'])
        self.assertEqual(room['scores']['1'], 1)
        self.assertEqual(room['turn'], '1')
        self.assertFalse(room['memory_waiting'])

    def test_checkers_move_rejects_out_of_turn_player(self):
        room=make_room('checkers')
        room['status']='playing'; room['turn']='w'
        room['players']={'1':{'name':'Ali','side':'w'},'2':{'name':'Vali','side':'b'}}
        ok,err=chat_games.checkers_move(room, 2, 2, 1)
        self.assertFalse(ok)
        self.assertIn('navbat', err.lower())

    def test_checkers_winner_text_uses_side_not_user_id(self):
        room=make_room('checkers')
        room['players']={'1':{'name':'Ali','side':'w'},'2':{'name':'Vali','side':'b'}}
        room['status']='finished'; room['winner']='w'
        text,_=chat_games.render(room)
        self.assertIn('G‘olib: Ali', text)

    def test_checkers_legal_move_and_selection_can_be_retried(self):
        room=make_room('checkers')
        room['status']='playing'; room['turn']='w'
        room['players']={'1':{'name':'Ali','side':'w'},'2':{'name':'Vali','side':'b'}}
        self.assertTrue(chat_games.checkers_move(room, 1, 5, 0)[0])
        self.assertEqual(room['chat_selected']['1'], [5,0])
        ok,err=chat_games.checkers_move(room, 1, 5, 0)
        self.assertFalse(ok)  # cannot move a piece to itself
        self.assertEqual(room['chat_selected']['1'], [5,0])  # bad move doesn't discard selection
        self.assertTrue(chat_games.checkers_move(room, 1, 4, 1)[0])
        self.assertEqual(room['board'][4][1], 'w')
        self.assertEqual(room['turn'], 'b')

    def test_join_after_first_checkers_choice_autoselects_opposite(self):
        room=make_room('checkers')
        self.assertTrue(chat_games.choose_side(room, 1, 'b')[0])
        self.assertTrue(chat_games.add_player(room, 2, FakeUser(2,'Vali'))[0])
        self.assertEqual(room['players']['2']['side'], 'w')
        self.assertEqual(room['status'], 'playing')
        self.assertEqual(room['turn'], 'w')

    def test_join_after_first_ttt_choice_autoselects_opposite(self):
        room=make_room('tictactoe')
        self.assertTrue(chat_games.choose_mark(room, 1, 'o')[0])
        self.assertTrue(chat_games.add_player(room, 2, FakeUser(2,'Vali'))[0])
        self.assertEqual(room['players']['2']['mark'], 'x')
        self.assertEqual(room['status'], 'playing')
        self.assertEqual(room['turn'], 'x')

    def test_ttt_win_and_turn_protection(self):
        room=make_room('tictactoe')
        room.update(status='playing', turn='x', board=['x','x',None,'o','o',None,None,None,None])
        room['players']={'1':{'name':'Ali','mark':'x'},'2':{'name':'Vali','mark':'o'}}
        ok,err=chat_games.ttt_move(room, 2, 2)
        self.assertFalse(ok)
        self.assertIn('navbat', err.lower())
        self.assertTrue(chat_games.ttt_move(room, 1, 2)[0])
        self.assertEqual(room['status'], 'finished')
        self.assertEqual(room['winner'], '1')

    def test_all_memory_symbol_sets_have_18_unique_symbols(self):
        for category, symbols in chat_games.MEMORY_SYMBOLS.items():
            self.assertEqual(len(symbols), 18, category)
            self.assertEqual(len(set(symbols)), 18, category)

    def test_ttt_full_board_without_line_is_draw(self):
        room=make_room('tictactoe')
        room.update(status='playing', turn='x', board=['x','o','x','x','o','o','o','x',None])
        room['players']={'1':{'name':'Ali','mark':'x'},'2':{'name':'Vali','mark':'o'}}
        self.assertTrue(chat_games.ttt_move(room, 1, 8)[0])
        self.assertEqual(room['status'], 'finished')
        self.assertIsNone(room['winner'])
        self.assertEqual(room['reason'], 'draw')

    def test_chess_rejects_illegal_move_without_changing_board(self):
        room={'board':game._initial_chess(),'castling':'KQkq','ep':None,
              'halfmove':0,'fullmove':1,'turn':'w','status':'playing',
              'winner':None,'reason':None,'last_move':None,'history':[]}
        before=[row[:] for row in room['board']]
        ok,err=game._chess_move(room,'w',6,4,3,4,'q')  # pawn cannot jump three squares
        self.assertFalse(ok)
        self.assertEqual(room['board'], before)
        self.assertEqual(room['turn'], 'w')

    def test_memory_ready_creates_exact_pairs_for_each_board_size(self):
        for size, (rows, cols) in chat_games.MEMORY_SIZES.items():
            room={'id':'memorytest','game':'memory','status':'lobby','players':{'1':{'name':'Ali'},'2':{'name':'Vali'}},
                  'memory_size':size,'memory_symbols':'fruits','memory_ready':{},'board':[], 'scores':{},
                  'version':0,'updated_at':0}
            self.assertTrue(chat_games.memory_ready(room,'1')[0])
            self.assertEqual(room['status'],'playing')
            self.assertEqual(len(room['board']),rows*cols)
            self.assertEqual(len(room['board']), rows*cols)
            symbols=[x['symbol'] for x in room['board']]
            self.assertEqual(len(set(symbols)), rows*cols//2)
            self.assertEqual(sorted(symbols.count(x) for x in set(symbols)), [2]*(rows*cols//2))

if __name__ == '__main__':
    unittest.main()
