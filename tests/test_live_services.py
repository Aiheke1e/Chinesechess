import struct
import tempfile
import unittest
from pathlib import Path

from bailian_tts import available_voices, repair_streaming_wav_header
from glm_client import SYSTEM_PROMPT
from live_host import load_settings


class LiveServiceTests(unittest.TestCase):
    def test_configured_bailian_voice_is_available(self):
        self.assertIn("Maia", available_voices())
        self.assertIn("longanyang", available_voices())

    def test_commentary_and_sandtable_are_enabled_by_default(self):
        settings = load_settings()
        self.assertTrue(settings.commentary_enabled)
        self.assertTrue(settings.sandtable_enabled)

    def test_prompt_prevents_technical_or_invented_commentary(self):
        self.assertIn("绝不编造", SYSTEM_PROMPT)
        self.assertIn("不说AI", SYSTEM_PROMPT)
        self.assertIn("坐标", SYSTEM_PROMPT)
        self.assertIn("候选", SYSTEM_PROMPT)
        self.assertIn("不要句句以", SYSTEM_PROMPT)
        self.assertIn("跳马、拱兵、出车、架炮", SYSTEM_PROMPT)

    def test_repairs_streaming_wav_size_markers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stream.wav"
            header = (b"RIFF" + struct.pack("<I", 0x7FFFFFFF) + b"WAVE" +
                      b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000,
                                             48000, 2, 16) +
                      b"data" + struct.pack("<I", 0x7FFFFFFF))
            path.write_bytes(header + b"\0" * 48000)
            repair_streaming_wav_header(path)
            repaired = path.read_bytes()[:44]
            self.assertEqual(struct.unpack_from("<I", repaired, 4)[0], 48036)
            self.assertEqual(struct.unpack_from("<I", repaired, 40)[0], 48000)


if __name__ == "__main__":
    unittest.main()
