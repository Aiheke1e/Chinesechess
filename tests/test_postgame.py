import inspect
import itertools
import subprocess
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from postgame import (detect_confirmation_button, detect_large_action_button,
                      detect_match_mode_button, detect_modal_close,
                      dismiss_nonboard_interruption, dismiss_rank_promotion,
                      detect_rules_overlay_dismiss,
                      handle_postgame, has_actionable_postgame,
                      parse_action_nodes, read_ui_xml, wait_for_screen_change)


class PostgameTests(unittest.TestCase):
    def test_rank_promotion_uses_text_not_tier_colour(self):
        with patch("postgame.read_ui_xml",
                   return_value='<node text="段位提升"/>'), \
             patch("postgame.adb") as adb_mock, \
             patch("postgame.time.sleep"):
            self.assertTrue(dismiss_rank_promotion("device"))
        adb_mock.assert_called_once_with(
            ["shell", "input", "keyevent", "4"], device="device")

    def test_unrelated_page_is_never_dismissed_as_rank_promotion(self):
        with patch("postgame.read_ui_xml",
                   return_value='<node text="活动中心"/>'), \
             patch("postgame.adb") as adb_mock:
            self.assertFalse(dismiss_rank_promotion("device"))
        adb_mock.assert_not_called()

    def test_unrecognized_animation_or_loading_page_never_sends_back(self):
        with patch("recognizer.recognize_position",
                   return_value=(None, None)), \
             patch("postgame.read_ui_xml", return_value=""), \
             patch("postgame.adb") as adb_mock, \
             patch("postgame.time.sleep"):
            for _ in range(4):
                self.assertFalse(dismiss_nonboard_interruption(
                    "device", np.zeros((1920, 1080, 3), dtype=np.uint8)))
        adb_mock.assert_not_called()

    def test_nonboard_rank_page_still_closes_with_explicit_text(self):
        with patch("recognizer.recognize_position", return_value=(None, None)), \
             patch("postgame.read_ui_xml", return_value='<node text="段位提升"/>'), \
             patch("postgame.adb") as adb_mock, \
             patch("postgame.time.sleep"):
            self.assertTrue(dismiss_nonboard_interruption(
                "device", np.zeros((1920, 1080, 3), dtype=np.uint8)))
        adb_mock.assert_called_once_with(
            ["shell", "input", "keyevent", "4"], device="device")

    def test_confirmation_modal_is_left_for_postgame_handler(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(image, (410, 1000), (670, 1110), (40, 190, 60), -1)
        with patch("postgame.read_ui_xml") as read_xml, \
             patch("postgame.adb") as adb_mock:
            self.assertFalse(dismiss_nonboard_interruption("device", image))
        read_xml.assert_not_called()
        adb_mock.assert_not_called()

    def test_nonboard_fallback_never_backs_out_of_recognized_board(self):
        squares = [None] * 90
        for index, piece in zip((0, 4, 85, 89), "rkRK"):
            squares[index] = piece
        with patch("recognizer.recognize_position",
                   return_value=(squares, "red_bottom")), \
             patch("postgame.adb") as adb_mock:
            self.assertFalse(dismiss_nonboard_interruption(
                "device", np.zeros((1920, 1080, 3), dtype=np.uint8)))
        adb_mock.assert_not_called()

    @staticmethod
    def _gold_mode_page(dimmed=False):
        image = np.full((1000, 600, 3), (65, 65, 65) if dimmed else
                        (80, 150, 210), dtype=np.uint8)
        if dimmed:
            image[:220] = (90, 90, 90)
        for center in (0.19, 0.40, 0.60, 0.81):
            x1, x2 = int(600 * (center - 0.085)), int(600 * (center + 0.085))
            image[720:820, x1:x2] = (35, 110, 180)
        if dimmed:
            cv2.line(image, (0, 245), (599, 245), (230, 230, 230), 4)
            cv2.line(image, (0, 705), (599, 705), (230, 230, 230), 4)
        return image

    def test_detects_new_gold_match_mode_page(self):
        point = detect_match_mode_button(self._gold_mode_page(), "15分钟")
        self.assertEqual(point, (360, 770))

    def test_detects_rules_overlay_and_returns_safe_top_blank(self):
        point = detect_rules_overlay_dismiss(self._gold_mode_page(dimmed=True))
        self.assertEqual(point, (33, 135))

    def test_clear_mode_page_is_not_treated_as_rules_overlay(self):
        self.assertIsNone(detect_rules_overlay_dismiss(
            self._gold_mode_page(dimmed=False)))

    def test_bright_live_board_lines_are_not_rules_overlay(self):
        image = np.full((1920, 1080, 3), 160, dtype=np.uint8)
        cv2.line(image, (0, 480), (1079, 480), (245, 245, 245), 8)
        cv2.line(image, (0, 1344), (1079, 1344), (245, 245, 245), 8)
        self.assertIsNone(detect_rules_overlay_dismiss(image))
    def test_central_confirmation_takes_priority_over_replay(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(image, (410, 1000), (670, 1110), (40, 190, 60), -1)
        cv2.rectangle(image, (330, 1600), (750, 1720), (40, 190, 60), -1)
        point = detect_confirmation_button(image)
        self.assertIsNotNone(point)
        self.assertLess(abs(point[1] - 1055), 10)

    def test_watcher_treats_central_confirmation_as_postgame(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(image, (410, 1000), (670, 1110), (40, 190, 60), -1)
        self.assertTrue(has_actionable_postgame(image))

    def test_handler_clicks_central_confirmation_first(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(image, (410, 1000), (670, 1110), (40, 190, 60), -1)
        cv2.rectangle(image, (330, 1600), (750, 1720), (40, 190, 60), -1)
        with (patch("postgame.screenshot", return_value=image),
              patch("postgame.read_ui_xml", return_value=""),
              patch("postgame.tap") as tap_mock,
              patch("postgame.wait_for_screen_change", return_value=False)):
            actions = handle_postgame("device", timeout=1)
        self.assertEqual(actions, ["结算确认"])
        self.assertLess(tap_mock.call_args.args[1], 1200)

    def test_detects_complete_match_mode_page(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        image[:1200] = (235, 145, 90)
        for center in (0.19, 0.40, 0.60, 0.81):
            x = int(1080 * center)
            cv2.rectangle(image, (x - 95, 1382), (x + 95, 1575),
                          (70, 160, 225), -1)
        point = detect_match_mode_button(image, "15分钟")
        self.assertIsNotNone(point)
        self.assertLess(abs(point[0] - 648), 5)

    def test_match_mode_requires_all_four_buttons(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        image[:1200] = (235, 145, 90)
        cv2.rectangle(image, (550, 1382), (740, 1575),
                      (70, 160, 225), -1)
        self.assertIsNone(detect_match_mode_button(image, "15分钟"))

    def test_returns_as_soon_as_next_board_is_visible(self):
        button = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(button, (330, 1580), (750, 1700), (40, 190, 60), -1)
        board = np.zeros_like(button)
        squares = [None] * 90
        for index in range(32):
            squares[index] = "P"
        with (patch("postgame.screenshot", side_effect=[button, button, board]),
              patch("postgame.read_ui_xml") as read_xml,
              patch("postgame.tap"),
              patch("postgame.wait_for_screen_change", return_value=True),
              patch("postgame.time.sleep"),
              patch("recognizer.recognize_position",
                    return_value=(squares, "red_bottom"))):
            self.assertEqual(handle_postgame("device", timeout=20),
                             ["结算页主按钮"])
        read_xml.assert_not_called()

    def test_confirm_and_replay_bypass_xml_and_return_on_new_board(self):
        modal = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(modal, (410, 1000), (670, 1110), (40, 190, 60), -1)
        replay = np.zeros_like(modal)
        cv2.rectangle(replay, (330, 1580), (750, 1700), (40, 190, 60), -1)
        board = np.full_like(modal, 160)
        squares = ["P"] * 32 + [None] * 58
        with patch("postgame.screenshot", side_effect=[modal, modal, replay,
                   replay, replay, board, board]), \
             patch("postgame.read_ui_xml") as xml, \
             patch("postgame.tap") as tap, patch("postgame.time.sleep"), \
             patch("recognizer.recognize_position", return_value=(squares, "red_bottom")):
            self.assertEqual(handle_postgame("mock"), ["结算确认", "结算页主按钮"])
        xml.assert_not_called()
        self.assertEqual(tap.call_count, 2)

    def test_mode_page_does_not_wait_out_the_postgame_timeout(self):
        replay = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(replay, (330, 1580), (750, 1700), (40, 190, 60), -1)
        with patch("postgame.screenshot", side_effect=[replay, replay, self._gold_mode_page()]), \
             patch("postgame.read_ui_xml") as xml, \
             patch("postgame.tap"), patch("postgame.time.sleep"), \
             patch("postgame.wait_for_screen_change", return_value=True), \
             patch("recognizer.recognize_position", return_value=(None, None)):
            self.assertEqual(handle_postgame("mock"), ["结算页主按钮"])
        xml.assert_not_called()

    def test_successful_replay_hands_off_before_loading_or_another_dump(self):
        replay = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(replay, (330, 1580), (750, 1700), (40, 190, 60), -1)
        with patch("postgame.screenshot", side_effect=[replay, replay]) as shots, \
             patch("postgame.read_ui_xml") as xml, \
             patch("postgame.tap"), patch("postgame.time.sleep"), \
             patch("postgame.wait_for_screen_change", return_value=True):
            self.assertEqual(handle_postgame("mock"), ["结算页主按钮"])
        self.assertEqual(shots.call_count, 2)
        xml.assert_not_called()

    def test_transient_control_disappearance_never_clicks_a_new_board(self):
        replay = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(replay, (330, 1580), (750, 1700), (40, 190, 60), -1)
        board = np.full_like(replay, 160)
        frames = iter([replay])
        with patch("postgame.screenshot", side_effect=lambda *a: next(frames, board)), \
             patch("postgame.time.monotonic", side_effect=itertools.count(0, .1)), \
             patch("postgame.time.sleep"), patch("postgame.tap") as tap, \
             patch("recognizer.recognize_position", return_value=(["P"] * 90, "red_bottom")):
            self.assertEqual(handle_postgame("mock", timeout=.4), [])
        tap.assert_not_called()

    def test_background_animation_does_not_acknowledge_an_unchanged_button(self):
        modal = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(modal, (410, 1000), (670, 1110), (40, 190, 60), -1)
        animated = modal.copy()
        animated[:600] = 160
        with patch("postgame.screenshot", side_effect=[animated, np.full_like(modal, 160)]) as shots, \
             patch("postgame.time.sleep"):
            self.assertTrue(wait_for_screen_change("mock", modal))
        self.assertEqual(shots.call_count, 2)

    def test_xml_timeout_returns_to_screen_polling_without_reading_stale_dump(self):
        with patch("postgame.adb", side_effect=subprocess.TimeoutExpired("adb", 2)) as adb:
            self.assertEqual(read_ui_xml("mock"), "")
        self.assertEqual(adb.call_count, 1)
        self.assertLessEqual(adb.call_args.kwargs["timeout"], 2)

    def test_postgame_allows_chained_reward_popups(self):
        default = inspect.signature(__import__("postgame").handle_postgame).parameters[
            "max_actions"].default
        self.assertGreaterEqual(default, 4)

    def test_parses_and_prioritizes_continue_text(self):
        xml = '''<hierarchy>
          <node text="返回大厅" bounds="[0,0][100,40]" />
          <node text="再来一局" bounds="[200,300][500,400]" />
          <node text="继续" bounds="[220,500][520,600]" />
        </hierarchy>'''
        actions = parse_action_nodes(xml)
        self.assertEqual(actions[0], ("继续", 370, 550))
        self.assertEqual(actions[1], ("再来一局", 350, 350))

    def test_detects_large_green_lower_button(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.rectangle(image, (330, 1580), (750, 1700), (40, 190, 60), -1)
        point = detect_large_action_button(image)
        self.assertIsNotNone(point)
        self.assertLess(abs(point[0] - 540), 10)
        self.assertLess(abs(point[1] - 1640), 10)

    def test_ignores_small_game_controls(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.circle(image, (100, 1650), 35, (40, 190, 60), -1)
        self.assertIsNone(detect_large_action_button(image))

    def test_ignores_orange_ingame_countdown(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.ellipse(image, (540, 1670), (215, 70), 0, 0, 360,
                    (20, 110, 230), -1)
        self.assertIsNone(detect_large_action_button(image))

    def test_detects_reward_modal_close_x(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.line(image, (930, 620), (975, 665), (240, 240, 240), 12)
        cv2.line(image, (975, 620), (930, 665), (240, 240, 240), 12)
        point = detect_modal_close(image)
        self.assertIsNotNone(point)
        self.assertLess(abs(point[0] - 952), 15)
        self.assertLess(abs(point[1] - 642), 15)

    def test_no_close_x_on_plain_image(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.line(image, (850, 650), (1050, 650), (240, 240, 240), 8)
        self.assertIsNone(detect_modal_close(image))

    def test_ignores_x_shape_on_bright_live_board(self):
        image = np.full((1920, 1080, 3), 165, dtype=np.uint8)
        cv2.line(image, (930, 620), (975, 665), (240, 240, 240), 12)
        cv2.line(image, (975, 620), (930, 665), (240, 240, 240), 12)
        self.assertIsNone(detect_modal_close(image))

    def test_does_not_repeat_click_when_screen_did_not_change(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        cv2.line(image, (930, 620), (975, 665), (240, 240, 240), 12)
        cv2.line(image, (975, 620), (930, 665), (240, 240, 240), 12)
        with (patch("postgame.screenshot", return_value=image),
              patch("postgame.read_ui_xml", return_value=""),
              patch("postgame.tap") as tap_mock,
              patch("postgame.wait_for_screen_change", return_value=False)):
            actions = handle_postgame("device", timeout=20)
        self.assertEqual(actions, ["关闭奖励弹窗"])
        self.assertEqual(tap_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
