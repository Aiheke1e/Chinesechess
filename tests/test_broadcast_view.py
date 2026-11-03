import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from broadcast_view import BroadcastView, BROADCAST_PALETTE
from control_panel import ControlPanel, THEME
from stream_brand import DEFAULT_AVATAR


class BroadcastViewTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.user = tk.StringVar(master=self.root, value="棋友老张")
        self.message = tk.StringVar(master=self.root, value="这匹马能跳过去吗？")

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()

    def test_live_text_stays_in_sync_and_destroy_removes_variable_listeners(self):
        first = BroadcastView(self.root, self.user, self.message, THEME)
        second = BroadcastView(self.root, self.user, self.message, THEME)
        self.assertEqual(len(self.message.trace_info()), 2)
        self.user.set("另一位棋友")
        self.message.set("这次能配合炮将军吗？")
        self.assertEqual(first.comment_text.get("1.0", "end-1c"), "另一位棋友：这次能配合炮将军吗？")
        self.assertEqual(second.comment_text.get("1.0", "end-1c"), "另一位棋友：这次能配合炮将军吗？")
        first.destroy()
        self.assertEqual(len(self.message.trace_info()), 1)
        self.message.set("只更新还存在的展示区")
        self.root.update_idletasks()
        second.destroy()
        self.assertEqual(self.message.trace_info(), [])
        self.assertEqual(self.user.trace_info(), [])

    def test_capture_window_close_and_reopen_does_not_leak_subscriptions(self):
        panel = object.__new__(ControlPanel)
        panel.root = self.root
        panel.interaction_user, panel.interaction_message = self.user, self.message
        panel.broadcast_window = None
        for _ in range(3):
            panel.open_broadcast_window()
            window = panel.broadcast_window
            window.withdraw()
            self.assertEqual(float(window.attributes("-alpha")), 1.0)
            self.assertEqual(len(self.message.trace_info()), 1)
            with patch.object(window, "deiconify"), patch.object(window, "lift"):
                panel.open_broadcast_window()
            self.assertIs(panel.broadcast_window, window)
            panel.close_broadcast_window()
            self.assertIsNone(panel.broadcast_window)
            self.assertEqual(self.message.trace_info(), [])

    def test_area_a_has_a_real_profile_avatar_separate_from_the_viewer_identity(self):
        from PIL import Image
        with Image.open(DEFAULT_AVATAR) as asset:
            self.assertEqual(asset.format, "PNG")
            self.assertEqual(asset.width, asset.height)
            self.assertGreaterEqual(asset.width, 512)
            asset.verify()
        view = BroadcastView(self.root, self.user, self.message, THEME)
        self.assertIsNotNone(view._avatar_source_image)
        self.assertEqual(view.content.winfo_children(), [view.avatar_label, view.comment_text])
        self.assertEqual(view.comment_text.get("1.0", "end-1c"), "棋友老张：这匹马能跳过去吗？")
        view.set_palette(THEME)
        self.assertEqual(view.comment_text.tag_cget("viewer", "foreground"), BROADCAST_PALETTE["jade"])
        self.assertEqual(self.user.get(), "棋友老张")
        self.assertEqual(self.message.get(), "这匹马能跳过去吗？")
        original = view._avatar_source_image
        self.assertFalse(view.set_avatar("missing-avatar-file.png", strict=True))
        self.assertIs(view._avatar_source_image, original)
        view.destroy()

    def test_collapsing_operator_preview_keeps_the_capture_window_in_sync(self):
        panel = object.__new__(ControlPanel)
        panel.root = self.root
        panel.interaction_user, panel.interaction_message = self.user, self.message
        panel.broadcast_window = None
        panel.preview_visible = True
        panel.broadcast_view = BroadcastView(self.root, self.user, self.message, THEME)
        panel.broadcast_view.pack(fill="x")
        panel.controls = tk.Frame(self.root)
        panel.controls.pack(fill="x")
        panel.preview_toggle = Mock()
        panel._schedule_ui_save = Mock()
        panel.open_broadcast_window()
        window = panel.broadcast_window
        window.withdraw()
        panel.toggle_preview()
        self.assertEqual(panel.broadcast_view.winfo_manager(), "")
        self.message.set("后台收起预览，直播画面仍应继续更新。")
        capture = window.winfo_children()[0]
        self.assertEqual(capture.comment_text.get("1.0", "end-1c"), self.user.get() + "：" + self.message.get())
        panel.toggle_preview()
        self.assertEqual(panel.broadcast_view.winfo_manager(), "pack")
        panel.close_broadcast_window()

    def test_popout_uses_saved_size_and_updates_both_views_from_the_same_real_comment(self):
        panel = object.__new__(ControlPanel)
        panel.root = self.root
        panel.interaction_user, panel.interaction_message = self.user, self.message
        panel.broadcast_window = None
        panel.broadcast_width = tk.StringVar(master=self.root, value="510")
        panel.broadcast_height = tk.StringVar(master=self.root, value="190")
        panel.broadcast_sized = True
        panel._schedule_ui_save = Mock()
        with patch("control_panel.chess_window_bounds", return_value=None):
            panel.open_broadcast_window()
        self.root.update_idletasks()
        window = panel.broadcast_window
        capture = window.winfo_children()[0]
        self.assertTrue(window.overrideredirect())
        self.assertEqual(tuple(map(int, window.resizable())), (1, 1))
        self.assertEqual(capture.comment_text.cget("background"), BROADCAST_PALETTE["panel"])
        self.assertFalse(hasattr(capture, "popout_button"))
        self.user.set("直播间收到的真实昵称")
        self.message.set("现在回复的是这条真实弹幕")
        self.assertEqual(capture.comment_text.get("1.0", "end-1c"), self.user.get() + "：" + self.message.get())
        panel.broadcast_width.set("620")
        panel.broadcast_height.set("220")
        panel.apply_broadcast_size()
        self.root.update_idletasks()
        self.assertEqual(panel._broadcast_dimensions(), (620, 220))
        panel.close_broadcast_window()
        with patch("control_panel.chess_window_bounds", return_value=None):
            panel.open_broadcast_window()
        self.root.update_idletasks()
        self.assertEqual(panel._broadcast_dimensions(), (620, 220))
        panel.close_broadcast_window()

    def test_settings_hide_and_reopen_preserve_the_capture_window_and_current_comment(self):
        panel = object.__new__(ControlPanel)
        panel.root = self.root
        panel.interaction_user, panel.interaction_message = self.user, self.message
        panel.broadcast_window = None
        panel.broadcast_visible = tk.BooleanVar(master=self.root, value=False)
        panel.open_broadcast_window()
        window = panel.broadcast_window
        self.root.update()
        window.geometry("510x180+30+40")
        self.root.update_idletasks()
        geometry = window.geometry()
        self.assertTrue(panel.broadcast_visible.get())
        panel.broadcast_visible.set(False)
        panel.toggle_broadcast_window()
        self.assertEqual(window.state(), "withdrawn")
        self.assertIs(panel.broadcast_window, window)
        self.assertEqual(len(self.message.trace_info()), 1)
        self.user.set("真实观众")
        self.message.set("收起期间的新弹幕")
        panel.broadcast_visible.set(True)
        panel.toggle_broadcast_window()
        self.root.update()
        self.assertIs(panel.broadcast_window, window)
        self.assertTrue(window.overrideredirect())
        self.assertEqual(window.geometry(), geometry)
        self.assertEqual(window.winfo_children()[0].comment_text.get("1.0", "end-1c"),
                         "真实观众：收起期间的新弹幕")
        panel.close_broadcast_window()
        self.assertEqual(self.message.trace_info(), [])

    def test_comment_keeps_unicode_nickname_and_empty_state_without_placeholder(self):
        self.user.set("🍊老张")
        view = BroadcastView(self.root, self.user, self.message, THEME)
        self.assertEqual(view.comment_text.get("viewer.first", "viewer.last"), "🍊老张")
        self.user.set("")
        self.message.set("")
        self.assertEqual(view.comment_text.get("1.0", "end-1c"), "")
        view.destroy()

    def test_state_switch_keeps_callback_and_disabled_behavior(self):
        panel = object.__new__(ControlPanel)
        panel.root = self.root
        style = ttk.Style(self.root)
        style.theme_use("clam")
        panel._configure_switch_style(style)
        panel._configure_switch_style(style)
        value = tk.BooleanVar(master=self.root, value=False)
        callback = Mock()
        switch = ttk.Checkbutton(self.root, variable=value, command=callback)
        switch.invoke()
        self.assertTrue(value.get())
        callback.assert_called_once()
        switch.state(["disabled"])
        switch.invoke()
        self.assertTrue(value.get())
        callback.assert_called_once()
        self.assertIn("Switch.indicator", str(style.layout("TCheckbutton")))


if __name__ == "__main__":
    unittest.main()
