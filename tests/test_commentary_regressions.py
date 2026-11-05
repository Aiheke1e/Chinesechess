import unittest
from unittest.mock import Mock

from board import Board
from commentary_text import connected_move_note
from live_host import LiveController, LiveSettings
from queen_commentary import move_effects


class CommentaryRegressionTests(unittest.TestCase):
    def test_recorded_routine_defence_does_not_become_an_idle_explanation(self):
        # These positions produced repeated '照应士/仕/兵/车' in the live log.
        cases = [
            ("rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/4C2C1/9/RNBAKABNR b", "b9c7"),
            ("rnb1kabnr/4a4/2c4c1/p1p1p1p1p/9/2P6/P3P1P1P/1C2C4/9/RNBAKABNR w", "b0c2"),
            ("4kab1r/4a4/2c1b1n2/p1n3p1p/1r7/2p1P4/P3N1P1P/1C2C4/8R/1RBAKAB2 w", "i1b1"),
        ]
        for fen, ucci in cases:
            board = Board.from_fen(fen)
            original = board.to_fen()
            self.assertEqual(move_effects(board, Board.from_ucci(ucci)), [])
            self.assertEqual(board.to_fen(), original)

    def test_recorded_continuous_check_keeps_the_rook_capture_and_exact_move(self):
        previous = {"number": 18, "ucci": "h2h0", "fen_before":
            "2bak1C2/4a4/2c6/p3p3p/2p3P2/4cR3/Pr6P/2N1B2r1/R3A4/2BAK4 b"}
        current = {"number": 19, "ucci": "h0f0", "fen_before":
            "2bak1C2/4a4/2c6/p3p3p/2p3P2/4c4/Pr6P/2N1B4/R3A4/2BAKR1r1 b"}
        text = connected_move_note(current, previous, {"ucci": "f4f0"})
        self.assertEqual(text, "车8平6，吃掉一辆车，继续将军。")

    def controller(self):
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(sandtable_enabled=False, speech_enabled=True)
        controller.pipeline = Mock()
        controller._notify = Mock()
        controller.last_engine_score = 0
        return controller

    def test_effect_shortened_in_move_call_is_not_repeated_in_idle_speech(self):
        controller = self.controller()
        controller.submit_chess({"event": "engine_analysis", "number": 12,
            "notation": "黑方 马8进7", "side": "b", "speech_token": "attack-12",
            "analysis": {"score_type": "cp", "score": 230},
            "queen_evidence": {"本手作用": ["这匹马盯住了对面的三路炮。"],
                "选择理由": ["马向中路靠拢"], "值得展开": True}})
        prepared = controller.pipeline.prepare_move.call_args
        self.assertEqual(prepared.kwargs["direct_text"], "马8进7，盯住了对面的三路炮。")
        self.assertEqual(prepared.args[1]["解说对应走法"], "黑方 马8进7")
        controller.pipeline.prepare_idle.assert_not_called()

    def test_quiet_pv_does_not_trigger_an_empty_opponent_prediction_or_review(self):
        controller = self.controller()
        controller.reply_context = {"实际应手": "红方 车九平八", "预判兑现": False}
        controller._needs_silence_filler = lambda: True
        controller.submit_chess({"event": "engine_analysis", "number": 20,
            "notation": "黑方 卒9进1", "side": "b", "speech_token": "quiet-20",
            "analysis": {"score_type": "cp", "score": 20},
            "queen_evidence": {"选择理由": ["卒向前推进"],
                "最可能应手": "红方 兵九进一", "主线参考": ["红方 兵九进一"]},
            "pv": [{"notation": "黑方 卒9进1", "ucci": "i6i5"},
                   {"notation": "红方 兵九进一", "ucci": "a3a4"}]})
        controller.pipeline.prepare_idle.assert_not_called()
        self.assertIsNone(controller.reply_context)

    def test_old_prediction_review_cannot_replace_a_new_verified_followup(self):
        controller = self.controller()
        controller.reply_context = {"实际应手": "红方 车九平八", "预判兑现": False}
        defence = "这匹马给正在受攻的二路车补上保护。"
        controller.submit_chess({"event": "engine_analysis", "number": 12,
            "notation": "黑方 马8进7", "side": "b", "speech_token": "two-effects",
            "analysis": {"score_type": "cp", "score": 230},
            "queen_evidence": {"本手作用": ["这匹马盯住了对面的三路炮。", defence],
                "选择理由": ["马向中路靠拢"], "值得展开": True}})
        followup = controller.pipeline.prepare_idle.call_args
        self.assertEqual(followup.kwargs["direct_text"], defence)
        self.assertNotIn("上一手预判复盘", followup.args[0])

    def test_sandbox_names_both_sides_of_the_conditional_line(self):
        text = LiveController._sandbox_speech({"kind": "tactic", "reason": "后续吃大子",
            "target": "黑方 马6进5，吃马", "route": [
                {"notation": "红方 马七进五"}, {"notation": "黑方 马6进5，吃马"},
                {"notation": "红方 炮五进四"}]})
        self.assertIn("假设对面马七进五", text)
        self.assertIn("咱们马6进5，吃马", text)
        self.assertIn("对面炮五进四", text)
        self.assertNotIn("吃完以后", text)


if __name__ == "__main__":
    unittest.main()
