import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import diagnostics


class DiagnosticCaptureTests(unittest.TestCase):
    def test_saves_png_and_context_json(self):
        image = np.zeros((120, 80, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as folder, patch.object(
                diagnostics, "current_package", return_value=("cn.jj.chess", "Main")):
            path = diagnostics.capture_diagnostic(
                "emulator-5554", "waiting_next_game",
                extra={"waited_seconds": 15}, image=image, folder=folder)
            self.assertIsNotNone(path)
            png = Path(path)
            metadata = json.loads(png.with_suffix(".json").read_text("utf-8"))
            self.assertTrue(png.is_file())
            self.assertEqual(metadata["reason"], "waiting_next_game")
            self.assertEqual(metadata["package"], "cn.jj.chess")
            self.assertEqual(metadata["image_size"], [80, 120])


if __name__ == "__main__":
    unittest.main()
