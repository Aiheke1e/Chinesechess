import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import player


class SpeechSyncTests(unittest.TestCase):
    def _config(self, folder, enabled=True):
        path = Path(folder) / "live_config.json"
        path.write_text(json.dumps({
            "commentary_enabled": True,
            "speech_enabled": enabled,
        }), encoding="utf-8")
        return str(path)

    def test_waits_until_matching_audio_really_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self._config(folder)
            markers = [
                {"token": "move-1", "enabled": True, "deferred": False},
                {"token": "old"},
                {"token": "move-1"},
            ]
            with patch.object(player, "LIVE_CFG_PATH", config), \
                 patch.object(player, "_read_speech_marker",
                              side_effect=markers), \
                 patch.object(player.time, "sleep"):
                self.assertTrue(player.wait_for_speech_start(
                    "move-1", plan_timeout=0.1, audio_timeout=0.1))

    def test_sandbox_speech_never_delays_live_move(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self._config(folder)
            with patch.object(player, "LIVE_CFG_PATH", config), \
                 patch.object(player, "_read_speech_marker", return_value={
                     "token": "sand-1", "enabled": True, "deferred": True}):
                self.assertFalse(player.wait_for_speech_start(
                    "sand-1", plan_timeout=0.01, audio_timeout=0.01))


if __name__ == "__main__":
    unittest.main()
