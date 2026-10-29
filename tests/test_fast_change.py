import unittest
from unittest.mock import patch

from board import Board, START_FEN
import player


class FastChangeTests(unittest.TestCase):
    def setUp(self):
        self.board = Board.from_fen(START_FEN)
        self.move = Board.from_ucci("h2e2")

    def test_unique_changed_endpoints_resolve_legal_move(self):
        scores = [0.0] * 90
        scores[self.move[0]] = 35.0
        scores[self.move[1]] = 31.0
        piece = self.board.sq[self.move[0]]
        endpoints = {self.move[0]: None, self.move[1]: piece}
        with patch.object(player, "board_change_scores", return_value=scores), \
             patch.object(player, "recognize_indices", return_value=endpoints):
            move, changed = player.resolve_move_from_change(
                self.board, object(), object(), "red_bottom")
        self.assertEqual(move, self.move)
        self.assertEqual(changed, 2)

    def test_highlight_without_piece_transfer_is_rejected(self):
        scores = [0.0] * 90
        scores[self.move[0]] = 35.0
        scores[self.move[1]] = 31.0
        endpoints = {
            self.move[0]: self.board.sq[self.move[0]],
            self.move[1]: None,
        }
        with patch.object(player, "board_change_scores", return_value=scores), \
             patch.object(player, "recognize_indices", return_value=endpoints):
            move, _ = player.resolve_move_from_change(
                self.board, object(), object(), "red_bottom")
        self.assertIsNone(move)

    def test_global_animation_uses_full_recognition_fallback(self):
        scores = [20.0] * 20 + [0.0] * 70
        with patch.object(player, "board_change_scores", return_value=scores):
            move, changed = player.resolve_move_from_change(
                self.board, object(), object(), "red_bottom")
        self.assertIsNone(move)
        self.assertGreater(changed, 12)

    def test_presence_fallback_resolves_glowing_opponent_move(self):
        board = Board.from_fen(START_FEN)
        move = Board.from_ucci("h2e2")
        after = board.copy()
        after.apply(move)
        presence = [None if piece is None else
                    ("R" if piece.isupper() else "r")
                    for piece in after.sq]
        presence[9] = "r"  # one stable decorative/hint false positive
        broken_strong = [None] * 90
        with patch.object(player, "recognize_occupancy",
                          return_value=broken_strong), \
             patch.object(player, "recognize_presence_occupancy",
                          return_value=presence):
            resolved, status, _ = player.assess_image_against_board(
                board, object(), "red_bottom")
        self.assertEqual(resolved, move)
        self.assertEqual(status, "presence_fallback")


if __name__ == "__main__":
    unittest.main()
