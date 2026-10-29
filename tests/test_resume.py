import unittest

from board import Board
from control_panel import ControlPanel, valid_turns


class ResumeTests(unittest.TestCase):
    def test_accepts_structurally_legal_endgame(self):
        board = Board.from_fen("4k4/9/9/9/4r4/4R4/9/9/9/4K4 w - - 0 1")
        self.assertIn("r", valid_turns(board.sq))

    def test_rejects_position_without_both_kings(self):
        board = Board.from_fen("4k4/9/9/9/9/9/9/9/9/9 w - - 0 1")
        self.assertEqual(valid_turns(board.sq), [])

    def test_commentary_log_label_includes_move_but_not_spoken_text(self):
        label = ControlPanel._commentary_log_label(
            "关键棋解说", {
                "解说对应手数": 16,
                "解说对应走法": "红方 车二进一，吃车",
            })
        self.assertEqual(label, "关键棋解说 · 第16手 · 红方 车二进一，吃车")


if __name__ == "__main__":
    unittest.main()
