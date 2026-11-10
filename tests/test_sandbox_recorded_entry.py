from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from board import Board
import game_sandbox as gs
from notation import move_description


FEN = "rnbakabR1/9/1c4n2/p1p1p1p1p/9/6P2/P1P1P3P/NCc1C1N2/4A4/R3KAB2 b - - 0 1"
LINE = ("g7h9", "e2e6", "b7b5", "e6e5", "c6c5", "e5b5")


class RecordedSandboxEntryTests(unittest.TestCase):
    def setUp(self):
        self.board = Board.from_fen(FEN)
        self.frame = cv2.imread(str(Path(__file__).resolve().parent.parent / "fixture/jj_sandbox_reply_during_entry.png"))
        board, route = self.board.copy(), []
        for ucci in LINE:
            move = Board.from_ucci(ucci)
            self.assertIn(move, board.legal_moves())
            route.append({"ucci": ucci, "notation": move_description(board, move)})
            board.apply(move)
        self.plan = {"phase": "after_move", "kind": "tactic", "route": route, "max_seconds": 30}

    def test_the_recorded_dimmed_picker_has_a_strict_reply_match_despite_no_presence_match(self):
        probe = gs._position_probe(self.board, self.frame, "red_bottom")
        self.assertIsNone(probe["presence_sequence"])
        self.assertEqual(probe["strong_sequence"], ["g7h9"])
        result = gs._advance_after_observed_reply(self.board, gs.validated_route(self.board, self.plan["route"]), probe)
        self.assertIsNotNone(result)
        self.assertTrue(gs._position_matches(result[0], self.frame, "red_bottom"))
        self.assertEqual(len(result[1]), 5)

    def test_real_capture_during_entry_does_not_falsely_reject_the_opened_sandbox(self):
        entered = Mock()
        with patch.object(gs, "_wait_for_position", return_value=np.zeros_like(self.frame)), \
             patch.object(gs, "screenshot", return_value=self.frame), \
             patch.object(gs, "_return_to_live", return_value=True), \
             patch.object(gs.time, "sleep"), patch.object(gs, "tap") as tap:
            result = gs.play_plan("mock", self.board, "red_bottom", self.plan,
                on_enter=entered, on_cue=lambda *args, **kwargs: False, review_seconds=0)
        self.assertTrue(result["entered"])
        self.assertTrue(result["safe_exit"])
        self.assertEqual(result["observed_reply"], "g7h9")
        entered.assert_called_once(); tap.assert_called_once()
        self.assertEqual(self.board.to_fen(), FEN)

    def test_a_different_legal_reply_is_not_fabricated_as_the_expected_line(self):
        move = Board.from_ucci("b9c7")
        self.assertIn(move, self.board.legal_moves())
        route = [({"ucci": "b9c7"}, move), *gs.validated_route(self.board, self.plan["route"])[1:]]
        probe = gs._position_probe(self.board, self.frame, "red_bottom")
        self.assertIsNone(gs._advance_after_observed_reply(self.board, route, probe))


if __name__ == "__main__":
    unittest.main()
