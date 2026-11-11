from pathlib import Path
import unittest
from unittest.mock import patch

import cv2

from board import Board, START_FEN
import game_sandbox as gs
from recognizer import recognize_occupancy, recognize_highlight_indices


class SandboxReliabilityTests(unittest.TestCase):
    def test_an_unavailable_screenshot_is_a_safe_skip_not_a_probe_type_error(self):
        board = Board.from_fen(START_FEN)
        plan = {"route": [{"ucci": "h2e2"}, {"ucci": "b9c7"}]}
        with patch.object(gs, "_wait_for_position", return_value=None), \
             patch.object(gs, "screenshot", return_value=None), \
             patch("diagnostics.capture_diagnostic", return_value=None), patch.object(gs, "tap") as tap:
            result = gs.play_plan("mock", board, "red_bottom", plan)
        self.assertFalse(result["entered"])
        self.assertTrue(result["safe_exit"])
        self.assertEqual(len(result["probe"]["strong_differences"]), 90)
        tap.assert_not_called()

    def test_real_glowing_rook_is_recognized_without_inventing_a_piece_or_side(self):
        frame = cv2.imread(str(Path(__file__).resolve().parent.parent / "fixture/jj_sandbox_rook_glow.png"))
        board = Board.from_fen("3akab2/1r7/2n1b4/p1R1p4/2c2P2p/1N3N3/P3P3P/B3C4/9/3AKAB2 w - - 0 1")
        self.assertIsNone(recognize_occupancy(frame, "red_bottom")[10])
        self.assertEqual(recognize_highlight_indices(frame, "red_bottom", [10])[10], "r")
        self.assertTrue(gs._position_matches(board, frame, "red_bottom"))
        wrong_side = board.copy()
        wrong_side.sq[10] = "R"
        self.assertFalse(gs._position_matches(wrong_side, frame, "red_bottom"))
        missing = board.copy()
        missing.sq[10] = None
        self.assertFalse(gs._position_matches(missing, frame, "red_bottom"))

    def return_to_live(self, frames, *, matches=lambda frame: [], postgame=lambda frame: False):
        stream = iter(frames[1:])
        latest = [frames[-1]]
        def screenshot(*args):
            latest[0] = next(stream, latest[0])
            return latest[0]
        with patch.object(gs, "screenshot", side_effect=screenshot), \
             patch.object(gs, "_dismiss_interruption", return_value=None), \
             patch.object(gs, "_detect_sandbox_exit", side_effect=lambda frame: (240, 1850) if frame == "sandbox" else None), \
             patch.object(gs, "_live_sequence", side_effect=lambda b, frame, ori: matches(frame)), \
             patch("postgame.has_actionable_postgame", side_effect=postgame), \
             patch("diagnostics.capture_diagnostic"), patch.object(gs, "_position_probe", return_value={}), \
             patch.object(gs.time, "sleep"), patch.object(gs, "tap") as tap, patch.object(gs, "adb") as adb:
            ok = gs._return_to_live("mock", Board.from_fen(START_FEN), "red_bottom", initial_image=frames[0])
        adb.assert_not_called()
        return ok, tap

    def test_a_matching_position_inside_sandbox_is_never_accepted_as_live_return(self):
        ok, tap = self.return_to_live(["sandbox"])
        self.assertFalse(ok)
        self.assertEqual(tap.call_count, 2)
        self.assertTrue(all(c.args == (240, 1850) for c in tap.call_args_list))

    def test_a_slow_exit_waits_for_the_real_board_without_blind_taps(self):
        ok, tap = self.return_to_live(["sandbox", "loading", "loading", "live"],
                                     matches=lambda frame: [] if frame == "live" else None)
        self.assertTrue(ok)
        tap.assert_called_once_with(240, 1850, device="mock")

    def test_an_unknown_screen_does_not_get_a_coordinate_exit_or_back_key(self):
        ok, tap = self.return_to_live(["unknown"], matches=lambda frame: None)
        self.assertFalse(ok)
        tap.assert_not_called()

    def test_a_confirmed_real_game_result_is_safe_even_though_the_board_is_gone(self):
        ok, tap = self.return_to_live(["result", "result"], matches=lambda frame: None,
                                     postgame=lambda frame: frame == "result")
        self.assertTrue(ok)
        tap.assert_not_called()
        ok, _ = self.return_to_live(["result", "unknown"], matches=lambda frame: None,
                                   postgame=lambda frame: frame == "result")
        self.assertFalse(ok)

    def test_a_2_5_second_animation_keeps_polling_without_repeated_board_input(self):
        now = [0.]
        def read(*args):
            now[0] += .8
            return "frame"
        with patch.object(gs, "_endpoint_confirmed", side_effect=lambda *args: now[0] >= 2.4), \
             patch.object(gs, "_detect_sandbox_exit", return_value=(240, 1850)), \
             patch.object(gs, "screenshot", side_effect=read), patch.object(gs.time, "sleep"), \
             patch.object(gs.time, "monotonic", side_effect=lambda: now[0]), patch.object(gs, "tap") as tap:
            _, ok = gs._settle_endpoint("mock", Board(), (0, 1), "frame", "red_bottom", 10, lambda: False)
        self.assertTrue(ok)
        self.assertGreaterEqual(now[0], 2.4)
        tap.assert_not_called()

    def test_move_animation_cannot_authorize_a_click_after_platform_closes_sandbox(self):
        with patch.object(gs, "_detect_sandbox_exit", return_value=None), \
             patch.object(gs, "screenshot") as read, patch.object(gs, "tap") as tap:
            _, ok = gs._settle_sandbox_position("mock", Board(), object(), "red_bottom", 10, lambda: False)
        self.assertFalse(ok)
        read.assert_not_called()
        tap.assert_not_called()
