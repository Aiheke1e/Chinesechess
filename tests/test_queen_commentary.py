import unittest

from board import Board, START_FEN
from notation import move_description
from queen_commentary import build_queen_evidence


class QueenCommentaryTests(unittest.TestCase):
    def test_compares_existing_multipv_without_new_search(self):
        board = Board.from_fen(START_FEN)
        moves = board.legal_moves()[:3]
        candidates = [
            {"score_type": "cp", "score": score,
             "pv": [Board.ucci(move)]}
            for move, score in zip(moves, (120, 20, -80))
        ]
        evidence = build_queen_evidence(board, moves[0], candidates)
        self.assertEqual(evidence["最终选择"],
                         move_description(board, moves[0]))
        self.assertEqual(len(evidence["候选取舍"]), 2)
        self.assertTrue(evidence["值得展开"])
        self.assertIn("Pikafish前三候选", evidence["方法"])
        self.assertNotIn("FEN", str(evidence))

    def test_capture_is_marked_as_worthy_of_explanation(self):
        squares = [None] * 90
        squares[85] = "K"
        squares[4] = "k"
        squares[49] = "R"
        squares[40] = "n"
        board = Board(squares, turn="r")
        move = (49, 40)
        evidence = build_queen_evidence(board, move, [{
            "score_type": "cp", "score": 300,
            "pv": [Board.ucci(move)],
        }])
        self.assertIn("吃掉马", evidence["选择理由"])
        self.assertTrue(evidence["值得展开"])

    def test_quiet_move_uses_visible_geometry_not_generic_intent(self):
        board = Board.from_fen(START_FEN)
        move = Board.from_ucci("g3g4")
        evidence = build_queen_evidence(board, move, [{
            "score_type": "cp", "score": 20,
            "pv": [Board.ucci(move)],
        }])
        self.assertIn("兵向前推进", evidence["选择理由"])
        self.assertNotIn("改善棋子位置", evidence["选择理由"])

    def test_reply_line_belongs_to_chosen_move_even_when_candidates_are_reordered(self):
        board = Board.from_fen(START_FEN)
        move = Board.from_ucci("h2e2")
        evidence = build_queen_evidence(board, move, [
            {"score_type": "cp", "score": 120, "pv": ["b2e2", "h7e7"]},
            {"score_type": "cp", "score": 110, "pv": ["h2e2", "b7e7"]},
        ])
        board.apply(move)
        self.assertEqual(evidence["最可能应手"],
                         move_description(board, Board.from_ucci("b7e7")))
        self.assertEqual(len(evidence["候选取舍"]), 1)
        self.assertNotEqual(evidence["候选取舍"][0]["走法"], evidence["最终选择"])

    def test_missing_chosen_line_does_not_borrow_another_candidates_reply(self):
        board = Board.from_fen(START_FEN)
        evidence = build_queen_evidence(board, Board.from_ucci("h2e2"), [
            {"score_type": "cp", "score": 120, "pv": ["b2e2", "h7e7"]},
        ])
        self.assertEqual(evidence["主线参考"], [])
        self.assertEqual(evidence["最可能应手"], "")

    def test_recapture_flag_requires_reply_to_land_on_the_moved_piece(self):
        squares = [None] * 90
        for square, piece in ((85, "K"), (4, "k"), (49, "P"), (45, "R"),
                              (36, "p"), (27, "r"), (30, "N")):
            squares[square] = piece
        board = Board(squares, "r")
        chosen = (45, 36)
        for reply, expected in (((27, 36), True), ((27, 30), False)):
            with self.subTest(reply=reply):
                evidence = build_queen_evidence(board, chosen, [{
                    "score_type": "cp", "score": 100,
                    "pv": [Board.ucci(chosen), Board.ucci(reply)],
                }])
                self.assertTrue(evidence["最可能应手"])
                self.assertEqual(evidence["应手回吃本子"], expected)


if __name__ == "__main__":
    unittest.main()
