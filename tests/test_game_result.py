from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

import numpy as np

from board import START_FEN
from broadcast_flow import BroadcastFlow
from game_result import classify_result, read_result
from live_host import LiveController, LiveSettings


class GameResultTests(unittest.TestCase):
    def test_explicit_subject_and_finish_cause_are_required(self):
        for text, result, cause in (
            ("对 方 超 时，您获胜！", "won", "opponent_timeout"),
            ("对手走棋超时，您获胜", "won", "opponent_timeout"),
            ("您落子超时，被判负", "lost", "own_timeout"),
            ("对方认输，您获胜", "won", "opponent_resigned"),
            ("对手已经退出，您获胜", "won", "opponent_left"),
            ("您投降，本局结束", "lost", "own_resigned"),
        ):
            self.assertEqual(classify_result(text), {"result": result, "finish_cause": cause,
                                                    "finish_evidence": "jj_result_text"})
        for text in ("对面还在思考", "等了60秒", "超时", "对方没有超时", "对方超时了吗？",
                     "如果对方超时，您获胜", "超时规则：对方超时您获胜", "对方超时，您也超时",
                     "对方超时，您输了"):
            self.assertEqual(classify_result(text), {}, text)

    def test_local_ocr_is_gated_to_a_confirmed_result_and_cleans_its_crop(self):
        frame = np.zeros((1920, 1080, 3), np.uint8)
        with patch("postgame.has_actionable_postgame", return_value=False), patch("game_result.subprocess.run") as run:
            self.assertEqual(read_result(frame), {})
            run.assert_not_called()
        completed = subprocess.CompletedProcess([], 0, "对方超时，您获胜".encode("utf-8"), b"")
        with patch("postgame.has_actionable_postgame", return_value=True), \
             patch("game_result.subprocess.run", return_value=completed) as run:
            self.assertEqual(read_result(frame)["finish_cause"], "opponent_timeout")
            self.assertFalse(Path(run.call_args.args[0][-1]).exists())

    def test_ocr_timeout_is_optional_and_does_not_invent_a_result(self):
        with patch("postgame.has_actionable_postgame", return_value=True), \
             patch("game_result.subprocess.run", side_effect=subprocess.TimeoutExpired("mock", 3)):
            self.assertEqual(read_result(np.zeros((1920, 1080, 3), np.uint8)), {})

    def test_confirmed_timeout_closings_rotate_and_include_the_requested_joke(self):
        flow, lines = BroadcastFlow(), []
        event = {"result": "won", "reason": "jj_result_screen", "finish_cause": "opponent_timeout",
                 "finish_evidence": "jj_result_text"}
        for _ in range(8):
            flow.start({})
            lines.append(flow.end(event))
            self.assertEqual(flow.end(event), "")
        self.assertEqual(len(set(lines)), 8)
        self.assertTrue(all("超时" in line or "时间" in line for line in lines))
        self.assertIn("又吓跑一个，有请下一位受害者", "".join(lines))
        flow.start({})
        self.assertNotIn("受害者", flow.end(event, playful=False))

    def test_unknown_results_and_our_timeout_are_never_announced_as_an_opponent_fleeing(self):
        for result, evidence in (("finished", "jj_result_text"), ("won", "long_wait")):
            flow = BroadcastFlow()
            line = flow.end({"result": result, "reason": "jj_result_screen", "finish_cause": "opponent_timeout",
                             "finish_evidence": evidence})
            for word in ("吓跑", "受害者", "对面超时"):
                self.assertNotIn(word, line)
        flow = BroadcastFlow()
        line = flow.end({**classify_result("您超时，判负"), "reason": "jj_result_screen"})
        self.assertIn("咱们超时", line)
        self.assertNotIn("对面", line)

    def test_verified_finish_cause_reaches_the_actual_speech_pipeline(self):
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings()
        controller.pipeline = Mock()
        controller._notify = Mock(); controller._close_sandtable = Mock()
        controller.submit_chess({"event": "game_over", "fen": START_FEN,
            **classify_result("对方超时，您获胜"), "reason": "jj_result_screen"})
        controller.pipeline.submit.assert_called_once()
        self.assertEqual(controller.pipeline.submit.call_args.args[1], "获胜收官")
        self.assertIn("对面这手超时", controller.pipeline.submit.call_args.kwargs["direct_text"])


if __name__ == "__main__":
    unittest.main()
