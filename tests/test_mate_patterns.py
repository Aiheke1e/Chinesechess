import threading
import time
import unittest
from unittest.mock import Mock, patch

from board import Board, START_FEN
from live_host import LiveController, LiveSettings, SpeechPipeline
from mate_patterns import PATTERNS, build_finisher, confirmed_finisher_note, identify_pattern
import live_host
import player


# Terminal examples are deliberately small: each named formation can be checked
# independently of an engine's mate score or any platform's animation label.
POSITIONS = {
    "重炮": "3aka3/9/4C4/9/4C4/9/9/9/9/4K4 b",
    "马后炮": "4k4/9/4N4/9/4C4/9/9/9/9/4K4 b",
    "列马车": "3Rk4/9/2N6/9/9/4P4/9/9/9/4K4 b",
    "钓鱼马": "1R2k4/9/2N6/9/9/4P4/9/9/9/4K4 b",
    "铁门栓": "3akR2R/4a4/4b4/9/4C4/9/9/9/9/4K4 b",
    "天地炮": "2Cak4/R8/9/9/4C4/9/9/9/9/4K4 b",
    "闷宫": "C2ak4/4a4/9/9/9/9/9/9/9/4K4 b",
    "闷杀": "3aka3/4n4/5N3/9/9/9/9/9/9/4K4 b",
    "卧槽马": "3ak4/2N1a4/9/9/9/9/9/9/9/5K3 b",
    "侧面虎": "3k5/3R5/9/2N6/9/9/9/9/9/4K4 b",
    "八角马": "3N5/5R3/5k3/9/9/9/9/9/9/4K4 b",
    "对面笑": "3k5/9/3R5/9/9/9/9/9/9/4K4 b",
    "海底捞月": "C1P1R4/4k4/9/4N4/9/9/9/9/9/4K4 b",
    "双将": "3R5/3Rk4/9/3NP4/9/9/9/9/9/4K4 b",
    "夹车炮": "4nC3/3k1R3/4nC3/9/9/9/9/9/9/4K4 b",
    "困毙": "4k4/3R1R3/9/9/9/4P4/9/9/9/4K4 b",
    "组合将杀": "4k4/3RPR3/9/9/9/9/9/9/9/4K4 b",
}


def rotate(board):
    return Board([p.swapcase() if p else None for p in reversed(board.sq)],
                 "r" if board.turn == "b" else "b")


def predecessor(final):
    """Find a legal predecessor, including a possible final capture of a rook."""
    for target, piece in enumerate(final.sq):
        if not piece or not piece.isupper() or piece == "K":
            continue
        for source, occupied in enumerate(final.sq):
            if occupied:
                continue
            for captured in (None, "r"):
                before = final.copy()
                before.turn = "r"
                before.sq[target], before.sq[source] = captured, piece
                move = (source, target)
                if not before.in_check("b") and move in before.legal_moves():
                    return {"event": "engine_analysis", "fen_before": before.to_fen(),
                            "ucci": Board.ucci(move), "number": 21, "side": "r",
                            "notation": "由棋盘生成", "speech_token": "finish-21"}
    raise AssertionError("No legal predecessor for " + final.to_fen())


class MatePatternTests(unittest.TestCase):
    def test_named_terminal_shapes_work_for_both_colors(self):
        for name, fen in POSITIONS.items():
            final = Board.from_fen(fen)
            with self.subTest(name=name):
                self.assertFalse(final.legal_moves())
                self.assertEqual(identify_pattern(final), name)
                self.assertEqual(identify_pattern(rotate(final)), name)

    def test_long_copy_requires_actual_legal_finishing_move_and_keeps_exact_notation(self):
        for name, fen in POSITIONS.items():
            with self.subTest(name=name):
                final = Board.from_fen(fen)
                event = predecessor(final)
                plan = build_finisher(event)
                self.assertEqual(plan["name"], name)
                self.assertEqual(plan["terminal_fen"], final.to_fen())
                self.assertIn(plan["notation"].split(" ", 1)[-1].replace("，吃", "吃"), plan["text"])
                self.assertTrue(90 <= len(plan["text"]) <= 220)
                self.assertTrue(SpeechPipeline._chess_line_valid(plan["text"], "绝杀长解说", {}))
                self.assertIn(name, plan["text"])
                self.assertTrue(plan["text"].startswith(f"这手准备{name}。"))
                before = rotate(Board.from_fen(event["fen_before"]))
                a, b = Board.from_ucci(event["ucci"])
                mirrored = build_finisher({**event, "fen_before": before.to_fen(),
                                          "ucci": Board.ucci((89 - a, 89 - b)), "side": "b"})
                self.assertEqual(mirrored["name"], name)
                self.assertTrue(mirrored["notation"].startswith("黑方 "))

    def test_mate_score_alone_check_or_illegal_move_cannot_trigger_celebration(self):
        ordinary = {"fen_before": START_FEN, "ucci": "a0a1",
                    "analysis": {"score_type": "mate", "score": 1}}
        self.assertIsNone(build_finisher(ordinary))
        self.assertIsNone(build_finisher({**ordinary, "ucci": "a0a9"}))
        actual = predecessor(Board.from_fen(POSITIONS["马后炮"]))
        self.assertIsNone(build_finisher({**actual, "side": "b"}))
        checking = Board.from_fen("3k5/9/9/9/9/R8/9/9/9/5K3 w")
        self.assertIsNone(build_finisher({"fen_before": checking.to_fen(), "ucci": "a4d4"}))
        self.assertIsNone(identify_pattern(Board.from_fen(START_FEN)))
        missing = Board.from_fen(POSITIONS["重炮"])
        missing.sq[4] = None
        self.assertIsNone(identify_pattern(missing))

    def test_two_rooks_require_replayed_alternating_checks(self):
        before = Board.from_fen("9/4k4/R1R6/9/9/4P4/9/9/9/4K4 w")
        previous = {"number": 20, "fen_before": before.to_fen(), "ucci": Board.ucci((18, 9))}
        before.apply((18, 9))
        self.assertIn((13, 4), before.legal_moves())
        before.apply((13, 4))
        current = {"number": 21, "fen_before": before.to_fen(), "ucci": Board.ucci((20, 2))}
        reply = {"ucci": Board.ucci((13, 4))}
        self.assertNotEqual(build_finisher(current)["name"], "双车错")
        plan = build_finisher(current, previous, reply)
        self.assertEqual(plan["name"], "双车错")
        self.assertNotEqual(build_finisher(current, previous, {"ucci": "bad"})["name"], "双车错")
        self.assertEqual(set(PATTERNS), (set(POSITIONS) - {"组合将杀"}) | {"双车错"})

    def test_controller_prepares_one_long_clip_without_sandbox_or_prediction(self):
        controller = object.__new__(LiveController)
        controller.pipeline = Mock()
        controller.settings = LiveSettings()
        controller.settings.sandtable_enabled = True
        controller._sandtable_plan = Mock()
        controller.reply_context = {"实际应手": "黑方 将5平4"}
        event = predecessor(Board.from_fen(POSITIONS["马后炮"]))
        controller.submit_chess(event)
        args = controller.pipeline.prepare_move.call_args
        self.assertEqual(args.args[0], "绝杀长解说")
        self.assertTrue(args.args[1]["绝杀长播报"])
        self.assertEqual(args.args[1]["speech_token"], "finish-21")
        self.assertEqual(args.args[1]["解说对应手数"], 21)
        self.assertIn("马后炮", args.kwargs["direct_text"])
        controller._sandtable_plan.assert_not_called()
        controller.pipeline.prepare_idle.assert_not_called()
        controller.pipeline.submit.assert_not_called()
        self.assertIsNone(controller.reply_context)
        stored = controller.commentary_analyses["finish-21"]
        final_event = {"result": "won", "fen": stored["finisher"]["terminal_fen"]}
        self.assertIn("马后炮成了", confirmed_finisher_note(final_event, stored))
        self.assertNotIn("落子确认", confirmed_finisher_note(final_event, stored))
        self.assertEqual(confirmed_finisher_note({**final_event, "fen": START_FEN}, stored), "")
        self.assertEqual(confirmed_finisher_note({**final_event, "result": "finished"}, stored), "")


class FinisherSpeechTests(unittest.TestCase):
    def test_player_waits_for_finish_instead_of_start_and_ignores_old_completion(self):
        with patch.object(player, "_speech_enabled", return_value=True), patch.object(
                player, "_read_speech_marker", side_effect=[
                    {"token": "finish", "enabled": True, "wait_finished": True},
                    {"token": "finish", "status": "started"},
                    {"token": "old", "status": "finished"},
                    {"token": "finish", "status": "finished"}]) as read, patch.object(player.time, "sleep"):
            self.assertTrue(player.wait_for_speech_start("finish"))
            self.assertEqual(read.call_count, 4)

    def test_finish_wait_can_be_stopped_and_never_times_out_into_a_click(self):
        markers = [{"token": "finish", "enabled": True, "wait_finished": True},
                   {"token": "finish", "status": "started"}]
        with patch.object(player, "_speech_enabled", return_value=True), patch.object(
                player, "_read_speech_marker", side_effect=markers), patch.object(
                player.time, "sleep"):
            with self.assertRaisesRegex(player.SpeechSyncError, "完整播完"):
                player.wait_for_speech_start("finish", finish_timeout=0)
        with patch.object(player, "_speech_enabled", return_value=True), patch.object(
                player, "_read_speech_marker", side_effect=markers), patch.object(
                player.time, "sleep"), patch.object(player, "write_runtime_status"):
            self.assertFalse(player.wait_for_speech_start("finish", cancelled=Mock(side_effect=[False, False, True])))

    def test_full_text_is_spoken_and_finish_ack_follows_audio(self):
        event = predecessor(Board.from_fen(POSITIONS["重炮"]))
        text = build_finisher(event)["text"]
        payload = {"speech_token": "finish", "绝杀长播报": True, "主播执棋": "红方"}
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True), Mock(), tts=Mock(return_value="mate.wav"))
        state = {"decision": threading.Event(), "approved": True, "epoch": 0, "chess_epoch": 0}
        state["decision"].set()
        order = []
        with patch.object(live_host.winsound, "PlaySound", side_effect=lambda *a: order.append("play")), patch.object(
                pipeline, "_audio_duration", return_value=0.04), patch.object(
                pipeline, "_mark_speech_started", side_effect=lambda *a: order.append("start")), patch.object(
                pipeline, "_mark_speech_finished", side_effect=lambda *a: order.append("finish")):
            start = time.monotonic()
            pipeline._prepare_move_worker("finish", "绝杀长解说", payload, state, text)
            self.assertGreaterEqual(time.monotonic() - start, 0.04)
            self.assertEqual(order, ["play", "start", "finish"])
            self.assertEqual(pipeline.tts.call_args.args[0], text)
            pipeline.close()

    def test_interrupted_finisher_has_no_finish_acknowledgement(self):
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True), Mock())
        state = {"approved": True, "epoch": 0, "chess_epoch": 0}
        def interrupt(*args):
            state["approved"] = False
        with patch.object(live_host.winsound, "PlaySound", side_effect=interrupt), patch.object(
                pipeline, "_audio_duration", return_value=0.1), patch.object(
                pipeline, "_mark_speech_finished") as finished, patch.object(pipeline, "_mark_speech_started"):
            self.assertFalse(pipeline._play_synced_audio("mate.wav", {
                "speech_token": "finish", "绝杀长播报": True}, state, "长文案", "绝杀长解说"))
            finished.assert_not_called()
            pipeline.close()


if __name__ == "__main__":
    unittest.main()
