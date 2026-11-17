from contextlib import ExitStack
import json
import itertools
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

from board import Board, START_FEN
from control_panel import ControlPanel
import game_sandbox as gs
from live_host import LiveController, LiveSettings, SpeechPipeline
from notation import move_description
import player
import sandbox_narration as sn


FEN = "rnbak3r/4a4/1c2b1nc1/p1p1p1p1p/9/2P3P2/P3P3P/1CN4C1/9/R1BAKABNR w - - 0 1"
LINE = ("h2e2", "h7h2", "h0g2", "h2e2", "b2e2", "b9a7")


def plan_fixture():
    board = Board.from_fen(FEN)
    probe, line = board.copy(), []
    for ucci in LINE:
        move = Board.from_ucci(ucci)
        line.append((ucci, move_description(probe, move)))
        probe.apply(move)
    return board, line, gs.build_plan(line[0][1], {}, line, line[0][0], board)


class SandboxPresentationTests(unittest.TestCase):
    def test_four_ply_exchange_keeps_the_final_recapture_and_drops_quiet_tail(self):
        board, line, plan = plan_fixture()
        self.assertEqual([item["ucci"] for item in plan["route"]], list(LINE[1:5]))
        self.assertEqual(len(plan["route"]), 4)
        self.assertIn("兑炮", plan["summary"])
        board.apply(Board.from_ucci(LINE[0]))
        self.assertEqual(len(gs.validated_route(board, plan["route"])), 4)
        text = LiveController._sandbox_speech(plan)
        for item in plan["route"]:
            self.assertIn(LiveController._spoken_notation(item["notation"]), text)
        self.assertIn("吃炮之后的接法", text)

    def test_pv_ending_before_a_known_recapture_does_not_invent_a_result(self):
        board, line, _ = plan_fixture()
        # Black just captured our cannon; the next red recapture is omitted.
        short = line[:4]
        self.assertIsNone(gs.build_plan(short[0][1], {}, short, short[0][0], board))

    def test_repeated_route_and_failed_attempts_have_a_cooldown(self):
        now = [100]
        cadence = gs.SandboxCadence(clock=lambda: now[0])
        _, _, plan = plan_fixture()
        self.assertTrue(cadence.allowed(plan, 1))
        cadence.record(1, plan)
        now[0] += 30
        self.assertFalse(cadence.allowed(plan, 5))
        other = {**plan, "route": [{"ucci": "a0a1"}]}
        self.assertTrue(cadence.allowed(other, 5))
        self.assertTrue(cadence.allowed({**plan, "kind": "mate"}, 2))
        now[0] += 121
        self.assertTrue(cadence.allowed(plan, 7))

    def test_two_ply_route_can_shift_after_the_actual_first_reply(self):
        board, _, plan = plan_fixture()
        board.apply(Board.from_ucci(LINE[0]))
        route = gs.validated_route(board, plan["route"][:2])
        result = gs._advance_after_observed_reply(board, route, {"presence_sequence": [LINE[1]]})
        self.assertIsNotNone(result)
        self.assertEqual(len(result[1]), 1)
        self.assertEqual(Board.ucci(result[1][0][1]), LINE[2])

    def test_receipts_are_isolated_and_do_not_accept_preparing_as_played(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(sn, "MARKERS", Path(folder)):
            payload = {"speech_token": "demo:sandbox:1", "_sandbox_cue": True}
            sn.receipt(payload, "preparing")
            self.assertFalse(sn.wait_receipt(payload["speech_token"], deadline=time.monotonic() + .01, cancelled=lambda: False))
            sn.receipt(payload, "started")
            self.assertTrue(sn.wait_receipt(payload["speech_token"], deadline=time.monotonic() + .1, cancelled=lambda: False))
            self.assertFalse(sn.wait_receipt("another:sandbox:1", deadline=time.monotonic() + .01, cancelled=lambda: False))
            sn.receipt(payload, "finished")
            self.assertTrue(sn.wait_receipt(payload["speech_token"], finished=True, deadline=time.monotonic() + .1, cancelled=lambda: False))
            sn.receipt(payload, "cancelled")
            self.assertFalse(sn.wait_receipt(payload["speech_token"], finished=True, deadline=time.monotonic() + .1, cancelled=lambda: False))

    def test_windows_reader_lock_cannot_drop_the_finished_receipt(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(sn, "MARKERS", Path(folder)):
            actual_replace = sn.os.replace
            attempts = []
            def replace(*args):
                attempts.append(True)
                if len(attempts) < 3:
                    raise PermissionError("reader still holds previous receipt")
                return actual_replace(*args)
            payload = {"speech_token": "demo:sandbox:summary", "_sandbox_cue": True}
            with patch.object(sn.os, "replace", side_effect=replace):
                sn.receipt(payload, "finished")
            self.assertTrue(sn.wait_receipt(payload["speech_token"], finished=True,
                deadline=time.monotonic() + .1, cancelled=lambda: False))

    def test_rejected_real_move_discards_every_segment_and_ignores_late_cues(self):
        _, line, plan = plan_fixture()
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(commentary_enabled=True)
        controller.pipeline, controller._notify, controller._close_sandtable = Mock(), Mock(), Mock()
        controller.sandbox_speech_tokens, controller.sandbox_speech_by_move = set(), {}
        controller._prepare_sandbox_narration("move-6", plan, {})
        expected = set(controller.sandbox_speech_tokens)
        controller.submit_chess({"event": "move_rejected", "speech_token": "move-6", "notation": line[0][1]})
        self.assertEqual(controller.sandbox_speech_tokens, set())
        self.assertEqual(controller.sandbox_speech_by_move, {})
        discarded = {call.args[0] for call in controller.pipeline.discard_move.call_args_list}
        self.assertTrue(expected <= discarded)
        controller.submit_chess({"event": "game_sandbox", "action": "cue", "cue": "1", "speech_token": "move-6"})
        controller.pipeline.commit_move.assert_not_called()

    def test_deferred_mate_call_does_not_block_entered_sandbox_audio(self):
        heard = threading.Event()
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            folder = Path(folder)
            for item in (patch.object(sn, "MARKERS", folder / "receipts"),
                         patch("live_host.SPEECH_STARTED", folder / "real_started.json"),
                         patch("live_host.SPEECH_PREPARING", folder / "real_preparing.json"),
                         patch("live_host.winsound.PlaySound"),
                         patch.object(SpeechPipeline, "_audio_duration", return_value=.005)):
                stack.enter_context(item)
            pipeline = SpeechPipeline(LiveSettings(speech_enabled=True),
                lambda kind, *args: heard.set() if kind == "reply" else None,
                tts=lambda *args: str(folder / "cue.wav"))
            try:
                pipeline.prepare_move("绝杀长解说", {"speech_token": "mate", "绝杀长播报": True},
                                      direct_text="炮五平四，重炮绝杀。")
                token = "mate:sandbox:intro"
                pipeline.prepare_move("沙盘推演解说", {"speech_token": token, "_sandbox_cue": True},
                                      direct_text="看这路重炮，摆到吃将收尾。", replace_older=False)
                pipeline.commit_move(token)
                self.assertTrue(heard.wait(1))
                self.assertTrue(sn.wait_receipt(token, finished=True, deadline=time.monotonic() + 1, cancelled=lambda: False))
                self.assertFalse((folder / "real_started.json").exists())
            finally:
                pipeline.close()

    def exercise(self, cue_callback, *, plan_override=None, board_override=None, shift_on_entry=False):
        board, _, plan = plan_fixture()
        if board_override is not None:
            board = board_override.copy()
        else:
            board.apply(Board.from_ucci(LINE[0]))
        plan = plan_override or plan
        frame = np.zeros((1920, 1080, 3), np.uint8)
        with ExitStack() as stack:
            for item in (
                patch.object(gs, "_wait_for_position", return_value=frame),
                patch.object(gs, "screenshot", return_value=frame),
                patch.object(gs, "_region_difference", return_value=5),
                patch.object(gs, "_position_matches", return_value=True,
                             side_effect=itertools.chain([False], itertools.repeat(True)) if shift_on_entry else None),
                patch.object(gs, "_position_probe", return_value={"presence_sequence": [LINE[1]]}),
                patch.object(gs, "_detect_sandbox_exit", return_value=(200, 1850)),
                patch.object(gs, "_endpoint_confirmed", return_value=True),
                patch.object(gs, "_return_to_live", return_value=True),
                patch.object(gs.time, "sleep"),
            ):
                stack.enter_context(item)
            taps = stack.enter_context(patch.object(gs, "tap"))
            result = gs.play_plan("mock", board, "red_bottom", plan, on_cue=cue_callback,
                                  review_seconds=0)
        return result, taps.call_count

    def test_intro_steps_and_result_are_requested_in_exact_order(self):
        cues = []
        result, taps = self.exercise(lambda name, **data: cues.append((name, data["finish"])))
        self.assertEqual(cues, [("intro", True), ("1", False), ("1", True),
            ("2", False), ("2", True), ("3", False), ("3", True),
            ("4", False), ("4", True), ("summary", True)])
        self.assertEqual(result["completed"], 4)
        self.assertEqual(taps, 9)

    def test_unready_step_voice_prevents_its_board_taps_and_result_summary(self):
        cues = []
        def callback(name, **data):
            cues.append(name)
            return name != "2"
        result, taps = self.exercise(callback)
        self.assertEqual(result["completed"], 1)
        self.assertTrue(result["narration_failed"])
        self.assertNotIn("summary", cues)
        self.assertEqual(taps, 3)

    def test_reply_during_entry_shifts_the_copied_board_and_skips_its_old_cue(self):
        cues = []
        result, taps = self.exercise(lambda name, **data: cues.append(name), shift_on_entry=True)
        self.assertEqual(result["observed_reply"], LINE[1])
        self.assertEqual(result["completed"], 3)
        self.assertNotIn("1", cues)
        self.assertIn("2", cues)
        self.assertEqual(taps, 7)

    def test_platform_exit_during_step_voice_stops_all_remaining_simulated_taps(self):
        def callback(name, **data):
            if name == "2":
                gs._detect_sandbox_exit.return_value = None
            return True
        result, taps = self.exercise(callback)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed_step"], 2)
        self.assertEqual(taps, 3)

    def test_multistep_mate_budget_includes_all_legal_plies_and_voice(self):
        board = Board.from_fen("3aka3/9/C8/9/4C4/9/9/9/9/4K4 w - - 0 1")
        probe, line = board.copy(), []
        for ucci in ("a7a6", "e9e8", "a6a7", "e8e9", "a7e7"):
            move = Board.from_ucci(ucci)
            line.append((ucci, move_description(probe, move)))
            probe.apply(move)
        plan = gs.build_plan(line[0][1], {}, line, line[0][0], board)
        self.assertGreater(plan["max_seconds"], 12)
        self.assertLessEqual(plan["max_seconds"], 90)
        self.assertFalse(any(item.get("illustration") for item in plan["route"]))
        self.assertIn("无棋可解", sn.script(plan)["summary"])
        now = [100.0]
        def callback(*args, **kwargs):
            now[0] += 1.0
            return True
        with patch.object(gs.time, "monotonic", side_effect=lambda: now[0]):
            result, taps = self.exercise(callback, plan_override=plan, board_override=board)
        self.assertGreaterEqual(now[0], 112)
        self.assertTrue(result["mate_confirmed"])
        self.assertEqual(result["completed"], 5)
        self.assertEqual(taps, 11)

    def test_actual_speech_pipeline_and_player_wait_for_each_cue_without_touching_real_ack(self):
        board, line, plan = plan_fixture()
        heard = []
        frame = np.zeros((1920, 1080, 3), np.uint8)
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            folder = Path(folder)
            config = folder / "live.json"
            config.write_text(json.dumps({"commentary_enabled": True, "speech_enabled": True}), encoding="utf-8")
            for item in (
                patch.object(sn, "MARKERS", folder / "receipts"),
                patch.object(player, "LIVE_CFG_PATH", str(config)),
                patch.object(player, "write_runtime_status"),
                patch("game_sandbox.enabled", return_value=True),
                patch("live_host.SPEECH_STARTED", folder / "real_started.json"),
                patch("live_host.SPEECH_PREPARING", folder / "real_preparing.json"),
                patch("live_host.winsound.PlaySound"),
                patch.object(SpeechPipeline, "_audio_duration", return_value=.005),
                patch.object(gs, "_wait_for_position", return_value=frame),
                patch.object(gs, "screenshot", return_value=frame),
                patch.object(gs, "_region_difference", return_value=5),
                patch.object(gs, "_position_matches", return_value=True),
                patch.object(gs, "_detect_sandbox_exit", return_value=(200, 1850)),
                patch.object(gs, "_endpoint_confirmed", return_value=True),
                patch.object(gs, "_return_to_live", return_value=True),
                patch.object(gs.time, "sleep"),
            ):
                stack.enter_context(item)
            settings = LiveSettings(commentary_enabled=True, speech_enabled=True)
            pipeline = SpeechPipeline(settings, lambda kind, text, data:
                heard.append((text, data)) if kind == "reply" else None,
                llm=Mock(side_effect=AssertionError("沙盘不调用模型")),
                tts=lambda *args: str(folder / "speech.wav"))
            controller = object.__new__(LiveController)
            controller.settings, controller.pipeline = settings, pipeline
            controller.sandbox_speech_tokens, controller.sandbox_speech_by_move = set(), {}
            controller._notify = Mock()
            controller.last_engine_score = None
            try:
                controller.submit_chess({"event": "engine_analysis", "number": 6,
                    "speech_token": "real-6", "side": "r", "fen_before": FEN,
                    "ucci": LINE[0], "notation": line[0][1], "analysis": {}, "sandbox_plan": plan})
                board.apply(Board.from_ucci(LINE[0]))
                events = Mock()
                events.emit.side_effect = lambda event, **data: controller.submit_chess({"event": event, **data})
                result = player.run_game_sandbox("mock", board, "red_bottom", plan,
                                                 "real-6", 6, events, lambda: False)
                self.assertEqual(result["completed"], 4)
                self.assertFalse(result["narration_failed"], [
                    p.read_text(encoding="utf-8") for p in (folder / "receipts").glob("*.json")])
                self.assertEqual([data["沙盘步骤"] for _, data in heard], ["intro", "1", "2", "3", "4", "summary"])
                self.assertEqual([text for text, _ in heard], list(sn.script(plan).values()))
                self.assertFalse((folder / "real_started.json").exists())
                pipeline.llm.assert_not_called()
                header, _ = ControlPanel._speech_log_entry({"kind": "reply", "text": heard[-1][0], "data": heard[-1][1]})
                self.assertIn("结果", header)
            finally:
                pipeline.close()


if __name__ == "__main__":
    unittest.main()
