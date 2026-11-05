from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import Mock, patch

from board import Board
import game_sandbox
from live_host import LiveController, LiveSettings
import player


class SandboxReadinessTests(unittest.TestCase):
    def run_demo(self, speech_enabled):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "live.json"
            config.write_text(json.dumps({"commentary_enabled": True, "speech_enabled": speech_enabled}), encoding="utf-8")
            events = Mock()
            def play(*args, **kwargs):
                self.assertTrue(kwargs["on_cue"]("intro", finish=True, deadline=1e20))
                self.assertTrue(kwargs["on_cue"]("1", finish=False, deadline=1e20))
                return {"entered": True, "safe_exit": True, "completed": 1}
            with patch.object(player, "LIVE_CFG_PATH", str(config)), \
                 patch.object(player, "write_runtime_status"), \
                 patch("game_sandbox.enabled", return_value=True), \
                 patch("game_sandbox.play_plan", side_effect=play) as simulate, \
                 patch("sandbox_narration.prepared", return_value=False), \
                 patch("sandbox_narration.wait_receipt") as wait:
                result = player.run_game_sandbox("mock", Board(), "black_bottom", {
                    "reason": "关键交换", "route": [{"ucci": "a0a1"}]},
                    "no-old-receipt", 1, events, lambda: False)
            return result, events, simulate, wait

    def test_text_only_sandbox_emits_cues_without_waiting_for_audio(self):
        result, events, play, wait = self.run_demo(False)
        self.assertTrue(result["entered"])
        play.assert_called_once()
        wait.assert_not_called()
        self.assertEqual([call.kwargs["cue"] for call in events.emit.call_args_list
                          if call.kwargs.get("action") == "cue"], ["intro", "1"])

    def test_old_host_without_step_receipts_cannot_open_a_silent_audio_demo(self):
        result, _, play, wait = self.run_demo(True)
        self.assertFalse(result["entered"])
        self.assertTrue(result["safe_exit"])
        play.assert_not_called()
        wait.assert_not_called()

    def test_defending_against_mate_does_not_open_an_optional_sandbox(self):
        board = Board.from_fen("9/3k5/9/4R4/9/2C6/9/3A5/4AK3/r8 b - - 0 1")
        analysis = {"score_type": "mate", "score": -6}
        self.assertIsNone(game_sandbox.build_plan("黑方 将4进1", analysis, [], "d8d7", board))
        self.assertIsNone(LiveController._sandtable_plan({"analysis": analysis, "sandbox_plan": {"kind": "tactic"}}))

    def test_losing_commentary_prioritizes_danger_and_does_not_repeat_attack_boasts(self):
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(commentary_enabled=True, sandtable_enabled=False)
        controller.pipeline, controller._notify = Mock(), Mock()
        def event(number, notation, analysis):
            return {"event": "engine_analysis", "number": number, "notation": notation,
                    "speech_token": str(number), "analysis": analysis, "side": "b",
                    "queen_evidence": {"本手作用": ["这辆车盯住了对面的七路炮。"]}}
        controller.submit_chess(event(68, "黑方 士5进4", {"score_type": "cp", "score": -308}))
        self.assertIn("局面吃亏", controller.pipeline.prepare_move.call_args.kwargs["direct_text"])
        controller.submit_chess(event(105, "黑方 车1平3", {"score_type": "mate", "score": -8}))
        self.assertIn("先防守", controller.pipeline.prepare_move.call_args.kwargs["direct_text"])
        controller.submit_chess(event(106, "黑方 车3平1", {"score_type": "mate", "score": -8}))
        text = controller.pipeline.prepare_move.call_args.kwargs["direct_text"]
        self.assertEqual(text, "车3平1。")
        self.assertNotIn("盯住", text)


if __name__ == "__main__":
    unittest.main()
