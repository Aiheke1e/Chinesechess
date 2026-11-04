import queue
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import adb_bridge
from control_panel import ControlPanel
from live_host import LiveController, LiveSettings, SpeechPipeline


class StopTests(unittest.TestCase):
    def panel(self):
        panel = object.__new__(ControlPanel)
        panel.task_cancel = threading.Event()
        panel.waiting_for_board = True
        panel.auto_retry = True
        panel.closing = False
        panel.process = Mock()
        panel.process.poll.return_value = None
        panel.live_controller = Mock()
        panel.root = Mock()
        panel.status = Mock()
        panel.board_state = Mock()
        panel.append = Mock()
        panel.output = queue.Queue()
        panel._cancel_sandtable_jobs = Mock()
        panel._close_sandtable_ui = Mock()
        panel._terminate_task = Mock()
        return panel

    def test_stop_cancels_waiter_process_speech_and_scheduled_restart(self):
        panel = self.panel()
        panel.prepare_and_wait = Mock()
        panel._schedule_prepare()
        restart = panel.root.after.call_args.args[1]
        with tempfile.TemporaryDirectory() as folder, patch(
                "control_panel.STOP_FILE", str(Path(folder) / "stop.request")):
            panel.stop()
        restart()
        self.assertTrue(panel.task_cancel.is_set())
        self.assertFalse(panel.waiting_for_board)
        self.assertFalse(panel.auto_retry)
        panel._terminate_task.assert_called_once_with(panel.process)
        panel.live_controller.stop_chess_tasks.assert_called_once()
        panel.live_controller.stop_tasks.assert_not_called()
        panel.prepare_and_wait.assert_not_called()

    def test_cancelled_queue_cannot_start_a_game_or_overwrite_stopped_status(self):
        panel = self.panel()
        old = panel.task_cancel
        old.set()
        panel.task_cancel = threading.Event()  # Even if a new task has begun.
        panel.start = Mock()
        panel.output.put(("assistant_task", old, ("autostart", {})))
        panel.output.put(("assistant_task", old, ("status", "old status")))
        panel.output.put(("player_output", old, "old recognition warning"))
        panel._poll()
        panel.start.assert_not_called()
        panel.status.set.assert_not_called()
        panel.append.assert_not_called()

    def test_postgame_completion_schedules_next_game_only_with_auto_continue(self):
        for enabled in (True, False):
            with self.subTest(auto_continue=enabled):
                panel = self.panel()
                panel.continuous = Mock(get=Mock(return_value=enabled))
                panel._schedule_prepare = Mock()
                panel.output.put(("done", 10, panel.process))
                panel._poll()
                self.assertFalse(panel.task_cancel.is_set())
                if enabled:
                    panel._schedule_prepare.assert_called_once_with()
                else:
                    panel._schedule_prepare.assert_not_called()

    def test_cancelled_screenshot_does_not_reach_recognition(self):
        panel = self.panel()
        panel._assistant_cfg = Mock(return_value={"jj_package": "cn.jj.chess"})

        def screenshot(*args):
            panel.task_cancel.set()
            return object()

        with patch("control_panel.list_devices", return_value=["device"]), patch(
                "control_panel.current_package", return_value=("cn.jj.chess", "Main")), patch(
                "control_panel.match_live_session", return_value=None), patch(
                "control_panel.screenshot", side_effect=screenshot), patch(
                "control_panel.recognize_position") as recognize:
            panel._prepare_worker(cancel=panel.task_cancel)
        recognize.assert_not_called()

    def test_stop_interrupts_an_inflight_adb_command(self):
        cancel = threading.Event()
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)

        def communicate(timeout=None):
            if timeout is not None:
                cancel.set()
                raise subprocess.TimeoutExpired("adb", timeout)
            return b"", b""

        process.communicate.side_effect = communicate
        with patch("adb_bridge.subprocess.Popen", return_value=process):
            with adb_bridge.cancellable_adb(cancel), self.assertRaises(adb_bridge.TaskCancelled):
                adb_bridge.adb(["exec-out", "screencap", "-p"])
        process.kill.assert_called_once()

    def test_stop_while_tts_is_generating_prevents_late_playback(self):
        started, release, finished = (threading.Event() for _ in range(3))

        def tts(*args):
            started.set()
            release.wait(2)
            return "cancelled-test.wav"

        def notify(kind, text, data=None):
            if kind == "speech_status" and text == "等待下一条内容":
                finished.set()

        with patch("live_host.winsound.PlaySound") as play:
            pipeline = SpeechPipeline(LiveSettings(speech_enabled=True, cooldown=0),
                                      notify, tts=tts)
            try:
                pipeline.play_high("我方走棋播报", {}, "马二进三。")
                self.assertTrue(started.wait(1))
                pipeline.suspended = True
                pipeline.clear(stop_audio=True)
                release.set()
                self.assertTrue(finished.wait(1))
                self.assertFalse(pipeline.prepare_idle({}))
                self.assertFalse(pipeline.play_high("我方走棋播报", {}, "旧内容"))
            finally:
                release.set()
                pipeline.close()
        self.assertFalse(any(call.args[0] for call in play.call_args_list))

    def test_suspended_controller_ignores_chess_and_viewer_speech(self):
        controller = object.__new__(LiveController)
        controller.suspended = True
        controller.pipeline = Mock()
        controller.submit_chess({"event": "engine_analysis", "notation": "红方 马二进三"})
        controller.receive({"type": "chat", "text": "问主播 你好"}, force=True)
        self.assertEqual(controller.pipeline.mock_calls, [])

    def test_stop_between_worker_check_and_enqueue_rejects_the_job(self):
        pipeline = SpeechPipeline(LiveSettings(), lambda *args: None)
        try:
            def stop_during_check():
                pipeline.suspended = True
                pipeline.clear(stop_audio=True)
                return True

            with patch.object(pipeline, "_ensure_worker", side_effect=stop_during_check), patch(
                    "live_host.winsound.PlaySound"):
                self.assertFalse(pipeline.submit(1, "弹幕回复", {}, "旧任务"))
                self.assertFalse(pipeline.prepare_move("关键走棋解说", {
                    "speech_token": "old-move"}, "旧任务"))
            self.assertEqual(pipeline.jobs, [])
            self.assertEqual(pipeline.prepared, {})
        finally:
            pipeline.close()


if __name__ == "__main__":
    unittest.main()
