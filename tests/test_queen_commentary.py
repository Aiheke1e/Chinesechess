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


if __name__ == "__main__":
    unittest.main()
