from pathlib import Path
import io
from contextlib import ExitStack, redirect_stdout
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from board import Board
from game_result import mating_animation_confirms_move, mating_move_after
import mover
import player
from recognizer import OX, OY, DX, DY


ROOT = Path(__file__).resolve().parent.parent
FEN = "3a1k3/2N1ar3/n1r6/p3C1P2/9/2P2c3/P3P3p/9/9/1RBAKABR1 w - - 0 1"


class RecordedMateAnimationTests(unittest.TestCase):
    def setUp(self):
        self.board = Board.from_fen(FEN)
        self.move = Board.from_ucci("h0h9")
        self.frame = cv2.imread(str(ROOT / "fixture/jj_live_mate_animation.png"))

    def test_recorded_live_animation_proves_the_legal_mating_move_at_supported_scales(self):
        terminal = mating_move_after(self.board.sq, self.move)
        self.assertIsNotNone(terminal)
        self.assertTrue(terminal.in_check(terminal.turn))
        self.assertFalse(terminal.legal_moves())
        for size in ((1080, 1920), (720, 1280), (540, 960)):
            with self.subTest(size=size):
                self.assertTrue(mating_animation_confirms_move(cv2.resize(self.frame, size), self.board.sq, self.move, "red_bottom"))
        self.assertEqual(self.board.to_fen(), FEN)

    def test_banner_alone_an_ordinary_move_and_wrong_orientation_never_prove_a_win(self):
        blank = np.zeros_like(self.frame)
        blank[760:1270, 180:950] = self.frame[760:1270, 180:950]
        self.assertFalse(mating_animation_confirms_move(blank, self.board.sq, self.move, "red_bottom"))
        self.assertFalse(mating_animation_confirms_move(self.frame, self.board.sq, self.move, "black_bottom"))
        self.assertFalse(mating_animation_confirms_move(self.frame, self.board.sq, Board.from_ucci("h0h8"), "red_bottom"))
        self.assertIsNone(mating_move_after(self.board.sq, Board.from_ucci("h0a9")))
        terminal = mating_move_after(self.board.sq, self.move)
        self.assertIsNone(mating_move_after(terminal.sq, Board.from_ucci("h9f9")))
        self.assertFalse(mating_animation_confirms_move(None, self.board.sq, self.move, "red_bottom"))

    def test_missing_moved_piece_cannot_be_inferred_from_its_banner(self):
        image = self.frame.copy()
        x, y = round(OX + 7 * DX), round(OY)
        # Erase only the destination piece, leaving the actual banner intact.
        image[y-44:y+44, x-44:x+44] = 0
        self.assertFalse(mating_animation_confirms_move(image, self.board.sq, self.move, "red_bottom"))

    def test_an_ordinary_board_or_generic_gold_rectangle_is_not_the_mate_banner(self):
        ordinary = cv2.imread(str(ROOT / "fixture/jj_sandbox_reply_before_open.png"))
        self.assertFalse(mating_animation_confirms_move(ordinary, self.board.sq, self.move, "red_bottom"))
        image = self.frame.copy()
        image[760:1270, 180:950] = (40, 180, 220)
        self.assertFalse(mating_animation_confirms_move(image, self.board.sq, self.move, "red_bottom"))

    def test_mover_confirms_two_live_animation_frames_without_resubmitting_any_input(self):
        before = object()
        def local(frame, orientation, indices):
            return {i: self.board.sq[i] for i in indices} if frame is before else {}
        with patch.object(mover, "screenshot", side_effect=[before, self.frame, self.frame]) as read, \
             patch.object(mover, "recognize_indices", side_effect=local), \
             patch.object(mover, "recognize_presence_indices", return_value={}), \
             patch.object(mover, "repetition_warning", return_value=False), \
             patch.object(mover.time, "sleep"), patch.object(mover, "tap") as tap, \
             patch("diagnostics.capture_diagnostic") as diagnostic:
            ok, message = mover.move_piece(*self.move, "mock", expected_before=self.board.sq)
        self.assertTrue(ok)
        self.assertIn("连续两帧绝杀动画与棋子核验", message)
        self.assertEqual(read.call_count, 3)
        self.assertEqual(tap.call_count, 2)
        diagnostic.assert_not_called()

    def test_player_records_the_real_22nd_move_and_win_without_waiting_for_settlement(self):
        before = object()
        session = {"board": self.board.copy(), "fen": FEN, "orientation": "red_bottom",
                   "my_color": "r", "own_moves": 21, "rejected_moves": {}}
        events, engine = Mock(), Mock(last_analysis={}, last_candidates=[])
        cfg = dict(player.DEFAULT_CFG, my_color="r", max_moves=1, dry_run=False, auto_continue_after_game=False)
        with ExitStack() as stack:
            for item in (
                patch.object(player.sys, "argv", ["player.py", "--once"]),
                patch.object(player, "load_cfg", return_value=cfg),
                patch.object(player, "list_devices", return_value=["mock"]),
                patch.object(player, "match_live_session", return_value=session),
                patch.object(player, "EventLog", return_value=events),
                patch.object(player, "save_session"), patch.object(player, "write_runtime_status"),
                patch.object(player, "pick_move", return_value=(self.move, "mock engine")),
                patch.object(player, "wait_for_speech_start", return_value=False),
                patch("pikafish.get_engine", return_value=engine),
                patch.object(mover, "screenshot", side_effect=[before, self.frame, self.frame]),
                patch.object(mover, "recognize_indices", side_effect=lambda frame, orientation, indices:
                             {i: self.board.sq[i] for i in indices} if frame is before else {}),
                patch.object(mover, "recognize_presence_indices", return_value={}),
                patch.object(mover, "repetition_warning", return_value=False),
                patch.object(mover.time, "sleep"), patch.object(mover, "tap"),
            ):
                stack.enter_context(item)
            wait = stack.enter_context(patch.object(player, "wait_for_postgame_screen"))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(player.main(), 0)
        records = [(c.args[0], c.kwargs) for c in events.emit.call_args_list]
        end = next(data for kind, data in records if kind == "end")
        self.assertEqual((end["result"], end["own_moves"]), ("won", 22))
        self.assertEqual(next(data for kind, data in records if kind == "move_applied")["ucci"], "h0h9")
        self.assertNotIn("move_unconfirmed", [kind for kind, _ in records])
        wait.assert_not_called()
