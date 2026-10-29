import unittest

from health_check import collect_readiness, readiness_level


class HealthCheckTests(unittest.TestCase):
    def test_ready_local_stack_is_green(self):
        items = collect_readiness(
            {"commentary_enabled": True, "speech_enabled": True,
             "voice": "Maia", "room": "123456789"},
            device_provider=lambda: ["emulator-5554"],
            package_provider=lambda device: ("cn.jj.chess", "Main"),
            resolution_provider=lambda device: (1080, 1920),
            engine_path=__file__, tts_config_path=__file__,
            llm_key_loader=lambda: "key",
            tts_key_loader=lambda: "key", voice_loader=lambda: ["Maia"])
        self.assertEqual(readiness_level(items), "ok")

    def test_missing_device_is_error_and_optional_live_parts_warn(self):
        items = collect_readiness(
            {"commentary_enabled": False, "speech_enabled": False,
             "voice": "Maia", "room": ""},
            device_provider=lambda: [], engine_path=__file__)
        self.assertEqual(readiness_level(items), "error")
        self.assertTrue(any(item["key"] == "room" and item["level"] == "warn"
                            for item in items))


if __name__ == "__main__":
    unittest.main()
