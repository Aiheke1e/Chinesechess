import unittest
from unittest.mock import patch

import numpy as np

from board import Board
from game_sandbox import build_plan, play_plan
from notation import move_description
from sandbox_narration import script
from thinking_sandbox import ThinkingSandbox, thinking_candidate


def short_plan():
    board = Board.from_fen("1rbakab1r/9/2n6/p3p3p/2p1cNp2/1C7/P1P1N3P/4C4/4A2n1/1RBAK1c2 b - - 0 1")
    probe, line = board.copy(), []
    for ucci in ("i9h9", "e2e5", "e6e5", "b0b2"):
        move = Board.from_ucci(ucci)
        line.append((ucci, move_description(probe, move)))
        probe.apply(move)
    plan = build_plan(line[0][1], {}, line, line[0][0], board)
    board.apply(Board.from_ucci(line[0][0]))
    return board, thinking_candidate(plan)


class ThinkingSandboxTests(unittest.TestCase):
    def test_complete_short_exchange_can_be_deferred_with_a_bounded_budget(self):
        board, plan = short_plan()
        self.assertTrue(plan["thinking_wait"])
        self.assertLessEqual(plan["max_seconds"], 20)
        self.assertEqual([item["ucci"] for item in plan["route"]], ["e2e5", "e6e5"])
        self.assertNotEqual(board.turn, plan["own"])

    def test_mate_and_longer_demos_keep_their_original_timing(self):
        _, short = short_plan()
        for plan in (None, {"kind": "mate", "phase": "before_move"},
                     {**short, "thinking_wait": False, "route": short["route"] * 3}):
            self.assertIs(thinking_candidate(plan), plan)

    def test_unchanged_wait_is_required_and_each_demo_runs_only_once(self):
        board, plan = short_plan()
        wait = ThinkingSandbox()
        wait.arm(board, plan, "one", 8)
        self.assertIsNone(wait.take(board, 8, 7.9, stable=True))
        self.assertIsNone(wait.take(board, 8, 8, stable=False))
        self.assertEqual(wait.take(board, 8, 8, stable=True)["token"], "one")
        self.assertIsNone(wait.take(board, 8, 30, stable=True))

    def test_move_new_scope_and_verified_disconnect_cancel_the_pending_demo(self):
        board, plan = short_plan()
        for condition in ("move", "number", "disconnect"):
            wait = ThinkingSandbox(); wait.arm(board, plan, "one", 8)
            current = board.copy()
            if condition == "move":
                current.apply(Board.from_ucci(plan["route"][0]["ucci"]))
            self.assertIsNone(wait.take(current, 9 if condition == "number" else 8, 30,
                                        stable=True, disconnected=condition == "disconnect"))
            self.assertIsNone(wait.take(board, 8, 40, stable=True))

    def test_waiting_intro_varies_and_describes_a_hypothesis(self):
        _, plan = short_plan()
        intros = [script({**plan, "narration_variant": i})["intro"] for i in range(4)]
        self.assertEqual(len(set(intros)), 4)
        self.assertTrue(script(plan)["1"].startswith("假设对面"))
        self.assertFalse(any(word in "".join(intros) for word in ("挂机", "掉线", "吓跑", "猜中")))

    def test_a_reply_arriving_before_entry_skips_the_wait_demo_without_advancing_it(self):
        board, plan = short_plan()
        with patch("game_sandbox._wait_for_position", return_value=None), \
             patch("game_sandbox.screenshot", return_value=np.zeros((1920, 1080, 3), np.uint8)), \
             patch("game_sandbox._position_probe", return_value={"strong_differences": [1, 2],
                   "presence_differences": [1, 2], "presence_sequence": ["e2e5"]}), \
             patch("game_sandbox._advance_after_observed_reply") as advance, \
             patch("game_sandbox.tap") as tap, patch("diagnostics.capture_diagnostic"):
            result = play_plan("mock", board, "black_bottom", plan)
        self.assertFalse(result["entered"])
        self.assertTrue(result["safe_exit"])
        tap.assert_not_called(); advance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
