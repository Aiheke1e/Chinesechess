import os
import unittest

from board import Board, START_FEN
from engine import choose_move
from pikafish import DEFAULT_EXE


class EngineProtocolTests(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(DEFAULT_EXE), "Pikafish binary not present")
    def test_modern_pikafish_uci_protocol(self):
        board = Board.from_fen(START_FEN)
        move, _ = choose_move(board, engine_path=DEFAULT_EXE, depth=2,
                              mode="pikafish")
        self.assertIn(move, board.legal_moves())


if __name__ == "__main__":
    unittest.main()
