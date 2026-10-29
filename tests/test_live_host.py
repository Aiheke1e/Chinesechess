import threading
import time
import unittest
from unittest.mock import patch
import heapq

from live_host import LiveController, LiveSettings, SpeechPipeline, normalize_room


class LiveHostTests(unittest.TestCase):
    @staticmethod
    def _idle_pipeline():
        pipeline = object.__new__(SpeechPipeline)
        pipeline.settings = LiveSettings()
        pipeline.notify = lambda *args: None
        pipeline.closed = threading.Event()
        pipeline.condition = threading.Condition()
        pipeline.jobs = []
        pipeline.sequence = 0
        pipeline.cancel_epoch = 0
        pipeline._ensure_worker = lambda: True
        return pipeline

    def test_new_own_move_replaces_queued_old_chess_speech(self):
        pipeline = self._idle_pipeline()
        pipeline.submit(1, "对手走棋播报", {"走法": "卒7进1"}, "旧应手")
        pipeline.submit(1, "我方走棋播报", {"走法": "马二进三"}, "最新落子")
        self.assertEqual(len(pipeline.jobs), 1)
        self.assertEqual(pipeline.jobs[0][4], "我方走棋播报")

    def test_jump_to_latest_keeps_only_newest_chess_job(self):
        pipeline = self._idle_pipeline()
        pipeline.submit(1, "我方走棋播报", {"走法": "马二进三"}, "旧落子")
        pipeline.submit(2, "对手走棋播报", {"走法": "马8进7"}, "最新应手")
        with patch("live_host.winsound.PlaySound") as play_mock:
            pipeline.jump_to_latest()
        self.assertEqual(len(pipeline.jobs), 1)
        self.assertEqual(pipeline.jobs[0][4], "对手走棋播报")
        play_mock.assert_not_called()

    def test_move_selected_waits_for_importance_analysis(self):
        prepared = []

        class Pipeline:
            def cancel_idle(self):
                pass

            def prepare_move(self, kind, payload, direct_text=None):
                prepared.append((kind, payload, direct_text))

        controller = object.__new__(LiveController)
        controller.pipeline = Pipeline()
        controller._notify = lambda *args: None
        controller.submit_chess({
            "event": "move_selected", "number": 2,
            "notation": "红方 马二进三", "speech_token": "move-2"})
        self.assertEqual(prepared, [])
        self.assertIn("move-2", controller.pending_move_events)

    def test_confirmed_move_plays_preheated_audio(self):
        tts_ready = threading.Event()
        finished = threading.Event()
        records = []

        def notify(kind, text, data=None):
            records.append((kind, text))
            if kind == "speech_status" and text == "等待下一条内容":
                finished.set()

        def fake_tts(text, voice):
            tts_ready.set()
            return "prepared.wav"

        with patch("live_host.winsound.PlaySound") as play_mock:
            pipeline = SpeechPipeline(
                LiveSettings(cooldown=0, speech_enabled=True), notify,
                llm=lambda *args, **kwargs: "跳马抢位，马二进三。",
                tts=fake_tts)
            pipeline.prepare_move("我方走棋解说", {
                "当前局面": "test", "当前走子": "马二进三",
                "speech_token": "confirmed"})
            self.assertTrue(tts_ready.wait(1))
            play_mock.assert_not_called()
            self.assertTrue(pipeline.commit_move("confirmed"))
            self.assertTrue(finished.wait(1))
            pipeline.close()
        played_files = [call.args[0] for call in play_mock.call_args_list
                        if call.args and call.args[0] is not None]
        self.assertEqual(played_files, ["prepared.wav"])
        self.assertIn(("reply", "跳马抢位，马二进三。"), records)

    def test_rejected_move_discards_preheated_audio(self):
        tts_ready = threading.Event()
        with patch("live_host.winsound.PlaySound") as play_mock:
            pipeline = SpeechPipeline(
                LiveSettings(cooldown=0, speech_enabled=True),
                lambda *args: None,
                llm=lambda *args, **kwargs: "平车压马。",
                tts=lambda *args: (tts_ready.set() or "discarded.wav"))
            pipeline.prepare_move("我方走棋解说", {
                "当前局面": "test", "当前走子": "车九平八",
                "speech_token": "rejected"})
            self.assertTrue(tts_ready.wait(1))
            self.assertTrue(pipeline.discard_move("rejected"))
            time.sleep(0.1)
            pipeline.close()
        played_files = [call.args[0] for call in play_mock.call_args_list
                        if call.args and call.args[0] is not None]
        self.assertEqual(played_files, [])

    def test_chess_sentence_is_capped_at_twenty_characters(self):
        text = SpeechPipeline._short_chess_text(
            "这是一句明显超过二十个汉字而且非常像机器报告的解说。第二句也很长很长。")
        sentences = [part for part in text.replace("！", "。").split("。") if part]
        self.assertTrue(sentences)
        self.assertTrue(all(len(part) <= 20 for part in sentences))

    def test_high_move_call_is_short_and_has_no_side_label(self):
        text = LiveController._high_move_text("黑方 马8进7")
        self.assertEqual(text, "跳马，马8进7。")
        self.assertNotIn("黑方", text)
        self.assertNotIn("对手", text)
        self.assertLessEqual(len(text.rstrip("。")), 20)

    def test_idle_commentary_waits_for_explicit_wait_window(self):
        spoken = threading.Event()
        records = []

        def notify(kind, text, data=None):
            records.append((kind, text))
            if kind == "reply":
                spoken.set()

        pipeline = SpeechPipeline(
            LiveSettings(cooldown=0, speech_enabled=False), notify,
            llm=lambda *args, **kwargs: "刚才跳马抢位，顺手照看中路。",
            tts=lambda *args: self.fail("文本模式不应调用 TTS"))
        pipeline.prepare_idle({"走法": "马二进三"})
        time.sleep(0.1)
        self.assertFalse(spoken.is_set())
        self.assertTrue(pipeline.open_idle_window())
        self.assertTrue(spoken.wait(1))
        pipeline.close()

    def test_opponent_arrival_cancels_unplayed_idle_commentary(self):
        spoken = threading.Event()
        pipeline = SpeechPipeline(
            LiveSettings(cooldown=0, speech_enabled=False),
            lambda kind, *args: spoken.set() if kind == "reply" else None,
            llm=lambda *args, **kwargs: "这段不应该播放。",
            tts=lambda *args: "unused.wav")
        pipeline.prepare_idle({"走法": "炮二平五"})
        time.sleep(0.05)
        self.assertTrue(pipeline.cancel_idle())
        time.sleep(0.1)
        self.assertFalse(spoken.is_set())
        pipeline.close()

    def test_disabled_commentary_ignores_chess_events(self):
        submitted = []

        class Pipeline:
            def submit(self, *args, **kwargs):
                submitted.append((args, kwargs))

        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(commentary_enabled=False)
        controller.pipeline = Pipeline()
        controller.submit_chess({
            "event": "move_selected", "number": 1,
            "notation": "红方 马二进三", "speech_token": "off"})
        self.assertEqual(submitted, [])

    def test_ordinary_own_and_opponent_moves_stay_silent(self):
        calls = []

        class Pipeline:
            def cancel_idle(self):
                calls.append(("cancel", None))

            def prepare_idle(self, payload):
                calls.append(("idle", payload))

            def play_high(self, kind, payload, direct_text=None):
                calls.append((kind, direct_text))

            def submit(self, priority, kind, payload, direct_text=None):
                calls.append((kind, direct_text))

        controller = object.__new__(LiveController)
        controller.enabled = False
        controller.last_engine_score = None
        controller.pipeline = Pipeline()
        controller._notify = lambda *args: None
        controller.submit_chess({
            "event": "engine_analysis", "notation": "红方 兵五进一",
            "speech_token": "move-1",
            "material": "红方：车2；黑方：车2", "summary": "局面均势",
            "candidates": [{"move": "红方 兵五进一", "assessment": "均势",
                            "line": ["红方 兵五进一"]}], "pv": []})
        controller.submit_chess({
            "event": "opponent_move", "notation": "黑方 卒5进1"})
        spoken = [item for item in calls if item[0] not in ("cancel",)]
        self.assertEqual(spoken, [])

    def test_only_important_opponent_capture_gets_commentary(self):
        submitted = []

        class Pipeline:
            def cancel_idle(self):
                pass

            def submit(self, priority, kind, payload, direct_text=None):
                submitted.append((kind, direct_text))

        controller = object.__new__(LiveController)
        controller.last_engine_score = None
        controller.opponent_move_count = 0
        controller.pipeline = Pipeline()
        controller._notify = lambda *args: None
        controller.submit_chess({
            "event": "opponent_move", "notation": "黑方 马8进7"})
        controller.submit_chess({
            "event": "opponent_move", "notation": "黑方 炮5进4，吃马"})
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0][0], "关键应手解说")
        self.assertIn("马", submitted[0][1])

    def test_plain_move_uses_piece_specific_streamer_wording(self):
        text = LiveController._casual_move_text("红方 马二进三", 2)
        self.assertIn("马二进三", text)
        self.assertNotIn("我方", text)
        self.assertTrue("跳马" in text or "马儿" in text or "这匹马" in text)

    def test_near_win_gets_closing_but_not_premature_victory(self):
        submitted = []

        class Pipeline:
            def cancel_idle(self):
                pass

            def submit(self, priority, kind, payload, direct_text=None):
                submitted.append((kind, direct_text))

        controller = object.__new__(LiveController)
        controller.last_engine_score = None
        controller.pipeline = Pipeline()
        controller._notify = lambda *args: None
        controller.submit_chess({
            "event": "engine_analysis", "number": 8,
            "notation": "红方 车八进七", "material": "红方占优",
            "summary": "我方明显优势",
            "analysis": {"score_type": "cp", "score": 720},
            "candidates": [], "pv": []})
        self.assertEqual(submitted[0][0], "收官解说")
        self.assertIn("车八进七", submitted[0][1])
        self.assertTrue(any(phrase in submitted[0][1] for phrase in (
            "棋没结束", "还差个句号", "别在终点线", "香槟")))

    def test_near_win_line_is_not_repeated_every_move(self):
        submitted = []

        class Pipeline:
            def submit(self, priority, kind, payload, direct_text=None):
                submitted.append((kind, direct_text))

            def prepare_idle(self, payload):
                pass

        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(commentary_density="自然")
        controller.last_engine_score = None
        controller.last_chess_spoken_at = time.monotonic()
        controller.quiet_move_count = 0
        controller.closing_announced = False
        controller.pipeline = Pipeline()
        controller._notify = lambda *args: None
        for number, score in ((8, 720), (9, 740)):
            controller.submit_chess({
                "event": "engine_analysis", "number": number,
                "notation": "红方 车八进一", "summary": "我方明显优势",
                "analysis": {"score_type": "cp", "score": score},
                "candidates": [], "pv": []})
        self.assertEqual(len(submitted), 1)

    def test_confirmed_win_uses_rotating_victory_line(self):
        submitted = []

        class Pipeline:
            def cancel_idle(self):
                pass

            def submit(self, priority, kind, payload, direct_text=None):
                submitted.append((kind, direct_text))

        controller = object.__new__(LiveController)
        controller.victory_count = 1
        controller.pipeline = Pipeline()
        controller._notify = lambda *args: None
        controller.submit_chess({
            "event": "game_over", "result": "won",
            "reason": "no_legal_moves"})
        self.assertEqual(submitted[0][0], "获胜收官")
        self.assertIn("下一位受害者", submitted[0][1])

    def test_surrender_result_gets_cautious_closing_line(self):
        submitted = []

        class Pipeline:
            def cancel_idle(self):
                pass

            def submit(self, priority, kind, payload, direct_text=None):
                submitted.append((kind, direct_text))

        controller = object.__new__(LiveController)
        controller.pipeline = Pipeline()
        controller._notify = lambda *args: None
        controller.submit_chess({
            "event": "game_over", "result": "finished",
            "reason": "jj_result_screen"})
        self.assertEqual(submitted[0][0], "结算收官")
        self.assertIn("看样子", submitted[0][1])

    def test_capture_uses_detailed_commentary(self):
        submitted = []

        class Pipeline:
            def submit(self, priority, kind, payload, direct_text=None):
                submitted.append((kind, direct_text))

        controller = object.__new__(LiveController)
        controller.enabled = False
        controller.last_engine_score = None
        controller.pipeline = Pipeline()
        controller._notify = lambda *args: None
        controller.submit_chess({
            "event": "engine_analysis", "number": 3,
            "notation": "红方 炮五进四，吃马", "summary": "我方稍优",
            "analysis": {"score_type": "cp", "score": 120},
            "candidates": [], "pv": []})
        self.assertEqual(submitted, [("关键走棋解说", None)])

    def test_key_own_move_is_preheated_before_click_confirmation(self):
        prepared = []

        class Pipeline:
            def prepare_move(self, kind, payload, direct_text=None):
                prepared.append((kind, payload, direct_text))

        controller = object.__new__(LiveController)
        controller.last_engine_score = None
        controller.pipeline = Pipeline()
        controller._notify = lambda *args: None
        controller.submit_chess({
            "event": "engine_analysis", "number": 4,
            "notation": "红方 炮五进四，吃车", "summary": "红方占优",
            "analysis": {"score_type": "cp", "score": 420},
            "speech_token": "key-4", "candidates": [], "pv": []})
        self.assertEqual(prepared[0][0], "关键走棋解说")
        self.assertEqual(prepared[0][1]["speech_token"], "key-4")

    def test_sandtable_triggers_on_big_capture_and_reuses_pv(self):
        prepared = []
        notices = []

        class Pipeline:
            def prepare_move(self, kind, payload, direct_text=None):
                prepared.append((kind, payload, direct_text))

        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(sandtable_enabled=True)
        controller.last_engine_score = None
        controller.closing_announced = False
        controller.pipeline = Pipeline()
        controller._notify = lambda kind, text, data=None: notices.append(
            (kind, text, data or {}))
        controller.submit_chess({
            "event": "engine_analysis", "number": 6,
            "notation": "红方 车八进五，吃车", "summary": "红方占优",
            "analysis": {"score_type": "cp", "score": 520, "depth": 18},
            "speech_token": "sand-6", "candidates": [],
            "pv": [
                {"ucci": "b4b9", "notation": "红方 车八进五，吃车"},
                {"ucci": "c7b9", "notation": "黑方 马3退2，吃车"},
                {"ucci": "h2e2", "notation": "红方 炮二平五"},
                {"ucci": "h9g7", "notation": "黑方 马8进7"},
            ]})
        show = [item for item in notices
                if item[0] == "sandtable" and item[2].get("action") == "show"]
        self.assertEqual(len(show), 1)
        self.assertEqual(len(show[0][2]["route"]), 4)
        self.assertEqual(prepared[0][0], "沙盘推演解说")
        controller.sandtable_timer.cancel()

    def test_sandtable_kill_confidence_requires_depth(self):
        base = {"analysis": {"score_type": "cp", "score": 900, "depth": 18}}
        self.assertGreaterEqual(LiveController._kill_confidence(base), 80)
        shallow = {"analysis": {"score_type": "cp", "score": 900, "depth": 6}}
        self.assertLess(LiveController._kill_confidence(shallow), 80)
        mate = {"analysis": {"score_type": "mate", "score": 5, "depth": 10}}
        self.assertEqual(LiveController._kill_confidence(mate), 100)

    def test_sandtable_pauses_on_reply_and_closes_before_next_own_move(self):
        notices = []

        class Pipeline:
            def cancel_idle(self):
                pass

        controller = object.__new__(LiveController)
        controller.pipeline = Pipeline()
        controller.sandtable_active = True
        controller.opponent_move_count = 0
        controller._notify = lambda kind, text, data=None: notices.append(
            (kind, (data or {}).get("action")))
        controller.submit_chess({
            "event": "opponent_move", "notation": "黑方 卒7进1"})
        self.assertIn(("sandtable", "pause"), notices)
        self.assertTrue(controller.sandtable_active)
        controller.submit_chess({
            "event": "move_selected", "notation": "红方 马二进三",
            "speech_token": "next"})
        self.assertIn(("sandtable", "close"), notices)
        self.assertFalse(controller.sandtable_active)

    def test_speech_worker_survives_idle_timeouts(self):
        pipeline = SpeechPipeline(
            LiveSettings(cooldown=0), lambda *args: None,
            llm=lambda *args, **kwargs: "测试", tts=lambda *args: "test.wav")
        time.sleep(0.7)
        self.assertTrue(pipeline.generator.is_alive())
        pipeline.close()

    def test_normalizes_room_id_and_url(self):
        self.assertEqual(normalize_room("123456789"), "123456789")
        self.assertEqual(
            normalize_room("https://live.douyin.com/987654321?foo=bar"),
            "987654321")
        with self.assertRaises(ValueError):
            normalize_room("https://example.com/123456789")

    def test_speech_generation_is_background_and_does_not_block_move(self):
        completed = threading.Event()
        records = []

        def slow_llm(kind, payload, history, model):
            time.sleep(0.15)
            return "测试讲解"

        def fake_tts(text, voice):
            self.fail("文本模式不应调用 TTS")

        def notify(kind, text, data=None):
            records.append((kind, text))
            if kind == "speech_status" and text == "等待下一条内容":
                completed.set()

        with patch("live_host.winsound.PlaySound"):
            pipeline = SpeechPipeline(
                LiveSettings(cooldown=0), notify, llm=slow_llm, tts=fake_tts)
            started = time.monotonic()
            pipeline.submit(1, "象棋解说", {"走法": "炮二平五"})
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.08)
            self.assertTrue(completed.wait(2))
            pipeline.close()
        self.assertIn(("reply", "测试讲解"), records)
        self.assertTrue(any(kind == "queued" for kind, _ in records))
        self.assertIn(("speech_status", "仅文本模式：未调用 TTS"), records)

    def test_speech_switch_enables_tts(self):
        completed = threading.Event()
        tts_calls = []

        def notify(kind, text, data=None):
            if kind == "speech_status" and text == "等待下一条内容":
                completed.set()

        def fake_tts(text, voice):
            tts_calls.append((text, voice))
            return "test.wav"

        with patch("live_host.winsound.PlaySound"):
            pipeline = SpeechPipeline(
                LiveSettings(cooldown=0, speech_enabled=True), notify,
                llm=lambda *args, **kwargs: "语音测试", tts=fake_tts)
            pipeline.submit(1, "弹幕回复", {"弹幕": "测试"})
            self.assertTrue(completed.wait(2))
            pipeline.close()
        self.assertEqual(tts_calls, [("语音测试", "Maia")])

    def test_gift_combo_is_thanked_once_with_highest_count(self):
        submitted = []
        finished = threading.Event()

        class Pipeline:
            def submit(self, priority, kind, payload, direct_text=None):
                submitted.append((priority, kind, payload))
                finished.set()

        controller = object.__new__(LiveController)
        controller.gift_lock = threading.Lock()
        controller.pending_gifts = {}
        controller.gift_timers = {}
        controller.pipeline = Pipeline()
        controller.enabled = True
        controller._queue_gift("小明", "小心心", 1)
        controller._queue_gift("小明", "小心心", 6)
        self.assertTrue(finished.wait(2))
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0][2]["数量"], 6)


if __name__ == "__main__":
    unittest.main()
