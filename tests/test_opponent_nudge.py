from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from board import Board, START_FEN
import opponent_nudge as nudge


ROOT = Path(__file__).resolve().parent.parent


class OpponentNudgeTests(unittest.TestCase):
    def setUp(self):
        self.live = cv2.resize(cv2.imread(str(ROOT / "fixture/jj_opponent_countdown.png")), (1080, 1920))
        self.panel = cv2.imread(str(ROOT / "fixture/jj_emoji_panel.png"))
        self.panel[20:225, 420:660] = self.live[20:225, 420:660]
        self.board = Board.from_fen(START_FEN)
        self.board.turn = "b"

    def run_attempt(self, frames, unchanged=True, stop=lambda: False):
        with patch.object(nudge, "screenshot", side_effect=frames), \
             patch.object(nudge, "_unchanged", side_effect=unchanged if isinstance(unchanged, list) else None,
                          return_value=unchanged), \
             patch.object(nudge.time, "sleep"), patch.object(nudge, "tap") as tap:
            result = nudge.send_wait_cloud("mock", self.board, "red_bottom", "r", stop)
        return result, [call.args for call in tap.call_args_list]

    def test_real_picker_cloud_and_smile_are_recognized_at_supported_scales(self):
        for size in ((1080, 1920), (720, 1280), (540, 960)):
            with self.subTest(size=size):
                panel = cv2.resize(self.panel, size)
                live = cv2.resize(self.live, size)
                self.assertTrue(nudge.emoji_panel_visible(panel))
                cloud = nudge.cloud_button(panel)
                self.assertIsNotNone(cloud)
                self.assertLess(cloud[0], size[0] * .24)
                self.assertGreater(cloud[1], size[1] * .86)
                self.assertIsNone(nudge.cloud_button(live))
                self.assertIsNotNone(nudge.emoji_button(live))

    def test_only_first_free_emoji_is_selected_once_without_mutating_the_board(self):
        before = self.board.to_fen()
        result, taps = self.run_attempt([self.live, self.panel, self.panel, self.live])
        self.assertTrue(result["sent"])
        self.assertEqual(taps, [nudge.emoji_button(self.live), nudge.cloud_button(self.panel)])
        self.assertEqual(self.board.to_fen(), before)

    def test_tabs_and_cloud_are_both_required_and_paid_icons_are_never_fallbacks(self):
        cloud_only = self.panel.copy()
        cloud_only[1530:1650] = 0
        paid_only = self.panel.copy()
        paid_only[1650:1815, 90:275] = 0
        for image in (cloud_only, paid_only, None, np.zeros((100, 60, 3), np.uint8)):
            self.assertIsNone(nudge.cloud_button(image))
        result, taps = self.run_attempt([self.live, paid_only, paid_only])
        self.assertFalse(result["sent"])
        self.assertEqual(taps, [nudge.emoji_button(self.live), (540, 336)])

    def test_a_coin_price_added_to_the_cloud_slot_disables_sending(self):
        paid_cloud = self.panel.copy()
        # Real coin badge from the neighbouring paid item, transplanted under
        # the otherwise unchanged cloud: matching its picture alone is unsafe.
        paid_cloud[1780:1823, 100:146] = self.panel[1771:1814, 350:396]
        self.assertTrue(nudge.emoji_panel_visible(paid_cloud))
        self.assertIsNone(nudge.cloud_button(paid_cloud))
        result, taps = self.run_attempt([self.live, paid_cloud, paid_cloud])
        self.assertFalse(result["sent"])
        self.assertEqual(taps, [nudge.emoji_button(self.live), (540, 336)])

    def test_pause_our_turn_unknown_side_and_missing_board_never_send_input(self):
        for side, stop in (("b", lambda: False), ("?", lambda: False), ("r", lambda: True)):
            with patch.object(nudge, "screenshot") as shot, patch.object(nudge, "tap") as tap:
                self.assertFalse(nudge.send_wait_cloud("mock", self.board, "red_bottom", side, stop)["sent"])
                shot.assert_not_called()
                tap.assert_not_called()
        result, taps = self.run_attempt([None])
        self.assertFalse(result["sent"])
        self.assertEqual(taps, [])

    def test_already_open_picker_is_left_alone(self):
        result, taps = self.run_attempt([self.panel])
        self.assertFalse(result["sent"])
        self.assertEqual(taps, [])

    def test_changed_board_before_opening_cannot_trigger_a_click(self):
        result, taps = self.run_attempt([self.live], unchanged=False)
        self.assertTrue(result["board_changed"])
        self.assertEqual(taps, [])

    def test_move_during_opening_or_before_selection_dismisses_without_sending(self):
        for values, frames in (([True, False], [self.live, self.panel, self.panel]),
                               ([True, True, False], [self.live, self.panel, self.panel, self.panel])):
            result, taps = self.run_attempt(frames, unchanged=values)
            self.assertTrue(result["board_changed"])
            self.assertFalse(result["sent"])
            self.assertEqual(taps, [nudge.emoji_button(self.live), (540, 336)])

    def test_missing_picker_never_becomes_a_coordinate_click_on_the_board(self):
        result, taps = self.run_attempt([self.live, self.live, self.live])
        self.assertFalse(result["sent"])
        self.assertEqual(taps, [nudge.emoji_button(self.live)])

    def test_pause_after_opening_blocks_emoji_and_all_later_input(self):
        paused = [False]
        def screenshots():
            yield self.live
            paused[0] = True
            yield self.panel
        result, taps = self.run_attempt(screenshots(), stop=lambda: paused[0])
        self.assertFalse(result["sent"])
        self.assertEqual(taps, [nudge.emoji_button(self.live)])

    def test_unacknowledged_send_is_not_retried(self):
        result, taps = self.run_attempt([self.live, self.panel, self.panel, self.panel, self.panel])
        self.assertFalse(result["sent"])
        self.assertEqual(taps, [nudge.emoji_button(self.live), nudge.cloud_button(self.panel), (540, 336)])

    def test_optional_emoji_io_failure_does_not_stop_chess_and_closes_confirmed_picker(self):
        failure = subprocess.TimeoutExpired("mock", 1)
        result, taps = self.run_attempt([self.live, failure, self.panel])
        self.assertFalse(result["sent"])
        self.assertEqual(taps, [nudge.emoji_button(self.live), (540, 336)])

    def test_board_gate_rejects_even_one_occupancy_difference_and_sandbox(self):
        same = [None if p is None else ("R" if p.isupper() else "r") for p in self.board.sq]
        different = same[:]
        different[0] = None
        for observed, expected in ((same, True), (different, False)):
            with patch.object(nudge, "_detect_sandbox_exit", return_value=None), \
                 patch.object(nudge, "recognize_occupancy", return_value=observed), \
                 patch.object(nudge, "recognize_presence_occupancy", return_value=observed):
                self.assertEqual(nudge._unchanged(self.board, self.live, "red_bottom"), expected)
        with patch.object(nudge, "_detect_sandbox_exit", return_value=(20, 20)):
            self.assertFalse(nudge._unchanged(self.board, self.live, "red_bottom"))


if __name__ == "__main__":
    unittest.main()
