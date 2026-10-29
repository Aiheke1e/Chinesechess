import unittest
from unittest.mock import patch

import mover
from board import Board, START_FEN
from state import after_move


class MoverTests(unittest.TestCase):
    def setUp(self):
        self.before = Board.from_fen(START_FEN).sq
        self.move = Board.from_ucci("h2e2")
        self.after = after_move(self.before, self.move)

    def test_success_sends_exactly_two_taps(self):
        frames = iter((self.before, self.after))
        with patch.object(mover, "screenshot", side_effect=lambda *_: next(frames)), \
             patch.object(mover, "recognize_position",
                          side_effect=lambda frame, orientation: (frame, orientation)), \
             patch.object(mover, "recognize_occupancy",
                          side_effect=lambda frame, orientation: frame), \
             patch.object(mover, "tap") as tap, \
             patch.object(mover.time, "sleep"):
            ok, _ = mover.move_piece(*self.move, "device", expected_before=self.before)
        self.assertTrue(ok)
        self.assertEqual(tap.call_count, 2)

    def test_precondition_failure_sends_no_input(self):
        wrong = self.before[:]
        wrong[0] = None
        with patch.object(mover, "screenshot", return_value=wrong), \
             patch.object(mover, "recognize_position",
                          side_effect=lambda frame, orientation: (frame, orientation)), \
             patch.object(mover, "recognize_occupancy",
                          side_effect=lambda frame, orientation: frame), \
             patch.object(mover, "tap") as tap:
            ok, _ = mover.move_piece(
                *self.move, "device", expected_before=self.before,
                precondition_timeout=0)
        self.assertFalse(ok)
        tap.assert_not_called()

    def test_precondition_waits_for_transient_frame_to_settle(self):
        wrong = self.before[:]
        wrong[0] = None
        frames = iter((wrong, self.before, self.after))
        with patch.object(mover, "screenshot", side_effect=lambda *_: next(frames)), \
             patch.object(mover, "recognize_position",
                          side_effect=lambda frame, orientation: (frame, orientation)), \
             patch.object(mover, "recognize_occupancy",
                          side_effect=lambda frame, orientation: frame), \
             patch.object(mover, "tap") as tap, \
             patch.object(mover.time, "sleep"):
            ok, _ = mover.move_piece(
                *self.move, "device", expected_before=self.before,
                precondition_timeout=1)
        self.assertTrue(ok)
        self.assertEqual(tap.call_count, 2)

    def test_precondition_repairs_glowing_trusted_source_by_side(self):
        strong_probe = {index: self.before[index]
                        for index in mover._probe_indices(
                            self.before, *self.move)}
        strong_probe[self.move[0]] = None
        presence_probe = {index: self.before[index]
                          for index in strong_probe}
        frames = iter((self.before, self.after))
        with patch.object(mover, "screenshot",
                          side_effect=lambda *_: next(frames)), \
             patch.object(mover, "recognize_indices",
                          side_effect=lambda frame, orientation, indices: (
                              strong_probe if frame is self.before else
                              {index: frame[index] for index in indices})), \
             patch.object(mover, "recognize_presence_indices",
                          return_value=presence_probe), \
             patch.object(mover, "recognize_position",
                          side_effect=lambda frame, orientation: (frame, orientation)), \
             patch.object(mover, "recognize_occupancy",
                          side_effect=lambda frame, orientation: frame), \
             patch.object(mover, "tap") as tap, \
             patch.object(mover.time, "sleep"):
            ok, _ = mover.move_piece(
                *self.move, "device", expected_before=self.before)
        self.assertTrue(ok)
        self.assertEqual(tap.call_count, 2)

    def test_confirmation_repairs_glowing_destination_on_first_frame(self):
        before_frame, after_frame = object(), object()
        probe = mover._probe_indices(self.before, *self.move)

        def strong(frame, orientation, indices):
            if frame is before_frame:
                return {index: self.before[index] for index in indices}
            return {self.move[0]: None, self.move[1]: None}

        def presence(frame, orientation, indices):
            if frame is before_frame:
                return {index: self.before[index] for index in indices}
            return {self.move[0]: None,
                    self.move[1]: self.after[self.move[1]]}

        with patch.object(mover, "screenshot",
                          side_effect=[before_frame, after_frame]), \
             patch.object(mover, "recognize_indices", side_effect=strong), \
             patch.object(mover, "recognize_presence_indices",
                          side_effect=presence), \
             patch.object(mover, "tap") as tap, \
             patch.object(mover.time, "sleep"):
            ok, message = mover.move_piece(
                *self.move, "device", expected_before=self.before)
        self.assertTrue(ok)
        self.assertIn("高亮容错局部校验", message)
        self.assertEqual(tap.call_count, 2)

    def test_failed_confirmation_never_retries_input(self):
        with patch.object(mover, "screenshot", return_value=self.before), \
             patch.object(mover, "recognize_position",
                          side_effect=lambda frame, orientation: (frame, orientation)), \
             patch.object(mover, "recognize_occupancy",
                          side_effect=lambda frame, orientation: frame), \
             patch.object(mover, "tap") as tap:
            ok, message = mover.move_piece_retry(
                *self.move, "device", tries=3, expected_before=self.before,
                verify_timeout=0.01, poll_interval=0)
        self.assertFalse(ok)
        self.assertIn("未重试", message)
        self.assertEqual(tap.call_count, 2)

    def test_failed_confirmation_captures_immediate_frame(self):
        with patch.object(mover, "screenshot", return_value=self.before), \
             patch.object(mover, "recognize_position",
                          side_effect=lambda frame, orientation: (frame, orientation)), \
             patch.object(mover, "recognize_occupancy",
                          side_effect=lambda frame, orientation: frame), \
             patch.object(mover, "tap"), \
             patch("diagnostics.capture_diagnostic",
                   return_value="diagnostic.png") as capture:
            ok, message = mover.move_piece(
                *self.move, "device", expected_before=self.before,
                verify_timeout=0.01, poll_interval=0)
        self.assertFalse(ok)
        self.assertIn("diagnostic.png", message)
        capture.assert_called_once()


if __name__ == "__main__":
    unittest.main()
