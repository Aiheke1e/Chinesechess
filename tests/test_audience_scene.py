from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from audience_scene import AudienceSceneDirector
from board import Board
from control_panel import ControlPanel
import game_sandbox
from live_host import LiveController, LiveSettings, SpeechPipeline
import player
import sandbox_narration as sn
from tests.test_sandbox_presentation import plan_fixture


def event_fixture():
    board, line, plan = plan_fixture()
    event = {"event": "engine_analysis", "number": 8, "speech_token": "scene-8", "ucci": line[0][0],
             "notation": line[0][1], "fen_before": board.to_fen(), "side": board.turn,
             "analysis": {"score_type": "cp", "score": 25, "depth": 18}, "sandbox_plan": plan}
    return board, plan, event


class AudienceSceneTests(unittest.TestCase):
    def test_scene_is_rare_and_does_not_reset_on_a_recovered_player(self):
        now = [100.]
        director = AudienceSceneDirector(clock=lambda: now[0])
        _, plan, event = event_fixture()
        self.assertIsNone(director.choose({**event, "number": 7}, plan))
        self.assertIsNotNone(director.choose(event, plan))
        now[0] += 300
        self.assertIsNone(director.choose({**event, "number": 20}, plan))
        director.end_game()
        now[0] = 200
        self.assertIsNone(director.choose(event, plan))
        now[0] = 280
        self.assertIsNotNone(director.choose(event, plan))

    def test_mate_defence_disadvantage_and_short_routes_do_not_ask_the_audience(self):
        _, plan, event = event_fixture()
        for change, changed_plan in (({"analysis": {"score_type": "mate", "score": 3}}, plan),
                ({"analysis": {"score_type": "cp", "score": -300}}, plan),
                ({"dry_run": True}, plan), ({}, {**plan, "kind": "mate"}),
                ({}, {**plan, "reason": "解将后的衔接"}), ({}, {**plan, "route": plan["route"][:2]})):
            self.assertIsNone(AudienceSceneDirector().choose({**event, **change}, changed_plan))

    def test_losing_material_in_the_demonstrated_line_is_not_praised(self):
        _, plan, event = event_fixture()
        plan = deepcopy(plan)
        for item in plan["route"]:
            if item.get("capture") and item["side"] != plan["own"]:
                item["capture"] = "R" if plan["own"] == "r" else "r"
        self.assertIsNone(AudienceSceneDirector().choose(event, plan))

    def test_questions_rotate_and_no_viewer_or_real_reply_is_fabricated(self):
        now = [100.]
        director = AudienceSceneDirector(clock=lambda: now[0])
        _, plan, event = event_fixture()
        questions = []
        for index in range(4):
            if index:
                director.end_game()
                now[0] += 181
            scene = director.choose(event, plan)
            cues = sn.script({**plan, "audience_scene": scene})
            questions.append(cues["question"])
            self.assertEqual(list(cues)[:2], ["question", "intro"])
            text = "".join(cues.values())
            for made_up in ("你刚才说", "某某棋友", "有棋友说", "猜中", "押中"):
                self.assertNotIn(made_up, text)
            for item in plan["route"]:
                self.assertIn(LiveController._spoken_notation(item["notation"]), text)
            self.assertIn("兑炮", cues["summary"])
            self.assertIn("双方各少一门炮", cues["summary"])
        self.assertEqual(len(set(questions)), 4)

    def test_presentation_never_changes_the_selected_real_move_or_engine_route(self):
        board, plan, event = event_fixture()
        original_event, original_plan, original_fen = deepcopy(event), deepcopy(plan), board.to_fen()
        director = AudienceSceneDirector()
        scene = director.choose(event, plan)
        sn.script({**plan, "audience_scene": scene})
        self.assertEqual(event, original_event)
        self.assertEqual(plan, original_plan)
        self.assertEqual(board.to_fen(), original_fen)
        board.apply(Board.from_ucci(event["ucci"]))
        self.assertEqual(len(game_sandbox.validated_route(board, plan["route"])), len(plan["route"]))

    def test_question_has_a_separate_short_budget_from_the_complete_route(self):
        _, plan, event = event_fixture()
        scene = AudienceSceneDirector().choose(event, plan)
        self.assertGreater(sn.demo_budget({**plan, "audience_scene": scene}), 30)
        self.assertGreaterEqual(sn.demo_budget({**plan, "audience_scene": scene}),
                                sn.demo_budget(plan))
        self.assertLessEqual(sn.question_seconds(scene), 8)

    def test_controller_only_adds_the_scene_when_interaction_is_enabled(self):
        _, _, event = event_fixture()
        for enabled in (False, True):
            controller = object.__new__(LiveController)
            controller.settings = LiveSettings(speech_enabled=True)
            controller.enabled = enabled
            controller.pipeline, controller._notify = Mock(), Mock()
            controller.sandbox_speech_tokens = set()
            controller.sandbox_speech_by_move = {}
            controller.submit_chess(deepcopy(event))
            calls = controller.pipeline.prepare_move.call_args_list
            questions = [call for call in calls if call.args[1].get("沙盘步骤") == "question"]
            self.assertEqual(len(questions), int(enabled))
            main = calls[-1]
            self.assertEqual(main.args[1]["我方计划走法"], event["notation"])
            self.assertEqual(controller.commentary_analyses[event["speech_token"]]["ucci"], event["ucci"])
            if enabled:
                self.assertLessEqual(questions[0].args[1]["_sandbox_scene_seconds"], 8)

    def run_player(self, success=True, speech=True):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "live.json"
            config.write_text(json.dumps({"commentary_enabled": True, "speech_enabled": speech}), encoding="utf-8")
            clock, order = [100.], []
            events = Mock()
            board, plan, event = event_fixture()
            board.apply(Board.from_ucci(event["ucci"]))
            original = board.to_fen()
            def wait(token, **kwargs):
                order.append(token.rsplit(":", 1)[-1])
                clock[0] += 4
                return success
            def play(device, copied, orientation, received_plan, **kwargs):
                order.append("open")
                self.assertIsNot(copied, board)
                self.assertEqual(received_plan["route"], plan["route"])
                self.assertEqual(kwargs["max_seconds"], plan["max_seconds"])
                copied.sq[0] = None
                return {"entered": True, "safe_exit": True, "completed": len(plan["route"])}
            with patch.object(player, "LIVE_CFG_PATH", str(config)), patch.object(player, "write_runtime_status"), patch(
                    "game_sandbox.enabled", return_value=True), patch("game_sandbox.play_plan", side_effect=play) as simulate, patch(
                    "sandbox_narration.prepared", return_value=True), patch("sandbox_narration.question_budget", return_value=30), patch(
                    "sandbox_narration.wait_receipt", side_effect=wait) as waiting, patch.object(player.time, "monotonic", side_effect=lambda: clock[0]):
                result = player.run_game_sandbox("mock", board, "red_bottom", plan, "scene", 8, events, lambda: False)
            self.assertEqual(board.to_fen(), original)
            return result, order, simulate.call_count, waiting.call_count

    def test_audio_question_finishes_before_open_and_uses_the_existing_time_budget(self):
        result, order, calls, _ = self.run_player()
        self.assertEqual(order, ["question", "open"])
        self.assertTrue(result["safe_exit"])
        self.assertEqual(calls, 1)

    def test_cancelled_question_sends_no_sandbox_or_real_board_clicks(self):
        result, order, calls, _ = self.run_player(success=False)
        self.assertEqual(order, ["question"])
        self.assertFalse(result["entered"])
        self.assertTrue(result["safe_exit"])
        self.assertEqual(calls, 0)

    def test_text_only_question_does_not_wait_for_nonexistent_audio(self):
        result, order, _, waited = self.run_player(speech=False)
        self.assertTrue(result["safe_exit"])
        self.assertEqual(order, ["open"])
        self.assertEqual(waited, 0)

    def test_question_metadata_is_scoped_to_its_own_move(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(sn, "MARKERS", Path(folder)):
            payload = {"speech_token": "scene:sandbox:question", "_sandbox_cue": True,
                       "沙盘步骤": "question", "_sandbox_scene_seconds": 30}
            sn.receipt(payload, "preparing")
            self.assertEqual(sn.question_budget(payload["speech_token"]), 30)
            self.assertIsNone(sn.question_budget("next:sandbox:question"))
            sn.receipt(payload, "cancelled")
            self.assertIsNone(sn.question_budget(payload["speech_token"]))

    def test_real_pipeline_question_and_each_step_follow_their_receipts(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(sn, "MARKERS", Path(folder) / "receipts"), patch(
                "live_host.winsound.PlaySound"), patch.object(SpeechPipeline, "_audio_duration", return_value=.01):
            board, plan, event = event_fixture()
            scene = AudienceSceneDirector().choose(event, plan)
            shown_plan = {**plan, "audience_scene": scene}
            shown_plan["scene_seconds"] = sn.question_seconds(scene)
            heard, opened = [], []
            pipeline = SpeechPipeline(LiveSettings(speech_enabled=True, cooldown=0),
                lambda kind, text, data: heard.append((text, data)) if kind == "reply" else None,
                llm=Mock(side_effect=AssertionError("提问和推演只用已验证路线")),
                tts=Mock(return_value=str(Path(folder) / "voice.wav")))
            controller = object.__new__(LiveController)
            controller.pipeline = pipeline
            controller.sandbox_speech_tokens, controller.sandbox_speech_by_move = set(), {}
            token = "full-scene"
            controller._prepare_sandbox_narration(token, shown_plan, {"解说对应手数": 8, "解说对应走法": event["notation"]})
            def emit(name, **data):
                if data.get("action") == "cue":
                    pipeline.commit_move(sn.cue_token(token, data["cue"]), deadline=data["deadline"])
                if data.get("action") == "end":
                    for segment in controller.sandbox_speech_by_move[token].values():
                        pipeline.discard_move(segment)
            events = Mock()
            events.emit.side_effect = emit
            def play(*args, **kwargs):
                self.assertEqual([data["沙盘步骤"] for _, data in heard], ["question"])
                opened.append(True)
                kwargs["on_enter"]()
                self.assertTrue(kwargs["on_cue"]("intro", finish=True, deadline=1e20))
                for index, item in enumerate(plan["route"], 1):
                    self.assertTrue(kwargs["on_cue"](str(index), finish=False, deadline=1e20))
                    kwargs["on_step"](index, item)
                    self.assertTrue(kwargs["on_cue"](str(index), finish=True, deadline=1e20))
                self.assertTrue(kwargs["on_cue"]("summary", finish=True, deadline=1e20))
                return {"entered": True, "safe_exit": True, "completed": len(plan["route"])}
            config = Path(folder) / "live.json"
            config.write_text(json.dumps({"commentary_enabled": True, "speech_enabled": True}), encoding="utf-8")
            board.apply(Board.from_ucci(event["ucci"]))
            original = board.to_fen()
            try:
                with patch.object(player, "LIVE_CFG_PATH", str(config)), patch.object(player, "write_runtime_status"), patch(
                        "game_sandbox.enabled", return_value=True), patch("game_sandbox.play_plan", side_effect=play):
                    result = player.run_game_sandbox("mock", board, "red_bottom", plan, token, 8, events, lambda: False)
                self.assertEqual([data["沙盘步骤"] for _, data in heard], ["question", "intro", "1", "2", "3", "4", "summary"])
                self.assertEqual(result["completed"], len(plan["route"]))
                self.assertEqual(board.to_fen(), original)
                pipeline.llm.assert_not_called()
            finally:
                pipeline.close()

    def test_question_is_logged_as_host_speech_without_a_fake_viewer(self):
        header, body = ControlPanel._speech_log_entry({"kind": "reply", "text": "棋友们，后手在哪儿？",
            "data": {"kind": "沙盘推演解说", "沙盘步骤": "question"}})
        self.assertIn("互动提问", header)
        self.assertEqual(body, "棋友们，后手在哪儿？")


if __name__ == "__main__":
    unittest.main()
