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

            def prepare_move(self, kind, payload, direct_text=None, **kwargs):
                prepared.append((kind, payload, direct_text, kwargs))

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

    def test_chess_sentence_is_never_cut_mid_thought(self):
        text = SpeechPipeline._short_chess_text(
            "这是一句明显超过二十个汉字而且非常像机器报告的解说。第二句也很长很长。")
        self.assertEqual(
            text,
            "这是一句明显超过二十个汉字而且非常像机器报告的解说。第二句也很长很长。")

    def test_chess_speech_is_rewritten_to_first_person(self):
        text = SpeechPipeline._first_person_text(
            "棋小雅：黑方这车一压，红方马得退。",
            {"主播执棋": "黑方"})
        self.assertEqual(text, "咱们这车一压，对面马得退。")
        for forbidden in ("红方", "黑方", "我方", "对方", "对手"):
            self.assertNotIn(forbidden, text)

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

    def test_prediction_is_occasional_and_uses_wait_channel(self):
        prepared = []

        class Pipeline:
            def prepare_idle(self, payload, kind="等待期长讲解",
                             direct_text=None):
                prepared.append((kind, payload, direct_text))

        controller = object.__new__(LiveController)
        controller.pipeline = Pipeline()
        controller.settings = LiveSettings(commentary_enabled=True)
        controller.last_engine_score = None
        controller.last_chess_spoken_at = time.monotonic()
        controller.quiet_move_count = 0
        controller.next_prediction_move = 4
        controller.prediction_count = 0
        controller.closing_announced = False
        controller._notify = lambda *args, **kwargs: None
        base = {
            "event": "engine_analysis", "notation": "红方 马二进三",
            "summary": "局面均势", "analysis": {
                "score_type": "cp", "score": 20, "depth": 18},
            "candidates": [], "speech_token": "predict",
            "pv": [
                {"ucci": "h0g2", "notation": "红方 马二进三"},
                {"ucci": "h9g7", "notation": "黑方 马8进7"},
                {"ucci": "b0c2", "notation": "红方 马八进七"},
            ],
        }
        controller.submit_chess({**base, "number": 3})
        self.assertEqual(prepared, [])
        controller.submit_chess({**base, "number": 4})
        self.assertEqual(prepared[0][0], "应手预判")
        self.assertEqual(prepared[0][1]["引擎预测应手"], "黑方 马8进7")
        self.assertIn("马8进7", prepared[0][2])
        controller.submit_chess({**base, "number": 5})
        self.assertEqual(len(prepared), 1)

    def test_prediction_text_never_changes_engine_move(self):
        text = LiveController._engine_prediction_text("黑方 炮8平5", 2)
        self.assertIn("炮8平5", text)
        self.assertNotIn("黑方", text)

    def test_engine_prediction_never_calls_language_model(self):
        spoken = threading.Event()
        pipeline = SpeechPipeline(
            LiveSettings(cooldown=0, speech_enabled=False),
            lambda kind, *args: spoken.set() if kind == "reply" else None,
            llm=lambda *args, **kwargs: self.fail("预判不应调用语言模型"),
            tts=lambda *args: self.fail("文本模式不应调用语音"))
        pipeline.prepare_idle(
            {"引擎预测应手": "黑方 马8进7"}, kind="应手预判",
            direct_text="我猜他可能走马8进7。")
        self.assertTrue(pipeline.open_idle_window())
        self.assertTrue(spoken.wait(1))
        pipeline.close()

    def test_opening_names_come_from_verified_move_patterns(self):
        spoken = []

        class Pipeline:
            def submit(self, priority, kind, payload, direct_text=None):
                spoken.append((kind, payload["布局名称"], direct_text))

        controller = object.__new__(LiveController)
        controller.pipeline = Pipeline()
        controller._reset_opening()
        for move in (
                "红方 炮二平五", "黑方 马8进7",
                "红方 马二进三", "黑方 马2进3"):
            controller._record_opening_move(move)
        self.assertEqual(spoken[0][1], "中炮")
        self.assertEqual(spoken[-1][1], "中炮对屏风马")
        self.assertIn("中炮对屏风马", spoken[-1][2])

    def test_two_headed_snake_requires_both_flank_pawns(self):
        moves = ["红方 兵七进一", "黑方 马8进7", "红方 兵三进一"]
        names = [name for name, _ in LiveController._opening_matches(moves)]
        self.assertIn("红方两头蛇", names)
        incomplete = [name for name, _ in LiveController._opening_matches(
            moves[:-1])]
        self.assertNotIn("红方两头蛇", incomplete)

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

    def test_important_opponent_capture_does_not_block_own_move_audio(self):
        submitted = []
        notes = []

        class Pipeline:
            def cancel_idle(self):
                pass

            def submit(self, priority, kind, payload, direct_text=None):
                submitted.append((kind, direct_text))

        controller = object.__new__(LiveController)
        controller.last_engine_score = None
        controller.opponent_move_count = 0
        controller.pipeline = Pipeline()
        controller._notify = lambda kind, text, data=None: notes.append(
            (kind, text, data or {}))
        controller.submit_chess({
            "event": "opponent_move", "notation": "黑方 马8进7"})
        controller.submit_chess({
            "event": "opponent_move", "notation": "黑方 炮5进4，吃马"})
        self.assertEqual(submitted, [])
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0][0], "chess_note")
        self.assertIn("马", notes[0][1])
        self.assertFalse(notes[0][2]["spoken"])

    def test_plain_move_uses_piece_specific_streamer_wording(self):
        text = LiveController._casual_move_text("红方 马二进三", 2)
        self.assertIn("马二进三", text)
        self.assertNotIn("我方", text)
        self.assertTrue("跳马" in text or "马儿" in text or "这匹马" in text)

    def test_large_cp_advantage_does_not_claim_a_forced_win(self):
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
        self.assertEqual(submitted[0][0], "关键走棋解说")
        self.assertIn("车八进七", submitted[0][1])
        self.assertFalse(any(phrase in submitted[0][1] for phrase in (
            "胜利", "收官", "下一位", "香槟")))

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

    def test_forced_mate_closing_line_is_spoken_only_once(self):
        submitted = []

        class Pipeline:
            def submit(self, priority, kind, payload, direct_text=None):
                submitted.append((kind, direct_text))

            def prepare_idle(self, *args, **kwargs):
                pass

        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(commentary_density="自然")
        controller.last_engine_score = None
        controller.last_chess_spoken_at = time.monotonic()
        controller.quiet_move_count = 0
        controller.closing_announced = False
        controller.pipeline = Pipeline()
        controller._notify = lambda *args: None
        for number, move, mate in (
                (8, "红方 车六进二", 3),
                (9, "红方 车六平五", 2),
                (10, "红方 车五进一", 1)):
            controller.submit_chess({
                "event": "engine_analysis", "number": number,
                "notation": move, "summary": "预计很快将胜",
                "analysis": {"score_type": "mate", "score": mate},
                "candidates": [], "pv": []})
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0][0], "收官解说")

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
        self.assertEqual(submitted[0][0], "关键走棋解说")
        self.assertIn("炮五进四", submitted[0][1])
        self.assertIn("吃掉一枚马", submitted[0][1])
        self.assertNotIn("收下这枚", submitted[0][1])

    def test_queen_style_explanation_is_prepared_for_wait_window(self):
        prepared_moves = []
        prepared_idle = []

        class Pipeline:
            def prepare_move(self, kind, payload, direct_text=None):
                prepared_moves.append((kind, payload, direct_text))

            def prepare_idle(self, payload, kind="等待期长讲解",
                             direct_text=None):
                prepared_idle.append((kind, payload, direct_text))

        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(
            commentary_enabled=True, queen_commentary_enabled=True)
        controller.last_engine_score = None
        controller.pipeline = Pipeline()
        controller._notify = lambda *args: None
        controller.submit_chess({
            "event": "engine_analysis", "number": 3,
            "notation": "红方 炮五进四，吃马", "side": "r",
            "summary": "我方稍优", "speech_token": "queen-3",
            "analysis": {"score_type": "cp", "score": 120},
            "candidates": [], "pv": [],
            "queen_evidence": {
                "最终选择": "红方 炮五进四，吃马",
                "选择理由": ["吃掉马"],
                "候选取舍": [{"走法": "马三进五", "取舍": "较慢"}],
                "值得展开": True,
            },
        })
        self.assertEqual(len(prepared_moves), 1)
        self.assertEqual(len(prepared_idle), 1)
        self.assertEqual(prepared_idle[0][0], "QUEEN式等待期解说")
        self.assertIn("QUEEN式证据", prepared_idle[0][1])

    def test_key_own_move_is_preheated_before_click_confirmation(self):
        prepared = []

        class Pipeline:
            def prepare_move(self, kind, payload, direct_text=None, **kwargs):
                prepared.append((kind, payload, direct_text, kwargs))

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
        self.assertEqual(prepared[0][1]["解说对应手数"], 4)
        self.assertEqual(prepared[0][1]["解说对应走法"], "红方 炮五进四，吃车")

    def test_sandtable_triggers_on_big_capture_and_reuses_pv(self):
        prepared = []
        notices = []

        class Pipeline:
            def prepare_move(self, kind, payload, direct_text=None, **kwargs):
                prepared.append((kind, payload, direct_text, kwargs))

        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(sandtable_enabled=True)
        controller.last_engine_score = None
        controller.closing_announced = False
        controller.sandbox_speech_tokens = set()
        controller.sandbox_speech_by_move = {}
        controller.pipeline = Pipeline()
        controller._notify = lambda kind, text, data=None: notices.append(
            (kind, text, data or {}))
        controller.submit_chess({
            "event": "engine_analysis", "number": 6,
            "notation": "黑方 车9平8", "summary": "黑方占优",
            "fen_before": "1rbakab1r/9/2n6/p3p3p/2p1cNp2/1C7/P1P1N3P/4C4/4A2n1/1RBAK1c2 b - - 0 1",
            "ucci": "i9h9", "side": "b",
            "analysis": {"score_type": "cp", "score": 796, "depth": 21},
            "speech_token": "sand-6", "candidates": [],
            "pv": [
                {"ucci": "i9h9", "notation": "黑方 车9平8"},
                {"ucci": "e2e5", "notation": "红方 炮五进三，吃炮"},
                {"ucci": "e6e5", "notation": "黑方 卒5进1，吃炮"},
                {"ucci": "b0b2", "notation": "红方 车八进二"},
            ]})
        show = [item for item in notices
                if item[0] == "sandtable" and item[2].get("action") == "show"]
        # Player owns JJ's real sandbox lifecycle.  The live host only prepares
        # synchronized speech and must not draw a fake mini-board.
        self.assertEqual(show, [])
        self.assertEqual(len(prepared[0][1]["沙盘路线"]), 3)
        self.assertEqual(prepared[0][0], "沙盘推演解说")
        self.assertEqual(prepared[0][1]["speech_token"], "sand-6:sandbox")
        self.assertIn("卒5进1", prepared[0][2])
        self.assertEqual(prepared[1][0], "关键走棋解说")
        self.assertEqual(prepared[1][1]["speech_token"], "sand-6")
        self.assertNotIn("收下这枚。", prepared[1][2])

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

    def test_sandtable_speech_waits_until_real_sandbox_enters(self):
        decisions = []

        class Pipeline:
            def commit_move(self, token):
                decisions.append(("commit", token))

            def discard_move(self, token):
                decisions.append(("discard", token))

        controller = object.__new__(LiveController)
        controller.pipeline = Pipeline()
        controller.settings = LiveSettings(commentary_enabled=True)
        controller.sandbox_speech_tokens = {"sand-1:sandbox"}
        controller.sandbox_speech_by_move = {"sand-1": "sand-1:sandbox"}
        controller._notify = lambda *args, **kwargs: None
        controller.submit_chess({
            "event": "move_applied", "notation": "红方 车八进五",
            "speech_token": "sand-1"})
        self.assertEqual(decisions, [])
        controller.submit_chess({
            "event": "game_sandbox", "action": "entered",
            "speech_token": "sand-1"})
        self.assertEqual(decisions, [("commit", "sand-1:sandbox")])
        self.assertNotIn("sand-1:sandbox", controller.sandbox_speech_tokens)

    def test_quiet_move_is_never_described_as_empty_capture(self):
        text = LiveController._synced_move_text(
            "红方 车九平二", ["改善棋子位置"], number=5)
        self.assertIn("车九平二", text)
        self.assertIn("平车换线", text)
        self.assertNotIn("直接收下这枚", text)

    def test_quiet_move_uses_engine_grounded_geometry(self):
        self.assertEqual(
            LiveController._synced_move_text(
                "红方 马七退五", ["争夺中路", "马向中路靠拢"], 6),
            "回马靠中，走马七退五。")
        self.assertEqual(
            LiveController._synced_move_text(
                "红方 马二进三", ["马向中路靠拢"], 2),
            "跳马靠中，走马二进三。")
        self.assertEqual(
            LiveController._synced_move_text(
                "红方 炮二退一", ["炮回撤调整"], 7),
            "炮先回一格，走炮二退一。")

    def test_capture_names_the_exact_captured_piece_once(self):
        text = LiveController._synced_move_text(
            "红方 兵三进一，吃卒", ["吃子"], number=7)
        self.assertEqual(text.count("卒"), 1)
        self.assertIn("吃掉一枚卒", text)
        self.assertNotIn("吃到东西", text)

    def test_idle_move_text_has_no_canned_opponent_tail(self):
        text = LiveController._idle_move_text(
            "红方 炮八平五", {"选择理由": ["争夺中路"]}, 6)
        self.assertIn("中路", text)
        self.assertNotIn("怎么应对", text)
        self.assertNotIn("有好戏", text)

    def test_idle_rook_horizontal_move_never_claims_it_left_back_rank(self):
        text = LiveController._idle_move_text(
            "红方 车九平八",
            {"选择理由": ["调动后排大子", "车横向换线"]}, 5)
        self.assertIn("平车", text)
        self.assertNotIn("接出底线", text)

    def test_local_move_words_preserve_direction_piece_and_distance(self):
        self.assertIn("回马", LiveController._synced_move_text(
            "红方 马五退六", ["马回撤调整"], 8))
        elephant = LiveController._synced_move_text(
            "红方 相七进五", [], 16)
        self.assertNotIn("飞象", elephant)
        self.assertNotIn("补一手象", elephant)
        cannon = LiveController._synced_move_text(
            "黑方 炮2进2", ["炮向前推进"], 14)
        self.assertNotIn("一格", cannon)

    def test_repeated_pawn_pushes_rotate_factual_wording(self):
        lines = {
            LiveController._synced_move_text(
                "红方 兵三进一", ["兵向前推进"], number)
            for number in (13, 17, 23)
        }
        self.assertGreaterEqual(len(lines), 2)

    def test_advantage_band_only_triggers_when_crossed(self):
        controller = object.__new__(LiveController)
        controller.last_engine_score = 250
        first, _ = controller._commentary_importance({
            "number": 8, "notation": "红方 车八平七",
            "analysis": {"score_type": "cp", "score": 330}})
        second, _ = controller._commentary_importance({
            "number": 9, "notation": "红方 车七平八",
            "analysis": {"score_type": "cp", "score": 350}})
        self.assertGreaterEqual(first, 5)
        self.assertLess(second, 5)

    def test_pawn_capture_is_worth_a_factual_call_at_natural_density(self):
        controller = object.__new__(LiveController)
        controller.last_engine_score = 0
        importance, reasons = controller._commentary_importance({
            "number": 6, "notation": "红方 兵五进一，吃卒",
            "analysis": {"score_type": "cp", "score": 10}})
        self.assertEqual(importance, 5)
        self.assertIn("吃子", reasons)

    def test_chess_speech_rejects_empty_capture_and_generic_tail(self):
        payload = {"我方计划走法": "红方 车二进八"}
        self.assertFalse(SpeechPipeline._chess_line_valid(
            "直接收下这枚。", "关键走棋解说", payload))
        self.assertFalse(SpeechPipeline._chess_line_valid(
            "接下来看对面怎么应对，挺有意思。", "等待期长讲解", payload))
        self.assertTrue(SpeechPipeline._chess_line_valid(
            "车二进八，把车送到前沿。", "关键走棋解说", payload))

    def test_chess_speech_rejects_wrong_piece_action(self):
        payload = {"我方计划走法": "红方 相五进三，吃卒"}
        self.assertFalse(SpeechPipeline._chess_line_valid(
            "相五进三，吃卒，跳马抢位。", "关键走棋解说", payload))
        self.assertTrue(SpeechPipeline._chess_line_valid(
            "飞相吃卒，相五进三。", "关键走棋解说", payload))

    def test_prediction_followup_uses_actual_reply(self):
        text = LiveController._prediction_followup({
            "预判走法": "黑方 马8进7", "实际应手": "黑方 车9平8",
            "预判兑现": False})
        self.assertIn("车9平8", text)
        self.assertIn("马8进7", text)

    def test_normal_speech_commits_before_adb_move(self):
        decisions = []

        class Pipeline:
            def commit_move(self, token):
                decisions.append(("commit", token))

        controller = object.__new__(LiveController)
        controller.pipeline = Pipeline()
        controller.settings = LiveSettings(commentary_enabled=True)
        controller.sandbox_speech_tokens = set()
        controller.submit_chess({
            "event": "move_command", "notation": "红方 马二进三",
            "speech_token": "normal-1"})
        self.assertEqual(decisions, [("commit", "normal-1")])

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

    def test_normalizes_douyin_share_text_to_web_rid(self):
        from unittest.mock import MagicMock, patch
        response = MagicMock()
        response.url = "https://webcast.amemv.com/douyin/webcast/reflow/7702045080483400482"
        response.read.return_value = b'owner:{"webRid\\":\\"856344539553\\"}'
        response.__enter__.return_value = response
        with patch("live_host.urlopen", return_value=response):
            room = normalize_room(
                "正在直播，来支持Ta！ https://v.douyin.com/zOaht4OpSbs/ 0@3.com")
        self.assertEqual(room, "856344539553")

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
