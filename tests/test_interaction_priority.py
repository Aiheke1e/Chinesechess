import json
import threading
import time
import unittest
from unittest.mock import Mock, patch

import glm_client
import player
from chat_selection import ChatSelector
from interaction_text import spoken_reply, viewer_address, welcome_line
from live_host import LiveController, LiveSettings, SpeechPipeline


class InteractionTextTests(unittest.TestCase):
    def test_welcome_has_only_the_name_and_entry_notice(self):
        self.assertEqual(welcome_line("小王"), "欢迎小王进入直播间。")
        self.assertEqual(welcome_line("李***"), "欢迎李棋友进入直播间。")
        self.assertEqual(spoken_reply("这首挺合你胃口。[捂脸][鼓掌]😀"), "这首挺合你胃口。")

    def test_logged_criticism_and_listening_comments_cannot_turn_into_chess_praise(self):
        self.assertEqual(spoken_reply("李棋友，你夸得我不好意思，棋艺再好也不能气病。", payload={
            "观众": "李***", "弹幕": "主播棋下的，能把人气出病来。[捂脸]"}),
            "李棋友，看得你着急了，咱们少点惊险，多点稳当。")
        self.assertEqual(spoken_reply("李棋友，这首歌挺不错，您喜欢吗？", payload={
            "观众": "李***", "弹幕": "出招吧真好听"}), "李棋友，合你胃口就好，听着舒服最重要。")
        self.assertEqual(spoken_reply("李棋友，晚上好。", payload={"观众": "李***"}), "李棋友，晚上好。")

    def test_visible_nickname_is_used_once_without_guessing_masked_characters(self):
        self.assertEqual(viewer_address("睡懵的象棋小仙女🧜🏻‍♀️"), "睡懵的象棋小仙女")
        self.assertEqual(viewer_address("***"), "")
        self.assertEqual(welcome_line("😀"), "欢迎新朋友进入直播间。")
        for answer in ("这位棋友，晚上好。", "晚上好。", "小王，晚上好。"):
            self.assertEqual(spoken_reply(answer, payload={"观众": "小王"}), "小王，晚上好。")
        self.assertEqual(spoken_reply("棋友老张，晚上好。", payload={"观众": "棋友老张"}), "棋友老张，晚上好。")
        answer = spoken_reply("这位棋友，" + "这匹马要看住将门" * 9, payload={"观众": "睡懵的象棋小仙女🧜🏻‍♀️"})
        self.assertLessEqual(len(answer), 55)
        self.assertIn("这匹马", answer)
        self.assertTrue(answer.startswith("睡懵的象棋小仙女，"))

    def test_replies_do_not_invent_giveaways_links_or_gift_conditions(self):
        answer = spoken_reply("棋谱在直播间页面有链接，点击就能领取啦！", payload={
            "观众": "原***", "弹幕": "哪里有棋谱领"})
        self.assertEqual(answer, "原棋友，棋谱的领取入口，我这边还没有信息，以直播间公布的说明为准。")
        self.assertEqual(spoken_reply("送我个礼物，我就能告诉你怎么领啦！", payload={
            "观众": "小王", "弹幕": "怎么领"}), "小王，你想领什么？说具体一点。")
        self.assertIn("不用送礼", spoken_reply("先刷礼物，我才回答。", payload={"观众": "小王", "弹幕": "怎么下？"}))

    def test_welcome_does_not_call_a_model(self):
        heard = threading.Event()
        records = []
        llm = Mock(return_value="欢迎之后的一大段套话")
        def notify(kind, text, data):
            if kind == "reply":
                records.append(text)
                heard.set()
        with patch.object(SpeechPipeline, "_write_speech_marker"):
            pipeline = SpeechPipeline(LiveSettings(cooldown=0), notify, llm=llm)
            try:
                pipeline.submit(4, "欢迎观众", {"观众": "小王"})
                self.assertTrue(heard.wait(1))
                self.assertEqual(records, ["欢迎小王进入直播间。"])
                llm.assert_not_called()
            finally:
                pipeline.close()

    def test_welcome_is_at_most_once_per_minute(self):
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings()
        controller.enabled = True
        controller.seen, controller.welcomed = {}, set()
        controller.last_welcome = 0
        controller.pipeline, controller._notify = Mock(), Mock()
        controller.pipeline.can_reply_chat.return_value = True
        for second in range(100, 162):
            with patch("live_host.time.monotonic", return_value=second):
                controller.receive({"type": "member", "id": str(second), "user_id": "111111",
                                    "user": f"棋友{second}"})
        self.assertEqual(controller.pipeline.submit.call_count, 2)
        for call in controller.pipeline.submit.call_args_list:
            self.assertEqual(call.kwargs["direct_text"], welcome_line(call.args[2]["观众"]))

    def test_placeholder_ids_do_not_merge_different_viewers(self):
        selector = ChatSelector(clock=lambda: 100)
        selector.offer({"user_id": "111111", "user": "大***", "text": "这盘为什么要跳马？"})
        selector.offer({"user_id": "111111", "user": "李***", "text": "出招吧真好听"})
        self.assertEqual(len(selector.pending), 2)
        self.assertEqual({row["user"] for row in selector.pending.values()}, {"大***", "李***"})

    def test_reply_request_does_not_contain_other_viewers_history(self):
        with patch("glm_client.chat", return_value="听着开心就好。") as chat:
            glm_client.respond("弹幕回复", {"观众": "李***", "弹幕": "出招吧真好听"},
                               [{"role": "assistant", "content": "别的观众刚才在说跳马"}])
        request = json.loads(chat.call_args.args[0][0]["content"])
        self.assertEqual(request["输入"]["弹幕"], "出招吧真好听")
        self.assertNotIn("最近对话", request)
        self.assertEqual(request["称呼"], "李棋友")
        self.assertNotIn("别的观众", str(chat.call_args.args))
        self.assertEqual(chat.call_args.kwargs["system_prompt"], glm_client.INTERACTION_PROMPT)


class InteractionPriorityTests(unittest.TestCase):
    def setUp(self):
        self.markers = patch.object(SpeechPipeline, "_write_speech_marker").start()
        self.sound = patch("live_host.winsound.PlaySound").start()
        self.pipelines = []

    def tearDown(self):
        for pipeline in self.pipelines:
            pipeline.close()
        patch.stopall()

    def pipeline(self, notify, llm=None):
        pipeline = SpeechPipeline(LiveSettings(cooldown=0, speech_enabled=True), notify,
            llm=llm or (lambda *args, **kwargs: "当前弹幕的回复。"), tts=lambda text, voice: text + ".wav")
        self.pipelines.append(pipeline)
        return pipeline

    def test_chat_generation_bypasses_an_inflight_gift_model_request(self):
        gift_started, release, chat_heard = (threading.Event() for _ in range(3))
        def llm(kind, *args, **kwargs):
            if kind == "礼物答谢":
                gift_started.set()
                release.wait(2)
                return "谢谢礼物。"
            return "当前弹幕的回复。"
        def notify(kind, text, data):
            if kind == "reply" and data["kind"] == "弹幕回复": chat_heard.set()
        pipeline = self.pipeline(notify, llm)
        with patch.object(pipeline, "_audio_duration", return_value=.01):
            try:
                pipeline.submit(0, "礼物答谢", {"观众": "送礼观众"})
                self.assertTrue(gift_started.wait(1))
                pipeline.submit(9, "弹幕回复", {"观众": "小王", "弹幕": "主播晚上好"})
                self.assertTrue(chat_heard.wait(.5))
            finally:
                release.set()

    def test_chat_preempts_an_ordinary_move_audio(self):
        move_heard, chat_heard = threading.Event(), threading.Event()
        def notify(kind, text, data):
            if kind == "reply":
                (chat_heard if data["kind"] == "弹幕回复" else move_heard).set()
        pipeline = self.pipeline(notify)
        with patch.object(pipeline, "_audio_duration", side_effect=lambda path: .01 if "当前弹幕" in path else 2):
            pipeline.prepare_move("关键走棋解说", {"speech_token": "move"}, "马二进三。")
            pipeline.commit_move("move", deadline=time.monotonic() + 2)
            self.assertTrue(move_heard.wait(1))
            self.assertTrue(pipeline.can_reply_chat())
            pipeline.submit(3, "弹幕回复", {"观众": "小王", "弹幕": "主播晚上好"})
            self.assertTrue(chat_heard.wait(.5))

    def test_unstarted_move_waits_for_chat_and_extends_only_its_priority_wait(self):
        generating, release, acknowledged = (threading.Event() for _ in range(3))
        order = []
        def llm(*args, **kwargs):
            generating.set()
            release.wait(2)
            return "当前弹幕的回复。"
        def notify(kind, text, data):
            if kind == "reply": order.append(data["kind"])
        pipeline = self.pipeline(notify, llm)
        with patch.object(pipeline, "_audio_duration", return_value=.01), patch.object(
                pipeline, "_mark_speech_started", side_effect=lambda payload: acknowledged.set()):
            try:
                pipeline.submit(3, "弹幕回复", {"观众": "小王", "弹幕": "主播晚上好"})
                self.assertTrue(generating.wait(1))
                pipeline.prepare_move("关键走棋解说", {"speech_token": "move"}, "马二进三。")
                pipeline.commit_move("move", deadline=time.monotonic() + .05)
                self.assertFalse(acknowledged.wait(.09))
                release.set()
                self.assertTrue(acknowledged.wait(1))
                self.assertEqual(order[:2], ["弹幕回复", "关键走棋解说"])
            finally:
                release.set()

    def test_verified_finisher_finishes_before_chat_starts(self):
        mate_heard, chat_heard = threading.Event(), threading.Event()
        order = []
        def notify(kind, text, data):
            if kind == "reply":
                order.append(data["kind"])
                (chat_heard if data["kind"] == "弹幕回复" else mate_heard).set()
        pipeline = self.pipeline(notify)
        with patch.object(pipeline, "_audio_duration", side_effect=lambda path: .01 if "当前弹幕" in path else .15), patch.object(
                pipeline, "_mark_speech_finished", side_effect=lambda payload: order.append("绝杀播完")):
            pipeline.prepare_move("绝杀长解说", {"speech_token": "mate", "绝杀长播报": True}, "车二进一，重炮绝杀。")
            pipeline.commit_move("mate", deadline=time.monotonic() + 2)
            self.assertTrue(mate_heard.wait(1))
            self.assertFalse(pipeline.can_reply_chat())
            pipeline.submit(3, "弹幕回复", {"观众": "小王", "弹幕": "主播晚上好"})
            self.assertFalse(chat_heard.wait(.04))
            self.assertTrue(chat_heard.wait(1))
            self.assertLess(order.index("绝杀播完"), order.index("弹幕回复"))

    def test_player_waits_through_chat_without_accepting_a_wrong_move_token(self):
        clock = [100.0]
        def marker(path):
            if path == player.SPEECH_PREPARING_PATH:
                return {"token": "move", "enabled": True}
            return {"token": "move" if clock[0] >= 100.12 else "old", "status": "started"}
        def advance(seconds):
            clock[0] += seconds
        with patch.object(player, "_speech_enabled", return_value=True), patch.object(
                player, "_read_speech_marker", side_effect=marker), patch.object(
                player, "_chat_priority_active", side_effect=lambda: clock[0] < 100.12), patch.object(
                player.time, "monotonic", side_effect=lambda: clock[0]), patch.object(
                player.time, "sleep", side_effect=advance):
            self.assertTrue(player.wait_for_speech_start("move", audio_timeout=.06, priority_timeout=.3))

    def test_priority_wait_has_a_limit_and_never_allows_an_unplayed_move(self):
        with patch.object(player, "_speech_enabled", return_value=True), patch.object(
                player, "_read_speech_marker", return_value={"token": "move", "enabled": True}), patch.object(
                player, "_chat_priority_active", return_value=True):
            with self.assertRaises(player.SpeechSyncError):
                player.wait_for_speech_start("move", audio_timeout=.02, priority_timeout=.03)


if __name__ == "__main__":
    unittest.main()
