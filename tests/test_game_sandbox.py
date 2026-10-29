import unittest
from unittest.mock import patch

import cv2
import numpy as np

from board import Board, START_FEN
import game_sandbox
from notation import move_description


class GameSandboxTests(unittest.TestCase):
    def test_highlight_tolerant_position_match_requires_unchanged_board(self):
        board = Board.from_fen(START_FEN)
        presence = [None if piece is None else
                    ("R" if piece.isupper() else "r")
                    for piece in board.sq]
        presence[9] = "r"  # one decorative false-positive intersection
        with patch.object(game_sandbox, "recognize_occupancy",
                          return_value=[None] * 90), \
             patch.object(game_sandbox, "recognize_presence_occupancy",
                          return_value=presence):
            self.assertTrue(game_sandbox._position_matches(
                board, object(), "red_bottom"))

        moved = board.copy()
        moved.apply(Board.from_ucci("h2e2"))
        moved_presence = [None if piece is None else
                          ("R" if piece.isupper() else "r")
                          for piece in moved.sq]
        with patch.object(game_sandbox, "recognize_occupancy",
                          return_value=[None] * 90), \
             patch.object(game_sandbox, "recognize_presence_occupancy",
                          return_value=moved_presence):
            self.assertFalse(game_sandbox._position_matches(
                board, object(), "red_bottom"))

    def test_wait_for_position_requires_two_consecutive_matches(self):
        board = Board.from_fen(START_FEN)
        frames = [object(), object(), object(), object()]
        with patch.object(game_sandbox, "screenshot",
                          side_effect=frames), \
             patch.object(game_sandbox, "_position_matches",
                          side_effect=[True, False, True, True]), \
             patch.object(game_sandbox.time, "sleep"):
            self.assertIs(frames[-1], game_sandbox._wait_for_position(
                "device", board, "red_bottom", timeout=1))

    def test_position_probe_reports_unique_opponent_move(self):
        board = Board.from_fen(START_FEN)
        move = Board.from_ucci("h2e2")
        after = board.copy()
        after.apply(move)
        occupancy = [None if piece is None else
                     ("R" if piece.isupper() else "r")
                     for piece in after.sq]
        with patch.object(game_sandbox, "recognize_occupancy",
                          return_value=occupancy), \
             patch.object(game_sandbox, "recognize_presence_occupancy",
                          return_value=occupancy):
            probe = game_sandbox._position_probe(
                board, object(), "red_bottom")
        self.assertEqual(probe["presence_sequence"], ["h2e2"])
        self.assertEqual(len(probe["presence_differences"]), 2)

    def test_fast_predicted_reply_shifts_sandbox_to_our_turn(self):
        board = Board.from_fen(START_FEN)
        route = []
        probe_board = board.copy()
        for _ in range(4):
            move = probe_board.legal_moves()[0]
            route.append(({"ucci": Board.ucci(move), "notation": "测试"},
                          move))
            probe_board.apply(move)
        result = game_sandbox._advance_after_observed_reply(
            board, route, {"presence_sequence": [route[0][0]["ucci"]]})
        self.assertIsNotNone(result)
        advanced, remaining, observed = result
        self.assertEqual(observed, route[0][0]["ucci"])
        self.assertEqual(len(remaining), 3)
        expected = board.copy()
        expected.apply(route[0][1])
        self.assertEqual(advanced.sq, expected.sq)

    def test_unpredicted_reply_never_enters_shifted_sandbox(self):
        board = Board.from_fen(START_FEN)
        route = []
        probe_board = board.copy()
        for _ in range(4):
            move = probe_board.legal_moves()[0]
            route.append(({"ucci": Board.ucci(move), "notation": "测试"},
                          move))
            probe_board.apply(move)
        self.assertIsNone(game_sandbox._advance_after_observed_reply(
            board, route, {"presence_sequence": ["a0a1"]}))

    def test_detects_large_blue_acknowledgement_notice(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        blue = cv2.cvtColor(
            np.uint8([[[100, 220, 220]]]), cv2.COLOR_HSV2BGR)[0, 0]
        image[610:1310, 135:948] = blue
        point = game_sandbox._detect_blue_acknowledge(image)
        self.assertIsNotNone(point)
        self.assertTrue(450 <= point[0] <= 650)
        self.assertTrue(1150 <= point[1] <= 1280)

    def test_detects_sandbox_red_exit_button(self):
        image = np.zeros((1920, 1080, 3), dtype=np.uint8)
        image[1800:1900, 115:370] = (20, 40, 220)
        point = game_sandbox._detect_sandbox_exit(image)
        self.assertIsNotNone(point)
        self.assertTrue(200 <= point[0] <= 280)
        self.assertTrue(1820 <= point[1] <= 1880)

    def test_plan_drops_already_executed_real_move(self):
        board = Board.from_fen(START_FEN)
        line = []
        for _ in range(5):
            move = board.legal_moves()[0]
            line.append((Board.ucci(move), move_description(board, move)))
            board.apply(move)
        plan = game_sandbox.build_plan(
            "红方 车一平二，吃炮",
            {"score_type": "cp", "score": 900, "depth": 18},
            line, line[0][0])
        self.assertIsNotNone(plan)
        self.assertEqual(plan["route"][0]["ucci"], line[1][0])
        self.assertNotIn(line[0][0],
                         [item["ucci"] for item in plan["route"]])

    def test_ordinary_move_does_not_open_sandbox(self):
        line = [("h2e2", "红方 炮二平五"),
                ("h9g7", "黑方 马8进7"),
                ("b0c2", "红方 马八进七"),
                ("b9c7", "黑方 马2进3")]
        self.assertIsNone(game_sandbox.build_plan(
            "红方 炮二平五",
            {"score_type": "cp", "score": 20, "depth": 18},
            line, "h2e2"))

    def test_deferred_plan_keeps_current_move_as_first_step(self):
        line = [("h2e2", "红方 炮二平五"),
                ("h9g7", "黑方 马8进7"),
                ("b0c2", "红方 马八进七"),
                ("b9c7", "黑方 马2进3")]
        plan = game_sandbox.build_deferred_plan("吃掉大子", line)
        self.assertIsNotNone(plan)
        self.assertTrue(plan["deferred"])
        self.assertEqual(plan["route"][0]["ucci"], "h2e2")
        self.assertEqual(len(plan["route"]), 4)

    def test_sandbox_result_declares_opponent_move_is_not_interrupt(self):
        board = Board.from_fen(START_FEN)
        route = []
        probe = board.copy()
        for _ in range(3):
            move = probe.legal_moves()[0]
            route.append({"ucci": Board.ucci(move),
                          "notation": move_description(probe, move)})
            probe.apply(move)
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        entered = []
        with patch.object(game_sandbox, "_wait_for_position",
                          return_value=frame), \
             patch.object(game_sandbox, "screenshot",
                          return_value=frame), \
             patch.object(game_sandbox, "_region_difference",
                          return_value=5.0), \
             patch.object(game_sandbox, "_detect_sandbox_exit",
                          return_value=(220, 1850)), \
             patch.object(game_sandbox, "_position_matches",
                          return_value=True), \
             patch.object(game_sandbox, "_endpoint_confirmed",
                          return_value=True), \
             patch.object(game_sandbox, "_return_to_live",
                          return_value=True), \
             patch.object(game_sandbox, "tap"), \
             patch.object(game_sandbox.time, "sleep"):
            result = game_sandbox.play_plan(
                "device", board, "red_bottom", {"route": route},
                on_enter=lambda: entered.append(True), review_seconds=0)
        self.assertTrue(result["entered"])
        self.assertEqual(entered, [True])
        self.assertFalse(result["opponent_move_interrupts"])
        self.assertEqual(result["completed"], 3)

    def test_unconfirmed_entry_never_clicks_route(self):
        board = Board.from_fen(START_FEN)
        route = []
        probe = board.copy()
        for _ in range(3):
            move = probe.legal_moves()[0]
            route.append({"ucci": Board.ucci(move),
                          "notation": move_description(probe, move)})
            probe.apply(move)
        fake_frame = object()
        with patch.object(game_sandbox, "screenshot",
                          side_effect=[fake_frame, fake_frame, fake_frame]), \
             patch.object(game_sandbox, "_position_matches",
                          return_value=True), \
             patch.object(game_sandbox, "_sandbox_button",
                          return_value=(100, 1600)), \
             patch.object(game_sandbox, "_region_difference",
                          return_value=0.0), \
             patch.object(game_sandbox, "_detect_sandbox_exit",
                          return_value=None), \
             patch.object(game_sandbox, "tap") as tap_mock, \
             patch.object(game_sandbox.time, "sleep"):
            result = game_sandbox.play_plan(
                "device", board, "red_bottom", {"route": route})
        self.assertFalse(result["entered"])
        self.assertTrue(result["safe_exit"])
        tap_mock.assert_called_once_with(100, 1600, device="device")


if __name__ == "__main__":
    unittest.main()
