import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cache_cleanup


class CacheCleanupTests(unittest.TestCase):
    def test_deletes_only_scoped_generated_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            work = root / "work"
            diagnostics = work / "diagnostics"
            audio = root / "audio"
            diagnostics.mkdir(parents=True)
            audio.mkdir()
            picture = diagnostics / "failure.png"
            metadata = diagnostics / "failure.json"
            debug = work / "debug.png"
            speech = audio / "speech_123.wav"
            session = work / "session_state.json"
            secret = root / "secret.dpapi"
            for path in (picture, metadata, debug, speech, session, secret):
                path.write_bytes(b"1234")
            with (mock.patch.object(cache_cleanup, "WORK", work),
                  mock.patch.object(cache_cleanup, "DIAGNOSTICS", diagnostics),
                  mock.patch.object(cache_cleanup, "TTS_CACHE", audio)):
                scan = cache_cleanup.scan_generated_cache()
                result = cache_cleanup.cleanup_generated_cache(scan["files"])
            self.assertEqual(result["deleted"], 4)
            self.assertFalse(picture.exists())
            self.assertFalse(metadata.exists())
            self.assertFalse(debug.exists())
            self.assertFalse(speech.exists())
            self.assertTrue(session.exists())
            self.assertTrue(secret.exists())

    def test_rejects_arbitrary_path_even_if_caller_supplies_it(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "keep.txt"
            target.write_text("keep", encoding="utf-8")
            result = cache_cleanup.cleanup_generated_cache([target])
            self.assertEqual(result["deleted"], 0)
            self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
