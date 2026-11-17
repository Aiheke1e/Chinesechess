import json
import tempfile
import unittest
import threading
import time
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, Mock

import player
import live_host
from live_host import LiveSettings, SpeechPipeline
from board import Board, START_FEN


class SpeechSyncTests(unittest.TestCase):
    def setUp(self):
        self.priority = patch.object(player, "_chat_priority_active", return_value=False)
        self.priority.start()
        self.addCleanup(self.priority.stop)

    def _config(self, folder, enabled=True):
        path = Path(folder) / "live_config.json"
        path.write_text(json.dumps({
            "commentary_enabled": True,
            "speech_enabled": enabled,
        }), encoding="utf-8")
        return str(path)

    def test_waits_until_matching_audio_really_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self._config(folder)
            markers = [
                {"token": "move-1", "enabled": True, "deferred": False},
                {"token": "old"},
                {"token": "move-1", "status": "started"},
            ]
            with patch.object(player, "LIVE_CFG_PATH", config), \
                 patch.object(player, "_read_speech_marker",
                              side_effect=markers), \
                 patch.object(player.time, "sleep"):
                self.assertTrue(player.wait_for_speech_start(
                    "move-1", plan_timeout=0.1, audio_timeout=0.1))

    def test_sandbox_speech_never_delays_live_move(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self._config(folder)
            with patch.object(player, "LIVE_CFG_PATH", config), \
                 patch.object(player, "_read_speech_marker", return_value={
                     "token": "sand-1:sandbox", "enabled": True, "deferred": True}):
                self.assertFalse(player.wait_for_speech_start(
                    "sand-1:sandbox", plan_timeout=0.01, audio_timeout=0.01))

    def test_ordinary_speech_timeout_is_skipped_without_holding_the_move(self):
        with patch.object(player, "_speech_enabled", return_value=True), patch.object(
                player, "_read_speech_marker", return_value={}), patch.object(
                player, "_skip_ordinary_speech") as skipped:
            self.assertFalse(player.wait_for_speech_start(
                "move-1", plan_timeout=0, audio_timeout=0))
            skipped.assert_called_once_with("move-1", reason="plan_timeout")
        with patch.object(player, "_speech_enabled", return_value=True), patch.object(
                player, "_read_speech_marker", return_value={"token": "move-1", "enabled": True}), patch.object(
                player, "_skip_ordinary_speech") as skipped:
            self.assertFalse(player.wait_for_speech_start(
                "move-1", plan_timeout=0.01, audio_timeout=0.01))
            skipped.assert_called_once_with("move-1", reason="audio_start_timeout")

    def test_long_move_deadline_cannot_extend_ordinary_tts_wait(self):
        started = time.monotonic()
        def marker(path):
            if path == player.SPEECH_PREPARING_PATH:
                return {"token": "move-1", "enabled": True}
            return {"token": "old"}
        with patch.object(player, "_speech_enabled", return_value=True), patch.object(
                player, "_read_speech_marker", side_effect=marker), patch.object(
                player, "_skip_ordinary_speech") as skipped:
            self.assertFalse(player.wait_for_speech_start(
                "move-1", plan_timeout=0.1, audio_timeout=0.05,
                deadline=started + 15))
        self.assertLess(time.monotonic() - started, 0.5)
        skipped.assert_called_once_with("move-1", reason="audio_start_timeout")

    def test_audio_error_marker_cannot_be_mistaken_for_started_marker(self):
        with patch.object(player, "_speech_enabled", return_value=True), patch.object(
                player, "_read_speech_marker", side_effect=[
                    {"token": "move-1", "enabled": True},
                    {"token": "move-1", "status": "failed", "message": "音频设备不可用"}]):
            with self.assertRaisesRegex(player.SpeechSyncError, "音频设备"):
                player.wait_for_speech_start("move-1")

    def test_real_move_with_sandbox_route_is_not_marked_as_deferred(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
                live_host, "SPEECH_PREPARING", Path(folder) / "plan.json"):
            SpeechPipeline._mark_speech_preparing({
                "speech_token": "move-1", "沙盘路线": ["后续走法"]}, True)
            plan = json.loads(live_host.SPEECH_PREPARING.read_text(encoding="utf-8"))
            self.assertFalse(plan["deferred"])
            SpeechPipeline._mark_speech_preparing({"speech_token": "move-1:sandbox"}, True)
            self.assertEqual(json.loads(live_host.SPEECH_PREPARING.read_text(encoding="utf-8")), plan)

    def test_sandbox_acknowledgement_cannot_overwrite_real_move_acknowledgement(self):
        with patch.object(SpeechPipeline, "_write_speech_marker") as write:
            SpeechPipeline._mark_speech_started({"speech_token": "move-1:sandbox"})
            SpeechPipeline._mark_speech_failed({"speech_token": "move-1:sandbox"}, "失败")
        write.assert_not_called()

    def test_acknowledgement_waits_for_audio_lock_and_successful_playback_call(self):
        ready, acknowledged = threading.Event(), threading.Event()
        order = []
        def tts(*args):
            ready.set()
            return "prepared.wav"
        def played(*args):
            if args[0]: order.append("playback")
        def ack(*args):
            order.append("ack")
            acknowledged.set()
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True), lambda *args: None, tts=tts)
        with patch.object(pipeline, "_mark_speech_preparing"), patch.object(
                pipeline, "_mark_speech_started", side_effect=ack), patch.object(
                live_host.winsound, "PlaySound", side_effect=played):
            pipeline.playback_lock.acquire()
            try:
                pipeline.prepare_move("关键走棋解说", {"speech_token": "move-1"}, "马二进三。")
                self.assertTrue(ready.wait(1))
                pipeline.commit_move("move-1", deadline=time.monotonic() + 2)
                self.assertFalse(acknowledged.wait(0.05))
            finally:
                pipeline.playback_lock.release()
            self.assertTrue(acknowledged.wait(1))
            pipeline.close()
        self.assertEqual(order[:2], ["playback", "ack"])

    def test_expired_clip_never_plays_even_if_tts_finishes_late(self):
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True), lambda *args: None,
                                  tts=lambda *args: "late.wav")
        state = {"decision": threading.Event(), "approved": True, "epoch": 0,
                 "deadline": time.monotonic() - 1}
        state["decision"].set()
        with patch.object(pipeline, "_mark_speech_started") as ack, patch.object(
                live_host.winsound, "PlaySound") as play:
            pipeline._prepare_move_worker("move-1", "关键走棋解说", {"speech_token": "move-1"},
                                          state, "马二进三。")
            ack.assert_not_called()
            play.assert_not_called()
            pipeline.close()

    def test_playback_device_error_publishes_failure_without_start_ack(self):
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True), lambda *args: None,
                                  tts=lambda *args: "prepared.wav")
        state = {"decision": threading.Event(), "approved": True, "epoch": 0}
        state["decision"].set()
        with patch.object(live_host.winsound, "PlaySound", side_effect=RuntimeError("播放设备不可用")), patch.object(
                pipeline, "_mark_speech_failed") as failed, patch.object(
                pipeline, "_mark_speech_started") as started:
            pipeline._prepare_move_worker("move-1", "关键走棋解说", {"speech_token": "move-1"},
                                          state, "马二进三。")
            started.assert_not_called()
            failed.assert_called_once()
        with patch.object(live_host.winsound, "PlaySound"):
            pipeline.close()

    def test_new_move_cancels_previous_move_still_inside_tts(self):
        ready, release = threading.Event(), threading.Event()
        def tts(*args):
            ready.set()
            release.wait(2)
            return "old.wav"
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True), lambda *args: None, tts=tts)
        state = {"decision": threading.Event(), "approved": True, "epoch": 0}
        state["decision"].set()
        pipeline.prepared["old"] = state
        with patch.object(live_host.winsound, "PlaySound") as play, patch.object(
                pipeline, "_mark_speech_started") as ack:
            worker = threading.Thread(target=pipeline._prepare_move_worker,
                args=("old", "关键走棋解说", {"speech_token": "old"}, state, "马二进三。"))
            worker.start()
            self.assertTrue(ready.wait(1))
            pipeline.invalidate_chess()
            release.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            ack.assert_not_called()
            play.assert_not_called()
            pipeline.close()

    def test_player_does_not_click_or_restart_after_speech_failure(self):
        cfg = dict(player.DEFAULT_CFG, my_color="r", dry_run=False, max_moves=1)
        board = Board.from_fen(START_FEN)
        engine = Mock(last_analysis={}, last_candidates=[])
        events = Mock()
        with patch.object(player.sys, "argv", ["player.py", "--once"]), patch.object(
                player, "load_cfg", return_value=cfg), patch.object(
                player, "list_devices", return_value=["mock-device"]), patch.object(
                player, "match_live_session", return_value=None), patch.object(
                player, "read_stable_board", return_value=(board, "red_bottom")), patch.object(
                player, "save_session"), patch.object(player, "write_runtime_status"), patch.object(
                player, "EventLog", return_value=events), patch("pikafish.get_engine", return_value=engine), patch.object(
                player, "pick_move", return_value=(Board.from_ucci("h2e2"), "Pikafish")), patch.object(
                player, "wait_for_speech_start", side_effect=player.SpeechSyncError("语音超时")), patch.object(
                player, "move_piece_retry") as move, redirect_stdout(io.StringIO()):
            self.assertEqual(player.main(), 15)
        move.assert_not_called()
        self.assertEqual(board.to_fen(), Board.from_fen(START_FEN).to_fen())
        self.assertIn("speech_cancelled", [call.args[0] for call in events.emit.call_args_list])


if __name__ == "__main__":
    unittest.main()
