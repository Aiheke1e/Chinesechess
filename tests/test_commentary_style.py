import json
import threading
import unittest
from unittest.mock import Mock, patch

from commentary_style import CommentaryStyle, DEFAULT_PERSONA, LEGACY_PERSONAS
from live_host import LiveController, LiveSettings, SpeechPipeline, load_settings
from interaction_text import gift_reply, spoken_reply, welcome_line
from stream_brand import (HOST_NAME, DEFAULT_INTRODUCTION, LEGACY_INTRODUCTIONS,
                          migrate_host_identity)


class CommentaryStyleTests(unittest.TestCase):
    def test_each_move_scene_keeps_exact_move_and_passes_the_broadcast_guard(self):
        rows = [
            ("红方 炮五进四，吃车", []), ("黑方 车8进2，吃马", []),
            ("红方 马三进四，吃炮", []), ("红方 兵三进一，吃卒", []),
            ("红方 车九平二", ["先解除将军"]),
            ("红方 炮五进四，吃车", ["形成将军"]),
            ("红方 马七退五", ["争夺中路"]), ("红方 马二进三", []),
            ("红方 车九平二", []), ("红方 炮二退五", []),
            ("黑方 卒5平4", []), ("红方 相七进五", []),
            ("黑方 士6进5", []), ("黑方 将5平4", []),
        ]
        for notation, facts in rows:
            style = CommentaryStyle()
            payload = {"我方计划走法": notation, "QUEEN式证据": {"选择理由": facts}}
            factual = LiveController._synced_move_text(notation, facts)
            for _ in range(10):
                with self.subTest(notation=notation):
                    text = style.decorate("关键走棋解说", factual, payload, DEFAULT_PERSONA)
                    self.assertTrue(text.startswith(factual))
                    self.assertLessEqual(len(text), 46)
                    self.assertTrue(SpeechPipeline._chess_line_valid(text, "关键走棋解说", payload), text)
                    style.remember(text)

    def test_king_and_pawn_moves_never_invent_check_escape_or_forward_motion(self):
        for notation in ("黑方 将5平4", "红方 帅五平四", "黑方 卒5平4"):
            style = CommentaryStyle()
            for _ in range(8):
                text = style.decorate("关键走棋解说", LiveController._synced_move_text(notation),
                    {"我方计划走法": notation}, DEFAULT_PERSONA)
                for false_fact in ("将军", "解将", "避攻", "向前", "吃掉", "拿下"):
                    self.assertNotIn(false_fact, text)

    def test_jokes_rotate_themes_without_becoming_permanently_silent(self):
        style = CommentaryStyle()
        lines, families = [], []
        base = "车八进一，吃掉一辆车。"
        for _ in range(25):
            text = style.decorate("关键走棋解说", base, {"我方计划走法": "红方 车八进一，吃车"}, DEFAULT_PERSONA)
            lines.append(text)
            if text != base and style.recent_families[-1]:
                families.append(style.recent_families[-1])
            style.remember(text)
        self.assertGreaterEqual(len(set(lines)), 4)
        self.assertTrue(any(text != base for text in lines[-5:]))
        self.assertTrue(all(a != b for a, b in zip(families, families[1:])))
        self.assertLessEqual(sum(text != base for text in lines), 9)

    def test_quiet_moves_and_check_escape_are_not_automatically_given_a_joke(self):
        style = CommentaryStyle()
        for _ in range(10):
            self.assertEqual(style.decorate("关键走棋解说", "马二进三。", {
                "我方计划走法": "红方 马二进三"}, DEFAULT_PERSONA), "马二进三。")
            self.assertEqual(style.decorate("关键走棋解说", "仕六进五，先把将解开。", {
                "我方计划走法": "红方 仕六进五", "QUEEN式证据": {
                    "选择理由": ["先解除将军"]}}, DEFAULT_PERSONA), "仕六进五，先把将解开。")

    def test_exchange_does_not_get_a_celebratory_free_piece_joke(self):
        style = CommentaryStyle()
        text = "车八进一，吃掉一辆车。"
        for _ in range(8):
            self.assertEqual(style.decorate("关键走棋解说", text, {
                "我方计划走法": "红方 车八进一，吃车",
                "QUEEN式证据": {"应手回吃本子": True}}, DEFAULT_PERSONA), text)

    def test_serious_persona_and_unproven_followups_keep_factual_copy(self):
        style = CommentaryStyle()
        self.assertEqual(style.decorate("关键走棋解说", "车九平二。", {
            "我方计划走法": "红方 车九平二"}, "严肃讲棋，只报走法"), "车九平二。")
        self.assertEqual(style.opening("起马局", "先让马儿出来上班。", "不要玩梗"), "这是起马局。")
        for matched in (False, True):
            text = style.decorate("QUEEN式等待期解说", "原台词。", {
                "上一手预判复盘": {"预判兑现": matched, "实际应手": "黑方 炮7平8"}}, DEFAULT_PERSONA)
            self.assertIn("炮7平8", text)
            for banned in ("猜", "押", "蒙", "高手", "拿下"):
                self.assertNotIn(banned, text)
        self.assertEqual(style.decorate("QUEEN式等待期解说", "对手车2进9吃车。", {
            "上一手预判复盘": {"实际应手": "黑方 车2进9，吃车"}}, DEFAULT_PERSONA), "对手车2进9吃车。")

    def test_only_stock_personas_are_migrated_and_custom_settings_are_preserved(self):
        for old in LEGACY_PERSONAS:
            with patch("live_host.CONFIG_PATH") as config:
                config.read_text.return_value = json.dumps({"persona": old, "voice": "Maia"})
                self.assertEqual(load_settings().persona, DEFAULT_PERSONA)
                config.write_text.assert_not_called()
        with patch("live_host.CONFIG_PATH") as config:
            config.read_text.return_value = json.dumps({"persona": "严肃讲棋，不讲笑话"})
            self.assertEqual(load_settings().persona, "严肃讲棋，不讲笑话")

    def test_identity_migration_preserves_the_existing_tts_and_room(self):
        for introduction in LEGACY_INTRODUCTIONS:
            with self.subTest(introduction=introduction), patch("live_host.CONFIG_PATH") as config:
                config.read_text.return_value = json.dumps({
                    "persona": next(iter(LEGACY_PERSONAS)), "introduction": introduction,
                    "voice": "longanyang", "room": "123456789", "speech_enabled": True})
                settings = load_settings()
                self.assertTrue(settings.persona.startswith(f"你叫{HOST_NAME}"))
                self.assertEqual(settings.introduction, DEFAULT_INTRODUCTION)
                self.assertEqual(settings.voice, "longanyang")
                self.assertEqual(settings.room, "123456789")
                self.assertTrue(settings.speech_enabled)
                config.write_text.assert_not_called()

    def test_custom_style_migration_does_not_rewrite_tool_or_viewer_references(self):
        custom = "你叫小弈，严肃讲棋，不讲笑话。观众小弈爱下棋；以后考虑接豆包语音。"
        expected = f"你叫{HOST_NAME}，严肃讲棋，不讲笑话。观众小弈爱下棋；以后考虑接豆包语音。"
        with patch("live_host.CONFIG_PATH") as config:
            config.read_text.return_value = json.dumps({"persona": custom,
                "introduction": "我是小弈，有问题直接聊。", "voice": "Maia"})
            settings = load_settings()
            self.assertEqual(settings.persona, expected)
            self.assertEqual(settings.introduction, f"我是{HOST_NAME}，有问题直接聊。")
            config.write_text.assert_not_called()
        self.assertEqual(migrate_host_identity("我是豆包用户，称呼观众豆包棋友。"),
                         "我是豆包用户，称呼观众豆包棋友。")

    def test_actual_prepared_audio_and_transcript_use_the_same_styled_move(self):
        heard, acknowledged = threading.Event(), threading.Event()
        records = []
        def notify(kind, text, data):
            if kind == "reply":
                records.append((text, data))
                heard.set()
        tts, llm = Mock(return_value="styled.wav"), Mock()
        with patch.object(SpeechPipeline, "_write_speech_marker"), patch(
                "live_host.winsound.PlaySound"), patch.object(SpeechPipeline, "_audio_duration", return_value=.01):
            pipeline = SpeechPipeline(LiveSettings(speech_enabled=True, cooldown=0), notify, llm=llm, tts=tts)
            try:
                with patch.object(pipeline, "_mark_speech_started", side_effect=lambda payload: acknowledged.set()):
                    payload = {"speech_token": "move-17", "我方计划走法": "红方 炮五进四，吃车",
                               "解说对应手数": 17, "解说对应走法": "红方 炮五进四，吃车"}
                    pipeline.prepare_move("关键走棋解说", payload, "炮五进四，吃掉一辆车。")
                    pipeline.commit_move("move-17")
                    self.assertTrue(heard.wait(1))
                    self.assertTrue(acknowledged.wait(1))
                    spoken, provenance = records[0]
                    self.assertTrue(spoken.startswith("炮五进四，吃掉一辆车。"))
                    self.assertGreater(len(spoken), len("炮五进四，吃掉一辆车。"))
                    self.assertEqual(tts.call_args.args[0], spoken)
                    self.assertEqual(provenance["speech_token"], "move-17")
                    self.assertEqual(provenance["解说对应手数"], 17)
                    llm.assert_not_called()
            finally:
                pipeline.close()

    def test_observed_model_errors_cannot_turn_criticism_into_praise_or_start_a_game(self):
        criticism = spoken_reply("棋友，你这话说得我都不好意思了，棋下得这么好，人气病都出来了，那我岂不是要成网红主播了？哈哈。",
            payload={"观众": "李***", "弹幕": "主播棋下得能把人气出病来"})
        self.assertIn("意见收到", criticism)
        self.assertNotIn("网红", criticism)
        greeting = spoken_reply("棋友老张，晚上好呀，我这棋盘都等不及和你对弈了呢，别急，我先摆个棋局给你看看。",
            payload={"观众": "棋友老张", "弹幕": "主播晚上好"})
        self.assertIn("晚上好", greeting)
        self.assertNotIn("摆", greeting)
        self.assertNotIn("对弈", greeting)
        self.assertLessEqual(len(greeting), 55)

    def test_gift_thanks_keep_the_sender_and_real_gift_without_promises(self):
        style = CommentaryStyle()
        for raw in ("阿杰，你这小心心送得我心花怒放啊，下次下棋我可得好好照顾你，别让棋子们欺负你哦！",
                    "谢谢阿杰，再刷一个礼物，保证赢棋。", "谢谢小王的跑车。"):
            text = gift_reply(raw, {"观众": "阿杰", "礼物": "小心心", "数量": 3}, style)
            self.assertIn("阿杰", text)
            self.assertIn("小心心", text)
            self.assertLessEqual(len(text), 35)
            self.assertNotIn("小王", text)
            self.assertNotIn("跑车", text)
            for promise in ("下次", "照顾你", "再刷", "赢棋"):
                self.assertNotIn(promise, text)

    def test_gift_thanks_use_only_the_requested_boss_template(self):
        style = Mock()
        self.assertEqual(gift_reply("模型的一大段闲话", {"观众": "阿杰🐵", "礼物": "小心心", "数量": 6}, style),
                         "感谢阿杰老板送的小心心，谢谢老板。")
        self.assertEqual(gift_reply("", {"观众": "张老板", "礼物": "玫瑰"}), "感谢张老板送的玫瑰，谢谢老板。")
        style.pick.assert_not_called()

    def test_long_readable_name_is_not_replaced_or_cut_in_welcome_and_gift_thanks(self):
        nickname = "来自直播间的完整观众昵称" * 3
        self.assertEqual(welcome_line(nickname), f"欢迎{nickname}进入直播间。")
        self.assertIn(nickname, gift_reply("谢谢礼物", {"观众": nickname, "礼物": "小心心"}))
        self.assertTrue(spoken_reply("晚上好。", payload={"观众": nickname}).startswith(nickname + "，"))

    def test_interaction_style_hints_carry_topics_without_other_viewers_messages(self):
        heard = threading.Event()
        llm = Mock(return_value="晚上好。")
        with patch.object(SpeechPipeline, "_write_speech_marker"):
            pipeline = SpeechPipeline(LiveSettings(cooldown=0),
                lambda kind, text, data: heard.set() if kind == "reply" else None, llm=llm)
            try:
                pipeline.style.remember("别的观众，这笔战略投资，咱们先签收。")
                pipeline.submit(3, "弹幕回复", {"观众": "小王", "弹幕": "主播晚上好"})
                self.assertTrue(heard.wait(1))
                sent = llm.call_args.args[1]
                self.assertIn("财务", sent["最近用过的比喻"])
                self.assertNotIn("别的观众", str(sent))
                self.assertEqual(llm.call_args.args[2], [])
            finally:
                pipeline.close()

    def test_explicit_test_input_never_plays_audio_even_when_voice_is_enabled(self):
        heard = threading.Event()
        tts = Mock(return_value="should-not-play.wav")
        with patch.object(SpeechPipeline, "_write_speech_marker"), patch("live_host.winsound.PlaySound") as play:
            pipeline = SpeechPipeline(LiveSettings(speech_enabled=True, cooldown=0),
                lambda kind, text, data: heard.set() if kind == "reply" else None,
                llm=lambda *args, **kwargs: "这是一条测试回复。", tts=tts)
            try:
                pipeline.submit(3, "弹幕回复", {"观众": "测试昵称", "弹幕": "你好", "测试输入": True})
                self.assertTrue(heard.wait(1))
                tts.assert_not_called()
                play.assert_not_called()
            finally:
                pipeline.close()


if __name__ == "__main__":
    unittest.main()
