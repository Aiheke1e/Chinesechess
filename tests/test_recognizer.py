import os
import unittest

import cv2

from board import Board, START_FEN
from recognizer import recognize, recognize_occupancy

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RecognizerRegressionTests(unittest.TestCase):
    def test_saved_start_frames(self):
        expected = Board.from_fen(START_FEN).sq
        for name in ("jj_live.png", "jj_before.png", "jj_board.png", "jj_new.png"):
            with self.subTest(name=name):
                image = cv2.imread(os.path.join(HERE, name))
                self.assertIsNotNone(image)
                self.assertEqual(recognize(image), expected)

    def test_occupancy_preserves_side_without_claiming_piece_type(self):
        image = cv2.imread(os.path.join(HERE, "jj_live.png"))
        expected = Board.from_fen(START_FEN).sq
        occupancy = recognize_occupancy(image, "red_bottom")
        expected_occupancy = [
            None if piece is None else ("R" if piece.isupper() else "r")
            for piece in expected]
        self.assertEqual(occupancy, expected_occupancy)


if __name__ == "__main__":
    unittest.main()
