from datetime import datetime, timedelta, timezone
import time
import unittest
from unittest.mock import Mock, patch

from control_panel import ControlPanel
from live_clock import BEIJING, CLOCK_KIND, GameClockCadence, clock_line
from live_host import LiveController, LiveSettings, SpeechPipeline


class LiveClockTests(unittest.TestCase):
    def test_time_is_explicit_beijing_time_including_midnight_and_zero_minutes(self):
        self.assertEqual(clock_line(datetime(2026, 11, 6, 16, 0, tzinfo=timezone.utc)),
                         "现在是北京时间0点00分，你看到的不是直播录像。")
        self.assertIn("9点05分", clock_line(datetime(2026, 11, 6, 1, 5, tzinfo=timezone.utc)))

    def test_every_three_finished_real_games_counts_wins_losses_and_result_screens(self):
        cadence = GameClockCadence()
        due = []
        endings = (("won", "no_legal_moves"), ("lost", "no_legal_moves"), ("finished", "jj_result_screen"))
        for index, (result, reason) in enumerate(endings * 2, 1):
            cadence.observe({"event": "start", "dry_run": False})
            cadence.observe({"event": "move_applied"})
            end = {"event": "game_over", "result": result, "reason": reason}
            if cadence.observe(end): due.append(index)
            self.assertFalse(cadence.observe(end))
        self.assertEqual(due, [3, 6])
        self.assertEqual(cadence.completed, 6)

    def test_read_only_attempts_recovered_result_pages_and_unconfirmed_stops_do_not_count(self):
        cadence = GameClockCadence()
        end = {"event": "game_over", "result": "won", "reason": "no_legal_moves"}
        cadence.observe({"event": "start", "dry_run": True})
        cadence.observe({"event": "move_applied"})
        self.assertFalse(cadence.observe(end))
        cadence.observe({"event": "start"})
        self.assertFalse(cadence.observe(end))
        cadence.observe({"event": "move_applied"})
        self.assertFalse(cadence.observe({**end, "result": "stopped", "reason": "user_stop"}))
        self.assertEqual(cadence.completed, 0)

    def test_controller_schedules_a_separate_lower_priority_clock_call_after_third_game(self):
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings()
        controller.pipeline, controller._notify = Mock(), Mock()
        controller._record_opening_move = Mock()
        for index in range(1, 4):
            controller.submit_chess({"event": "move_applied", "notation": "红方 兵三进一"})
            controller.submit_chess({"event": "game_over", "result": "finished", "reason": "jj_result_screen"})
        calls = controller.pipeline.submit.call_args_list
        self.assertEqual([call.args[1] for call in calls], ["结算收官", "结算收官", "结算收官", CLOCK_KIND])
        self.assertGreater(calls[-1].args[0], calls[-2].args[0])
        self.assertEqual(calls[-1].args[2]["已完成盘数"], 3)

    def pipeline_job(self, *, speech=False, tts=None):
        records = []
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=speech, cooldown=0),
            lambda kind, text, data: records.append((kind, text, data)),
            llm=Mock(side_effect=AssertionError("报时不调用模型")), tts=tts or Mock())
        job = (5, 1, time.monotonic(), pipeline.cancel_epoch, CLOCK_KIND, {"_chess_epoch": pipeline.chess_epoch}, None)
        return pipeline, records, job

    def test_clock_uses_generation_time_and_enters_spoken_log_with_no_viewer_or_move(self):
        moment = datetime(2026, 11, 6, 21, 7, tzinfo=BEIJING)
        pipeline, records, job = self.pipeline_job()
        with patch("live_host.beijing_now", return_value=moment), patch("live_host.winsound.PlaySound"):
            try:
                pipeline._run_job(job)
                reply = next(record for record in records if record[0] == "reply")
                self.assertEqual(reply[1], clock_line(moment))
                header, body = ControlPanel._speech_log_entry({"kind": "reply", "text": reply[1], "data": reply[2]})
                self.assertEqual(header, "北京时间")
                self.assertEqual(body, clock_line(moment))
                self.assertNotIn("观众", reply[2])
                self.assertNotIn("解说对应走法", reply[2])
                pipeline.llm.assert_not_called()
            finally: pipeline.close()

    def test_new_game_drops_pending_clock_without_calling_tts(self):
        pipeline, records, job = self.pipeline_job(speech=True)
        with patch("live_host.winsound.PlaySound"):
            try:
                pipeline.set_chess_active(True)
                pipeline._run_job(job)
                pipeline.tts.assert_not_called()
                self.assertFalse(any(kind == "reply" for kind, *_ in records))
            finally: pipeline.close()

    def test_minute_change_while_synthesizing_does_not_play_an_outdated_time(self):
        moment = [datetime(2026, 11, 6, 21, 7, 59, tzinfo=BEIJING)]
        def tts(*args):
            moment[0] += timedelta(seconds=2)
            return "old-minute.wav"
        pipeline, records, job = self.pipeline_job(speech=True, tts=tts)
        with patch("live_host.beijing_now", side_effect=lambda: moment[0]), patch("live_host.winsound.PlaySound") as sound:
            try:
                pipeline._run_job(job)
                self.assertFalse(any(kind == "reply" for kind, *_ in records))
                sound.assert_not_called()
            finally: pipeline.close()

    def test_paused_commentary_does_not_advance_the_cadence(self):
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(commentary_enabled=False)
        controller.pipeline = Mock()
        controller.clock_cadence = GameClockCadence()
        controller.clock_cadence.active = True
        controller.submit_chess({"event": "game_over", "result": "won", "reason": "no_legal_moves"})
        self.assertEqual(controller.clock_cadence.completed, 0)
        controller.pipeline.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
