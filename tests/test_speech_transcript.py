import queue
import threading
import unittest
from unittest.mock import Mock

from control_panel import ControlPanel


class SpeechTranscriptTests(unittest.TestCase):
    def test_only_spoken_reply_events_form_transcript_entries(self):
        for kind in ("chess", "chess_note", "queued", "speech_status", "status",
                     "connection", "error", "chat", "gift", "member"):
            self.assertIsNone(ControlPanel._speech_log_entry({
                "kind": kind, "text": "分析或系统信息"}))

    def test_body_is_exact_speech_and_header_uses_its_original_move(self):
        entry = ControlPanel._speech_log_entry({
            "kind": "reply", "time": "09:08:03", "text": "炮五进四，吃掉一枚马。",
            "data": {"kind": "关键走棋解说", "解说对应手数": 10,
                     "解说对应走法": "黑方 炮5进4，吃马",
                     "局势分析": "深度17，优势6.62", "候选方案": ["其他路线"]},
        })
        header, body = entry
        self.assertIn("09:08:03", header)
        self.assertIn("第10手", header)
        self.assertIn("黑方 炮5进4，吃马", header)
        self.assertEqual(body, "炮五进四，吃掉一枚马。")
        self.assertNotIn("深度", str(entry))
        self.assertNotIn("其他路线", str(entry))

    def test_viewer_reply_never_inherits_a_chess_move(self):
        header, body = ControlPanel._speech_log_entry({
            "kind": "reply", "text": "谢谢棋友的小心心！", "data": {"kind": "礼物答谢"}})
        self.assertEqual(header, "礼物答谢")
        self.assertEqual(body, "谢谢棋友的小心心！")

    def test_queue_routes_only_reply_to_transcript_and_keeps_status_separate(self):
        panel = object.__new__(ControlPanel)
        panel.task_cancel = threading.Event()
        panel.output = queue.Queue()
        panel.root = Mock()
        panel.speech_status = Mock()
        panel.live_status = Mock()
        panel.append_speech = Mock()
        panel.append_live = Mock()
        for kind in ("chess", "chess_note", "status", "chat", "gift", "queued",
                     "speech_status", "connection", "error", "reply"):
            panel.output.put(("live_event", {"kind": kind, "text": kind}))
        panel._poll()
        panel.append_speech.assert_called_once_with({"kind": "reply", "text": "reply"})
        panel.append_live.assert_not_called()
        panel.live_status.set.assert_called_once_with("connection")
        panel.speech_status.set.assert_any_call("speech_status")


if __name__ == "__main__":
    unittest.main()
