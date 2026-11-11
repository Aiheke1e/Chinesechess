import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from events import SpeechLog
from live_host import LiveController


class LiveLogRetentionTests(unittest.TestCase):
    def test_statistics_keep_refreshing_ui_without_overwriting_speech_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            controller = object.__new__(LiveController)
            controller.log_lock = threading.Lock()
            controller.last_stats_logged = -1e9
            controller.speech_log = SpeechLog(str(path / "speech.jsonl"))
            controller.notify = Mock()
            now = [0.]
            with patch("live_host.LIVE_LOG", path / "live.jsonl"), \
                 patch("live_host.time.monotonic", side_effect=lambda: now[0]):
                for number in range(121):
                    now[0] = float(number)
                    controller._notify("room_stats", "", {"online": number})
                controller._notify("reply", "棋友们，新局开张！", {"kind": "新局开场", "_speech_mode": "text"})
            self.assertEqual(controller.notify.call_count, 122)
            rows = [json.loads(line) for line in (path / "live.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([r["data"]["online"] for r in rows if r["kind"] == "room_stats"], [0, 60, 120])
            self.assertTrue(all("T" in r["timestamp"] for r in rows))
            compact = json.loads((path / "speech.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(compact["text"], "棋友们，新局开张！")
            self.assertEqual(compact["mode"], "text")
