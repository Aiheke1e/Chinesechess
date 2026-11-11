import unittest
import itertools
from unittest.mock import Mock, patch

from board import Board, START_FEN
import game_sandbox
from notation import move_description
from live_host import LiveController, LiveSettings
from tests.test_mate_patterns import POSITIONS, predecessor, rotate


class SandboxFinishTests(unittest.TestCase):
    def plan(self, final=None):
        event = predecessor(final or Board.from_fen(POSITIONS["重炮"]))
        board = Board.from_fen(event["fen_before"])
        ucci = event["ucci"]
        notation = move_description(board, Board.from_ucci(ucci))
        return board, game_sandbox.build_plan(notation, {"score_type": "mate", "score": 1},
                                              [(ucci, notation)], ucci, board)

    def test_mate_demos_for_both_colors_end_with_an_illustrative_king_capture(self):
        for name, fen in POSITIONS.items():
            if name == "困毙":
                continue
            for final in (Board.from_fen(fen), rotate(Board.from_fen(fen))):
                with self.subTest(pattern=name, side=final.turn):
                    # The shared predecessor fixture searches red moves.
                    if final.turn == "r":
                        red_event = predecessor(rotate(final))
                        board = rotate(Board.from_fen(red_event["fen_before"]))
                        frm, to = Board.from_ucci(red_event["ucci"])
                        ucci = Board.ucci((89 - frm, 89 - to))
                        notation = move_description(board, Board.from_ucci(ucci))
                        plan = game_sandbox.build_plan(notation, {}, [(ucci, notation)], ucci, board)
                    else:
                        board, plan = self.plan(final)
                    original = board.to_fen()
                    self.assertEqual(plan["kind"], "mate")
                    self.assertEqual(plan["phase"], "before_move")
                    self.assertEqual(plan["route"][-1]["illustration"], "king_capture")
                    loser = "b" if board.turn == "r" else "r"
                    probe = board.copy()
                    steps = game_sandbox.validated_route(board, plan["route"], mate=True)
                    self.assertEqual(len(steps), len(plan["route"]))
                    for item, move in steps:
                        probe.apply(move)
                    self.assertEqual(probe.king_pos(loser), -1)
                    self.assertEqual(board.to_fen(), original)

    def test_unfinished_or_forged_mate_routes_never_reach_device_input(self):
        board = Board.from_fen(START_FEN)
        move = board.legal_moves()[0]
        route = [{"ucci": Board.ucci(move), "notation": "伪造收尾", "illustration": "king_capture"}]
        with patch.object(game_sandbox, "tap") as tap, patch.object(game_sandbox, "screenshot") as screen:
            result = game_sandbox.play_plan("mock", board, "red_bottom", {"kind": "mate", "route": route})
            self.assertFalse(result["entered"])
            tap.assert_not_called()
            screen.assert_not_called()

    def exercise(self, exit_after_mate=False, capture_confirmed=True, custom=None):
        board, plan = custom or self.plan()
        original = board.to_fen()
        frame = object()
        with patch.object(game_sandbox, "_wait_for_position", return_value=frame), patch.object(
                game_sandbox, "screenshot", return_value=frame), patch.object(
                game_sandbox, "_region_difference", return_value=5), patch.object(
                game_sandbox, "_sandbox_button", return_value=(100, 1600)), patch.object(
                game_sandbox, "_detect_sandbox_exit", side_effect=[(200, 1850), (200, 1850), None] if exit_after_mate else None,
                return_value=(200, 1850)), patch.object(
                game_sandbox, "_position_matches", return_value=True), patch.object(
                game_sandbox, "_endpoint_confirmed", side_effect=itertools.chain(
                    [True] * (len(plan["route"]) - 1), itertools.repeat(capture_confirmed))), patch.object(
                game_sandbox, "_return_to_live", return_value=True), patch.object(
                game_sandbox, "tap") as taps, patch.object(game_sandbox.time, "sleep"):
            result = game_sandbox.play_plan("mock", board, "red_bottom", plan, review_seconds=0)
        self.assertEqual(board.to_fen(), original)
        return result, taps.call_count

    def test_success_is_reported_only_after_the_king_capture_is_confirmed(self):
        result, clicks = self.exercise()
        self.assertTrue(result["king_captured"])
        self.assertEqual(result["completed"], 2)
        self.assertEqual(clicks, 5)
        result, clicks = self.exercise(capture_confirmed=False)
        self.assertFalse(result["king_captured"])
        self.assertIn("未完成吃将收尾", result["reason"])
        self.assertEqual(clicks, 5)  # No retry after a potentially terminal animation.

    def test_platform_leaving_sandbox_at_mate_cannot_cause_a_live_board_click(self):
        result, clicks = self.exercise(exit_after_mate=True)
        self.assertFalse(result["king_captured"])
        self.assertEqual(clicks, 3)  # Open + the legal mating move, no capture tap.

    def test_long_mate_route_is_not_cut_at_three_plies(self):
        board = Board.from_fen("3aka3/9/C8/9/4C4/9/9/9/9/4K4 w - - 0 1")
        probe, line = board.copy(), []
        for ucci in ("a7a6", "e9e8", "a6a7", "e8e9", "a7e7"):
            move = Board.from_ucci(ucci)
            self.assertIn(move, probe.legal_moves())
            line.append((ucci, move_description(probe, move)))
            probe.apply(move)
        plan = game_sandbox.build_plan(line[0][1], {}, line, line[0][0], board)
        self.assertEqual(len(plan["route"]), 6)
        self.assertEqual(plan["route"][-1]["illustration"], "king_capture")
        result, clicks = self.exercise(custom=(board, plan))
        self.assertTrue(result["king_captured"])
        self.assertEqual(result["completed"], 6)
        self.assertEqual(clicks, 13)

    def test_pre_move_demo_narration_precedes_the_ordinary_real_move_call(self):
        board = Board.from_fen("3aka3/9/C8/9/4C4/9/9/9/9/4K4 w - - 0 1")
        probe, line = board.copy(), []
        for ucci in ("a7a6", "e9e8", "a6a7", "e8e9", "a7e7"):
            move = Board.from_ucci(ucci)
            line.append((ucci, move_description(probe, move)))
            probe.apply(move)
        plan = game_sandbox.build_plan(line[0][1], {}, line, line[0][0], board)
        controller = object.__new__(LiveController)
        controller.pipeline, controller._notify = Mock(), Mock()
        controller.settings = LiveSettings(sandtable_enabled=True, speech_enabled=True)
        controller.sandbox_speech_tokens, controller.sandbox_speech_by_move = set(), {}
        controller.submit_chess({"event": "engine_analysis", "speech_token": "demo", "side": "r",
            "fen_before": board.to_fen(), "ucci": line[0][0], "notation": line[0][1],
            "number": 8, "analysis": {}, "sandbox_plan": plan})
        narration = controller.pipeline.prepare_move.call_args_list[0].kwargs["direct_text"]
        self.assertIn("重炮", narration)
        self.assertNotIn("吃将收尾", narration)  # Entry cannot promise an unconfirmed terminal action.
        controller.submit_chess({"event": "move_command", "speech_token": "demo", "notation": line[0][1]})
        controller.pipeline.commit_move.assert_not_called()
        controller.submit_chess({"event": "game_sandbox", "action": "entered", "speech_token": "demo"})
        controller.pipeline.commit_move.assert_not_called()
        controller.submit_chess({"event": "game_sandbox", "action": "cue", "cue": "intro",
                                 "speech_token": "demo", "deadline": 100})
        controller.pipeline.commit_move.assert_called_once_with("demo:sandbox:intro", deadline=100)
        controller.submit_chess({"event": "game_sandbox", "action": "end", "speech_token": "demo",
            "phase": "before_move", "entered": True, "safe_exit": True, "speech_deadline": 200})
        self.assertEqual(controller.pipeline.commit_move.call_args.args, ("demo",))
        self.assertEqual(controller.pipeline.commit_move.call_args.kwargs, {"deadline": 200})
