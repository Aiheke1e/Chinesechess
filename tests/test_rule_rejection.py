from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from board import Board, START_FEN
from game_rules import RULE_REJECTED, repetition_warning
import mover
from pikafish import DEFAULT_EXE, Pikafish
import player
from postgame import detect_confirmation_button, has_actionable_postgame, postgame_action
from session_state import load_session, match_live_session, save_session
from state import after_move


ROOT = Path(__file__).resolve().parent.parent
FEN = "4ka3/4a4/4b4/p2r5/6p2/9/6P2/3AB1n2/R5N2/1cCAK1B2 w - - 0 1"


class RuleRejectionTests(unittest.TestCase):
    def setUp(self):
        self.warning = cv2.imread(str(ROOT / "fixture/jj_long_chase.png"))

    def test_actual_chase_warning_never_becomes_a_result_control(self):
        for size in ((540, 960), (720, 1280), (1080, 1920)):
            image = cv2.resize(self.warning, size)
            self.assertTrue(repetition_warning(image))
            self.assertIsNone(detect_confirmation_button(image))
            self.assertIsNone(postgame_action(image))
            self.assertFalse(has_actionable_postgame(image))

    def test_normal_boards_and_real_confirmation_remain_distinct(self):
        for name in ("fixture/jj_opponent_offline.png", "fixture/jj_opponent_countdown.png",
                     "fixture/jj_rank_promotion.png"):
            self.assertFalse(repetition_warning(cv2.imread(str(ROOT / name))))
        image = np.zeros((1920, 1080, 3), np.uint8)
        cv2.rectangle(image, (410, 1000), (670, 1110), (40, 190, 60), -1)
        self.assertFalse(repetition_warning(image))
        self.assertIsNotNone(detect_confirmation_button(image))
        self.assertTrue(has_actionable_postgame(image))
        self.assertFalse(repetition_warning(None))

    def test_mover_confirms_explicit_rejection_without_extra_taps(self):
        board = Board.from_fen(FEN)
        move = Board.from_ucci("a1b1")
        clear = np.zeros_like(self.warning)
        with patch.object(mover, "screenshot", side_effect=[clear, self.warning, self.warning]), \
             patch.object(mover, "recognize_indices", side_effect=lambda im, side, indices:
                          {index: board.sq[index] for index in indices}), \
             patch.object(mover, "recognize_presence_indices", side_effect=lambda im, side, indices:
                          {index: board.sq[index] for index in indices}), \
             patch.object(mover, "recognize_occupancy", return_value=board.sq), \
             patch.object(mover, "recognize_position", return_value=(board.sq, "red_bottom")), \
             patch.object(mover.time, "sleep"), patch.object(mover, "tap") as taps:
            ok, message = mover.move_piece(*move, "mock", expected_before=board.sq)
        self.assertFalse(ok)
        self.assertTrue(message.startswith(RULE_REJECTED))
        self.assertEqual(taps.call_count, 2)

    def test_actual_warning_and_board_recognition_confirm_rejection_together(self):
        clear = cv2.imread(str(ROOT / "fixture/jj_long_chase_before.png"))
        clear = cv2.resize(clear, (1080, 1920))
        warning = cv2.resize(self.warning, (1080, 1920))
        board = Board.from_fen(FEN)
        with patch.object(mover, "screenshot", side_effect=[clear, warning, warning]), \
             patch.object(mover.time, "sleep"), patch.object(mover, "tap") as taps:
            ok, message = mover.move_piece(*Board.from_ucci("a1b1"), "mock", expected_before=board.sq)
        self.assertFalse(ok)
        self.assertTrue(message.startswith(RULE_REJECTED))
        self.assertEqual(taps.call_count, 2)

    def test_stale_toast_cannot_reject_a_successful_alternative(self):
        board = Board.from_fen(FEN)
        move = Board.from_ucci("d2e1")
        after = after_move(board.sq, move)
        clear = np.zeros_like(self.warning)
        with patch.object(mover, "screenshot", side_effect=[self.warning, clear, self.warning]), \
             patch.object(mover, "recognize_indices", side_effect=[
                 {index: board.sq[index] for index in mover._probe_indices(board.sq, *move)},
                 {index: after[index] for index in move}]), \
             patch.object(mover.time, "sleep"), patch.object(mover, "tap") as taps:
            ok, message = mover.move_piece(*move, "mock", expected_before=board.sq)
        self.assertTrue(ok)
        self.assertNotIn(RULE_REJECTED, message)
        self.assertEqual(taps.call_count, 2)

    def test_warning_with_an_unrelated_board_cannot_authorize_a_retry(self):
        board = Board.from_fen(FEN)
        move = Board.from_ucci("a1b1")
        clear = np.zeros_like(self.warning)
        with patch.object(mover, "screenshot", return_value=clear), \
             patch.object(mover, "repetition_warning", side_effect=[False, True, True]), \
             patch.object(mover, "recognize_indices", side_effect=lambda im, side, indices:
                          {index: board.sq[index] for index in indices}), \
             patch.object(mover, "recognize_presence_indices", side_effect=lambda im, side, indices:
                          {index: board.sq[index] for index in indices}), \
             patch.object(mover, "recognize_occupancy", return_value=[None] * 90), \
             patch.object(mover, "recognize_position", return_value=(board.sq, "red_bottom")), \
             patch.object(mover.time, "monotonic", side_effect=[0, 0, 0, .1, .2, .3, .4, 2]), \
             patch.object(mover.time, "sleep"), patch.object(mover, "tap"), \
             patch("diagnostics.capture_diagnostic", return_value=None):
            ok, message = mover.move_piece(*move, "mock", expected_before=board.sq, verify_timeout=1)
        self.assertFalse(ok)
        self.assertNotIn(RULE_REJECTED, message)

    def test_engine_research_excludes_only_platform_rejected_moves(self):
        board = Board.from_fen(FEN)
        engine = Mock(bestmove=Mock(return_value="d2e1"))
        move, _ = player.pick_move(board, player.DEFAULT_CFG, engine, excluded_moves=["a1b1"])
        self.assertEqual(Board.ucci(move), "d2e1")
        allowed = engine.bestmove.call_args.kwargs["searchmoves"]
        self.assertNotIn("a1b1", allowed)
        self.assertEqual(set(allowed), {Board.ucci(move) for move in board.legal_moves()} - {"a1b1"})
        engine.bestmove.return_value = "a1b1"
        with redirect_stdout(io.StringIO()), self.assertRaises(player.EngineMoveError):
            player.pick_move(board, player.DEFAULT_CFG, engine, excluded_moves=["a1b1"])

    @unittest.skipUnless(os.path.exists(DEFAULT_EXE), "Pikafish binary not present")
    def test_real_pikafish_honors_searchmoves(self):
        engine = Pikafish(DEFAULT_EXE)
        try:
            self.assertEqual(engine.bestmove(FEN, depth=3, searchmoves=["d2e1"]), "d2e1")
        finally:
            engine.quit()

    def test_resume_preserves_move_number_and_rejected_moves(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "session.json")
            board = Board.from_fen(FEN)
            rejected = {FEN: ["a1b1"]}
            save_session(board, "red_bottom", "r", path=path, own_moves=46, rejected_moves=rejected)
            loaded = load_session(path)
            self.assertEqual(loaded["own_moves"], 46)
            self.assertEqual(loaded["rejected_moves"], rejected)
            after = board.copy()
            after.apply(Board.from_ucci("d2e1"))
            occupancy = [None if piece is None else "R" if piece.isupper() else "r" for piece in after.sq]
            with patch("session_state.screenshot", return_value=object()), \
                 patch("session_state.recognize_occupancy", return_value=occupancy), \
                 patch("session_state.time.sleep"):
                recovered = match_live_session("mock", path=path)
            self.assertEqual(recovered["own_moves"], 47)
            self.assertEqual(recovered["rejected_moves"], rejected)
            # Old session files remain readable after upgrading.
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            data.pop("own_moves")
            data.pop("rejected_moves")
            Path(path).write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(load_session(path)["own_moves"], 0)

    def test_player_changes_move_in_same_game_without_postgame_or_counter_reset(self):
        board = Board.from_fen(FEN)
        session = {"board": board, "fen": FEN, "orientation": "red_bottom", "my_color": "r",
                   "own_moves": 46, "rejected_moves": {}}
        engine = Mock(last_analysis={}, last_candidates=[], bestmove=Mock(side_effect=["a1b1", "d2e1"]))
        events = Mock()
        cfg = dict(player.DEFAULT_CFG, my_color="r", max_moves=1,
                   move_interval_min=0, move_interval_max=0, auto_continue_after_game=True)
        with ExitStack() as stack:
            for item in (
                patch.object(player.sys, "argv", ["player.py"]),
                patch.object(player, "load_cfg", return_value=cfg),
                patch.object(player, "list_devices", return_value=["mock"]),
                patch.object(player, "match_live_session", return_value=session),
                patch.object(player, "write_runtime_status"),
                patch.object(player, "EventLog", return_value=events),
                patch("pikafish.get_engine", return_value=engine),
                patch.object(player, "wait_for_speech_start", return_value=False),
                patch.object(player, "move_piece_retry", side_effect=[
                    (False, RULE_REJECTED + " 长捉违规请变招"), (True, "OK")]),
            ):
                stack.enter_context(item)
            save = stack.enter_context(patch.object(player, "save_session"))
            postgame = stack.enter_context(patch.object(player, "wait_for_postgame_screen"))
            shots = stack.enter_context(patch.object(player, "screenshot"))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(player.main(), 0)
        postgame.assert_not_called()
        shots.assert_not_called()
        self.assertEqual(engine.bestmove.call_count, 2)
        self.assertNotIn("a1b1", engine.bestmove.call_args.kwargs["searchmoves"])
        selected = [call.kwargs for call in events.emit.call_args_list if call.args[0] == "move_selected"]
        applied = [call.kwargs for call in events.emit.call_args_list if call.args[0] == "move_applied"]
        self.assertEqual([item["number"] for item in selected], [47, 47])
        self.assertEqual([(item["number"], item["ucci"]) for item in applied], [(47, "d2e1")])
        self.assertNotIn("game_over", [call.args[0] for call in events.emit.call_args_list])
        self.assertEqual(save.call_args.kwargs["own_moves"], 47)
        self.assertEqual(save.call_args.kwargs["rejected_moves"], {FEN: ["a1b1"]})


if __name__ == "__main__":
    unittest.main()
