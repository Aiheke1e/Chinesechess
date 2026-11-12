from datetime import datetime, timedelta, timezone
import threading
import unittest
from unittest.mock import Mock, patch

from control_panel import ControlPanel
from live_host import LiveController, LiveSettings, SpeechPipeline
from stream_shutdown import StreamShutdown, next_shutdown_time, FAREWELL_KIND, PRELUDE_KIND


class StreamShutdownTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.flow = StreamShutdown(clock=lambda: self.now)

    def done(self, action, **overrides):
        data = {**action[3], "kind": action[1], "completed": True, "_speech_mode": "audio", **overrides}
        self.flow.speech_done(data)
        return data

    def test_no_game_still_requires_two_completed_audio_farewells(self):
        self.flow.arm(False)
        self.assertIsNone(self.flow.tick(idle=True))
        self.now += 2
        first = self.flow.tick(idle=True)
        self.assertEqual(first[1], FAREWELL_KIND)
        self.assertIsNone(self.flow.tick(idle=True))  # Enqueue/start is not completion.
        self.done(first)
        self.assertIsNone(self.flow.tick(idle=False))  # Actual playback lock must drain.
        last = self.flow.tick(idle=True)
        self.assertIn("爱你们", last[2])
        self.done(last)
        action = self.flow.tick(idle=True)
        self.assertEqual(action, ("close", self.flow.epoch))
        self.assertIsNone(self.flow.tick(idle=True))  # Only one close operation.

    def test_current_game_keeps_playing_and_only_verified_terminal_event_advances(self):
        self.flow.arm(True)
        self.now += 500
        self.assertIsNone(self.flow.tick(idle=True, prelude_safe=False))
        for event in ({"event":"end", "result":"finished"},
                      {"event":"game_over", "result":"recognition_pause"},
                      {"event":"game_over", "result":"finished", "reason":"unknown"},
                      {"event":"game_over", "result":"won", "dry_run":True}):
            self.flow.observe_game(event)
            self.assertEqual(self.flow.state, "waiting_game")
        self.flow.observe_game({"event":"game_over", "result":"draw"})
        entered = self.flow.entered
        self.now += 1
        self.flow.observe_game({"event":"game_over", "result":"draw"})
        self.assertEqual(self.flow.entered, entered)
        self.assertEqual(self.flow.state, "draining")

    def test_preludes_are_safe_gap_only_bounded_and_spaced(self):
        self.flow.arm(True)
        self.assertIsNone(self.flow.tick(idle=False, prelude_safe=True))
        self.assertIsNone(self.flow.tick(idle=True, prelude_safe=False))
        first = self.flow.tick(idle=True, prelude_safe=True)
        self.assertEqual(first[1], PRELUDE_KIND)
        self.done(first)
        self.now += 44
        self.assertIsNone(self.flow.tick(idle=True, prelude_safe=True))
        self.now += 1
        second = self.flow.tick(idle=True, prelude_safe=True)
        self.assertNotEqual(first[2], second[2])
        self.done(second)
        self.now += 200
        self.assertIsNone(self.flow.tick(idle=True, prelude_safe=True))

    def test_failed_interrupted_or_text_farewell_never_closes(self):
        for data in ({"completed":False}, {"_speech_mode":"text"}):
            self.flow.arm(False)
            self.now += 2
            first = self.flow.tick(idle=True)
            self.done(first, **data)
            self.assertEqual(self.flow.state, "error")
            self.assertIsNone(self.flow.tick(idle=True))

    def test_cancel_and_new_game_reject_old_completion_and_close_result(self):
        self.flow.arm(False)
        self.now += 2
        old = self.flow.tick(idle=True)
        epoch = self.flow.epoch
        self.flow.cancel()
        self.done(old)
        self.assertEqual(self.flow.state, "idle")
        self.assertFalse(self.flow.closed(epoch, success=True, message="old"))
        self.flow.arm(False)
        self.flow.observe_game({"event":"position", "fen":"new"})
        self.done(old)
        self.assertEqual(self.flow.state, "waiting_game")
        self.assertEqual(self.flow.farewells, 0)

    def test_timer_is_one_shot_rolls_to_tomorrow_and_does_not_arm_on_restart(self):
        now = datetime(2026, 11, 12, 23, 0, tzinfo=timezone(timedelta(hours=8)))
        target = next_shutdown_time("22:30", now)
        self.assertEqual(target.date(), (now+timedelta(days=1)).date())
        self.flow.schedule("23:30", now)
        self.assertFalse(self.flow.active)
        self.assertFalse(self.flow.due(now))
        self.assertTrue(self.flow.due(now+timedelta(minutes=30)))
        self.flow.arm(True)
        self.assertFalse(self.flow.due(now+timedelta(hours=3)))
        self.assertEqual(StreamShutdown().state, "idle")
        for value in ("24:00", "7:00", "23:60", "23:30:00", "hello"):
            with self.assertRaises(ValueError): next_shutdown_time(value, now)

    def test_speech_and_drain_timeouts_never_close(self):
        self.flow.arm(False)
        self.now += 91
        self.assertIsNone(self.flow.tick(idle=False))
        self.assertEqual(self.flow.state, "error")
        self.flow.arm(False)
        self.now += 2
        self.flow.tick(idle=True)
        self.now += 91
        self.flow.tick(idle=True)
        self.assertEqual(self.flow.state, "error")


class ShutdownIntegrationTests(unittest.TestCase):
    def panel(self, active=True):
        panel = object.__new__(ControlPanel)
        panel.stream_shutdown = StreamShutdown()
        panel.shutdown_cancel = threading.Event()
        panel.shutdown_scheduled = Mock()
        panel.live_controller = Mock()
        panel.live_controller.settings.speech_enabled = True
        panel.live_controller.game_active = active
        panel.live_controller.game_terminal = not active
        panel.process = Mock(poll=Mock(return_value=None)) if active else None
        panel.chess_session_requested = active
        panel.dry = Mock(get=Mock(return_value=False))
        panel.waiting_for_board = False
        panel.task_cancel = threading.Event()
        panel.continuous = Mock()
        panel._publish_auto_continue = Mock(return_value=True)
        panel._save_ui_settings = Mock()
        panel._cancel_scheduled_prepare = Mock()
        panel._sync_shutdown = Mock()
        panel.append = Mock()
        return panel

    def test_arm_does_not_stop_running_chess_or_chat_and_control_write_must_succeed(self):
        panel = self.panel()
        panel._arm_stream_shutdown()
        self.assertEqual(panel.stream_shutdown.state, "waiting_game")
        panel._publish_auto_continue.assert_called_once_with(False)
        self.assertFalse(panel.task_cancel.is_set())
        panel.live_controller.stop_chess_tasks.assert_not_called()
        panel.live_controller.pause.assert_not_called()
        panel.process.terminate.assert_not_called()
        panel = self.panel()
        panel._publish_auto_continue.return_value = False
        panel._arm_stream_shutdown()
        self.assertFalse(panel.stream_shutdown.active)
        panel.continuous.set.assert_not_called()

    def test_no_chess_uses_immediate_goodbye_and_speech_off_rejects_request(self):
        panel = self.panel(active=False)
        panel._arm_stream_shutdown()
        self.assertEqual(panel.stream_shutdown.state, "draining")
        panel = self.panel(active=False)
        panel.live_controller.settings.speech_enabled = False
        panel._arm_stream_shutdown()
        self.assertEqual(panel.stream_shutdown.state, "error")
        panel._publish_auto_continue.assert_not_called()

    def test_idle_startup_never_creates_a_broadcaster_controller(self):
        panel = self.panel(active=False)
        panel.closing = False
        panel.root = Mock()
        with patch("control_panel.CompanionShutdown") as companion:
            panel._shutdown_tick()
        companion.assert_not_called()
        panel.live_controller.shutdown_speech.assert_not_called()
        self.assertFalse(panel.stream_shutdown.active)

    def test_commentary_off_still_forwards_lifecycle(self):
        controller = object.__new__(LiveController)
        controller.pipeline = Mock()
        controller.settings = LiveSettings(commentary_enabled=False)
        controller._notify = Mock()
        controller.submit_chess({"event":"start"})
        self.assertTrue(controller.game_active)
        controller.submit_chess({"event":"game_over", "result":"lost"})
        self.assertFalse(controller.game_active)
        controller._notify.assert_called_with("game_lifecycle", "棋局状态更新", {"event":"game_over", "result":"lost"})

    def test_auto_continue_cannot_be_reenabled_until_request_is_cancelled(self):
        panel = self.panel()
        panel.status = Mock()
        panel.stream_shutdown.arm(True)
        panel.continuous.get.return_value = True
        panel.toggle_auto_continue()
        panel.continuous.set.assert_called_with(False)
        panel._publish_auto_continue.assert_not_called()

    def test_recovery_does_not_match_new_game_or_click_postgame(self):
        from tests.test_game_handoff import GameHandoffTests
        panel = GameHandoffTests().panel()
        panel._save_diagnostic = Mock()
        frame = object()
        with patch("control_panel.list_devices", return_value=["mock"]), \
             patch("control_panel.current_package", return_value=("cn.jj.chess", "Main")), \
             patch("control_panel.screenshot", return_value=frame), \
             patch("control_panel.tap") as tap, \
             patch("postgame.has_actionable_postgame", return_value=True), \
             patch("postgame.handle_postgame") as postgame, \
             patch.object(panel.task_cancel, "wait", return_value=False):
            panel._prepare_worker("auto", panel.task_cancel, resume_only=True)
        postgame.assert_not_called()
        tap.assert_not_called()
        self.assertEqual(panel.output.get()[2][0], "status")
        self.assertEqual(panel.output.get()[2][0], "shutdown_game_over")

    def test_pipeline_receipt_is_after_audio_finishes_and_cancelled_generation_cannot_finish(self):
        for cancel in (False, True):
            records, started, done = [], threading.Event(), threading.Event()
            def notify(kind, text, data):
                records.append((kind, data))
                if kind == "reply": started.set()
                if kind == "shutdown_speech_done": done.set()
            with patch("live_host.winsound.PlaySound"), \
                 patch.object(SpeechPipeline, "_prune_audio"), \
                 patch.object(SpeechPipeline, "_audio_duration", return_value=.15):
                pipeline = SpeechPipeline(LiveSettings(speech_enabled=True, cooldown=0), notify, tts=lambda *a:"mock.wav")
                try:
                    pipeline.set_shutdown_epoch(7)
                    pipeline.submit(5, FAREWELL_KIND, {"_shutdown_epoch":7, "_shutdown_token":"t"}, direct_text="谢谢大家，爱你们，拜拜！")
                    self.assertTrue(started.wait(1))
                    self.assertFalse(done.wait(.04))
                    if cancel: pipeline.set_shutdown_epoch(8)
                    self.assertTrue(done.wait(1))
                    receipt = next(data for kind, data in records if kind == "shutdown_speech_done")
                    self.assertEqual(receipt["completed"], not cancel)
                    if not cancel: self.assertEqual(receipt["_speech_mode"], "audio")
                finally:
                    pipeline.close()

    def test_unstarted_prelude_is_discarded_once_final_goodbye_begins(self):
        records, done = [], threading.Event()
        def notify(kind, text, data):
            records.append((kind, data))
            if kind == "shutdown_speech_done": done.set()
        with patch("live_host.winsound.PlaySound") as sound:
            pipeline = SpeechPipeline(LiveSettings(speech_enabled=True), notify, tts=Mock())
            try:
                pipeline.set_shutdown_epoch(2, final=True)
                pipeline.submit(5, PRELUDE_KIND, {"_shutdown_epoch":2,"_shutdown_token":"old"},
                                direct_text="这盘下完就收工。")
                self.assertTrue(done.wait(1))
                self.assertFalse(any(kind == "reply" for kind, _ in records))
                self.assertFalse(records[-1][1]["completed"])
                sound.assert_not_called()
            finally:
                pipeline.close()


if __name__ == "__main__":
    unittest.main()
