import json
import os
import tempfile
import unittest

from events import EventLog, SpeechLog


class EventLogTests(unittest.TestCase):
    def test_writes_utf8_json_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "nested", "events.jsonl")
            log = EventLog(path)
            log.emit("move", notation="炮二平五", ucci="h2e2")
            with open(path, encoding="utf-8") as stream:
                record = json.loads(stream.readline())
            self.assertEqual(record["event"], "move")
            self.assertEqual(record["notation"], "炮二平五")
            self.assertEqual(record["ucci"], "h2e2")
            self.assertIn("time", record)

    def test_disabled_log_does_nothing(self):
        EventLog(None).emit("ignored")

    def test_rotates_long_event_stream(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "events.jsonl")
            log = EventLog(path, max_bytes=80, backups=1)
            for number in range(8):
                log.emit("move", number=number, notation="炮二平五")
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(os.path.isfile(path + ".1"))

    def test_compact_speech_evidence_excludes_analysis_and_distinguishes_text_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "speech.jsonl")
            log = SpeechLog(path)
            payload = {"kind": "沙盘推演解说", "speech_token": "demo:1", "沙盘步骤": "1",
                       "_speech_mode": "text", "局势分析": "不保留分析", "cookie": "不保留凭据"}
            log.record("queued", "还没播", payload)
            log.record("reply", "咱们车九平八。", payload)
            log.record("reply", "炮五进四。", {"kind": "关键走棋解说", "_speech_mode": "audio"})
            log.record("speech_interrupted", "播报已中断", {"kind": "关键走棋解说", "reason": "手动暂停", "elapsed": 1.2})
            with open(path, encoding="utf-8") as stream:
                rows = [json.loads(line) for line in stream]
            self.assertEqual([r["event"] for r in rows], ["reply", "reply", "speech_interrupted"])
            self.assertEqual([r["mode"] for r in rows], ["text", "audio", "audio"])
            self.assertEqual(rows[0]["text"], "咱们车九平八。")
            self.assertEqual(rows[0]["沙盘步骤"], "1")
            self.assertIn("T", rows[0]["time"])
            self.assertNotIn("不保留", str(rows))
            self.assertEqual(rows[-1]["reason"], "手动暂停")


if __name__ == "__main__":
    unittest.main()
