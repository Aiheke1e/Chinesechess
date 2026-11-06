"""Regressions from the actual live transcript, without room or board actions."""
from copy import deepcopy
import threading
import unittest
from unittest.mock import Mock

from board import START_FEN
from interaction_chess import chess_comment
from interaction_text import gift_reply, local_reply, spoken_reply
from live_host import LiveSettings, SpeechPipeline


class LiveSpeechCaseTests(unittest.TestCase):
    def test_other_hosts_course_permissions_are_not_invented(self):
        for comment in (
            "黄老师抖音上购买的课程能在象棋微学堂上看吗？我购买的你的课程都在微学堂里面",
            "微学堂和抖音的课程互通吗", "你这个课程可以在哪看", "购买课程后能退款吗",
        ):
            text = spoken_reply("黄老师，这两边的课程是分开的。", payload={"观众":"棋友小陈", "弹幕":comment})
            self.assertIn("购买平台的说明", text)
            self.assertIn("查不到", text)
            self.assertNotIn("是分开的", text)
            self.assertNotIn("黄老师", text)
            self.assertTrue(text.startswith("棋友小陈，"))

    def test_colloquial_capture_and_elephant_attack_stay_in_chess_context(self):
        snapshot = {"fen":START_FEN, "side":"r", "moves":[]}
        original = deepcopy(snapshot)
        for comment in ("它是想破相吧", "打你象", "他吃你的马", "吃对面的车", "拿他那门炮"):
            self.assertTrue(chess_comment(comment), comment)
            text = local_reply(comment, snapshot)
            self.assertIsNotNone(text)
            self.assertNotIn("大力士", text)
            self.assertNotIn("破镜重圆", text)
            self.assertNotIn("肯定", text)
        self.assertEqual(snapshot, original)
        self.assertFalse(chess_comment("他打你的手机干什么"))

    def test_simple_praise_does_not_grow_into_an_unrelated_monologue(self):
        text = spoken_reply("看来你是棋盘上的侦探，咱们得好好聊聊你棋局里的秘密。", payload={"观众":"老张", "弹幕":"有研究"})
        self.assertLess(len(text), 30)
        self.assertNotIn("秘密", text)
        self.assertNotIn("侦探", text)

    def test_gift_honorific_is_not_stacked_on_the_masked_name_fallback(self):
        self.assertEqual(gift_reply("", {"观众":"阿***", "礼物":"小心心"}), "感谢阿老板送的小心心，谢谢老板。")
        self.assertEqual(gift_reply("", {"观众":"老王棋友", "礼物":"粉丝团灯牌"}), "感谢老王棋友老板送的粉丝团灯牌，谢谢老板。")

    def test_course_answer_bypasses_the_model_and_preserves_the_actual_comment(self):
        heard = threading.Event()
        records = []
        comment = "黄老师抖音上买的课程能在微学堂看吗"
        def notify(kind, text, data):
            if kind == "reply":
                records.append((text, data))
                heard.set()
        model = Mock(side_effect=AssertionError("不能让模型编造课程权限"))
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=False, cooldown=0), notify, llm=model)
        try:
            pipeline.submit(3, "弹幕回复", {"观众":"真实观众", "弹幕":comment})
            self.assertTrue(heard.wait(1))
            model.assert_not_called()
            text, data = records[0]
            self.assertIn("查不到", text)
            self.assertEqual(data["观众"], "真实观众")
            self.assertEqual(data["弹幕"], comment)
        finally:
            pipeline.close()
