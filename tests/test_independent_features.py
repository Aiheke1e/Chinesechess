import threading
import unittest
from unittest.mock import Mock, patch

import assistant_bootstrap
import control_panel
from chat_selection import ChatSelector
from live_host import LiveController, LiveSettings, SpeechPipeline


class IndependentFeatureTests(unittest.TestCase):
    def setUp(self):
        self.markers = patch.object(SpeechPipeline, "_write_speech_marker").start()
        self.play = patch("live_host.winsound.PlaySound").start()
        self.duration = patch.object(SpeechPipeline, "_audio_duration", return_value=.12).start()
        self.pipeline = None
        self.release = []

    def tearDown(self):
        for event in self.release:
            event.set()
        if self.pipeline:
            self.pipeline.close()
        patch.stopall()

    def make_pipeline(self, notify, llm=None, tts=None):
        self.pipeline = SpeechPipeline(LiveSettings(cooldown=0, speech_enabled=True), notify,
            llm=llm or (lambda *args, **kwargs: "晚上好。"),
            tts=tts or (lambda text, voice: text + ".wav"))
        return self.pipeline

    def test_pausing_chess_does_not_cut_a_reply_already_playing(self):
        started, finished = threading.Event(), threading.Event()
        def notify(kind, text, data):
            if kind == "reply": started.set()
            if kind == "interaction_done" and data["completed"]: finished.set()
        pipeline = self.make_pipeline(notify)
        pipeline.submit(3, "弹幕回复", {"观众": "小王", "弹幕": "晚上好"})
        self.assertTrue(started.wait(1))
        pipeline.pause_chess()
        self.assertTrue(finished.wait(1))
        self.assertFalse(any(call.args[0] is None for call in self.play.call_args_list))
        self.assertFalse(pipeline.prepare_move("关键走棋解说", {"speech_token": "paused"}, "马二进三。"))

    def test_pausing_interaction_preserves_prepared_move_and_its_playback(self):
        ready, heard, completed = (threading.Event() for _ in range(3))
        def notify(kind, text, data):
            if kind == "reply": heard.set()
            if kind == "speech_status" and text == "等待下一条内容": completed.set()
        pipeline = self.make_pipeline(notify, tts=lambda *args: (ready.set() or "move.wav"))
        pipeline.prepare_move("关键走棋解说", {"speech_token": "move"}, "马二进三。")
        self.assertTrue(ready.wait(1))
        pipeline.pause_interactions()
        self.assertTrue(pipeline.commit_move("move"))
        self.assertTrue(heard.wait(1))
        pipeline.pause_interactions()
        self.assertTrue(completed.wait(1))
        self.assertFalse(any(call.args[0] is None for call in self.play.call_args_list))

    def test_a_late_old_reply_cannot_clear_the_new_interaction_session(self):
        old_started, new_started, old_done, heard = (threading.Event() for _ in range(4))
        old_release, new_release = threading.Event(), threading.Event()
        self.release.extend((old_release, new_release))
        spoken = []
        def llm(kind, payload, *args, **kwargs):
            if payload["互动标识"] == "old":
                old_started.set()
                old_release.wait(2)
                return "旧回复。"
            new_started.set()
            new_release.wait(2)
            return "新回复。"
        def notify(kind, text, data):
            if kind == "interaction_done" and data["互动标识"] == "old": old_done.set()
            if kind == "reply":
                spoken.append(data["互动标识"])
                heard.set()
        pipeline = self.make_pipeline(notify, llm)
        pipeline.submit(3, "弹幕回复", {"观众": "小王", "互动标识": "old"})
        self.assertTrue(old_started.wait(1))
        pipeline.pause_interactions()
        pipeline.resume_interactions()
        self.assertTrue(pipeline.submit(3, "弹幕回复", {"观众": "小李", "互动标识": "new"}))
        self.assertTrue(new_started.wait(1))
        old_release.set()
        self.assertTrue(old_done.wait(1))
        self.assertFalse(pipeline.can_reply_chat())
        new_release.set()
        self.assertTrue(heard.wait(1))
        self.assertEqual(spoken, ["new"])

    def test_cancelled_mate_generation_does_not_block_chat_after_chess_resumes(self):
        generating, finished, heard = (threading.Event() for _ in range(3))
        release = threading.Event()
        self.release.append(release)
        spoken = []
        def llm(kind, *args, **kwargs):
            if kind == "绝杀长解说":
                generating.set()
                release.wait(2)
                return "车二进一，重炮绝杀。"
            return "晚上好。"
        def notify(kind, text, data):
            if kind == "reply":
                spoken.append(data["kind"])
                heard.set()
        pipeline = self.make_pipeline(notify, llm)
        original = pipeline._run_job
        def run(job):
            try:
                return original(job)
            finally:
                if job[4] == "绝杀长解说": finished.set()
        pipeline._run_job = run
        pipeline.submit(0, "绝杀长解说", {})
        self.assertTrue(generating.wait(1))
        pipeline.pause_chess()
        pipeline.resume_chess()
        self.assertTrue(pipeline.can_reply_chat())
        pipeline.submit(3, "弹幕回复", {"观众": "小王", "弹幕": "晚上好"})
        self.assertTrue(heard.wait(1))
        release.set()
        self.assertTrue(finished.wait(1))
        self.assertEqual(spoken, ["弹幕回复"])

    def test_pause_discards_pending_gifts_and_welcomes_without_cancelling_chess(self):
        generating, finished, heard = (threading.Event() for _ in range(3))
        release = threading.Event()
        self.release.append(release)
        spoken = []
        def llm(*args, **kwargs):
            generating.set()
            release.wait(2)
            return "谢谢小王的小心心。"
        def notify(kind, text, data):
            if kind == "reply":
                spoken.append(data["kind"])
                heard.set()
        pipeline = self.make_pipeline(notify, llm)
        original = pipeline._run_job
        def run(job):
            try:
                return original(job)
            finally:
                if job[4] == "礼物答谢": finished.set()
        pipeline._run_job = run
        pipeline.submit(4, "礼物答谢", {"观众": "小王", "礼物": "小心心"})
        self.assertTrue(generating.wait(1))
        pipeline.submit(4, "欢迎观众", {"观众": "小李"})
        pipeline.pause_interactions()
        pipeline.prepare_move("关键走棋解说", {"speech_token": "move"}, "马二进三。")
        self.assertTrue(pipeline.commit_move("move"))
        self.assertTrue(heard.wait(1))
        release.set()
        self.assertTrue(finished.wait(1))
        self.assertEqual(spoken, ["关键走棋解说"])

    def test_controller_enabling_chat_never_resumes_chess(self):
        controller = object.__new__(LiveController)
        controller.pipeline = self.make_pipeline(lambda *args: None)
        controller.chat_selector = ChatSelector()
        controller.gift_lock = threading.Lock()
        controller.gift_timers, controller.pending_gifts = {}, {}
        controller.pending_move_events = {}
        controller.sandbox_speech_tokens, controller.sandbox_speech_by_move = set(), {}
        controller._close_sandtable, controller._notify = Mock(), Mock()
        controller.enabled = True
        controller.connector = Mock()
        connector = controller.connector
        controller.stop_chess_tasks()
        self.assertTrue(controller.enabled)
        self.assertIs(controller.connector, connector)
        controller.pause()
        controller.enable()
        self.assertTrue(controller.chess_suspended)
        self.assertTrue(controller.pipeline.chess_suspended)
        self.assertTrue(controller.pipeline.can_reply_chat())
        controller.pause()
        controller.resume_chess_tasks()
        self.assertFalse(controller.enabled)
        self.assertTrue(controller.pipeline.interactions_suspended)
        self.assertFalse(controller.pipeline.chess_suspended)

    def test_desktop_bootstrap_only_autostarts_chess_when_explicitly_requested(self):
        for options in ([], ["--autostart"]):
            forwarded = []
            with self.subTest(options=options), patch("assistant_bootstrap.enable_high_dpi"), patch(
                    "assistant_bootstrap.os.chdir"), patch("assistant_bootstrap.write_log"), patch(
                    "assistant_bootstrap.acquire_single_instance", return_value=Mock()), patch(
                    "assistant_bootstrap.sys.argv", ["assistant_bootstrap.py", *options]), patch.object(
                    control_panel, "main", side_effect=lambda: forwarded.extend(assistant_bootstrap.sys.argv[1:])):
                self.assertEqual(assistant_bootstrap.main(), 0)
            self.assertEqual(forwarded, options)


if __name__ == "__main__":
    unittest.main()
