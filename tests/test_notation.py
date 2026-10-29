import unittest

from board import Board, START_FEN
from notation import move_description, move_notation


class NotationTests(unittest.TestCase):
    def test_red_cannon_horizontal(self):
        board = Board.from_fen(START_FEN)
        move = Board.from_ucci("h2e2")
        self.assertEqual(move_notation(board, move), "红方 炮二平五")

    def test_black_knight_advance(self):
        board = Board.from_fen(START_FEN)
        board.turn = "b"
        move = Board.from_ucci("h9g7")
        self.assertEqual(move_notation(board, move), "黑方 马8进7")

    def test_red_pawn_advance(self):
        board = Board.from_fen(START_FEN)
        move = Board.from_ucci("c3c4")
        self.assertEqual(move_notation(board, move), "红方 兵七进一")

    def test_capture_is_spoken_plainly(self):
        board = Board.from_fen("4k4/9/9/9/4r4/4R4/9/9/9/4K4 w - - 0 1")
        move = Board.from_ucci("e4e5")
        self.assertEqual(move_description(board, move), "红方 车五进一，吃车")


if __name__ == "__main__":
    unittest.main()
