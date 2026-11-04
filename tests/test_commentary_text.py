import unittest
from unittest.mock import Mock

from board import Board
from commentary_text import complete_note, connected_move_note, waiting_note, confirmed_win_note
from live_host import LiveController, SpeechPipeline, LiveSettings


class CommentaryTextTests(unittest.TestCase):
    def test_followup_explains_next_capture_without_repeating_current_capture(self):
        text = waiting_note({"选择理由": ["吃掉卒", "炮横向换线"],
                             "最可能应手": "黑方 车1平6",
                             "主线参考": ["黑方 车1平6", "红方 炮九平五，吃马"]}, allow_reply=True)
        self.assertIn("如果对面车1平6", text)
        self.assertIn("炮九平五吃马", text)
        self.assertNotIn("吃卒", text)
        self.assertLessEqual(len(text), 56)
        self.assertTrue(SpeechPipeline._chess_line_valid(text, "QUEEN式等待期解说",
                        {"我方计划走法": "红方 炮七平九，吃卒"}))

    def test_engine_alternatives_are_never_introduced_to_spectators(self):
        text = waiting_note({"候选取舍": [{"走法": "红方 车一平二",
                     "取舍": "与首选接近，属于另一种计划"}]})
        self.assertEqual(text, "")

    def test_own_move_effects_take_precedence_over_reply_predictions(self):
        effect = "这辆车盯住了对面的三路马。"
        evidence = {"本手作用": [effect], "最可能应手": "黑方 车1平6"}
        self.assertEqual(waiting_note(evidence), effect)
        self.assertEqual(LiveController._idle_move_text("红方 车九进一", evidence), effect)
        self.assertEqual(waiting_note(evidence, used=[effect]), "")
        text = LiveController._synced_move_text("红方 车九进一", effects=[effect])
        self.assertIn("盯住了对面的三路马", text)
        self.assertNotIn("这辆车", text)
        self.assertNotIn("对手可能", text)

    def test_missing_new_fact_means_no_filler(self):
        self.assertEqual(waiting_note({"选择理由": ["吃掉卒", "向前推进"]}), "")
        self.assertEqual(waiting_note({"最可能应手": "黑方 车1平6",
                         "主线参考": ["黑方 将5平4", "红方 炮九平五，吃马"]}), "")

    def test_complete_sentence_budget_never_cuts_a_word(self):
        self.assertEqual(complete_note("先看这一手。" + "很长" * 40 + "。"), "先看这一手。")
        self.assertEqual(complete_note("很长" * 40 + "。"), "")
        self.assertEqual(complete_note("第一句。第二句。第三句。"), "第一句。第二句。")

    def position(self, rook, pawn=None, cannon=None):
        squares = [None] * 90
        squares[85], squares[4], squares[49] = "K", "k", "P"
        squares[rook] = "R"
        if pawn is not None:
            squares[pawn] = "p"
        if cannon is not None:
            squares[cannon] = "c"
        return Board(squares, "r")

    def events(self, board, first, response, move):
        previous = {"number": 11, "fen_before": board.to_fen(),
                    "ucci": Board.ucci(first)}
        board.apply(first)
        board.apply(response)
        current = {"number": 12, "fen_before": board.to_fen(), "ucci": Board.ucci(move)}
        return current, previous, {"ucci": Board.ucci(response)}

    def test_two_confirmed_captures_by_same_piece_can_be_linked(self):
        current, previous, reply = self.events(self.position(45, 36, 27),
                                               (45, 36), (4, 3), (36, 27))
        self.assertEqual(connected_move_note(current, previous, reply), "车九进一，再吃一门炮。")
        current["number"] = 14
        self.assertEqual(connected_move_note(current, previous, reply), "")

    def test_changed_board_or_different_reply_cannot_claim_continuity(self):
        current, previous, reply = self.events(self.position(45, 36, 27),
                                               (45, 36), (4, 3), (36, 27))
        self.assertEqual(connected_move_note(current, previous, {"ucci": "e9f9"}), "")
        current["fen_before"] = previous["fen_before"]
        self.assertEqual(connected_move_note(current, previous, reply), "")

    def test_two_actual_checks_can_be_linked(self):
        current, previous, reply = self.events(self.position(41),
                                               (41, 40), (4, 3), (40, 39))
        self.assertEqual(connected_move_note(current, previous, reply), "车五平六，继续将军。")

    def test_only_applied_move_is_saved_as_confirmed_commentary_context(self):
        controller = object.__new__(LiveController)
        controller.pipeline = Mock()
        controller._record_opening_move = Mock()
        analysis = {"number": 11, "ucci": "a4a5"}
        controller.commentary_analyses = {"move-11": analysis}
        controller.submit_chess({"event": "move_applied", "speech_token": "move-11",
                                 "notation": "红方 车九进一"})
        self.assertEqual(controller.last_confirmed_analysis, analysis)
        controller.submit_chess({"event": "move_rejected", "speech_token": "move-12",
                                 "notation": "红方 车九进一"})
        self.assertIsNone(controller.last_confirmed_analysis)

    def test_vague_praise_and_circular_explanations_are_rejected(self):
        for text in ("吃卒主要是为了吃掉卒。", "车横过来更灵活。", "感觉炮七平九更稳妥。"):
            self.assertFalse(SpeechPipeline._chess_line_valid(text, "QUEEN式等待期解说", {}))

    def test_prediction_uses_one_specific_reply_without_betting_language(self):
        for count in range(1, 8):
            text = LiveController._engine_prediction_text("黑方 炮7平8", count)
            self.assertIn("炮7平8", text)
            self.assertIn("对手", text)
            self.assertEqual(SpeechPipeline._first_person_text(text, {"主播执棋": "黑方"}), text)
            for word in ("猜", "押", "八成", "蒙"):
                self.assertNotIn(word, text)
            self.assertTrue(SpeechPipeline._chess_line_valid(text, "应手预判", {}))

    def test_capture_prediction_names_our_piece_without_reversed_sentence(self):
        text = LiveController._engine_prediction_text("红方 车三进六，吃马", 2)
        self.assertEqual(text, "对手可能走车三进六，吃掉咱们一匹马。")
        self.assertNotIn("接下来留意", text)

    def test_matching_reply_connects_the_line_instead_of_celebrating_a_guess(self):
        for move in ("红方 车二进四", "红方 仕五退六", "红方 炮七平八"):
            text = LiveController._prediction_followup({"预判兑现": True, "实际应手": move})
            self.assertIn(move.split(" ", 1)[1], text)
            self.assertLessEqual(len(text), 56)
            for word in ("猜", "押", "蒙"):
                self.assertNotIn(word, text)

    def test_changed_reply_mentions_actual_move_and_varies_light_humor(self):
        lines = set()
        for move in ("红方 车八进八", "红方 车一平二", "红方 马八进七", "红方 炮七平八"):
            text = LiveController._prediction_followup({"预判兑现": False,
                "实际应手": move, "预判走法": "黑方 卒5进1"})
            self.assertIn(move.split(" ", 1)[1], text)
            self.assertNotIn("卒5进1", text)
            self.assertLessEqual(len(text), 56)
            self.assertTrue(SpeechPipeline._chess_line_valid(text, "QUEEN式等待期解说", {}))
            lines.add(text.split("，")[-1])
        self.assertGreater(len(lines), 1)

    def test_unexpected_capture_keeps_the_focus_on_the_board(self):
        text = LiveController._prediction_followup({"预判兑现": False,
                                                   "实际应手": "黑方 车2进9，吃车"})
        self.assertIn("吃车", text)
        self.assertNotIn("高手", text)
        self.assertNotIn("剧本", text)

    def test_pipeline_rejects_alternative_and_betting_templates(self):
        for text in ("也可以考虑车9进1，两种走法差距不大。",
                     "还真走了车二进四，刚才这手猜中了。",
                     "这回我押炮七平八，八成会往这边走。"):
            self.assertFalse(SpeechPipeline._chess_line_valid(text, "等待期长讲解", {}))

    def test_tactical_move_consumes_old_reply_review_without_playing_it(self):
        for notation, facts in (
                ("黑方 车8平6，吃仕", ["吃掉仕", "形成将军"]),
                ("黑方 炮9进3", ["形成将军"]),
                ("黑方 士6进5", ["先解除将军"])):
            with self.subTest(notation=notation):
                controller = object.__new__(LiveController)
                controller.settings = LiveSettings(sandtable_enabled=False)
                controller.pipeline = Mock()
                controller._notify = Mock()
                controller.last_engine_score = None
                controller.reply_context = {"实际应手": "红方 车三退一", "预判兑现": False}
                controller.submit_chess({"event": "engine_analysis", "number": 27,
                    "notation": notation, "analysis": {"score_type": "cp", "score": 57},
                    "queen_evidence": {"选择理由": facts, "值得展开": True}})
                controller.pipeline.prepare_idle.assert_not_called()
                controller.pipeline.submit.assert_called_once()
                self.assertIsNone(controller.reply_context)
                payload = controller.pipeline.submit.call_args.args[2]
                self.assertNotIn("上一手预判复盘", payload)
                text = controller.pipeline.submit.call_args.kwargs["direct_text"]
                self.assertNotIn("车三退一", text)
                self.assertNotIn("高手", text)

    def test_quiet_reply_review_is_attached_to_opponent_move_instead_of_current_own_move(self):
        data = LiveController._reply_review_payload({"主播执棋": "黑方", "speech_token": "move-27",
            "解说对应手数": 27, "解说对应走法": "黑方 车8平6，吃仕"},
            {"实际应手": "红方 车三退一", "预判兑现": False})
        self.assertEqual(data["解说对应走法"], "红方 车三退一")
        self.assertIsNone(data["解说对应手数"])
        self.assertEqual(data["speech_token"], "move-27:reply-review")

    def test_recapture_note_names_the_piece_and_only_calls_it_recapture_with_evidence(self):
        evidence = {"最可能应手": "红方 车八平三，吃卒"}
        self.assertEqual(waiting_note(evidence), "")
        ordinary = waiting_note(evidence, allow_reply=True)
        self.assertIn("咱们一枚卒", ordinary)
        self.assertNotIn("吃回", ordinary)
        recapture = waiting_note({**evidence, "应手回吃本子": True}, allow_reply=True)
        self.assertIn("吃回这枚卒", recapture)
        self.assertNotIn("这枚子", recapture)

    def test_capture_calls_vary_but_keep_the_exact_target_and_check(self):
        calls = {LiveController._synced_move_text("黑方 车8进2，吃马", [], n) for n in range(1, 5)}
        self.assertGreater(len(calls), 1)
        for text in calls:
            self.assertEqual(text.count("马"), 1)
            self.assertIn("车8进2", text)
        self.assertIn("吃仕带将", LiveController._synced_move_text(
            "黑方 车8平6，吃仕", ["形成将军"], 27))

    def test_closing_call_stays_with_current_check_without_program_wording(self):
        text = LiveController._closing_move_text("黑方 车6退1", 30, True, ["形成将军"])
        self.assertIn("将军", text)
        self.assertIn("收官", text)
        self.assertNotIn("核准", text)
        self.assertNotIn("拿下", text)

    def test_specific_victory_call_requires_confirmed_mate_and_matching_board(self):
        before = Board.from_fen("4k4/3R1R3/4n4/4R4/9/9/9/9/9/4K4 w")
        move = (31, 22)
        last = {"fen_before": before.to_fen(), "ucci": Board.ucci(move)}
        final = before.copy()
        final.apply(move)
        event = {"result": "won", "reason": "no_legal_moves", "fen": final.to_fen()}
        self.assertEqual(confirmed_win_note(event, last), "这手吃马将住了，这盘拿下！")
        self.assertEqual(confirmed_win_note({**event, "result": "finished"}, last), "")
        self.assertEqual(confirmed_win_note({**event, "fen": before.to_fen()}, last), "")


if __name__ == "__main__":
    unittest.main()
