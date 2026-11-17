import io
import itertools
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import Mock, patch

import cv2
import numpy as np

from board import Board, START_FEN
import player


class GameOverDetectionTests(unittest.TestCase):
    def test_startup_recheck_runs_in_read_only_watchdog_thread(self):
        board = Board.from_fen(START_FEN)
        thread_names = []

        def read_board(*args, **kwargs):
            thread_names.append(player.threading.current_thread().name)
            return (None, None) if len(thread_names) == 1 else (board, "red_bottom")

        with (patch.object(player, "postgame_screen_visible", return_value=False),
              patch.object(player, "read_stable_board", side_effect=read_board),
              patch.object(player.time, "sleep")):
            state, observed, orientation = player.monitor_startup_board(
                "mock", "r", 20, 0.35, "auto", lambda: False, timeout=1.0)

        self.assertEqual(state, "board")
        self.assertIs(observed, board)
        self.assertEqual(orientation, "red_bottom")
        self.assertEqual(thread_names, ["startup-board-watchdog"] * 2)

    def test_immediate_resignation_during_startup_is_a_completed_game(self):
        cfg = dict(player.DEFAULT_CFG, my_color="r", auto_continue_after_game=True)
        events = Mock()
        with ExitStack() as stack:
            patches = [
                patch.object(player.sys, "argv", ["player.py"]),
                patch.object(player, "load_cfg", return_value=cfg),
                patch.object(player, "list_devices", return_value=["mock"]),
                patch.object(player, "match_live_session", return_value=None),
                patch.object(player, "read_stable_board", return_value=(None, None)),
                patch.object(player, "postgame_screen_visible", return_value=True),
                patch.object(player, "result_screen_details", return_value={
                    "result": "won", "finish_cause": "opponent_resigned",
                    "finish_evidence": "jj_result_text"}),
                patch.object(player, "finish_session"),
                patch.object(player, "write_runtime_status"),
                patch.object(player, "EventLog", return_value=events),
                patch("postgame.handle_postgame", return_value=["结算页主按钮"]),
            ]
            for item in patches:
                stack.enter_context(item)
            with redirect_stdout(io.StringIO()):
                result = player.main()

        self.assertEqual(result, 10)
        names = [call.args[0] for call in events.emit.call_args_list]
        self.assertIn("game_over", names)
        self.assertIn("end", names)
        self.assertIn("postgame_continue", names)
        game_over = next(call.kwargs for call in events.emit.call_args_list
                         if call.args[0] == "game_over")
        self.assertEqual(game_over["result"], "won")
        self.assertEqual(game_over["finish_cause"], "opponent_resigned")

    def run_unconfirmed_input(self, postgame):
        board = Board.from_fen(START_FEN)
        original = board.to_fen()
        move = Board.from_ucci("h2e2")
        cfg = dict(player.DEFAULT_CFG, my_color="r", max_moves=1, dry_run=False,
                   auto_continue_after_game=False)
        events, engine = Mock(), Mock(last_analysis={}, last_candidates=[])
        with ExitStack() as stack:
            for item in (
                patch.object(player.sys, "argv", ["player.py", "--once"]),
                patch.object(player, "load_cfg", return_value=cfg),
                patch.object(player, "list_devices", return_value=["mock"]),
                patch.object(player, "match_live_session", return_value=None),
                patch.object(player, "read_stable_board", return_value=(board, "red_bottom")),
                patch.object(player, "save_session"), patch.object(player, "block_position"),
                patch.object(player, "write_runtime_status"), patch.object(player, "capture_diagnostic"),
                patch.object(player, "EventLog", return_value=events),
                patch("pikafish.get_engine", return_value=engine),
                patch.object(player, "pick_move", return_value=(move, "mock engine")),
                patch.object(player, "wait_for_speech_start", return_value=False),
                patch.object(player, "move_piece_retry", return_value=(False, "FAIL 输入已发送但未确认")),
                patch.object(player, "read_squares", return_value=(None, None)),
                patch.object(player, "screenshot", return_value=None),
                patch.object(player, "wait_for_postgame_screen", return_value=postgame),
                patch.object(player, "result_screen_details", return_value={}),
            ):
                stack.enter_context(item)
            with redirect_stdout(io.StringIO()):
                result = player.main()
        self.assertEqual(board.to_fen(), original)
        return result, [(c.args[0], c.kwargs) for c in events.emit.call_args_list]

    def test_result_animation_is_unconfirmed_input_not_a_rejected_move(self):
        result, events = self.run_unconfirmed_input(True)
        self.assertEqual(result, 0)
        names = [name for name, _ in events]
        self.assertIn("move_unconfirmed", names)
        self.assertIn("game_over", names)
        self.assertNotIn("move_rejected", names)
        self.assertNotIn("move_applied", names)
        self.assertEqual(next(data for name, data in events if name == "move_unconfirmed")["resolution"], "postgame")
        self.assertEqual(next(data for name, data in events if name == "game_over")["result"], "finished")

    def test_an_unexplained_input_failure_still_requires_recovery(self):
        result, events = self.run_unconfirmed_input(False)
        self.assertEqual(result, 14)
        names = [name for name, _ in events]
        self.assertIn("move_rejected", names)
        self.assertNotIn("move_unconfirmed", names)
        self.assertNotIn("game_over", names)

    def test_result_is_detected_even_when_board_may_still_be_visible(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with (patch("postgame.detect_large_action_button",
                    return_value=(540, 1670)),
              patch("postgame.detect_modal_close", return_value=None)):
            self.assertTrue(player.postgame_image_visible(image))

    def test_waits_for_delayed_surrender_result_screen(self):
        with (patch.object(player, "postgame_screen_visible",
                           side_effect=[False, False, True]),
              patch.object(player.time, "sleep")):
            self.assertTrue(player.wait_for_postgame_screen(
                "emulator-5554", timeout=5.0, interval=0.01))

    def test_central_confirmation_requires_two_frames(self):
        modal = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(modal, (410, 1000), (670, 1110), (40, 190, 60), -1)
        self.assertTrue(player.postgame_image_visible(modal))
        with patch.object(player, "screenshot", return_value=modal), \
             patch.object(player.time, "sleep"):
            self.assertTrue(player.postgame_screen_visible("mock", modal))
        with patch.object(player, "screenshot", return_value=np.zeros_like(modal)), \
             patch.object(player.time, "sleep"):
            self.assertFalse(player.postgame_screen_visible("mock", modal))

    def test_resignation_animation_waits_for_confirm_then_continues_without_back(self):
        # Reproduce the observed flow: an unreadable animation lasts several
        # polls, then only a central confirmation appears (no bottom replay).
        animation = np.zeros((1920, 1080, 3), dtype=np.uint8)
        modal = animation.copy()
        cv2.rectangle(modal, (410, 1000), (670, 1110), (40, 190, 60), -1)
        frames = iter([animation] * 4)
        board = Board.from_fen(START_FEN)
        board.turn = "b"
        cfg = dict(player.DEFAULT_CFG, my_color="r", auto_continue_after_game=True)
        events, engine = Mock(), Mock()
        with ExitStack() as stack:
            patches = [
                patch.object(player.sys, "argv", ["player.py"]),
                patch.object(player, "load_cfg", return_value=cfg),
                patch.object(player, "list_devices", return_value=["mock"]),
                patch.object(player, "match_live_session", return_value=None),
                patch.object(player, "read_stable_board", return_value=(board, "red_bottom")),
                patch.object(player, "infer_turn_from_standard", return_value=None),
                patch.object(player, "save_session"),
                patch.object(player, "write_runtime_status"),
                patch.object(player, "capture_diagnostic"),
                patch.object(player, "EventLog", return_value=events),
                patch("pikafish.get_engine", return_value=engine),
                patch.object(player, "screenshot", side_effect=lambda *a: next(frames, modal)),
                patch.object(player, "resolve_move_from_change", return_value=(None, 2)),
                patch.object(player, "assess_image_against_board", return_value=(None, "illegal", 2)),
                patch("recognizer.recognize_position", return_value=(None, None)),
                patch("postgame.read_ui_xml", return_value=""),
                patch.object(player.time, "monotonic", side_effect=itertools.count(100)),
                patch.object(player.time, "sleep"),
            ]
            for item in patches:
                stack.enter_context(item)
            adb = stack.enter_context(patch("postgame.adb"))
            move = stack.enter_context(patch.object(player, "move_piece_retry"))
            handler = stack.enter_context(patch("postgame.handle_postgame",
                return_value=["结算确认", "结算页主按钮"]))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(player.main(), 10)
        adb.assert_not_called()
        move.assert_not_called()
        handler.assert_called_once()
        self.assertEqual(handler.call_args.args, ("mock",))
        self.assertTrue(handler.call_args.kwargs["continue_allowed"]())
        names = [call.args[0] for call in events.emit.call_args_list]
        self.assertIn("recognition_warning", names)
        self.assertIn("game_over", names)
        self.assertIn("postgame_continue", names)
        self.assertNotIn("ui_interruption_dismissed", names)

    def test_checkmated_side_has_no_legal_moves(self):
        # 黑将在九宫顶边，被双车封死且正在被将。
        board = Board.from_fen("4k4/3R1R3/4R4/9/9/9/9/9/9/4K4 b")
        self.assertTrue(board.in_check("b"))
        self.assertEqual(board.legal_moves(), [])


if __name__ == "__main__":
    unittest.main()
