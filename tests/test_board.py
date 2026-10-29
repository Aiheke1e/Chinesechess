import unittest

from board import Board, START_FEN


def perft(board, depth):
    if depth == 0:
        return 1
    nodes = 0
    for move in board.legal_moves():
        captured = board.apply(move)
        nodes += perft(board, depth - 1)
        board.undo(move, captured)
    return nodes


class BoardTests(unittest.TestCase):
    def test_start_perft_and_restoration(self):
        board = Board.from_fen(START_FEN)
        self.assertEqual(perft(board, 1), 44)
        self.assertEqual(perft(board, 2), 1920)
        self.assertEqual(perft(board, 3), 79666)
        self.assertEqual(board.to_fen(), START_FEN)

    def test_rejects_malformed_fen(self):
        invalid = ("", "9/9 w", "9/9/9/9/9/9/9/9/9/8 w",
                   "9/9/9/9/9/9/9/9/9/10 w", START_FEN.replace(" w ", " x "))
        for fen in invalid:
            with self.subTest(fen=fen), self.assertRaises(ValueError):
                Board.from_fen(fen)

    def test_ucci_validation_and_round_trip(self):
        for value in ("", "a0", "j0a0", "a:a1", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Board.from_ucci(value)
        for move in ("a0a1", "h2e2", "i9h9"):
            self.assertEqual(Board.ucci(Board.from_ucci(move)), move)


if __name__ == "__main__":
    unittest.main()
