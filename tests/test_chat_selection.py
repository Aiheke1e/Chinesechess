import queue
import random
import threading
import time
import unittest
from unittest.mock import Mock, patch

from chat_selection import ChatSelector
from control_panel import ControlPanel
from live_host import LiveController, LiveSettings, SpeechPipeline


class ChatSelectionTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.selector = ChatSelector(clock=lambda: self.now, rng=random.Random(3))

    def event(self, number=1, user=None, text=None):
        return {"type": "chat", "id": str(number), "user_id": user or str(number),
                "user": user or f"棋友{number}", "text": text or f"第{number}手为什么跳马？"}

    def test_burst_is_bounded_and_selects_only_one_without_backlog(self):
        for number in range(10000):
            self.selector.offer(self.event(number))
        self.assertEqual(len(self.selector.pending), 60)
        self.assertLessEqual(len(self.selector.arrivals), 16)
        self.assertEqual(self.selector.stats()["chat_rate"], round(10000 / 15, 1))
        self.assertLessEqual(len(self.selector.recent_text), 300)
        self.assertEqual(self.selector.stats()["interval"], 30)
        self.now += 2
        self.assertIsNotNone(self.selector.take())
        self.assertIsNone(self.selector.take())
        self.assertFalse(self.selector.pending)

    def test_online_population_and_actual_chat_rate_both_control_interval(self):
        self.selector.update_online(12)
        self.assertEqual(self.selector.stats()["interval"], 8)
        self.selector.update_online(120)
        self.assertEqual(self.selector.stats()["interval"], 12)
        self.selector.update_online(1200)
        self.assertEqual(self.selector.stats()["interval"], 20)
        self.selector.update_online(12000)
        self.assertEqual(self.selector.stats()["interval"], 30)
        self.selector.update_online(12)
        for number in range(40):
            self.selector.offer(self.event(number))
        self.assertEqual(self.selector.stats()["interval"], 20)
        self.now += 46
        self.assertIsNone(self.selector.stats()["online"])
        self.assertEqual(self.selector.stats()["interval"], 8)
        self.assertFalse(self.selector.update_online(-1))
        self.assertFalse(self.selector.update_online("invalid"))

    def test_spam_duplicates_and_repeated_users_do_not_dominate(self):
        for text in ("666666", "哈哈哈哈哈", "👍👍👍", "https://example.com/ad", "买" * 200):
            self.assertFalse(self.selector.offer(self.event(text=text)))
        self.assertTrue(self.selector.offer(self.event(user="小王", text="为什么不走车？")))
        self.assertFalse(self.selector.offer(self.event(user="小李", text="为什么不走车？")))
        self.assertTrue(self.selector.offer(self.event(user="小王", text="这匹马怎么走？")))
        self.assertEqual(len(self.selector.pending), 1)
        self.now += 2
        self.assertEqual(self.selector.take()["text"], "这匹马怎么走？")
        self.now += 9
        self.selector.offer(self.event(5, "小王", "这盘现在谁占优势？"))
        self.now += 2
        self.assertIsNone(self.selector.take())
        self.selector.offer(self.event(6, "小李", "炮为什么要沉底？"))
        self.now += 2
        self.assertEqual(self.selector.take()["user"], "小李")

    def test_stale_comments_and_previous_room_are_discarded(self):
        self.selector.offer(self.event())
        self.now += 26
        self.assertIsNone(self.selector.take())
        self.selector.update_online(500)
        self.selector.offer(self.event(2))
        self.selector.reset()
        self.assertIsNone(self.selector.stats()["online"])
        self.assertFalse(self.selector.pending)

    def test_controller_ignores_legacy_prefix_and_only_submits_a_sample(self):
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(prefix="问主播")
        controller.enabled = True
        controller.seen = {}
        controller.pipeline = Mock()
        controller.pipeline.can_reply_chat.return_value = True
        controller.chat_selector = self.selector
        controller.chat_stats_at = time.monotonic()
        controller._notify = Mock()
        for number in range(200):
            controller.receive(self.event(number))
        controller.pipeline.submit.assert_not_called()
        self.now += 2
        controller._select_chat()
        controller._select_chat()
        controller.pipeline.submit.assert_called_once()
        payload = controller.pipeline.submit.call_args.args[2]
        self.assertIn("为什么跳马", payload["弹幕"])
        self.assertIn("观众", payload)
        self.assertLessEqual(len(controller.seen), 2000)


class SelectedReplyTests(unittest.TestCase):
    def test_switching_rooms_during_generation_cancels_the_old_reply(self):
        generating, release, done = [threading.Event() for _ in range(3)]
        events = []
        def llm(*args, **kwargs):
            generating.set()
            release.wait(2)
            return "旧直播间的回答"
        def notify(kind, text, data):
            events.append(kind)
            if kind == "interaction_done": done.set()
        pipeline = SpeechPipeline(LiveSettings(cooldown=0), notify, llm=llm)
        try:
            pipeline.submit(3, "弹幕回复", {"观众": "旧观众", "弹幕": "刚才的棋怎么走？"})
            self.assertTrue(generating.wait(1))
            pipeline.cancel_chats()
            release.set()
            self.assertTrue(done.wait(1))
            self.assertNotIn("reply", events)
        finally:
            release.set()
            pipeline.close()

    def test_one_reply_at_a_time_and_header_event_only_after_actual_audio_start(self):
        generating, release, ready, play_release, heard, done = [threading.Event() for _ in range(6)]
        events = []

        def llm(*args, **kwargs):
            generating.set()
            release.wait(2)
            return "小王，这匹马正看着将门呢。"

        def tts(*args):
            ready.set()
            play_release.wait(2)
            return "sample.wav"

        def notify(kind, text, data):
            events.append((kind, data))
            if kind == "reply": heard.set()
            if kind == "interaction_done": done.set()

        payload = {"观众": "小王", "弹幕": "这匹马有什么用？", "互动标识": "chosen-1"}
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True, cooldown=0), notify, llm=llm, tts=tts)
        with patch("live_host.winsound.PlaySound"), patch.object(pipeline, "_audio_duration", return_value=0.02):
            try:
                self.assertTrue(pipeline.submit(3, "弹幕回复", payload))
                self.assertTrue(generating.wait(1))
                self.assertFalse(pipeline.submit(3, "弹幕回复", {"观众": "小李"}))
                self.assertFalse(heard.is_set())
                release.set()
                self.assertTrue(ready.wait(1))
                self.assertFalse(heard.is_set())
                play_release.set()
                self.assertTrue(heard.wait(1))
                self.assertTrue(done.wait(1))
                reply = next(data for kind, data in events if kind == "reply")
                self.assertEqual(reply["观众"], "小王")
                self.assertEqual(reply["弹幕"], payload["弹幕"])
                self.assertTrue(next(data for kind, data in events if kind == "interaction_done")["completed"])
            finally:
                release.set(); play_release.set(); pipeline.close()

    def test_expired_comment_never_calls_model_or_updates_current_interaction(self):
        done = threading.Event()
        events = []
        def notify(kind, text, data):
            events.append(kind)
            if kind == "interaction_done": done.set()
        llm = Mock(return_value="过期内容")
        pipeline = SpeechPipeline(LiveSettings(cooldown=0), notify, llm=llm)
        try:
            pipeline.submit(3, "弹幕回复", {"_expires_at": time.monotonic() - 1})
            self.assertTrue(done.wait(1))
            llm.assert_not_called()
            self.assertNotIn("reply", events)
        finally:
            pipeline.close()

    def test_only_pending_finisher_interrupts_chat_audio(self):
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True), Mock())
        def start(*args):
            pipeline.prepared["new-move"] = {"decision": threading.Event(), "approved": None,
                                              "kind": "绝杀长解说"}
        with patch("live_host.winsound.PlaySound", side_effect=start), patch.object(
                pipeline, "_audio_duration", return_value=0.1):
            self.assertFalse(pipeline._play_synced_audio("sample.wav", {"观众": "小王"},
                {"approved": True, "epoch": 0}, "回复", "弹幕回复", acknowledge=False))
            pipeline.close()

    def test_header_uses_the_chosen_message_and_is_not_replaced_by_other_chat_or_chess(self):
        panel = object.__new__(ControlPanel)
        panel.output = queue.Queue()
        panel.root = Mock()
        panel.task_cancel = threading.Event()
        panel.live_controller = Mock(enabled=True)
        panel.interaction_id = None
        for name in ("interaction_state", "interaction_user", "interaction_message", "audience_text",
                     "chat_sampling_text", "speech_status"):
            setattr(panel, name, Mock())
        panel.append_speech = Mock()
        def event(kind, data, text="回复内容"):
            panel.output.put(("live_event", {"kind": kind, "data": data, "text": text}))
        event("queued", {"观众": "小李", "弹幕": "尚未回复"})
        event("chat", {"user": "路人", "text": "没选中"})
        panel._poll()
        panel.interaction_user.set.assert_not_called()
        event("reply", {"kind": "弹幕回复", "观众": "小王", "弹幕": "马能跳这里吗？", "互动标识": "one"})
        event("reply", {"kind": "关键走棋解说"})
        event("interaction_done", {"互动标识": "old", "completed": True})
        panel._poll()
        panel.interaction_user.set.assert_called_once_with("小王")
        panel.interaction_message.set.assert_called_once_with("马能跳这里吗？")
        self.assertEqual(panel.interaction_id, "one")
        event("room_stats", {"online": 1200, "interval": 20, "chat_rate": 4.2})
        event("interaction_done", {"互动标识": "one", "completed": True})
        panel._poll()
        panel.audience_text.set.assert_called_with("在线 1,200 人")
        panel.interaction_state.set.assert_called_with("已回复 · 自动抽选中")


if __name__ == "__main__":
    unittest.main()
