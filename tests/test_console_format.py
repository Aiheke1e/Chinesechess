import unittest

from console_format import compact_player_line, styled_player_line


class ConsoleFormatTests(unittest.TestCase):
    def test_hides_engine_technical_noise(self):
        self.assertIsNone(compact_player_line(
            "技术信息：Pikafish h2e2，坐标 h2e2\n"))
        self.assertIsNone(compact_player_line(
            "[候选思路] 方案1：炮八平五\n"))

    def test_compacts_move_and_confirmation(self):
        self.assertEqual(
            compact_player_line("第 3 回合｜我方走棋：红方 马八进七\n"),
            "\n第3手 · 红方 马八进七\n")
        self.assertEqual(
            compact_player_line("落子确认：成功，屏幕棋盘与预期一致。\n"),
            "✓ 落子已确认\n")
        self.assertEqual(
            compact_player_line("落子确认：成功（快速确认 0.9 秒）。\n"),
            "✓ 落子已确认 · 快速确认 0.9 秒\n")

    def test_compacts_opponent_without_coordinates(self):
        self.assertEqual(
            compact_player_line(
                "对方回应：黑方 马2进3  （1.2 秒，坐标 b9c7）\n"),
            "对方 · 黑方 马2进3 · 1.2秒\n")

    def test_styles_each_turn_with_separator_and_side_colour(self):
        parts = styled_player_line(
            "第 12 回合｜我方走棋：红方 车六平五\n")
        self.assertEqual(parts[0][1], "separator")
        self.assertIn("┈", parts[0][0])
        self.assertEqual(parts[1], ("第 12 手  ", "move_number"))
        self.assertEqual(parts[2][1], "move_red")

        reply = styled_player_line(
            "对方回应：黑方 马2进3  （1.2 秒，坐标 b9c7）\n")
        self.assertEqual(reply[0][1], "reply_black")

        black_move = styled_player_line(
            "第 13 回合｜我方走棋：黑方 炮8平5\n")
        self.assertEqual(black_move[2][1], "move_black")

        red_reply = styled_player_line(
            "对方回应：红方 兵七进一  （0.8 秒，坐标 c3c4）\n")
        self.assertEqual(red_reply[0][1], "reply_red")


if __name__ == "__main__":
    unittest.main()
