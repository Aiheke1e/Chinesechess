import unittest

from board import Board, START_FEN, RED, BLACK
from state import (after_move, matches_after_move, position_errors,
                   matches_move_with_limited_vision_loss,
                   occupancy_difference_count,
                   resolve_fast_reply, resolve_one_legal_move,
                   temporal_occupancy_consensus, trusted_position_matches,
                   validate_board, verify_trusted_observation)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.start = Board.from_fen(START_FEN)

    def test_start_is_valid(self):
        self.assertEqual(position_errors(self.start.sq), [])
        self.assertEqual(validate_board(self.start), [])

    def test_temporal_consensus_filters_one_noisy_frame(self):
        base = [None] * 90
        base[10] = "R"
        noisy = base[:]
        noisy[10] = None
        self.assertEqual(
            temporal_occupancy_consensus([base, noisy, base])[10], "R")

    def test_temporal_consensus_accepts_stable_new_state(self):
        old = [None] * 90
        old[10] = "R"
        new = [None] * 90
        new[11] = "R"
        result = temporal_occupancy_consensus([old, new, new])
        self.assertIsNone(result[10])
        self.assertEqual(result[11], "R")

    def test_large_visual_deviation_requests_new_frame(self):
        observed = self.start.sq[:]
        observed[0] = None
        observed[1] = None
        observed[2] = None
        status, sequence, differences = verify_trusted_observation(
            self.start, observed, max_differences=2)
        self.assertEqual((status, sequence, differences), ("retry", None, 3))

    def test_small_change_still_requires_unique_legal_move(self):
        move = Board.from_ucci("h2e2")
        observed = after_move(self.start.sq, move)
        status, sequence, differences = verify_trusted_observation(
            self.start, observed, max_differences=2)
        self.assertEqual(status, "recovered")
        self.assertEqual(sequence, [move])
        self.assertEqual(differences, 2)

    def test_impossible_two_square_change_is_rejected_by_rules(self):
        observed = self.start.sq[:]
        observed[54], observed[55] = None, observed[54]
        status, sequence, differences = verify_trusted_observation(
            self.start, observed, max_differences=2)
        self.assertEqual(status, "illegal")
        self.assertIsNone(sequence)
        self.assertLessEqual(differences, 2)

    def test_rejects_recognizer_piece_overcount(self):
        bad = self.start.sq[:]
        bad[22] = "p"  # sixth black pawn, seen in the failing game.log
        bad[19] = None
        self.assertTrue(position_errors(bad))

    def test_rejects_pawn_sideways_before_crossing_river(self):
        bad = self.start.sq[:]
        bad[3 * 9 + 6] = None
        bad[4 * 9 + 7] = "p"
        self.assertTrue(any("black pawn moved sideways" in error
                            for error in position_errors(bad)))

    def test_resolves_unique_quiet_move(self):
        move = Board.from_ucci("h2e2")
        observed = after_move(self.start.sq, move)
        self.assertEqual(resolve_one_legal_move(self.start, observed), move)

    def test_resolves_highlighted_piece_misclassification(self):
        move = Board.from_ucci("h9g7")
        board = Board.from_fen(START_FEN)
        board.turn = BLACK
        observed = after_move(board.sq, move)
        observed[move[1]] = "c"  # selected/highlighted knight looked like a cannon
        self.assertTrue(matches_after_move(board.sq, move, observed))
        self.assertEqual(resolve_one_legal_move(board, observed), move)

    def test_transition_rejects_unrelated_recognition_error(self):
        move = Board.from_ucci("h2e2")
        observed = after_move(self.start.sq, move)
        observed[0] = "n"
        self.assertFalse(matches_after_move(self.start.sq, move, observed))
        self.assertIsNone(resolve_one_legal_move(self.start, observed))

    def test_sent_move_can_tolerate_one_unrelated_missing_piece(self):
        move = Board.from_ucci("h2e2")
        observed = after_move(self.start.sq, move)
        observed[0] = None
        observed[move[1]] = "N"  # moved cannon is highlighted as another red piece
        self.assertTrue(matches_move_with_limited_vision_loss(
            self.start.sq, move, observed, max_collateral=1))

    def test_sent_move_fallback_rejects_two_unrelated_losses(self):
        move = Board.from_ucci("h2e2")
        observed = after_move(self.start.sq, move)
        observed[0] = None
        observed[1] = None
        self.assertFalse(matches_move_with_limited_vision_loss(
            self.start.sq, move, observed, max_collateral=1))

    def test_tracked_moved_piece_allows_same_color_glyph_variant(self):
        first = Board.from_ucci("h2e2")
        board = Board.from_fen(START_FEN)
        board.apply(first)
        second = Board.from_ucci("b7e7")
        observed = after_move(board.sq, second)
        observed[first[1]] = "N"  # trusted red cannon rendered/classified as another red type
        self.assertFalse(matches_after_move(board.sq, second, observed))
        self.assertTrue(matches_after_move(board.sq, second, observed, {first[1]}))
        self.assertEqual(resolve_one_legal_move(board, observed, {first[1]}), second)

    def test_trusted_position_never_tolerates_wrong_color_or_occupancy(self):
        expected = self.start.sq[:]
        wrong_color = expected[:]
        wrong_color[0] = "R"
        missing = expected[:]
        missing[0] = None
        self.assertFalse(trusted_position_matches(expected, wrong_color, {0}))
        self.assertFalse(trusted_position_matches(expected, missing, {0}))

    def test_resolves_opponent_reply_that_arrived_before_confirmation(self):
        board = Board.from_fen(START_FEN)
        ours = Board.from_ucci("h2e2")
        after_ours = board.copy()
        after_ours.apply(ours)
        reply = Board.from_ucci("b7e7")
        observed = after_move(after_ours.sq, reply)
        observed[ours[1]] = "N"  # our moved cannon uses a variant glyph
        self.assertEqual(resolve_fast_reply(board, ours, observed), reply)

    def test_resolves_unique_capture(self):
        board = Board.from_fen("4k4/9/9/9/4r4/4R4/9/9/9/4K4 w - - 0 1")
        move = Board.from_ucci("e4e5")
        self.assertIn(move, board.legal_moves())
        self.assertEqual(resolve_one_legal_move(board, after_move(board.sq, move)), move)

    def test_rejects_noise_and_unchanged_frame(self):
        self.assertIsNone(resolve_one_legal_move(self.start, self.start.sq[:]))
        noisy = self.start.sq[:]
        noisy[0] = None
        noisy[1] = None
        self.assertIsNone(resolve_one_legal_move(self.start, noisy))

    def test_move_flips_turn(self):
        move = Board.from_ucci("h2e2")
        self.start.apply(move)
        self.assertEqual(self.start.turn, BLACK)


if __name__ == "__main__":
    unittest.main()
