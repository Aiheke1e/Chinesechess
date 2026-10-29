import json
import os
import tempfile
import unittest

from events import EventLog


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


if __name__ == "__main__":
    unittest.main()
