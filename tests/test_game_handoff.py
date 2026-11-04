import queue
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from board import Board, START_FEN
from control_panel import ControlPanel, DEFAULT_ASSISTANT_CFG
from session_state import match_live_session


class GameHandoffTests(unittest.TestCase):
    def panel(self):
        panel = object.__new__(ControlPanel)
        panel.task_cancel = threading.Event()
        panel.waiting_for_board = True
        panel.closing = False
        panel.output = queue.Queue()
        panel._assistant_cfg = Mock(return_value=dict(DEFAULT_ASSISTANT_CFG))
        panel._save_diagnostic = Mock()
        return panel

    def test_new_board_uses_two_frames_and_short_interruptible_polling(self):
        start = Board.from_fen(START_FEN)
        after = start.copy()
        after.apply(Board.from_ucci("h2e2"))
        for boards in ((start, start), (start, after, after)):
            with self.subTest(changed=len(boards) == 3):
                panel = self.panel()
                images = [np.full((100, 100, 3), number, np.uint8)
                          for number in range(len(boards))]
                with patch("control_panel.list_devices", return_value=["mock"]), \
                     patch("control_panel.current_package", return_value=("cn.jj.chess", "Main")), \
                     patch("control_panel.screenshot", side_effect=images) as shots, \
                     patch("control_panel.match_live_session", return_value=None) as resume, \
                     patch("control_panel.recognize_position", side_effect=[
                         (board.sq, "red_bottom") for board in boards]), \
                     patch("control_panel.blocked_position_matches", return_value=False), \
                     patch("postgame.has_actionable_postgame", return_value=False), \
                     patch.object(panel.task_cancel, "wait", return_value=False) as wait:
                    panel._prepare_worker("auto", panel.task_cancel)
                self.assertEqual(shots.call_count, len(boards))
                self.assertTrue(all(call.args[0] <= .2 for call in wait.call_args_list))
                for call, image in zip(resume.call_args_list, images):
                    self.assertIs(call.kwargs["first_image"], image)
                records = [item[2] for item in panel.output.queue]
                starts = [item[1] for item in records if item[0] == "autostart"]
                self.assertEqual(len(starts), 1)
                self.assertEqual(starts[0]["turn"], boards[-1].turn)
                self.assertFalse(starts[0]["resume"])

    def test_result_modal_is_processed_before_trying_to_resume_its_background_board(self):
        panel = self.panel()
        modal, board_image = (np.zeros((100, 100, 3), np.uint8) for _ in range(2))
        board = Board.from_fen(START_FEN)
        with patch("control_panel.list_devices", return_value=["mock"]), \
             patch("control_panel.current_package", return_value=("cn.jj.chess", "Main")), \
             patch("control_panel.screenshot", side_effect=[modal, modal, board_image, board_image]), \
             patch("control_panel.match_live_session", return_value=None) as resume, \
             patch("control_panel.recognize_position", return_value=(board.sq, "red_bottom")), \
             patch("control_panel.blocked_position_matches", return_value=False), \
             patch("postgame.has_actionable_postgame", side_effect=[True, True, False, False]), \
             patch("postgame.handle_postgame", return_value=["结算确认"]) as handler, \
             patch.object(panel.task_cancel, "wait", return_value=False):
            panel._prepare_worker("auto", panel.task_cancel)
        handler.assert_called_once_with("mock")
        self.assertEqual(resume.call_count, 2)
        self.assertTrue(all(call.kwargs["first_image"] is board_image for call in resume.call_args_list))

    def session(self, board):
        return {"board": board, "orientation": "red_bottom", "my_color": "r"}

    def occupancy(self, board):
        return [None if piece is None else ("R" if piece.isupper() else "r")
                for piece in board.sq]

    def test_resume_reuses_first_image_but_still_confirms_an_opponent_move(self):
        board = Board.from_fen(START_FEN)
        after = board.copy()
        move = Board.from_ucci("h2e2")
        after.apply(move)
        first, second = object(), object()
        with patch("session_state.load_session", return_value=self.session(board)), \
             patch("session_state.screenshot", return_value=second) as shots, \
             patch("session_state.recognize_occupancy", return_value=self.occupancy(after)), \
             patch("session_state.time.sleep"):
            resumed = match_live_session("mock", first_image=first)
        shots.assert_called_once_with("mock")
        self.assertEqual(resumed["inferred_moves"], [move])
        self.assertEqual(resumed["board"].sq, after.sq)

    def test_unrelated_frame_does_not_capture_or_wait_for_a_second_frame(self):
        board = Board.from_fen(START_FEN)
        with patch("session_state.load_session", return_value=self.session(board)), \
             patch("session_state.screenshot") as shots, \
             patch("session_state.recognize_occupancy", return_value=[None] * 90), \
             patch("session_state.recognize_presence_occupancy", return_value=[None] * 90), \
             patch("session_state.resolve_legal_sequence_by_occupancy") as search, \
             patch("session_state.time.sleep") as sleep:
            self.assertIsNone(match_live_session("mock", first_image=object()))
        shots.assert_not_called()
        search.assert_not_called()
        sleep.assert_not_called()

    def test_resume_rejects_changed_confirmation_without_advancing_trusted_board(self):
        board = Board.from_fen(START_FEN)
        original = board.to_fen()
        after = board.copy()
        after.apply(Board.from_ucci("h2e2"))
        with patch("session_state.load_session", return_value=self.session(board)), \
             patch("session_state.screenshot", return_value=object()), \
             patch("session_state.recognize_occupancy", side_effect=[
                 self.occupancy(after), self.occupancy(board)]), \
             patch("session_state.time.sleep"):
            self.assertIsNone(match_live_session("mock", first_image=object()))
        self.assertEqual(board.to_fen(), original)

    def test_two_fast_moves_and_stable_highlight_fallback_remain_recoverable(self):
        for fallback in (False, True):
            with self.subTest(presence=fallback):
                board = Board.from_fen(START_FEN)
                after = board.copy()
                moves = [Board.from_ucci("h2e2"), Board.from_ucci("b7e7")]
                for move in moves:
                    after.apply(move)
                observed = self.occupancy(after)
                with patch("session_state.load_session", return_value=self.session(board)), \
                     patch("session_state.screenshot", return_value=object()), \
                     patch("session_state.recognize_occupancy", return_value=(
                         [None] * 90 if fallback else observed)), \
                     patch("session_state.recognize_presence_occupancy", return_value=observed), \
                     patch("session_state.time.sleep"):
                    resumed = match_live_session("mock", first_image=object())
                self.assertEqual(resumed["inferred_moves"], moves)
                self.assertEqual(resumed["board"].sq, after.sq)


if __name__ == "__main__":
    unittest.main()
