import tempfile
import time
import unittest
from pathlib import Path

from runtime_status import (heartbeat_age, read_runtime_status, rotate_log,
                            write_runtime_status)


class RuntimeStatusTests(unittest.TestCase):
    def test_round_trip_heartbeat(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "runtime.json")
            self.assertTrue(write_runtime_status(
                "waiting_opponent", "等待落子", details={"moves": 3}, path=path))
            payload = read_runtime_status(path)
            self.assertEqual(payload["stage"], "waiting_opponent")
            self.assertEqual(payload["details"]["moves"], 3)
            self.assertLess(heartbeat_age(payload), 2)

    def test_invalid_status_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime.json"
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(read_runtime_status(str(path)))

    def test_rotates_only_when_limit_is_reached(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "panel.log"
            path.write_text("123456", encoding="utf-8")
            self.assertTrue(rotate_log(str(path), max_bytes=5, backups=2))
            self.assertFalse(path.exists())
            self.assertEqual((Path(str(path) + ".1")).read_text("utf-8"), "123456")


if __name__ == "__main__":
    unittest.main()
