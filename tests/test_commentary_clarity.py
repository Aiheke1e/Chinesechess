import threading
import unittest
from unittest.mock import Mock, patch

from board import Board
from commentary_text import opponent_capture_note
from live_host import LiveSettings, SpeechPipeline
from mate_patterns import build_finisher
from queen_commentary import move_effects
from sandbox_narration import script
from tests.test_sandbox_presentation import plan_fixture


class CommentaryClarityTests(unittest.TestCase):
    def position(self, piece="R", target="c", screen=False):
        board = Board([None] * 90, "r")
        for square, value in ((85, "K"), (4, "k"), (49, "P"), (81, piece), (75, target), (57, "r")):
            board.sq[square] = value
        if screen: board.sq[74] = "P"
        return board

    def test_rook_looking_at_a_defended_cheaper_piece_is_not_a_new_threat(self):
        board = self.position()
        self.assertEqual(move_effects(board, (81, 72)), [])
        # A cannon attacking a defended rook remains a useful concrete fact.
        self.assertIn("盯住了对面的四路车", "".join(move_effects(
            self.position("C", "r", True), (81, 72))))

    def test_recorded_horse_escape_is_explained_before_its_attack_on_a_cannon(self):
        board = Board.from_fen("3akabnr/9/4b4/p5p1p/2p1n4/4C1P2/P1P1N1ccP/NC7/3R5/2BAKAB2 w")
        original = board.to_fen()
        effects = move_effects(board, Board.from_ucci("e3f1"))
        self.assertIn("避开了对面的攻击", effects[0])
        self.assertIn("七路炮", "".join(effects))
        self.assertEqual(board.to_fen(), original)

    def test_repeated_target_is_not_rebroadcast_with_a_different_rook_move(self):
        notes = []
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=False), lambda *args: notes.append(args), llm=Mock())
        pipeline.history.append({"role": "assistant", "content": "这辆车盯住了对面的五路马。"})
        payload = {"speech_token": "repeat-target", "我方计划走法": "红方 车六进一",
                   "QUEEN式证据": {"本手作用": ["这辆车盯住了对面的五路马。"]}}
        state = {"epoch": pipeline.cancel_epoch, "chess_epoch": pipeline.chess_epoch,
                 "decision": threading.Event(), "approved": True}
        state["decision"].set()
        with patch.object(pipeline, "_write_speech_marker"):
            try:
                pipeline._prepare_move_worker("repeat-target", "我方走棋播报", payload, state,
                                              "车六进一，盯住了对面的五路马。")
                self.assertEqual([args[1] for args in notes if args[0] == "reply"], ["车六进一。"])
                self.assertTrue(pipeline._recently_spoken("这辆车盯住了对面的五路马。"))
            finally: pipeline.close()

    def test_recorded_rook_trade_is_not_reported_as_a_lost_rook(self):
        previous = {"ucci": "b0b9", "fen_before":
            "1r1ak1bnr/4a4/2n1b2c1/p3C1p1p/2p6/6P2/P1P1c3P/N1C6/8R/1RBAKABN1 w"}
        reply = {"ucci": "c7b9", "notation": "黑方 马3退2，吃车", "fen":
            "1n1ak1bnr/4a4/4b2c1/p3C1p1p/2p6/6P2/P1P1c3P/N1C6/8R/2BAKABN1 w"}
        text = opponent_capture_note(reply, previous)
        self.assertIn("兑车", text)
        self.assertNotIn("丢", text)
        # An unrelated board must not borrow that exchange conclusion.
        self.assertNotIn("兑车", opponent_capture_note({**reply, "fen": previous["fen_before"]}, previous))
        self.assertNotIn("重新盘", opponent_capture_note({"notation": "黑方 卒3进1，吃兵"}))

    def test_verified_sandbox_exchange_names_recapture_and_both_costs(self):
        _, _, plan = plan_fixture()
        cues = script(plan)
        self.assertIn("这是回吃", cues["4"])
        self.assertIn("双方各换掉一门炮", cues["summary"])

    def test_sandbox_keeps_check_and_escape_facts_in_the_exact_step(self):
        cues = script({"own": "r", "route": [
            {"side": "r", "notation": "红方 车五进一", "check": True, "escape": True},
            {"side": "b", "notation": "黑方 将5平6", "escape": True}]})
        self.assertIn("车五进一，解将反将", cues["1"])
        self.assertIn("将5平6，先解将", cues["2"])

    def test_recorded_mate_copy_explains_an_actual_king_exit_instead_of_generic_padding(self):
        board = Board.from_fen("4kabr1/2N1a4/3Rb3n/p7p/cC4p2/4C1P2/8P/N8/9/2BAKAB2 b")
        board.sq[11], board.sq[28], board.turn = None, "N", "r"
        original = board.to_fen()
        finisher = build_finisher({"fen_before": original, "ucci": "b6c8", "number": 25})
        self.assertEqual(finisher["name"], "卧槽马")
        self.assertIn("想将5平4，那个落点有咱们的车看着", finisher["text"])
        self.assertIn("马八进七", finisher["text"])
        self.assertNotIn("别光看", finisher["text"])
        self.assertNotIn("先别急着庆祝", finisher["text"])
        self.assertEqual(board.to_fen(), original)


if __name__ == "__main__":
    unittest.main()
