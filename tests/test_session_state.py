import os
import tempfile
import time
import unittest

from board import Board, START_FEN
from session_state import (block_position, blocked_position_matches,
                           clear_blocked_position, infer_turn_from_standard,
                           load_session, save_session)
from state import resolve_legal_sequence_by_occupancy


class SessionStateTests(unittest.TestCase):
    def test_round_trip_trusted_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "session.json")
            board = Board.from_fen(START_FEN)
            board.apply(Board.from_ucci("h2e2"))
            save_session(board, "red_bottom", "r", {67}, path=path)
            loaded = load_session(path)
            self.assertEqual(loaded["board"].to_fen(), board.to_fen())
            self.assertEqual(loaded["tolerant_indices"], [67])

    def test_inactive_session_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "session.json")
            save_session(Board.from_fen(START_FEN), "red_bottom", "r",
                         active=False, path=path)
            self.assertIsNone(load_session(path))

    def test_recovers_two_fast_moves_by_occupancy(self):
        board = Board.from_fen(START_FEN)
        after = board.copy()
        moves = [Board.from_ucci("h2e2"), Board.from_ucci("b7e7")]
        for move in moves:
            after.apply(move)
        occupancy = [None if piece is None else
                     ("R" if piece.isupper() else "r") for piece in after.sq]
        self.assertEqual(resolve_legal_sequence_by_occupancy(
            board, occupancy, max_plies=2), moves)

    def test_repairs_wrong_turn_on_first_visible_move(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "session.json")
            board = Board.from_fen(START_FEN)
            board.apply(Board.from_ucci("g0e2"))
            board.turn = "r"  # Simulate the old ambiguous auto-detection bug.
            save_session(board, "black_bottom", "b", path=path)
            loaded = load_session(path)
            self.assertEqual(loaded["board"].turn, "b")

    def test_infers_turn_directly_on_first_captureless_move(self):
        board = Board.from_fen(START_FEN)
        board.apply(Board.from_ucci("c0e2"))
        self.assertEqual(infer_turn_from_standard(board.sq), "b")

    def test_blocked_position_is_only_a_temporary_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "blocked.json")
            board = Board.from_fen(START_FEN)
            block_position(board, "test", path=path)
            self.assertTrue(blocked_position_matches(board.sq, path=path))
            self.assertFalse(blocked_position_matches(
                board.sq, path=path, max_age=-1))
            self.assertTrue(clear_blocked_position(path))
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
