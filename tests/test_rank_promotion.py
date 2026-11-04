import itertools
from pathlib import Path
import unittest
from unittest.mock import call, patch

import cv2
import numpy as np

from board import Board
from postgame import (detect_rank_promotion_dismiss, handle_postgame,
                      has_actionable_postgame)


ROOT = Path(__file__).resolve().parent.parent


class RankPromotionTests(unittest.TestCase):
    def setUp(self):
        self.rank = cv2.imread(str(ROOT / "fixture/jj_rank_promotion.png"))
        self.assertIsNotNone(self.rank)

    def test_observed_rank_page_is_actionable_across_scale_and_brightness(self):
        frames = [self.rank, cv2.resize(self.rank, (720, 1280)),
                  cv2.convertScaleAbs(self.rank, alpha=.65, beta=5),
                  cv2.cvtColor(cv2.cvtColor(self.rank, cv2.COLOR_BGR2GRAY),
                               cv2.COLOR_GRAY2BGR)]
        for frame in frames:
            with self.subTest(size=frame.shape):
                point = detect_rank_promotion_dismiss(frame)
                self.assertIsNotNone(point)
                height, width = frame.shape[:2]
                self.assertAlmostEqual(point[0] / width, 88 / 1080, delta=.003)
                self.assertAlmostEqual(point[1] / height, 1830 / 1920, delta=.003)
                self.assertTrue(has_actionable_postgame(frame))

    def test_rank_emblem_and_colour_are_not_used_as_page_identity(self):
        different_rank = self.rank.copy()
        different_rank[180:550, 50:490] = (20, 160, 100)
        self.assertIsNotNone(detect_rank_promotion_dismiss(different_rank))

    def test_requires_both_the_promotion_title_and_the_return_arrow(self):
        for region in ((slice(10, 100), slice(155, 385)),
                       (slice(875, 955), slice(10, 85))):
            incomplete = self.rank.copy()
            incomplete[region] = 100
            self.assertIsNone(detect_rank_promotion_dismiss(incomplete))

    def test_live_board_and_unknown_fullscreen_pages_never_get_a_return_target(self):
        for name in ("jj_live.png", "jj_before.png", "jj_new.png"):
            self.assertIsNone(detect_rank_promotion_dismiss(cv2.imread(str(ROOT / name))))
        self.assertIsNone(detect_rank_promotion_dismiss(np.full_like(self.rank, 100)))

    def test_promotion_returns_to_result_flow_without_xml_or_clicking_the_finished_board(self):
        # Closing promotion briefly exposes the old terminal board before
        # JJ reveals replay.  Keep waiting for that control, never for a move.
        board_image = np.full((1920, 1080, 3), 160, np.uint8)
        replay = np.zeros_like(board_image)
        cv2.rectangle(replay, (330, 1580), (750, 1700), (40, 190, 60), -1)
        terminal = Board.from_fen(
            "2bakRCc1/4a4/2n4r1/p1N1p4/1r3Np1p/2P6/P3P3P/5C3/9/2BAKAB2 b")
        frames = [self.rank, self.rank, board_image, board_image,
                  replay, replay, board_image]
        with patch("postgame.screenshot", side_effect=frames), \
             patch("postgame.read_ui_xml") as xml, \
             patch("postgame.adb") as adb, patch("postgame.tap") as tap, \
             patch("postgame.time.sleep"), \
             patch("recognizer.recognize_position", return_value=(terminal.sq, "red_bottom")):
            actions = handle_postgame("mock")
        self.assertEqual(actions, ["关闭段位提升", "结算页主按钮"])
        self.assertEqual(tap.call_args_list,
                         [call(44, 915, device="mock"), call(540, 1640, device="mock")])
        xml.assert_not_called()
        adb.assert_not_called()

    def test_vanished_promotion_never_sends_its_old_return_coordinates(self):
        board_image = np.full((1920, 1080, 3), 160, np.uint8)
        frames = iter([self.rank])
        with patch("postgame.screenshot", side_effect=lambda *a: next(frames, board_image)), \
             patch("postgame.tap") as tap, \
             patch("postgame.time.monotonic", side_effect=itertools.count(0, .1)), \
             patch("postgame.time.sleep"), \
             patch("recognizer.recognize_position", return_value=(["P"] * 90, "red_bottom")):
            self.assertEqual(handle_postgame("mock", timeout=.4), [])
        tap.assert_not_called()

    def test_unchanged_rank_page_is_not_repeatedly_clicked_within_one_attempt(self):
        with patch("postgame.screenshot", return_value=self.rank), \
             patch("postgame.tap") as tap, patch("postgame.time.sleep"), \
             patch("postgame.wait_for_screen_change", return_value=False):
            handle_postgame("mock")
        tap.assert_called_once_with(44, 915, device="mock")


if __name__ == "__main__":
    unittest.main()
