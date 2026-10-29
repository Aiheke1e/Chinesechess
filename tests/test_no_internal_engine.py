import unittest

import engine
from board import Board, START_FEN


class NoInternalEngineTests(unittest.TestCase):
    def test_python_search_was_removed(self):
        self.assertFalse(hasattr(engine, "search"))
        self.assertFalse(hasattr(engine, "_order_moves"))

    def test_internal_mode_is_rejected(self):
        board = Board.from_fen(START_FEN)
        with self.assertRaisesRegex(ValueError, "仅支持 Pikafish"):
            engine.choose_move(board, mode="internal")


if __name__ == "__main__":
    unittest.main()
