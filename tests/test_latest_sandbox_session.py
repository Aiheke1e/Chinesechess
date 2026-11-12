from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import cv2

from board import Board
import game_sandbox as gs
from notation import move_description


class LatestSandboxSessionTests(unittest.TestCase):
    def plan(self, fen, line):
        board = Board.from_fen(fen)
        probe, pv = board.copy(), []
        for ucci in line:
            move = Board.from_ucci(ucci)
            self.assertIn(move, probe.legal_moves())
            pv.append((ucci, move_description(probe, move)))
            probe.apply(move)
        return board, gs.build_plan(pv[0][1], {}, pv, line[0], board)

    def test_recorded_one_ply_mate_does_not_reopen_the_just_shown_mating_line(self):
        now = [100.]
        first, plan = self.plan(
            "3ak4/4ar3/n1r1N4/p3C1P2/9/2P2c3/P3P3p/9/9/1RBAKABR1 w - - 0 1",
            ("e7c8", "e9f9", "h0h9"))
        final, short = self.plan(
            "3a1k3/2N1ar3/n1r6/p3C1P2/9/2P2c3/P3P3p/9/9/1RBAKABR1 w - - 0 1",
            ("h0h9",))
        self.assertEqual(len(plan["route"]), 3)
        self.assertEqual(len(short["route"]), 1)
        first.apply(Board.from_ucci("e7c8"))
        first.apply(Board.from_ucci("e9f9"))
        self.assertEqual(first.to_fen(), final.to_fen())
        cadence = gs.SandboxCadence(clock=lambda: now[0])
        cadence.record(21, plan, result={"mate_confirmed": True})
        now[0] += 9
        self.assertFalse(cadence.allowed(short, 22))
        self.assertTrue(cadence.allowed({"kind": "mate", "route": [{"ucci": "b0b9"}]}, 22))
        now[0] += 141
        self.assertTrue(cadence.allowed(short, 22))

    def test_a_one_ply_ordinary_capture_is_not_confused_with_a_mating_suffix(self):
        now = [100.]
        cadence = gs.SandboxCadence(clock=lambda: now[0])
        cadence.record(10, {"kind": "tactic", "route": [{"ucci": "e8d7"}, {"ucci": "h0h9"}]})
        now[0] += 30
        self.assertTrue(cadence.allowed({"kind": "mate", "route": [{"ucci": "h0h9"}]}, 14))

    def test_recorded_fast_recapture_is_consumed_before_the_single_entry_click(self):
        board = Board.from_fen("rn1ak4/4ar3/3Cb4/p1p1pc3/6P2/2P6/P3P3p/2N1C4/9/1RBAKABR1 b - - 0 1")
        frame = cv2.imread(str(Path(__file__).resolve().parent.parent / "fixture/jj_sandbox_reply_before_open.png"))
        probe, route = board.copy(), []
        for ucci in ("e8d7", "g5g6", "f6f3", "h0h9", "f8f9", "h9f9", "e9f9"):
            move = Board.from_ucci(ucci)
            self.assertIn(move, probe.legal_moves())
            route.append({"ucci": ucci, "notation": move_description(probe, move)})
            probe.apply(move)
        self.assertEqual(gs._position_probe(board, frame, "red_bottom")["strong_sequence"], ["e8d7"])
        entered = Mock()
        with patch.object(gs, "_wait_for_position", side_effect=[None, frame]) as stable, \
             patch.object(gs, "screenshot", return_value=frame), \
             patch.object(gs, "_detect_sandbox_exit", return_value=(240, 1850)), \
             patch.object(gs, "_region_difference", return_value=5), \
             patch.object(gs, "_return_to_live", return_value=True), \
             patch.object(gs.time, "sleep"), patch.object(gs, "tap") as tap:
            result = gs.play_plan("mock", board, "red_bottom", {"kind": "tactic", "phase": "after_move", "route": route},
                                  on_cue=lambda *args, **kwargs: False, on_enter=entered, review_seconds=0)
        self.assertTrue(result["entered"])
        self.assertEqual(result["observed_reply"], "e8d7")
        self.assertEqual([call.kwargs["required_hits"] for call in stable.call_args_list], [2, 2])
        entered.assert_called_once()
        tap.assert_called_once()
        self.assertEqual(board.turn, "b")  # Read-only reconciliation does not change trusted live history.
