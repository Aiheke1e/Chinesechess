from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from board import Board
import game_sandbox
from live_host import LiveController, LiveSettings, SpeechPipeline
from notation import move_description
import sandbox_narration as sn
from tests.test_mate_patterns import POSITIONS, predecessor


class SpeechContinuityTests(unittest.TestCase):
    def setUp(self):
        self.sound = patch("live_host.winsound.PlaySound").start()
        patch.object(SpeechPipeline, "_write_speech_marker").start()
        patch.object(SpeechPipeline, "_prune_audio").start()
        self.pipelines = []

    def tearDown(self):
        for pipeline in self.pipelines:
            pipeline.close()
        patch.stopall()

    def pipeline(self, notify, tts=None):
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True, cooldown=0), notify,
            llm=lambda *a, **kw: "收到，咱们接着看棋。", tts=tts or (lambda *a: "mock.wav"))
        self.pipelines.append(pipeline)
        return pipeline

    def test_expiry_after_chat_starts_does_not_cut_the_audio_or_clear_display_early(self):
        started, done = threading.Event(), threading.Event()
        records = []
        def notify(kind, text, data):
            records.append((kind, data))
            if kind == "reply":
                pipeline.active_chat_payload["_expires_at"] = time.monotonic() + .04
                started.set()
            if kind == "interaction_done": done.set()
        pipeline = self.pipeline(notify)
        with patch.object(pipeline, "_audio_duration", return_value=.18):
            at = time.monotonic()
            pipeline.submit(3, "弹幕回复", {"观众": "小王", "弹幕": "晚上好", "_expires_at": at + 60})
            self.assertTrue(started.wait(1))
            self.assertFalse(done.wait(.11))
            self.assertTrue(done.wait(1))
            self.assertGreaterEqual(time.monotonic() - at, .18)
            self.assertFalse(any(kind == "speech_interrupted" for kind, _ in records))
            self.assertFalse(any(call.args[0] is None for call in self.sound.call_args_list))

    def test_chat_expiring_during_generation_never_starts_or_updates_the_display(self):
        done, records = threading.Event(), []
        def notify(kind, text, data):
            records.append(kind)
            if kind == "interaction_done": done.set()
        def tts(*a):
            pipeline.active_chat_payload["_expires_at"] = time.monotonic() - 1
            return "mock.wav"
        pipeline = self.pipeline(notify, tts)
        pipeline.submit(3, "弹幕回复", {"弹幕": "晚上好", "_expires_at": time.monotonic() + 60})
        self.assertTrue(done.wait(1))
        self.assertNotIn("reply", records)
        self.assertFalse(any(call.args[0] is not None for call in self.sound.call_args_list))

    def test_ready_chat_waits_for_the_gift_sentence_to_finish(self):
        gift, chat, gift_finished = threading.Event(), threading.Event(), threading.Event()
        records = []
        def notify(kind, text, data):
            records.append(kind)
            if kind == "reply":
                if data["kind"] == "礼物答谢": gift.set()
                else:
                    self.assertTrue(gift_finished.is_set())
                    chat.set()
        pipeline = self.pipeline(notify)
        original = pipeline._play_synced_audio
        def play(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[4] == "礼物答谢": gift_finished.set()
            return result
        with patch.object(pipeline, "_audio_duration", return_value=.18), \
             patch.object(pipeline, "_play_synced_audio", side_effect=play):
            pipeline.submit(4, "礼物答谢", {"观众": "小王", "礼物": "小心心"})
            self.assertTrue(gift.wait(1))
            pipeline.submit(3, "弹幕回复", {"弹幕": "晚上好"})
            self.assertFalse(chat.wait(.08))
            self.assertTrue(chat.wait(1))
            self.assertNotIn("speech_interrupted", records)

    def test_mate_can_still_interrupt_a_short_interaction(self):
        started, interrupted = threading.Event(), threading.Event()
        reasons = []
        def notify(kind, text, data):
            if kind == "reply": started.set()
            if kind == "speech_interrupted":
                reasons.append(data["reason"]); interrupted.set()
        pipeline = self.pipeline(notify)
        with patch.object(pipeline, "_audio_duration", return_value=1):
            pipeline.submit(3, "礼物答谢", {"观众": "小王", "礼物": "小心心"})
            self.assertTrue(started.wait(1))
            pipeline.prepare_move("绝杀长解说", {"speech_token": "mate", "绝杀长播报": True}, "车二进一，重炮绝杀。")
            self.assertTrue(interrupted.wait(.5))
            self.assertEqual(reasons, ["关键将杀优先"])

    def test_pausing_interactions_still_stops_accepted_audio_immediately(self):
        started, interrupted = threading.Event(), threading.Event()
        pipeline = self.pipeline(lambda kind, *a: (started if kind == "reply" else interrupted).set()
            if kind in ("reply", "speech_interrupted") else None)
        with patch.object(pipeline, "_audio_duration", return_value=1):
            pipeline.submit(3, "弹幕回复", {"弹幕": "晚上好"})
            self.assertTrue(started.wait(1))
            pipeline.pause_interactions()
            self.assertTrue(interrupted.wait(.5))

    def test_one_move_finisher_waits_for_safe_demo_return_instead_of_racing_it(self):
        event = predecessor(Board.from_fen(POSITIONS["重炮"]))
        board = Board.from_fen(event["fen_before"])
        notation = move_description(board, Board.from_ucci(event["ucci"]))
        event.update(notation=notation, analysis={"score_type": "mate", "score": 1})
        event["sandbox_plan"] = game_sandbox.build_plan(notation, event["analysis"],
            [(event["ucci"], notation)], event["ucci"], board)
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(sandtable_enabled=True, speech_enabled=True)
        controller.pipeline, controller._notify = Mock(), Mock()
        controller.sandbox_speech_tokens, controller.sandbox_speech_by_move = set(), {}
        controller.submit_chess(event)
        prepared = controller.pipeline.prepare_move.call_args
        self.assertEqual(prepared.args[0], "绝杀长解说")
        self.assertEqual(len(prepared.args[1]["_tts_after_sandbox"]), 3)  # Intro, legal mate, summary.
        controller.submit_chess({"event": "move_command", "speech_token": event["speech_token"], "notation": notation})
        controller.pipeline.commit_move.assert_not_called()
        controller.submit_chess({"event": "game_sandbox", "action": "end", "phase": "before_move",
            "speech_token": event["speech_token"], "safe_exit": True, "entered": False, "speech_deadline": 100})
        controller.pipeline.commit_move.assert_called_once_with(event["speech_token"], deadline=100)

    def test_long_finisher_does_not_take_the_tts_slot_ahead_of_demo_cues(self):
        ready, generated, heard = set(), threading.Event(), threading.Event()
        tts = Mock(side_effect=lambda *a: (generated.set() or "mate.wav"))
        pipeline = self.pipeline(lambda kind, *a: heard.set() if kind == "reply" else None, tts)
        payload = {"speech_token": "mate", "绝杀长播报": True, "_tts_after_sandbox": ["intro", "step"]}
        with patch("live_host.sandbox_audio_ready", side_effect=lambda item: item in ready), \
             patch.object(pipeline, "_audio_duration", return_value=.01):
            pipeline.prepare_move("绝杀长解说", payload, "车二进一，重炮绝杀。")
            self.assertFalse(generated.wait(.05))
            ready.add("intro")
            self.assertFalse(generated.wait(.05))
            ready.add("step")
            self.assertTrue(generated.wait(1))
            self.assertFalse(heard.is_set())
            pipeline.commit_move("mate", deadline=time.monotonic() + 1)
            self.assertTrue(heard.wait(1))

    def test_failed_demo_cancels_finisher_before_it_calls_tts(self):
        entered = threading.Event()
        tts = Mock(return_value="mate.wav")
        pipeline = self.pipeline(Mock(), tts)
        with patch("live_host.sandbox_audio_ready", side_effect=lambda token: (entered.set() or False)):
            pipeline.prepare_move("绝杀长解说", {"speech_token": "mate", "绝杀长播报": True,
                "_tts_after_sandbox": ["intro"]}, "车二进一，重炮绝杀。")
            self.assertTrue(entered.wait(1))
            pipeline.discard_move("mate")
            end = time.monotonic() + 1
            while "mate" in pipeline.prepared and time.monotonic() < end: time.sleep(.01)
            self.assertNotIn("mate", pipeline.prepared)
            tts.assert_not_called()

    def test_audio_ready_is_not_confused_with_preparing_or_cancelled(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(sn, "MARKERS", Path(folder)):
            payload = {"_sandbox_cue": True, "speech_token": "demo:intro"}
            for status, ready in (("preparing", False), ("ready", True), ("started", True),
                                  ("finished", True), ("cancelled", False), ("failed", False)):
                sn.receipt(payload, status)
                self.assertEqual(sn.audio_ready("demo:intro"), ready)

    def test_cancelled_finisher_is_not_mislabelled_as_interrupting_itself(self):
        pipeline = self.pipeline(Mock())
        pipeline.prepared["mate"] = {"kind": "绝杀长解说", "decision": threading.Event()}
        self.assertEqual(pipeline._speech_interruption_reason("绝杀长解说", {},
            {"epoch": pipeline.cancel_epoch, "approved": False}), "本段播报已取消")


if __name__ == "__main__":
    unittest.main()
