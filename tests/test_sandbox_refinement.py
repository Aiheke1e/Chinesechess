import unittest
from unittest.mock import Mock, patch

import numpy as np

from board import Board
import game_sandbox as gs
import sandbox_narration as sn
from sandbox_narration import script
from sandbox_story import SandboxStory
from tests.test_sandbox_presentation import plan_fixture, LINE


class SandboxRefinementTests(unittest.TestCase):
    def test_step_lines_explain_verified_recaptures_and_rotate_the_wording(self):
        route = [
            {"ucci": "a0a1", "notation": "红方 车一平二", "side": "r", "capture": "n"},
            {"ucci": "b0a1", "notation": "黑方 车九平八", "side": "b", "capture": "R"},
            {"ucci": "a1a2", "notation": "红方 马三进四", "side": "r", "capture": None},
        ]
        first = script({"kind": "tactic", "phase": "after_move", "own": "r",
                        "reason": "关键交换", "route": route, "narration_variant": 0})
        second = script({"kind": "tactic", "phase": "after_move", "own": "r",
                         "reason": "关键交换", "route": route, "narration_variant": 1})
        self.assertIn("回吃", first["1"])
        self.assertIn("回吃", second["1"])
        self.assertNotEqual(first["1"], second["1"])
        self.assertNotEqual(first["2"], second["2"])

    def test_route_budget_scales_past_the_old_thirty_second_cutoff(self):
        route = [{"ucci": f"a{index % 10}b{index % 10}",
                  "notation": ("红方 " if index % 2 == 0 else "黑方 ") + "车一平二",
                  "side": "r" if index % 2 == 0 else "b"}
                 for index in range(8)]
        plan = {"kind": "tactic", "phase": "after_move", "own": "r",
                "reason": "关键交换", "route": route}
        budget = sn.demo_budget(plan)
        self.assertGreater(budget, 30)
        self.assertLessEqual(budget, 65)

    def test_cached_summary_does_not_override_eight_rotating_natural_endings(self):
        _, _, plan = plan_fixture()
        plan["summary"] = "这条线双方各换掉一辆车，交换的代价相同。回到实战。"
        story = SandboxStory()
        lines = [script(story.prepare(str(i), plan))["summary"] for i in range(8)]
        self.assertEqual(len(set(lines)), 8)
        self.assertTrue(all("兑炮" in line for line in lines))
        self.assertFalse(any(word in "".join(lines) for word in ("交换的代价相同", "回到实战", "赚了一门炮")))

    def test_entry_accepts_a_delayed_transition_without_reclicking_the_button(self):
        board, _, plan = plan_fixture(); board.apply(Board.from_ucci(LINE[0]))
        frame, now = np.zeros((1920, 1080, 3), np.uint8), [0.]
        def screenshot(*a): now[0] += .8; return frame
        entered = Mock()
        with patch.object(gs, "_wait_for_position", return_value=frame), \
             patch.object(gs, "screenshot", side_effect=screenshot), \
             patch.object(gs.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(gs.time, "sleep"), patch.object(gs, "_region_difference", return_value=5), \
             patch.object(gs, "_detect_sandbox_exit", side_effect=lambda image: (200, 1850) if now[0] >= 2.4 else None), \
             patch.object(gs, "_position_matches", return_value=True), \
             patch.object(gs, "_return_to_live", return_value=True), patch.object(gs, "tap") as tap:
            result = gs.play_plan("mock", board, "red_bottom", plan, on_enter=entered,
                                  on_cue=lambda *a, **kw: False, review_seconds=0)
        self.assertTrue(result["entered"])
        entered.assert_called_once()
        tap.assert_called_once()
        self.assertEqual(result["completed"], 0)

    def test_a_partial_entry_is_cleaned_up_even_if_the_last_frame_looks_unchanged(self):
        board, _, plan = plan_fixture(); board.apply(Board.from_ucci(LINE[0]))
        frame, now = np.zeros((1920, 1080, 3), np.uint8), [0.]
        def screenshot(*a): now[0] += .5; return frame
        with patch.object(gs, "_wait_for_position", return_value=frame), \
             patch.object(gs, "screenshot", side_effect=screenshot), \
             patch.object(gs.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(gs.time, "sleep"), patch.object(gs, "_region_difference", return_value=0), \
             patch.object(gs, "_detect_sandbox_exit", side_effect=lambda image: (200, 1850) if now[0] == .5 else None), \
             patch.object(gs, "_position_matches", return_value=False), \
             patch.object(gs, "_return_to_live", return_value=True) as restore, \
             patch("diagnostics.capture_diagnostic", return_value="entry.png"), patch.object(gs, "tap") as tap:
            result = gs.play_plan("mock", board, "red_bottom", plan)
        self.assertFalse(result["entered"])
        self.assertTrue(result["safe_exit"])
        self.assertEqual(result["diagnostic"], "entry.png")
        self.assertFalse(result["entry_check"]["exit_visible"])
        restore.assert_called_once(); tap.assert_called_once()


if __name__ == "__main__":
    unittest.main()
