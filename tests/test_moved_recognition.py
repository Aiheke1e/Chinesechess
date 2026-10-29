import os
import unittest

import cv2
import numpy as np

from board import Board, START_FEN
from recognizer import DX, DY, OX, OY, recognize

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def relocate_piece(image, move):
    """Build a location-independence fixture by moving one circular piece."""
    result = image.copy()
    frm, to = move
    rf, cf = divmod(frm, 9)
    rt, ct = divmod(to, 9)
    fx, fy = int(OX + cf * DX), int(OY + rf * DY)
    tx, ty = int(OX + ct * DX), int(OY + rt * DY)
    radius = 47
    yy, xx = np.ogrid[-48:48, -48:48]
    mask = xx * xx + yy * yy <= radius * radius
    source = image[fy-48:fy+48, fx-48:fx+48]
    # The tested moves start on rows with an empty row directly below/above.
    background_y = fy + int(DY) if rf < 5 else fy - int(DY)
    background = image[background_y-48:background_y+48, fx-48:fx+48]
    result[fy-48:fy+48, fx-48:fx+48][mask] = background[mask]
    result[ty-48:ty+48, tx-48:tx+48][mask] = source[mask]
    return result


class MovedRecognitionTests(unittest.TestCase):
    def test_piece_type_survives_new_square_background(self):
        image = cv2.imread(os.path.join(HERE, "jj_live.png"))
        start = Board.from_fen(START_FEN)
        for ucci in ("h9g7", "h7e7", "h0g2", "h2e2"):
            with self.subTest(move=ucci):
                move = Board.from_ucci(ucci)
                observed = recognize(relocate_piece(image, move))
                self.assertEqual(observed[move[1]], start.sq[move[0]])


if __name__ == "__main__":
    unittest.main()
