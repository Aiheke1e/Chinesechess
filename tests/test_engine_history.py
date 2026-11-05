from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from board import Board, START_FEN
from engine_history import PositionHistory
from pikafish import DEFAULT_EXE, Pikafish
from player import DEFAULT_CFG, pick_move
from session_state import load_session, save_session


class EngineHistoryTests(unittest.TestCase):
    def position(self):
        board = Board.from_fen(START_FEN)
        history = PositionHistory(board)
        for ucci in ("h2e2", "b7e7"):
            move = Board.from_ucci(ucci)
            board.apply(move)
            history.append(move, board)
        return board, history

    def test_confirmed_both_sides_moves_reach_engine_in_order(self):
        board, history = self.position()
        engine = Mock(bestmove=Mock(return_value="b0c2"))
        move, _ = pick_move(board, DEFAULT_CFG, engine, history=history, excluded_moves=["a0a1"])
        self.assertEqual(Board.ucci(move), "b0c2")
        options = engine.bestmove.call_args.kwargs
        self.assertEqual(options["root_fen"], START_FEN)
        self.assertEqual(options["moves"], ["h2e2", "b7e7"])
        self.assertNotIn("a0a1", options["searchmoves"])
        self.assertEqual(history.moves, ["h2e2", "b7e7"])

    def test_checkpoint_and_fast_reply_resume_keep_history_once(self):
        board = Board.from_fen(START_FEN)
        history = PositionHistory(board)
        move = Board.from_ucci("h2e2")
        board.apply(move)
        history.append(move, board)
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "session.json")
            save_session(board, "red_bottom", "r", path=path, own_moves=1, history=history.export())
            session = load_session(path)
            reply = Board.from_ucci("b7e7")
            board.apply(reply)
            session["inferred_moves"] = [reply]
            restored = PositionHistory.restore(board, session)
        self.assertEqual(restored.root_fen, START_FEN)
        self.assertEqual(restored.moves, ["h2e2", "b7e7"])
        self.assertEqual(restored.board.to_fen(), board.to_fen())

    def test_corrupt_or_unrelated_history_resets_to_the_verified_current_board(self):
        board, _ = self.position()
        for stored in (None, {"root_fen": START_FEN, "moves": ["a0a9"]},
                       {"root_fen": START_FEN, "moves": []},
                       {"root_fen": "bad", "moves": ["h2e2"]}):
            history = PositionHistory.restore(board, {"history": stored})
            self.assertEqual(history.root_fen, board.to_fen())
            self.assertEqual(history.moves, [])

    def test_unconfirmed_attempt_cannot_be_added_to_history(self):
        board, history = self.position()
        before = history.export()
        move = Board.from_ucci("b0c2")
        # An unchanged trusted board is not confirmation of this attempted move.
        history.append(move, board)
        self.assertEqual(history.moves, [])
        self.assertEqual(history.root_fen, board.to_fen())
        self.assertEqual(before["moves"], ["h2e2", "b7e7"])

    def test_recorded_rook_cannon_chase_is_avoided_before_another_platform_rejection(self):
        board = Board.from_fen("4ka3/4a4/4r4/9/2pn5/p8/6R2/B6C1/4A4/3AK4 b - - 0 1")
        history = PositionHistory(board)
        for _ in range(3):
            for ucci in ("e7h7", "h2e2", "h7e7", "e2h2"):
                move = Board.from_ucci(ucci)
                self.assertIn(move, board.legal_moves())
                board.apply(move)
                history.append(move, board)
        self.assertIn("e7h7", history.repeated_chases(board))
        engine = Mock(bestmove=Mock(return_value="e8f7"))
        pick_move(board, DEFAULT_CFG, engine, history=history)
        self.assertNotIn("e7h7", engine.bestmove.call_args.kwargs["searchmoves"])

    def test_quiet_horse_repetition_is_not_mistaken_for_long_chase(self):
        board = Board.from_fen(START_FEN)
        history = PositionHistory(board)
        for _ in range(3):
            for ucci in ("b0c2", "b9c7", "c2b0", "c7b9"):
                move = Board.from_ucci(ucci)
                board.apply(move)
                history.append(move, board)
        self.assertNotIn("b0c2", history.repeated_chases(board))

    @unittest.skipUnless(os.path.exists(DEFAULT_EXE), "Pikafish binary not present")
    def test_real_engine_receives_the_root_and_moves_protocol(self):
        board, history = self.position()
        engine = Pikafish(DEFAULT_EXE)
        try:
            with patch.object(engine, "send", wraps=engine.send) as send:
                result = engine.bestmove(board.to_fen(), depth=3,
                                         root_fen=history.root_fen, moves=history.moves)
            self.assertIn(Board.from_ucci(result), board.legal_moves())
            self.assertIn(f"position fen {START_FEN} moves h2e2 b7e7", [call.args[0] for call in send.call_args_list])
            with self.assertRaises(ValueError):
                engine.bestmove(board.to_fen(), root_fen=START_FEN, moves=["h2e2\ngo depth 1"])
        finally:
            engine.quit()


if __name__ == "__main__":
    unittest.main()
