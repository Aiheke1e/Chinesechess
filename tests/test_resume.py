import unittest

from board import Board
from control_panel import valid_turns


class ResumeTests(unittest.TestCase):
    def test_accepts_structurally_legal_endgame(self):
        board = Board.from_fen("4k4/9/9/9/4r4/4R4/9/9/9/4K4 w - - 0 1")
        self.assertIn("r", valid_turns(board.sq))

    def test_rejects_position_without_both_kings(self):
        board = Board.from_fen("4k4/9/9/9/9/9/9/9/9/9 w - - 0 1")
        self.assertEqual(valid_turns(board.sq), [])


if __name__ == "__main__":
    unittest.main()
