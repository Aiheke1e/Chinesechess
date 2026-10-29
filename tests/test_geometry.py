import os
import unittest

import cv2

from geometry import detect_standard_geometry

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GeometryTests(unittest.TestCase):
    def test_detects_saved_standard_frames(self):
        for name in ("jj_live.png", "jj_before.png", "jj_board.png", "jj_new.png"):
            with self.subTest(name=name):
                geometry = detect_standard_geometry(cv2.imread(os.path.join(HERE, name)))
                self.assertIsNotNone(geometry)
                self.assertAlmostEqual(geometry["ox"], 67.3, delta=5)
                self.assertAlmostEqual(geometry["dx"], 118.0, delta=2)
                self.assertAlmostEqual(geometry["oy"], 435.6, delta=6)
                self.assertAlmostEqual(geometry["dy"], 113.9, delta=2)


if __name__ == "__main__":
    unittest.main()
