import unittest
import queue
import threading
from unittest.mock import Mock, patch

from control_panel import ControlPanel
from live_host import LiveSettings, SpeechPipeline
from monitoring import MonitorIssues, live_view, player_view, speech_view
from chat_selection import ChatSelector


class MonitoringTests(unittest.TestCase):
    def test_paused_sampling_still_counts_traffic_without_selecting_comments(self):
        selector = ChatSelector(clock=lambda: 100)
        for number in range(30):
            selector.offer({"user": str(number), "text": "这手棋怎么走？"}, eligible=False)
        self.assertEqual(selector.stats()["chat_rate"], 2)
        self.assertIsNone(selector.take())
        self.assertFalse(selector.pending)

    def test_player_stage_requires_the_current_process_heartbeat(self):
        snapshot = {"pid": 5, "updated_at": 99, "stage": "waiting_opponent"}
        self.assertEqual(player_view(snapshot, pid=5, running=True, now=100)[0], "等待对手")
        self.assertEqual(player_view(snapshot, pid=6, running=True, now=100)[0], "启动中")
        self.assertEqual(player_view(snapshot, pid=5, running=False, now=100)[0], "待机")
        self.assertEqual(player_view(snapshot, pid=5, running=True, stopped=True, now=200)[0], "已停止")

    def test_stale_heartbeat_is_visible_without_changing_control_policy(self):
        snapshot = {"pid": 5, "updated_at": 70, "stage": "engine_thinking"}
        self.assertEqual(player_view(snapshot, pid=5, running=True, now=100)[2], "error")
        self.assertEqual(player_view(snapshot, pid=5, running=True, now=100, warning_after=60)[0], "引擎思考")

    def test_voice_switch_is_not_a_playback_acknowledgement(self):
        self.assertEqual(speech_view(True, {})[0], "待播")
        self.assertEqual(speech_view(True, {"preparing": 1})[0], "准备播报")
        self.assertEqual(speech_view(True, {"playing": True, "queued": 2})[:2], ("正在播报", "排队 2 条"))
        self.assertEqual(speech_view(False, {"playing": True})[0], "仅文本")
        self.assertEqual(speech_view(True, {"suspended": True}, "失败")[0], "已暂停")

    def test_silent_room_and_missing_population_are_not_connection_failures(self):
        view = live_view("connected", None, 0)
        self.assertEqual(view[2], "ok")
        self.assertIn("待获取", view[1])
        self.assertNotIn("在线 0 人", view[1])
        self.assertEqual(live_view("error", 1200, 3.2)[2], "error")
        self.assertEqual(live_view("connecting")[2], "warn")

    def test_recovery_only_clears_the_recovered_source(self):
        issues = MonitorIssues()
        issues.set("识别", "正在复核", "warn")
        issues.set("播报", "语音设备不可用")
        issues.set("直播", "连接失败")
        sequence = issues.sequence
        issues.set("直播", "连接失败")
        self.assertEqual(issues.sequence, sequence)
        issues.clear("直播")
        self.assertEqual(issues.rows()[0][0], "播报")
        self.assertEqual(len(issues.rows()), 2)

    def test_pipeline_reports_playing_only_after_windows_accepts_audio(self):
        seen = []
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True),
            lambda *args: seen.append(pipeline.activity_snapshot()["playing"]))
        pipeline.current_playback = "high"  # The lock may be held before playback is accepted.
        self.assertFalse(pipeline.activity_snapshot()["playing"])
        with patch("live_host.winsound.PlaySound"), patch.object(pipeline, "_audio_duration", return_value=0.02):
            try:
                self.assertTrue(pipeline._play_synced_audio("sample.wav", {},
                    {"approved": True, "epoch": 0}, "播报", "关键走棋解说", acknowledge=False))
                self.assertIn(True, seen)
                self.assertFalse(pipeline.activity_snapshot()["playing"])
            finally:
                pipeline.close()

    def test_device_error_does_not_leave_a_false_playing_state(self):
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True), Mock())
        with patch("live_host.winsound.PlaySound", side_effect=OSError("device")):
            with self.assertRaises(OSError):
                pipeline._play_synced_audio("sample.wav", {}, {"approved": True, "epoch": 0},
                                            "播报", "关键走棋解说", acknowledge=False)
            self.assertFalse(pipeline.activity_snapshot()["playing"])
        with patch("live_host.winsound.PlaySound"):
            pipeline.close()

    def test_connection_recovery_does_not_hide_a_voice_fault(self):
        panel = object.__new__(ControlPanel)
        panel.monitor_issues = MonitorIssues()
        panel.monitor_issues.set("直播", "连接失败")
        panel.monitor_issues.set("播报", "语音设备不可用")
        panel.speech_status = Mock(get=Mock(return_value="等待新内容"))
        panel.voice_state = Mock()
        panel.live_speech = Mock(get=Mock(return_value=True))
        panel.live_status = Mock(get=Mock(return_value="已连接抖音直播间"))
        panel.stream_state = Mock()
        panel.indicators = {}
        panel._sync_live_badges()
        self.assertNotIn("直播", panel.monitor_issues.active)
        self.assertIn("播报", panel.monitor_issues.active)

    def test_structured_timeout_error_is_not_lost_when_text_has_no_failure_keyword(self):
        panel = object.__new__(ControlPanel)
        panel.output = queue.Queue()
        panel.root = Mock()
        panel.task_cancel = threading.Event()
        panel.live_controller = Mock(connector_epoch=1)
        panel.live_status = Mock()
        panel.audience_text = Mock()
        panel.monitor_issues = MonitorIssues()
        panel.output.put(("live_event", {"kind": "connection", "text": "连接直播间超时，请检查网络",
            "data": {"state": "error", "connector_epoch": 1}}))
        panel._poll()
        self.assertEqual(panel.connection_state, "error")
        self.assertIn("超时", panel.monitor_issues.active["直播"][0])


if __name__ == "__main__":
    unittest.main()
