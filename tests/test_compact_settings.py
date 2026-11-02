import unittest
from unittest.mock import Mock, patch

from control_panel import ControlPanel
from live_host import LiveController, LiveSettings


class CompactSettingsTests(unittest.TestCase):
    def test_saving_visible_settings_preserves_removed_advanced_values(self):
        controller = object.__new__(LiveController)
        controller.settings = LiveSettings(introduction="原来的开场词", cooldown=4.5)
        controller.pipeline = Mock()
        controller._notify = Mock()
        controller._close_sandtable = Mock()
        panel = object.__new__(ControlPanel)
        panel.live_controller = controller
        panel.append_live = Mock()
        for field, value in {
            "live_room": "123456789", "live_model": "glm-4-flash", "live_voice": "Maia",
            "live_density": "自然", "live_welcome": True, "live_gifts": True,
            "live_speech": False, "live_commentary": True, "live_sandtable": True,
            "live_queen_commentary": True,
        }.items():
            setattr(panel, field, Mock(get=Mock(return_value=value)))
        panel.persona_text = Mock(get=Mock(return_value="讲棋简洁，和棋友轻松聊天。"))
        with patch("live_host.save_settings") as save, patch("control_panel.messagebox.showerror") as error:
            self.assertTrue(panel.save_live_settings(quiet=True))
            error.assert_not_called()
        saved = save.call_args.args[0]
        self.assertEqual(saved.persona, "讲棋简洁，和棋友轻松聊天。")
        self.assertEqual(saved.introduction, "原来的开场词")
        self.assertEqual(saved.cooldown, 4.5)
        self.assertEqual(saved.room, "123456789")


if __name__ == "__main__":
    unittest.main()
