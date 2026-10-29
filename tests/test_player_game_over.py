import unittest
from unittest.mock import patch

import numpy as np

from board import Board
import player


class GameOverDetectionTests(unittest.TestCase):
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

    def test_checkmated_side_has_no_legal_moves(self):
        # 黑将在九宫顶边，被双车封死且正在被将。
        board = Board.from_fen("4k4/3R1R3/4R4/9/9/9/9/9/9/4K4 b")
        self.assertTrue(board.in_check("b"))
        self.assertEqual(board.legal_moves(), [])


if __name__ == "__main__":
    unittest.main()
