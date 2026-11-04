import io
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import Mock, patch

import cv2
import numpy as np

from board import Board, START_FEN
from control_panel import ControlPanel, load_ui_settings
from game_control import auto_continue_enabled, write_auto_continue
from postgame import handle_postgame
import player


class AutoContinueTests(unittest.TestCase):
    def panel(self):
        panel = object.__new__(ControlPanel)
        panel.root, panel.status, panel.append = Mock(), Mock(), Mock()
        panel.continuous = Mock(get=Mock(return_value=False))
        panel.auto_retry = True
        panel.task_cancel = threading.Event()
        panel.closing = False
        panel.waiting_for_board = False
        panel.waiter_automatic = False
        panel.process = Mock(poll=Mock(return_value=None))
        panel.live_controller = Mock()
        panel.output = queue.Queue()
        panel._save_ui_settings = Mock()
        panel._terminate_task = Mock()
        return panel

    def test_live_setting_overrides_stale_config_and_unreadable_controls_disable_matching(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "control.json"
            self.assertFalse(auto_continue_enabled(path, default=True))
            write_auto_continue(True, path)
            self.assertTrue(auto_continue_enabled(path, default=False))
            write_auto_continue(False, path)
            self.assertFalse(auto_continue_enabled(path, default=True))
            path.write_text("broken", encoding="utf-8")
            self.assertFalse(auto_continue_enabled(path, default=True))

    def test_switching_off_keeps_current_game_and_chat_running(self):
        panel = self.panel()
        with tempfile.TemporaryDirectory() as folder, patch(
                "control_panel.GAME_CONTROL_PATH", Path(folder) / "controls.json") as path:
            panel.toggle_auto_continue()
            self.assertFalse(auto_continue_enabled(path))
        self.assertFalse(panel.auto_retry)
        self.assertFalse(panel.task_cancel.is_set())
        panel._terminate_task.assert_not_called()
        panel.live_controller.stop_chess_tasks.assert_not_called()
        panel.live_controller.stop_tasks.assert_not_called()

    def test_pending_restart_cannot_revive_after_switch_is_toggled_off_then_on(self):
        panel = self.panel()
        panel.continuous.get.return_value = True
        panel.prepare_and_wait = Mock()
        panel._schedule_prepare()
        callback = panel.root.after.call_args.args[1]
        with patch.object(panel, "_publish_auto_continue", return_value=True):
            panel.continuous.get.return_value = False
            panel.toggle_auto_continue()
            panel.continuous.get.return_value = True
            panel.toggle_auto_continue()
        callback()  # Also reject a callback already delivered by Tk.
        panel.prepare_and_wait.assert_not_called()
        panel.root.after_cancel.assert_called_once()

    def test_cancelled_automatic_waiter_cannot_deliver_its_queued_autostart(self):
        panel = self.panel()
        panel.waiting_for_board = True
        panel.waiter_automatic = True
        panel.start = Mock()
        panel.output.put(("assistant_task", panel.task_cancel, ("autostart", {
            "standard": True, "pieces": 32, "turn": "r"})))
        with patch.object(panel, "_publish_auto_continue", return_value=True):
            panel.toggle_auto_continue()
        panel._poll()
        self.assertFalse(panel.waiting_for_board)
        panel.start.assert_not_called()
        panel.live_controller.stop_chess_tasks.assert_called_once()
        panel.live_controller.stop_tasks.assert_not_called()

    def test_manual_start_with_auto_continue_off_launches_one_game_with_live_controls(self):
        panel = self.panel()
        panel.movetime = Mock(get=Mock(return_value="800"))
        panel.max_moves = Mock(get=Mock(return_value="150"))
        panel.dry = Mock(get=Mock(return_value=False))
        panel._launch = Mock()
        with tempfile.TemporaryDirectory() as folder, patch(
                "control_panel.GAME_CONTROL_PATH", Path(folder) / "controls.json") as path:
            panel.start(False, initial_turn="r", min_pieces=4)
            self.assertFalse(auto_continue_enabled(path))
            arguments = panel._launch.call_args.args[0]
            self.assertEqual(arguments[arguments.index("--control-file") + 1], str(path))
        panel._launch.assert_called_once()

    def test_manual_wait_is_allowed_with_switch_off_but_automatic_wait_is_rejected(self):
        panel = self.panel()
        panel.auto_retry = False
        panel.process = None
        panel.dry, panel.turn_choice = Mock(), Mock(get=Mock(return_value="自动判断"))
        with patch("control_panel.threading.Thread") as thread:
            panel.prepare_and_wait(automatic=True)
            thread.assert_not_called()
            panel.prepare_and_wait()
            thread.assert_called_once()
        self.assertTrue(panel.waiting_for_board)
        self.assertFalse(panel.waiter_automatic)

    def test_readonly_check_does_not_inherit_a_previous_auto_retry(self):
        panel = self.panel()
        panel.continuous.get.return_value = True
        panel.movetime = Mock(get=Mock(return_value="800"))
        panel.max_moves = Mock(get=Mock(return_value="150"))
        panel.dry = Mock(get=Mock(return_value=True))
        panel._launch = Mock()
        with patch.object(panel, "_publish_auto_continue", return_value=True) as publish:
            panel.start(True, initial_turn="r", min_pieces=4)
        self.assertFalse(panel.auto_retry)
        publish.assert_called_once_with(False)

    def replay(self):
        image = np.zeros((1920, 1080, 3), np.uint8)
        cv2.rectangle(image, (330, 1580), (750, 1700), (40, 190, 60), -1)
        return image

    def test_switching_off_during_button_confirmation_prevents_the_replay_tap(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "control.json"
            write_auto_continue(True, path)
            calls = 0
            def screenshot(*args):
                nonlocal calls
                calls += 1
                if calls == 2:
                    write_auto_continue(False, path)
                return self.replay()
            with patch("postgame.screenshot", side_effect=screenshot), \
                 patch("postgame.tap") as tap, patch("postgame.time.sleep"):
                self.assertEqual(handle_postgame("mock", continue_allowed=lambda:
                    auto_continue_enabled(path)), [])
            tap.assert_not_called()
            self.assertEqual(calls, 2)

    def test_switching_off_during_slow_xml_read_prevents_its_stale_action(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "control.json"
            write_auto_continue(True, path)
            def xml(*args):
                write_auto_continue(False, path)
                return '<hierarchy><node text="再来一局" bounds="[300,1500][750,1700]"/></hierarchy>'
            with patch("postgame.screenshot", return_value=np.zeros((1920, 1080, 3), np.uint8)), \
                 patch("recognizer.recognize_position", return_value=(None, None)), \
                 patch("postgame.read_ui_xml", side_effect=xml), patch("postgame.tap") as tap:
                self.assertEqual(handle_postgame("mock", continue_allowed=lambda:
                    auto_continue_enabled(path)), [])
            tap.assert_not_called()

    def test_player_checks_current_switch_at_game_end_instead_of_startup_config(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "control.json"
                write_auto_continue(enabled, path)
                board = Board.from_fen(START_FEN)
                board.turn = "b"
                cfg = dict(player.DEFAULT_CFG, my_color="r", auto_continue_after_game=True)
                with ExitStack() as stack:
                    for item in [
                        patch.object(player.sys, "argv", ["player.py", "--control-file", str(path)]),
                        patch.object(player, "load_cfg", return_value=cfg),
                        patch.object(player, "list_devices", return_value=["mock"]),
                        patch.object(player, "match_live_session", return_value=None),
                        patch.object(player, "read_stable_board", return_value=(board, "red_bottom")),
                        patch.object(player, "infer_turn_from_standard", return_value=None),
                        patch.object(player, "save_session"), patch.object(player, "write_runtime_status"),
                        patch.object(player, "EventLog"), patch("pikafish.get_engine", return_value=Mock()),
                        patch.object(player, "screenshot", return_value=self.replay()),
                        patch.object(player.time, "sleep"),
                    ]:
                        stack.enter_context(item)
                    handler = stack.enter_context(patch("postgame.handle_postgame", return_value=["结算页主按钮"]))
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(player.main(), 10 if enabled else 0)
                    if enabled:
                        handler.assert_called_once()
                        write_auto_continue(False, path)
                        self.assertFalse(handler.call_args.kwargs["continue_allowed"]())
                    else:
                        handler.assert_not_called()

    def test_disabled_preference_survives_restart(self):
        panel = self.panel()
        panel.theme_name, panel.opacity = Mock(get=Mock(return_value="清晰蓝白")), Mock(get=Mock(return_value=100))
        panel._broadcast_dimensions = lambda: (600, 160)
        with tempfile.TemporaryDirectory() as folder, patch(
                "control_panel.UI_CFG", str(Path(folder) / "ui.json")):
            ControlPanel._save_ui_settings(panel)
            self.assertFalse(load_ui_settings()["auto_continue"])

    def test_enabling_after_the_previous_game_ended_resumes_the_existing_session(self):
        panel = self.panel()
        panel.chess_session_requested = True
        panel.auto_retry = False
        panel.process.poll.return_value = 0
        panel.continuous.get.return_value = True
        old_token = panel.task_cancel
        panel.prepare_and_wait = Mock()
        with patch.object(panel, "_publish_auto_continue", return_value=True):
            panel.toggle_auto_continue()
        callback = panel.root.after.call_args.args[1]
        self.assertTrue(old_token.is_set())
        self.assertIsNot(panel.task_cancel, old_token)
        callback()
        panel.prepare_and_wait.assert_called_once_with(automatic=True)

    def test_enabling_during_current_game_updates_child_without_starting_another_worker(self):
        panel = self.panel()
        panel.chess_session_requested = True
        panel.auto_retry = False
        panel.continuous.get.return_value = True
        with tempfile.TemporaryDirectory() as folder, patch(
                "control_panel.GAME_CONTROL_PATH", Path(folder) / "control.json") as path:
            panel.toggle_auto_continue()
            self.assertTrue(auto_continue_enabled(path))
        self.assertTrue(panel.auto_retry)
        self.assertFalse(panel.task_cancel.is_set())
        panel.root.after.assert_not_called()

    def test_enabling_a_mode_after_global_pause_does_not_start_a_game(self):
        panel = self.panel()
        panel.chess_session_requested = False
        panel.task_cancel.set()
        panel.process.poll.return_value = 0
        panel.continuous.get.return_value = True
        with patch.object(panel, "_publish_auto_continue", return_value=True):
            panel.toggle_auto_continue()
        panel.root.after.assert_not_called()
        self.assertTrue(panel.task_cancel.is_set())


if __name__ == "__main__":
    unittest.main()
