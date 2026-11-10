from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from board import Board, START_FEN
from live_host import LiveController, LiveSettings, SpeechPipeline
from opponent_status import countdown_side
import opponent_nudge as nudge


ROOT = Path(__file__).resolve().parent.parent


class NudgeTurnGuardTests(unittest.TestCase):
    def setUp(self):
        self.opponent = cv2.resize(cv2.imread(str(ROOT / "fixture/jj_opponent_countdown.png")), (1080, 1920))
        own_faces = cv2.imread(str(ROOT / "fixture/jj_own_countdown.png"))
        self.own = self.opponent.copy()
        for a, b in ((20, 225), (1650, 1855)):
            self.own[a:b, 420:660] = own_faces[a:b, 420:660]

    def test_actual_clock_evidence_distinguishes_own_turn_offline_and_ambiguous_frames(self):
        for width, height in ((1080, 1920), (720, 1280), (540, 960)):
            self.assertEqual(countdown_side(cv2.resize(self.opponent, (width, height))), "opponent")
            self.assertEqual(countdown_side(cv2.resize(self.own, (width, height))), "self")
        both = self.opponent.copy()
        both[1650:1855, 420:660] = self.own[1650:1855, 420:660]
        for frame in (None, both, np.zeros_like(both), cv2.imread(str(ROOT / "fixture/jj_opponent_offline.png"))):
            self.assertIsNone(countdown_side(frame))

    def test_stale_opponent_board_cannot_trigger_input_when_our_clock_is_running(self):
        board = Board.from_fen(START_FEN); board.turn = "b"
        self.assertIsNotNone(nudge.emoji_button(self.own))
        with patch.object(nudge, "screenshot", return_value=self.own), \
             patch.object(nudge, "_unchanged", return_value=True), patch.object(nudge, "tap") as tap:
            self.assertFalse(nudge.send_wait_cloud("mock", board, "red_bottom", "r")["sent"])
        tap.assert_not_called()

    def test_turn_change_while_the_picker_opens_cancels_cloud_selection(self):
        panel = cv2.imread(str(ROOT / "fixture/jj_emoji_panel.png"))
        panel[20:225, 420:660] = self.opponent[20:225, 420:660]
        changed = panel.copy()
        for a, b in ((20, 225), (1650, 1855)):
            changed[a:b, 420:660] = self.own[a:b, 420:660]
        board = Board.from_fen(START_FEN); board.turn = "b"
        with patch.object(nudge, "screenshot", side_effect=[self.opponent, panel, changed, changed]), \
             patch.object(nudge, "_unchanged", return_value=True), patch.object(nudge.time, "sleep"), \
             patch.object(nudge, "tap") as tap:
            result = nudge.send_wait_cloud("mock", board, "red_bottom", "r")
        self.assertFalse(result["sent"])
        self.assertEqual([c.args for c in tap.call_args_list], [nudge.emoji_button(self.opponent), (540, 336)])

    def test_failed_or_unverified_selection_never_creates_the_nudge_voice(self):
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(); controller.pipeline = Mock()
        controller.submit_chess({"event": "waiting_for_opponent", "fen": START_FEN, "after_move_number": 4})
        for result in ({"sent": False, "reason": "未确认乌云面板，不再点击"}, {"sent": True}, None):
            controller.submit_chess({"event": "opponent_status", "status": "waiting_long", "fen": START_FEN,
                "after_move_number": 4, "evidence": "stable_board_wait", "nudge": result})
        controller.pipeline.prepare_idle.assert_not_called()

    def test_voice_and_text_recheck_the_start_guard_after_generation(self):
        for speech in (True, False):
            records, done = [], threading.Event()
            guard = Mock(return_value=False)
            pipeline = SpeechPipeline(LiveSettings(speech_enabled=speech),
                lambda kind, *args: records.append(kind), tts=Mock(return_value="mock.wav"))
            original = pipeline._prepare_idle_worker
            def worker(state):
                try: original(state)
                finally: done.set()
            with patch.object(pipeline, "_prepare_idle_worker", side_effect=worker), \
                 patch.object(pipeline, "_audio_duration", return_value=.01), patch("live_host.winsound.PlaySound") as sound:
                try:
                    pipeline.prepare_idle({}, kind="对手状态解说", direct_text="阁下神速些吧。", start_guard=guard)
                    pipeline.open_idle_window()
                    self.assertTrue(done.wait(1))
                    self.assertNotIn("reply", records)
                    guard.assert_called_once()
                    sound.assert_not_called()
                finally: pipeline.close()

    def test_a_real_move_during_the_final_snapshot_invalidates_the_voice(self):
        controller = object.__new__(LiveController)
        event = {"fen": START_FEN, "after_move_number": 4}
        controller.opponent_wait_scope = (START_FEN, 4)
        controller.chess_device, controller.chess_my_color, controller.chess_orientation = "mock", "b", "black_bottom"
        def probe(*args): controller.opponent_wait_scope = None; return True
        with patch.object(nudge, "still_waiting", side_effect=probe) as read:
            self.assertFalse(controller._nudge_still_due(event))
            read.assert_called_once()


if __name__ == "__main__":
    unittest.main()
