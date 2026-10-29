import unittest

from board import Board, START_FEN
from pikafish import parse_info_line
from player import engine_commentary, material_summary


class EngineAnalysisTests(unittest.TestCase):
    def test_parses_public_uci_analysis(self):
        analysis = parse_info_line(
            "info depth 12 seldepth 18 score cp 43 nodes 12345 nps 500000 "
            "time 25 pv b2e2 b9c7")
        self.assertEqual(analysis["depth"], 12)
        self.assertEqual(analysis["score"], 43)
        self.assertEqual(analysis["pv"], ["b2e2", "b9c7"])

    def test_parses_multipv_rank(self):
        analysis = parse_info_line(
            "info depth 10 multipv 3 score cp 12 pv h2e2 h9g7")
        self.assertEqual(analysis["multipv"], 3)

    def test_material_summary_is_human_readable(self):
        text = material_summary(Board.from_fen(START_FEN))
        self.assertIn("红方：车2", text)
        self.assertIn("黑方：车2", text)

    def test_formats_score_and_legal_principal_variation(self):
        board = Board.from_fen(START_FEN)
        summary, pv = engine_commentary(board, {
            "depth": 10,
            "seldepth": 14,
            "score_type": "cp",
            "score": 50,
            "nodes": 1000,
            "time": 100,
            "pv": ["b2e2"],
        })
        self.assertIn("我方稍优", summary)
        self.assertEqual(pv[0][0], "b2e2")
        self.assertIn("炮八平五", pv[0][1])


if __name__ == "__main__":
    unittest.main()
