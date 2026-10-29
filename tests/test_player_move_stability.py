import unittest

import player


class StableOpponentMoveTests(unittest.TestCase):
    def test_requires_same_move_twice(self):
        passing_square = (63, 65)
        final_square = (63, 67)

        accepted, pending = player.stable_move_candidate(None, passing_square)
        self.assertIsNone(accepted)
        self.assertEqual(pending, passing_square)

        accepted, pending = player.stable_move_candidate(pending, final_square)
        self.assertIsNone(accepted)
        self.assertEqual(pending, final_square)

        accepted, pending = player.stable_move_candidate(pending, final_square)
        self.assertEqual(accepted, final_square)
        self.assertIsNone(pending)

    def test_unrecognized_frame_breaks_confirmation(self):
        move = (63, 67)
        _, pending = player.stable_move_candidate(None, move)
        accepted, pending = player.stable_move_candidate(pending, None)
        self.assertIsNone(accepted)
        self.assertIsNone(pending)


if __name__ == "__main__":
    unittest.main()
