from pathlib import Path
import unittest
from unittest.mock import patch

import cv2

from board import Board
import game_sandbox as gs
from recognizer import recognize_occupancy, recognize_presence_occupancy
from sandbox_narration import result_note, script
from sandbox_story import SandboxStory
from tests.test_sandbox_presentation import plan_fixture


class ContinuousSessionTests(unittest.TestCase):
    def test_recorded_dimmed_glow_requires_complementary_recognizers(self):
        board = Board.from_fen("2bakab1r/9/2n1c2cn/p1p1p1p1p/1r7/2P3P2/P3P3P/1CN3NC1/9/1RBAKAB1R w - - 0 1")
        frame = cv2.imread(str(Path(__file__).resolve().parent.parent / "fixture/jj_sandbox_complementary_glow.png"))
        self.assertIsNone(recognize_occupancy(frame, "red_bottom")[26])
        self.assertIsNotNone(recognize_presence_occupancy(frame, "red_bottom")[26])
        self.assertTrue(gs._position_matches(board, frame, "red_bottom"))
        # A real move cannot be repaired into the old position by merging.
        moved = board.copy()
        moved.apply(Board.from_ucci("b2a2"))
        self.assertFalse(gs._position_matches(moved, frame, "red_bottom"))

    def test_an_in_flight_move_can_settle_without_a_second_input(self):
        with patch.object(gs, "_endpoint_confirmed", side_effect=[False, True, True]), \
             patch.object(gs, "_detect_sandbox_exit", return_value=(240, 1850)), \
             patch.object(gs, "screenshot", return_value="settled"), \
             patch.object(gs.time, "sleep"), patch.object(gs.time, "monotonic", return_value=1), \
             patch.object(gs, "tap") as tap:
            frame, ok = gs._settle_endpoint("mock", Board(), (0, 1), "moving", "red_bottom", 5, lambda: False)
        self.assertTrue(ok)
        self.assertEqual(frame, "settled")
        tap.assert_not_called()

    def test_auto_exit_or_pause_never_retries_capture_or_waits_on_live_board(self):
        for cancelled, mode in ((False, None), (True, (240, 1850))):
            with patch.object(gs, "_endpoint_confirmed", return_value=False), \
                 patch.object(gs, "_detect_sandbox_exit", return_value=mode), \
                 patch.object(gs.time, "monotonic", return_value=1), \
                 patch.object(gs, "screenshot") as read, patch.object(gs, "tap") as tap:
                _, ok = gs._settle_endpoint("mock", Board(), (0, 1), "frame", "red_bottom", 5, lambda: cancelled)
            self.assertFalse(ok)
            tap.assert_not_called()
            read.assert_not_called()

    def test_failed_mate_does_not_reopen_with_a_different_short_suffix(self):
        now = [100.]
        cadence = gs.SandboxCadence(clock=lambda: now[0])
        plan = {"kind": "mate", "route": [{"ucci": u} for u in ("i6i8", "i8e8")]}
        cadence.record(25, plan, result={"entered": True, "safe_exit": True, "king_captured": False})
        other = {"kind": "mate", "route": [{"ucci": u} for u in ("i9g8", "g8e9")]}
        now[0] += 25
        self.assertFalse(cadence.allowed(other, 26))
        now[0] += 125
        self.assertTrue(cadence.allowed(other, 28))
        fresh_game = gs.SandboxCadence(clock=lambda: now[0])
        self.assertTrue(fresh_game.allowed(other, 1))

    def test_opening_names_the_actual_target_without_promising_capture_success(self):
        _, _, plan = plan_fixture()
        story = SandboxStory()
        lines = [script(story.prepare(str(i), plan))["intro"] for i in range(8)]
        self.assertEqual(len(set(lines)), 8)
        self.assertTrue(all("吃炮之后的接法" in line for line in lines))
        mate = script({"kind": "mate", "pattern": "重炮", "route": []})["intro"]
        self.assertIn("重炮", mate)
        self.assertNotIn("吃将收尾", mate)

    def test_unequal_exchange_summary_removes_the_cancelled_trade(self):
        route = [{"side": "r", "capture": "p"}, {"side": "b", "capture": "N"},
                 {"side": "r", "capture": "n"}]
        for variant in range(8):
            line = result_note(route, "r", variant=variant)
            self.assertIn("一匹马", line)
            self.assertIn("还能拿一枚卒", line)
            self.assertNotIn("可不是白拿", line)
        route.append({"side": "b", "capture": "R"})
        self.assertIn("用一辆车换一枚卒", result_note(route, "r"))


if __name__ == "__main__":
    unittest.main()
