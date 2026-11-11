import io
from contextlib import redirect_stdout
import unittest
from unittest.mock import Mock, patch

from board import Board
import player
from tests.test_mate_patterns import POSITIONS, predecessor


class SandboxOrderTests(unittest.TestCase):
    def run_mate(self, safe_exit):
        event = predecessor(Board.from_fen(POSITIONS["重炮"]))
        board = Board.from_fen(event["fen_before"])
        original = board.to_fen()
        move = Board.from_ucci(event["ucci"])
        cfg = dict(player.DEFAULT_CFG, my_color="r", dry_run=False, max_moves=1, auto_continue_after_game=False)
        engine = Mock(last_analysis={"score_type": "mate", "score": 1, "pv": [event["ucci"]]}, last_candidates=[])
        order = []
        def demo(device, current, orientation, plan, *args):
            self.assertEqual(current.to_fen(), original)
            self.assertFalse(any(item.get("illustration") for item in plan["route"]))
            order.append("demo")
            return {"entered": True, "safe_exit": safe_exit, "mate_confirmed": safe_exit}
        def speech(*args, **kwargs):
            order.append("speech")
            return True
        def real_move(*args, **kwargs):
            order.append("real_move")
            return True, "局部快速校验"
        with patch.object(player.sys, "argv", ["player.py", "--once"]), patch.object(
                player, "load_cfg", return_value=cfg), patch.object(
                player, "list_devices", return_value=["mock"]), patch.object(
                player, "match_live_session", return_value=None), patch.object(
                player, "read_stable_board", return_value=(board, "red_bottom")), patch.object(
                player, "save_session"), patch.object(player, "write_runtime_status"), patch.object(
                player, "EventLog"), patch("pikafish.get_engine", return_value=engine), patch.object(
                player, "pick_move", return_value=(move, "mock engine")), patch.object(
                player, "run_game_sandbox", side_effect=demo), patch.object(
                player, "wait_for_speech_start", side_effect=speech), patch.object(
                player, "move_piece_retry", side_effect=real_move), redirect_stdout(io.StringIO()):
            player.main()
        return order

    def test_mate_demo_runs_before_the_real_finishing_move(self):
        self.assertEqual(self.run_mate(True), ["demo", "speech", "real_move"])

    def test_unconfirmed_return_from_demo_never_clicks_the_live_board(self):
        self.assertEqual(self.run_mate(False), ["demo"])
