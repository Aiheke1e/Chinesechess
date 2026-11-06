from contextlib import ExitStack, redirect_stdout
import io
import itertools
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from board import Board, START_FEN
from commentary_text import opponent_wait_note
from control_panel import ControlPanel
from live_host import ChessEventTailer, LiveController, LiveSettings, SpeechPipeline
from opponent_status import OpponentWaitMonitor, opponent_disconnected
import player


ROOT = Path(__file__).resolve().parent.parent


class OpponentStatusTests(unittest.TestCase):
    def setUp(self):
        self.offline = cv2.imread(str(ROOT / "fixture/jj_opponent_offline.png"))
        self.thinking = cv2.imread(str(ROOT / "fixture/jj_opponent_countdown.png"))

    def test_explicit_label_is_detected_at_supported_scales(self):
        self.assertTrue(opponent_disconnected(self.offline))
        for size in ((720, 1280), (1080, 1920)):
            self.assertTrue(opponent_disconnected(cv2.resize(self.offline, size)))

    def test_countdown_live_board_and_missing_frame_do_not_prove_disconnection(self):
        self.assertFalse(opponent_disconnected(self.thinking))
        for name in ("jj_live.png", "jj_before.png", "fixture/jj_rank_promotion.png"):
            self.assertFalse(opponent_disconnected(cv2.imread(str(ROOT / name))))
        self.assertFalse(opponent_disconnected(None))

    def test_our_own_offline_label_is_not_attributed_to_opponent(self):
        frame = np.zeros_like(self.offline)
        # Move the entire observed opponent header to our own bottom avatar.
        frame[800:960] = self.offline[:160]
        self.assertFalse(opponent_disconnected(frame))

    def test_two_frames_confirm_offline_once_and_missing_frame_breaks_confirmation(self):
        monitor = OpponentWaitMonitor(clock=lambda: 0)
        self.assertIsNone(monitor.observe_screen(self.offline))
        self.assertIsNone(monitor.observe_screen(None))
        self.assertIsNone(monitor.observe_screen(self.offline))
        self.assertEqual(monitor.observe_screen(self.offline), "disconnected")
        for _ in range(5):
            self.assertIsNone(monitor.observe_screen(self.offline))

    def test_clearing_the_label_needs_two_stable_board_frames(self):
        now = [0]
        monitor = OpponentWaitMonitor(clock=lambda: now[0])
        monitor.observe_screen(self.offline)
        monitor.observe_screen(self.offline)
        monitor.observe_screen(self.thinking)
        self.assertIsNone(monitor.observe_board(True))
        self.assertIsNone(monitor.observe_board(False))
        self.assertIsNone(monitor.observe_board(True))
        self.assertEqual(monitor.observe_board(True), "cleared")
        now[0] = 300
        self.assertIsNone(monitor.observe_board(True))

    def test_long_wait_is_bounded_and_requires_a_trusted_unchanged_board(self):
        now = [0]
        monitor = OpponentWaitMonitor(clock=lambda: now[0])
        monitor.observe_screen(self.thinking)
        monitor.observe_board(True)
        now[0] = 19
        self.assertIsNone(monitor.observe_board(True))
        now[0] = 20
        self.assertEqual(monitor.observe_board(True), "waiting_long")
        now[0] = 100
        self.assertIsNone(monitor.observe_board(True))
        monitor.begin_wait()
        monitor.observe_screen(self.thinking)
        now[0] = 150
        self.assertIsNone(monitor.observe_board(False))
        monitor.observe_board(True)
        self.assertIsNone(monitor.observe_board(True))  # Cooldown across turns.
        now[0] = 216
        self.assertEqual(monitor.observe_board(True), "waiting_long")

    def test_player_emits_one_confirmed_status_without_tapping_or_searching(self):
        for frame, disconnected in ((self.offline, True), (self.thinking, False)):
            with self.subTest(disconnected=disconnected), ExitStack() as stack:
                board = Board.from_fen(START_FEN)
                board.turn = "b"
                cfg = dict(player.DEFAULT_CFG, my_color="r", auto_continue_after_game=False)
                events, engine = Mock(), Mock()
                for item in (
                    patch.object(player.sys, "argv", ["player.py"]),
                    patch.object(player, "load_cfg", return_value=cfg),
                    patch.object(player, "list_devices", return_value=["mock"]),
                    patch.object(player, "match_live_session", return_value=None),
                    patch.object(player, "read_stable_board", return_value=(board, "red_bottom")),
                    patch.object(player, "infer_turn_from_standard", return_value=None),
                    patch.object(player, "save_session"),
                    patch.object(player, "write_runtime_status"),
                    patch.object(player, "EventLog", return_value=events),
                    patch("pikafish.get_engine", return_value=engine),
                    patch.object(player, "screenshot", return_value=frame),
                    patch.object(player, "postgame_screen_visible", side_effect=[False, False, True]),
                    patch.object(player, "resolve_move_from_change", return_value=(None, 0)),
                    patch.object(player, "assess_image_against_board", return_value=(None, "unchanged", 0)),
                    patch.object(player.time, "monotonic", side_effect=itertools.count(100)),
                    patch.object(player.time, "sleep"),
                ):
                    stack.enter_context(item)
                move = stack.enter_context(patch.object(player, "move_piece_retry"))
                search = stack.enter_context(patch.object(player, "pick_move"))
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(player.main(), 0)
                statuses = [call for call in events.emit.call_args_list
                            if call.args[0] == "opponent_status"]
                self.assertEqual(len(statuses), int(disconnected))
                if disconnected:
                    self.assertEqual(statuses[0].kwargs["status"], "disconnected")
                    self.assertEqual(statuses[0].kwargs["evidence"], "avatar_disconnect_label")
                    self.assertEqual(statuses[0].kwargs["fen"], board.to_fen())
                move.assert_not_called()
                search.assert_not_called()

    def controller(self, pipeline=None):
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(commentary_enabled=True, speech_enabled=True)
        controller.pipeline = pipeline or Mock()
        controller._notify = Mock()
        controller._record_opening_move = Mock()
        controller._close_sandtable = Mock()
        return controller

    def wait_event(self):
        return {"event": "waiting_for_opponent", "fen": START_FEN, "after_move_number": 3}

    def status_event(self, status="disconnected"):
        return {"event": "opponent_status", "status": status, "fen": START_FEN,
                "after_move_number": 3, "waited_seconds": 40,
                "evidence": "avatar_disconnect_label" if status == "disconnected" else "stable_board_wait"}

    def test_controller_uses_one_short_direct_line_without_calling_a_model(self):
        controller = self.controller()
        controller.submit_chess(self.wait_event())
        controller.submit_chess(self.status_event())
        controller.submit_chess(self.status_event())
        prepared = controller.pipeline.prepare_idle
        prepared.assert_called_once()
        self.assertEqual(prepared.call_args.kwargs["kind"], "对手状态解说")
        text = prepared.call_args.kwargs["direct_text"]
        self.assertIn("断线", text)
        self.assertNotIn("赢", text)
        self.assertIsNone(prepared.call_args.args[0]["解说对应走法"])
        self.assertIn("_expires_at", prepared.call_args.args[0])

    def test_long_wait_uses_the_requested_nudge_line_once_and_the_same_speech_log(self):
        controller = self.controller()
        controller.submit_chess(self.wait_event())
        event = {**self.status_event("waiting_long"), "nudge": {"sent": True}}
        controller.submit_chess(event)
        controller.submit_chess(event)
        prepared = controller.pipeline.prepare_idle
        prepared.assert_called_once()
        self.assertEqual(prepared.call_args.kwargs["direct_text"], "阁下神速些吧。")
        header, body = ControlPanel._speech_log_entry({"kind": "reply", "text": "阁下神速些吧。",
            "data": {"kind": prepared.call_args.kwargs["kind"]}})
        self.assertEqual((header, body), ("对手状态", "阁下神速些吧。"))

    def test_player_nudges_only_a_stable_long_wait_and_never_in_read_only_mode(self):
        for dry, changed in ((False, False), (False, True), (True, False)):
            with self.subTest(dry=dry, changed=changed), ExitStack() as stack:
                board = Board.from_fen(START_FEN)
                board.turn = "b"
                cfg = dict(player.DEFAULT_CFG, my_color="r", dry_run=dry, auto_continue_after_game=False)
                events, monitor = Mock(), Mock()
                monitor.observe_screen.return_value = None
                monitor.observe_board.return_value = "waiting_long"
                for item in (
                    patch.object(player.sys, "argv", ["player.py"]),
                    patch.object(player, "load_cfg", return_value=cfg),
                    patch.object(player, "list_devices", return_value=["mock"]),
                    patch.object(player, "match_live_session", return_value=None),
                    patch.object(player, "read_stable_board", return_value=(board, "red_bottom")),
                    patch.object(player, "infer_turn_from_standard", return_value=None),
                    patch.object(player, "save_session"), patch.object(player, "write_runtime_status"),
                    patch.object(player, "EventLog", return_value=events),
                    patch.object(player, "OpponentWaitMonitor", return_value=monitor),
                    patch("pikafish.get_engine", return_value=Mock()),
                    patch.object(player, "screenshot", return_value=self.thinking),
                    patch.object(player, "postgame_screen_visible", side_effect=[False, True]),
                    patch.object(player, "resolve_move_from_change", return_value=(None, 0)),
                    patch.object(player, "assess_image_against_board", return_value=(None, "unchanged", 0)),
                    patch.object(player.time, "sleep"),
                ):
                    stack.enter_context(item)
                sender = stack.enter_context(patch.object(player, "send_wait_cloud", return_value={
                    "sent": not changed, "reason": "test", "board_changed": changed}))
                move = stack.enter_context(patch.object(player, "move_piece_retry"))
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(player.main(), 0)
                self.assertEqual(sender.call_count, int(not dry))
                notices = [c for c in events.emit.call_args_list if c.args[0] == "opponent_status"]
                self.assertEqual(len(notices), int(not changed))
                move.assert_not_called()

    def test_late_status_or_unverified_claim_cannot_create_speech(self):
        controller = self.controller()
        controller.submit_chess(self.status_event())
        controller.submit_chess(self.wait_event())
        controller.submit_chess({**self.status_event(), "after_move_number": 2})
        controller.submit_chess({**self.status_event(), "evidence": "timeout"})
        controller.pipeline.prepare_idle.assert_not_called()
        controller.submit_chess({"event": "opponent_move", "notation": "黑方 马8进7", "ucci": "h9g7"})
        controller.submit_chess(self.status_event())
        controller.pipeline.prepare_idle.assert_not_called()

    def test_ordinary_wait_does_not_accuse_opponent_of_running_away(self):
        text = opponent_wait_note("waiting_long")
        for claim in ("掉线", "断线", "挂机", "吓跑", "赢了"):
            self.assertNotIn(claim, text)
        for number in range(3):
            text = opponent_wait_note("disconnected", number)
            self.assertLessEqual(len(text), 56)
            self.assertTrue(SpeechPipeline._chess_line_valid(text, "对手状态解说", {}))
        self.assertNotIn("吓", opponent_wait_note("disconnected", playful=False))

    def test_game_end_cancels_status_and_uses_the_observed_disconnect(self):
        controller = self.controller()
        controller.submit_chess(self.wait_event())
        controller.submit_chess(self.status_event())
        controller.submit_chess({"event": "game_over", "result": "finished", "reason": "jj_result_screen"})
        controller.pipeline.cancel_idle.assert_called()
        controller.pipeline.invalidate_chess.assert_called_once()
        self.assertEqual(controller.pipeline.submit.call_args.kwargs["direct_text"],
                         "对面断线后，这局已经结束了。")
        controller.pipeline.prepare_idle.reset_mock()
        controller.submit_chess(self.status_event())
        controller.pipeline.prepare_idle.assert_not_called()

    def test_clearing_status_does_not_cancel_unrelated_chess_explanation(self):
        pipeline = object.__new__(SpeechPipeline)
        pipeline.idle_lock = threading.Lock()
        for kind in ("等待期长讲解", "对手状态解说"):
            state = {"kind": kind, "cancel": threading.Event(), "window": threading.Event()}
            pipeline.idle_state = state
            self.assertEqual(pipeline.cancel_idle(kind="对手状态解说"), kind == "对手状态解说")
            self.assertEqual(state["cancel"].is_set(), kind == "对手状态解说")

    def test_late_tts_cannot_play_after_opponent_moves_or_chess_is_paused(self):
        for trigger in ("move", "pause", "cleared", "game_over"):
            with self.subTest(trigger=trigger), tempfile.TemporaryDirectory() as folder:
                ready, release, done = (threading.Event() for _ in range(3))
                heard = []
                def tts(*args):
                    ready.set()
                    release.wait(2)
                    return str(Path(folder) / "wait.wav")
                pipeline = SpeechPipeline(LiveSettings(speech_enabled=True),
                    lambda kind, text, data: heard.append(text) if kind == "reply" else None,
                    llm=Mock(side_effect=AssertionError("等待提示不调用模型")), tts=tts)
                original = pipeline._prepare_idle_worker
                def worker(state):
                    try:
                        original(state)
                    finally:
                        done.set()
                controller = self.controller(pipeline)
                with patch("live_host.winsound.PlaySound") as sound, patch.object(
                        pipeline, "_prepare_idle_worker", side_effect=worker), \
                        patch.object(pipeline, "submit"):
                    try:
                        controller.submit_chess(self.wait_event())
                        controller.submit_chess(self.status_event())
                        self.assertTrue(ready.wait(1))
                        if trigger == "move":
                            controller.submit_chess({"event": "opponent_move", "notation": "黑方 马8进7"})
                        elif trigger == "pause":
                            pipeline.pause_chess()
                        elif trigger == "cleared":
                            controller.submit_chess(self.status_event("cleared"))
                        else:
                            controller.submit_chess({"event": "game_over", "result": "finished", "reason": "jj_result_screen"})
                        release.set()
                        self.assertTrue(done.wait(1))
                        self.assertEqual(heard, [])
                        self.assertFalse(any(call.args[0] is not None for call in sound.call_args_list))
                        pipeline.llm.assert_not_called()
                    finally:
                        release.set()
                        pipeline.close()

    def test_normal_speech_log_contains_the_exact_status_line_sent_to_tts(self):
        heard, done = threading.Event(), threading.Event()
        records = []
        def notify(kind, text, data):
            if kind == "reply":
                records.append((text, data))
                heard.set()
        tts = Mock(return_value="wait.wav")
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True), notify, tts=tts)
        original = pipeline._prepare_idle_worker
        def worker(state):
            try:
                original(state)
            finally:
                done.set()
        controller = self.controller(pipeline)
        with patch("live_host.winsound.PlaySound"), patch.object(pipeline, "_audio_duration", return_value=.01), \
             patch.object(pipeline, "_prepare_idle_worker", side_effect=worker):
            try:
                controller.submit_chess(self.wait_event())
                controller.submit_chess(self.status_event())
                self.assertTrue(heard.wait(1))
                self.assertTrue(done.wait(1))
                self.assertEqual(records[0][0], tts.call_args.args[0])
                self.assertEqual(records[0][1]["kind"], "对手状态解说")
                header, body = ControlPanel._speech_log_entry({
                    "kind": "reply", "text": records[0][0], "data": records[0][1]})
                self.assertEqual(header, "对手状态")
                self.assertEqual(body, tts.call_args.args[0])
            finally:
                pipeline.close()

    def test_event_tailer_forwards_status_as_well_as_wait_start(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            tailer = ChessEventTailer(path, lambda event: received.set())
            received = threading.Event()
            tailer.start()
            try:
                # Wait for the tailer to establish its initial end position.
                for _ in range(100):
                    if path.exists():
                        break
                    time.sleep(.005)
                time.sleep(.1)
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(self.status_event()) + "\n")
                self.assertTrue(received.wait(1))
            finally:
                tailer.close()
                tailer.join(1)


if __name__ == "__main__":
    unittest.main()
