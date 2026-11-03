from collections import deque
import queue
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from chat_selection import ChatSelector
from control_panel import ControlPanel
from live_host import LiveController, LiveSettings
from room_feed import room_message_entry


class RoomFeedTests(unittest.TestCase):
    def controller(self):
        controller = object.__new__(LiveController)
        controller.connector_epoch = 2
        controller.enabled = False
        controller.suspended = False
        controller.settings = LiveSettings()
        controller.seen = {}
        controller.chat_selector = ChatSelector()
        controller.pipeline = Mock()
        controller._notify = Mock()
        return controller

    def test_unselected_and_spam_chat_still_appear_in_received_feed(self):
        controller = self.controller()
        event = {"type": "chat", "id": "one", "user": "小王", "text": "666666",
                 "connector_epoch": 2}
        controller.receive(event)
        controller.receive(event)
        controller._notify.assert_called_once_with("room_message", "", event)
        controller.pipeline.submit.assert_not_called()
        self.assertFalse(controller.chat_selector.pending)

    def test_stop_blocks_speech_but_not_incoming_gifts_and_members(self):
        controller = self.controller()
        controller.suspended = True
        for kind in ("gift", "member", "like", "social"):
            controller.receive({"type": kind, "id": kind, "user": "小李", "text": "原始消息"})
        self.assertEqual(controller._notify.call_count, 4)
        controller.pipeline.submit.assert_not_called()
        controller.receive({"type": "chat", "id": "old", "connector_epoch": 1})
        self.assertEqual(controller._notify.call_count, 4)

    def test_entry_preserves_user_content_and_gift_count(self):
        self.assertEqual(room_message_entry({"time": "19:30:01", "data": {
            "type": "chat", "user": "小王", "text": "炮为什么要走这里？😀"}}),
            ("19:30:01", "弹幕", "小王", "炮为什么要走这里？😀"))
        self.assertEqual(room_message_entry({"data": {"type": "gift", "user": "小李",
            "gift": "小心心", "count": 6}})[-1], "送出 小心心 × 6")
        self.assertIsNone(room_message_entry({"data": {"type": "audience", "online": 100}}))

    def test_busy_room_uses_bounded_queue_without_blocking_control_events(self):
        panel = object.__new__(ControlPanel)
        panel.output = queue.Queue()
        panel.room_pending = deque(maxlen=3000)
        panel.room_pending_lock = threading.Lock()
        for number in range(10000):
            panel._receive_live_event({"kind": "room_message", "data": {"id": number}})
        self.assertEqual(len(panel.room_pending), 3000)
        panel._receive_live_event({"kind": "reply", "text": "关键走法"})
        self.assertEqual(panel.output.qsize(), 1)
        self.assertEqual(panel.output.get()[1]["kind"], "reply")


class RoomFeedUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.controller = Mock(enabled=False, connector_epoch=1)
        with patch("control_panel.LiveController", return_value=self.controller), patch(
                "control_panel.ControlPanel.refresh_device"), patch("control_panel.load_settings",
                return_value=LiveSettings(speech_enabled=False)), patch("control_panel.load_ui_settings",
                return_value={"theme": "云白青黛", "opacity": 100, "preview_visible": False}):
            self.panel = ControlPanel(self.root)

    def tearDown(self):
        if hasattr(self, "root"):
            for job in self.root.tk.call("after", "info"):
                self.root.tk.call("after", "cancel", job)
            self.root.destroy()

    def connection(self, epoch=1):
        self.controller.connector_epoch = epoch
        self.panel._receive_live_event({"kind": "connection", "text": "已连接抖音直播间",
            "data": {"state": "connected", "connector_epoch": epoch}})
        self.panel._poll()

    def test_two_pages_and_room_feed_only_reveals_once_per_connection(self):
        self.assertEqual(len(self.panel.pages.tabs()), 2)
        self.assertEqual(self.panel.log_visible, {"chess": True, "live": True, "room": False})
        self.assertEqual(len(self.panel.body.panes()), 2)
        self.connection()
        self.assertEqual(len(self.panel.body.panes()), 3)
        self.panel.toggle_log("room")
        self.connection()
        self.assertFalse(self.panel.log_visible["room"])
        self.connection(2)
        self.assertTrue(self.panel.log_visible["room"])

    def test_hiding_every_log_and_restoring_keeps_its_contents(self):
        self.panel.log.configure(state="normal")
        self.panel.log.insert("end", "本手记录")
        self.panel.log.configure(state="disabled")
        self.panel.toggle_log("chess")
        self.panel.toggle_log("live")
        self.assertEqual(len(self.panel.body.panes()), 0)
        self.panel.toggle_log("chess")
        self.assertEqual(len(self.panel.body.panes()), 1)
        self.assertIn("本手记录", self.panel.log.get("1.0", "end"))

    def test_room_feed_and_all_spoken_replies_stay_in_their_own_logs(self):
        self.connection()
        self.panel.toggle_log("room")
        self.panel._receive_live_event({"kind": "room_message", "time": "19:30:01", "data": {
            "type": "chat", "user": "小王", "text": "炮为什么要走这里？", "connector_epoch": 1}})
        self.panel._receive_live_event({"kind": "room_message", "data": {
            "type": "gift", "user": "小李", "gift": "小心心", "count": 6, "connector_epoch": 1}})
        for kind, text in (("弹幕回复", "小王，这步先看住中路。"), ("礼物答谢", "谢谢小李的小心心。"),
                           ("欢迎观众", "欢迎棋友。"), ("关键走棋解说", "车九平二。")):
            self.panel._receive_live_event({"kind": "reply", "text": text,
                "data": {"kind": kind, "观众": "小王", "互动标识": kind, "弹幕": "炮为什么要走这里？"}})
        self.panel._poll()
        room = self.panel.room_log.get("1.0", "end")
        speech = self.panel.live_log.get("1.0", "end")
        self.assertIn("小王：炮为什么要走这里？", room)
        self.assertIn("送出 小心心 × 6", room)
        self.assertNotIn("这步先看住中路", room)
        self.assertIn("回复观众 · 小王", speech)
        self.assertIn("谢谢小李的小心心。", speech)
        self.assertIn("欢迎棋友。", speech)
        self.assertIn("车九平二。", speech)
        self.assertIn("弹幕：炮为什么要走这里？", speech)
        self.assertEqual(self.panel.interaction_user.get(), "小王")
        self.assertFalse(self.panel.log_visible["room"])
        self.panel._receive_live_event({"kind": "interaction_done", "data": {
            "互动标识": "弹幕回复", "completed": True}})
        self.panel._poll()
        self.assertEqual(self.panel.interaction_user.get(), "等待互动")

    def test_run_switches_are_available_only_on_the_settings_page(self):
        switches = {"自动连局", "解说", "语音"}
        controls = {str(widget.cget("text")) for widget in self.panel._walk_widgets(self.panel.controls)
                    if isinstance(widget, tk.ttk.Checkbutton)}
        settings = {str(widget.cget("text")) for widget in self.panel._walk_widgets(self.panel.live_settings_page)
                    if isinstance(widget, tk.ttk.Checkbutton)}
        self.assertFalse(controls & switches)
        self.assertTrue(switches <= settings)

    def test_all_stateful_options_use_the_same_switch_group(self):
        self.assertEqual(len(self.panel.toggle_widgets), 12)
        for widget in self.panel.toggle_widgets.values():
            self.assertIsInstance(widget, tk.ttk.Checkbutton)
            self.assertIs(widget.master, self.panel.toggle_group)
        actions = [widget for widget in self.panel.settings_actions.winfo_children()
                   if isinstance(widget, tk.ttk.Button)]
        self.assertEqual(set(actions), {self.panel.live_connect_button, self.panel.interaction_button})

    def test_display_switches_follow_actual_visibility_and_external_window_close(self):
        self.panel.preview_toggle.invoke()
        self.assertTrue(self.panel.preview_visible)
        self.assertTrue(self.panel.preview_enabled.get())
        self.panel.preview_toggle.invoke()
        self.assertFalse(self.panel.preview_visible)
        self.assertFalse(self.panel.preview_enabled.get())
        self.panel.toggle_widgets["独立展示"].invoke()
        window = self.panel.broadcast_window
        window.withdraw()
        self.assertTrue(self.panel.broadcast_visible.get())
        self.root.tk.call(window.protocol("WM_DELETE_WINDOW"))
        self.assertFalse(self.panel.broadcast_visible.get())
        self.assertIsNone(self.panel.broadcast_window)
        self.connection()
        self.assertTrue(self.panel.log_vars["room"].get())
        self.panel.toggle_widgets["直播间消息"].invoke()
        self.assertFalse(self.panel.log_vars["room"].get())
        self.assertFalse(self.panel.log_visible["room"])

    def test_shared_interaction_button_pauses_and_resumes_without_starting_a_game(self):
        self.controller.suspended = False
        self.controller.enabled = True
        self.controller.pause.side_effect = lambda: setattr(self.controller, "enabled", False)
        self.controller.enable.side_effect = lambda: setattr(self.controller, "enabled", True)
        with patch.object(self.panel, "save_live_settings", return_value=True), patch.object(
                self.panel, "prepare_and_wait") as start_board:
            self.panel.interaction_button.invoke()
            self.assertEqual(self.panel.interaction_button.cget("text"), "开启互动")
            self.panel.interaction_button.invoke()
            self.assertEqual(self.panel.interaction_button.cget("text"), "暂停互动")
        start_board.assert_not_called()
        self.assertIs(self.panel.interaction_button.master, self.panel.live_connect_button.master)
        self.assertIsNot(self.panel.start_button.master, self.panel.live_connect_button.master)

    def test_top_only_keeps_start_and_pause_and_other_actions_live_in_settings(self):
        top_buttons = {widget for widget in self.panel._walk_widgets(self.panel.controls)
                       if isinstance(widget, (tk.ttk.Button, tk.ttk.Menubutton))}
        self.assertEqual(top_buttons, {self.panel.start_button, self.panel.stop_button})
        self.assertIn("暂停", self.panel.stop_button.cget("text"))
        self.assertEqual([self.panel.pages.tab(tab, "text").strip() for tab in self.panel.pages.tabs()],
                         ["工作台", "设置"])
        settings = set(self.panel._walk_widgets(self.panel.live_settings_page))
        for button in (self.panel.live_connect_button, self.panel.interaction_button,
                       self.panel.preview_toggle, *(button for button, _ in self.panel.log_buttons.values())):
            self.assertIn(button, settings)

    def test_switching_soft_theme_keeps_log_content_and_cleans_up_tk_callbacks(self):
        errors = []
        self.root.report_callback_exception = lambda *args: errors.append(args)
        self.panel.append_speech({"kind": "reply", "text": "当前台词。", "data": {"kind": "弹幕回复"}})
        for theme in ("雾蓝柔光", "午夜蓝", "云白青黛"):
            self.panel.theme_name.set(theme)
            self.root.update_idletasks()
        self.assertIn("当前台词。", self.panel.live_log.get("1.0", "end"))
        self.panel.broadcast_view.destroy()
        self.root.update_idletasks()
        self.assertFalse(errors)

    def test_enabling_interaction_after_stop_displays_replies_without_reviving_board_workers(self):
        canceled = self.panel.task_cancel
        canceled.set()
        with patch.object(self.panel, "save_live_settings", return_value=True), patch.object(
                self.panel, "prepare_and_wait") as start_board:
            self.panel.enable_live()
        self.assertTrue(canceled.is_set())
        self.assertIsNot(self.panel.task_cancel, canceled)
        self.assertFalse(self.panel.task_cancel.is_set())
        start_board.assert_not_called()
        self.panel._receive_live_event({"kind": "reply", "text": "小王，晚上好。", "data": {
            "kind": "弹幕回复", "观众": "小王", "弹幕": "主播晚上好", "互动标识": "resumed"}})
        self.panel._poll()
        self.assertIn("小王，晚上好。", self.panel.live_log.get("1.0", "end"))
        self.assertEqual(self.panel.interaction_user.get(), "小王")

    def test_previous_room_messages_never_leak_into_the_current_room(self):
        self.connection(2)
        self.panel._receive_live_event({"kind": "room_message", "data": {
            "type": "chat", "user": "旧观众", "text": "旧房间消息", "connector_epoch": 1}})
        self.panel._receive_live_event({"kind": "room_message", "data": {
            "type": "chat", "user": "新观众", "text": "新房间消息", "connector_epoch": 2}})
        self.panel._poll()
        text = self.panel.room_log.get("1.0", "end")
        self.assertNotIn("旧房间消息", text)
        self.assertIn("新房间消息", text)


if __name__ == "__main__":
    unittest.main()
