import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from broadcast_view import BroadcastView
from control_panel import ControlPanel, THEME


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
        self.assertEqual(first.user_label.cget("text"), "另一位棋友")
        self.assertEqual(second.message_label.cget("text"), "这次能配合炮将军吗？")
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
            self.root.tk.call(window.protocol("WM_DELETE_WINDOW"))
            self.assertIsNone(panel.broadcast_window)
            self.assertEqual(self.message.trace_info(), [])

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
