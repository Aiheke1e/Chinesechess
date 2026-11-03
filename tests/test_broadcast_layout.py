import unittest

from broadcast_layout import popup_position


class BroadcastLayoutTests(unittest.TestCase):
    def test_initial_placement_leaves_the_whole_game_and_avatar_row_uncovered(self):
        screen = (0, 40, 1920, 1040)
        for game in ((0, 0, 560, 1040), (1360, 0, 1920, 1040), (600, 300, 1160, 1000), (600, 40, 1160, 600)):
            with self.subTest(game=game):
                x, y = popup_position((560, 220), screen, game)
                self.assertGreaterEqual(x, screen[0])
                self.assertGreaterEqual(y, screen[1])
                self.assertLessEqual(x + 560, screen[2])
                self.assertLessEqual(y + 220, screen[3])
                self.assertFalse(x < game[2] and x + 560 > game[0] and y < game[3] and y + 220 > game[1])
