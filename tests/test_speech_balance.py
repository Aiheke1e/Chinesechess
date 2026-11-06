import threading
import unittest
from unittest.mock import patch

from live_host import LiveSettings, SpeechPipeline
from speech_balance import SpeechBalance


class SpeechBalanceTests(unittest.TestCase):
    def test_busy_chess_stream_has_both_reply_count_and_airtime_limits(self):
        now = [100.0]
        balance = SpeechBalance(clock=lambda: now[0])
        balance.set_chess_active(True)
        for _ in range(3):
            self.assertTrue(balance.can_chat())
            balance.record("弹幕回复", 5)
            self.assertFalse(balance.can_chat())
            now[0] += 11
        self.assertFalse(balance.can_chat())
        now[0] += 60
        self.assertTrue(balance.can_chat())
        balance.record("礼物答谢", 11)
        balance.record("弹幕回复", 10)
        self.assertFalse(balance.can_chat())
        balance.set_chess_active(False)
        self.assertTrue(balance.can_chat())

    def test_chess_slots_are_bounded_and_do_not_create_empty_speech(self):
        now = [100.0]
        balance = SpeechBalance(clock=lambda: now[0])
        balance.set_chess_active(True)
        self.assertFalse(balance.reserve_chess())
        now[0] += 16
        self.assertTrue(balance.reserve_chess())
        self.assertFalse(balance.can_chat())
        now[0] += 7
        self.assertTrue(balance.can_chat())
        self.assertFalse(balance.reserve_chess())
        self.assertTrue(balance.reserve_chess(12, force=True))
        self.assertFalse(balance.can_chat())
        balance.set_chess_active(False)
        self.assertTrue(balance.can_chat())

    def test_sandbox_gets_a_full_slot_before_the_next_chat_reply(self):
        now = [100.0]
        chat, sandbox = threading.Event(), threading.Event()
        def notify(kind, text, payload):
            if kind == "reply":
                (chat if payload["kind"] == "弹幕回复" else sandbox).set()
        with patch.object(SpeechPipeline, "_write_speech_marker"), patch("live_host.winsound.PlaySound"), patch.object(
                SpeechPipeline, "_audio_duration", return_value=.01):
            pipeline = SpeechPipeline(LiveSettings(speech_enabled=True, cooldown=0), notify,
                llm=lambda *args, **kwargs: "晚上好。", tts=lambda *args: "mock.wav")
            try:
                pipeline.balance = SpeechBalance(clock=lambda: now[0])
                pipeline.set_chess_active(True)
                pipeline.reserve_sandbox()
                pipeline.submit(3, "弹幕回复", {"观众": "小王", "弹幕": "晚上好"})
                pipeline.prepare_move("沙盘推演解说", {"speech_token": "demo:sandbox"}, "来，摆三手看看这轮交换。")
                pipeline.commit_move("demo:sandbox")
                self.assertTrue(sandbox.wait(1))
                self.assertFalse(chat.wait(.04))
                now[0] += 13
                self.assertTrue(chat.wait(1))
                for _ in range(3):
                    pipeline.balance.record("弹幕回复", 6)
                self.assertFalse(pipeline.can_reply_chat())
                pipeline.pause_chess()
                # Allow the current worker's finally block to release its slot.
                pipeline.cancel_chats()
                self.assertTrue(pipeline.can_reply_chat())
            finally:
                pipeline.close()

    def test_sandbox_waits_for_a_short_chat_and_blocks_the_next_reply(self):
        chat, sandbox = threading.Event(), threading.Event()
        def notify(kind, text, payload):
            if kind == "reply":
                (chat if payload["kind"] == "弹幕回复" else sandbox).set()
        with patch.object(SpeechPipeline, "_write_speech_marker"), patch("live_host.winsound.PlaySound"), patch.object(
                SpeechPipeline, "_audio_duration", side_effect=lambda path: 2 if "chat" in path else .01):
            pipeline = SpeechPipeline(LiveSettings(speech_enabled=True, cooldown=0), notify,
                llm=lambda *args, **kwargs: "晚上好。",
                tts=lambda text, voice: "chat.wav" if "晚上" in text else "sandbox.wav")
            try:
                pipeline.set_chess_active(True)
                pipeline.submit(3, "弹幕回复", {"观众": "小王", "弹幕": "晚上好"})
                self.assertTrue(chat.wait(1))
                pipeline.prepare_move("沙盘推演解说", {"speech_token": "demo:sandbox"}, "来，把这轮交换摆清楚。")
                pipeline.reserve_sandbox()
                pipeline.commit_move("demo:sandbox")
                self.assertFalse(sandbox.wait(.1))
                self.assertTrue(sandbox.wait(2.5))
            finally:
                pipeline.close()
