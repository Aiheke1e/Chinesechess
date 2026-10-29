import unittest

from board import Board, START_FEN
from mover import pt
from recognizer import canonicalize, infer_orientation


class OrientationTests(unittest.TestCase):
    def setUp(self):
        self.canonical = Board.from_fen(START_FEN).sq

    def test_infers_both_orientations(self):
        self.assertEqual(infer_orientation(self.canonical), "red_bottom")
        self.assertEqual(infer_orientation(list(reversed(self.canonical))), "black_bottom")

    def test_canonicalizes_black_bottom(self):
        screen = list(reversed(self.canonical))
        self.assertEqual(canonicalize(screen, "black_bottom"), self.canonical)

    def test_click_coordinates_rotate_180_degrees(self):
        for index in (0, 7, 19, 70, 89):
            self.assertEqual(pt(index, "black_bottom"), pt(89 - index, "red_bottom"))


if __name__ == "__main__":
    unittest.main()
