from collections import deque
import queue
import threading
import tempfile
import shutil
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from chat_selection import ChatSelector
from control_panel import ControlPanel
from live_host import LiveController, LiveSettings
from room_feed import room_message_entry
from stream_brand import AREA_A_TITLE, DEFAULT_AVATAR, AVATAR_PRESETS


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

    def test_long_real_nickname_survives_the_feed_and_selected_reply(self):
        controller = self.controller()
        controller.enabled = True
        controller.chat_selector.clock = lambda: 100
        controller.chat_stats_at = 0
        nickname = "这个昵称来自直播间并且需要完整保留" * 3
        event = {"type": "chat", "id": "real-chat", "user": nickname,
                 "user_id": "12345", "text": "主播晚上好", "connector_epoch": 2}
        controller.receive(event)
        self.assertEqual(controller._notify.call_args.args[2]["user"], nickname)
        controller.chat_selector.clock = lambda: 102
        controller._select_chat()
        self.assertEqual(controller.pipeline.submit.call_args.args[2]["观众"], nickname)

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
        self.assertEqual(self.panel.logs_empty.winfo_manager(), "pack")
        self.panel.toggle_log("chess")
        self.assertEqual(len(self.panel.body.panes()), 1)
        self.assertEqual(self.panel.logs_empty.winfo_manager(), "")
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
        self.assertEqual(self.panel.interaction_user.get(), "小王")
        self.assertEqual(self.panel.interaction_message.get(), "炮为什么要走这里？")

    def test_run_switches_are_available_only_on_the_settings_page(self):
        switches = {"自动连局", "解说", "语音"}
        controls = {str(widget.cget("text")) for widget in self.panel._walk_widgets(self.panel.controls)
                    if isinstance(widget, tk.ttk.Checkbutton)}
        settings = {str(widget.cget("text")) for widget in self.panel._walk_widgets(self.panel.live_settings_page)
                    if isinstance(widget, tk.ttk.Checkbutton)}
        self.assertFalse(controls & switches)
        self.assertTrue(switches <= settings)

    def test_broadcast_is_blank_until_reply_and_keeps_it_until_the_next_actual_reply(self):
        self.assertEqual(self.panel.interaction_user.get(), "")
        self.assertEqual(self.panel.interaction_message.get(), "")
        def event(kind, data, text="回复台词。"):
            self.panel._receive_live_event({"kind": kind, "text": text, "data": data})
        first = {"kind": "弹幕回复", "观众": "小王", "弹幕": "这匹马能跳过去吗？", "互动标识": "first"}
        second = {"kind": "弹幕回复", "观众": "小李", "弹幕": "主播晚上好", "互动标识": "second"}
        event("reply", first)
        event("interaction_done", {"互动标识": "first", "completed": True})
        event("queued", second)
        event("speech_status", second, "正在生成百炼语音")
        event("reply", {"kind": "欢迎观众", "观众": "新棋友"}, "欢迎新棋友进入直播间。")
        self.panel._poll()
        self.assertEqual(self.panel.interaction_user.get(), "小王")
        self.assertEqual(self.panel.interaction_message.get(), "这匹马能跳过去吗？")
        event("reply", second)
        event("interaction_done", {"互动标识": "first", "completed": False})
        self.panel._poll()
        self.assertEqual(self.panel.interaction_user.get(), "小李")
        self.assertEqual(self.panel.interaction_message.get(), "主播晚上好")
        self.panel.pause_live()
        self.assertEqual(self.panel.interaction_user.get(), "")
        self.assertEqual(self.panel.interaction_message.get(), "")

    def test_canceled_reply_clears_the_display_without_placeholder_words(self):
        self.panel._receive_live_event({"kind": "reply", "text": "回复台词。", "data": {
            "kind": "弹幕回复", "观众": "小王", "弹幕": "主播晚上好", "互动标识": "first"}})
        self.panel._receive_live_event({"kind": "interaction_done", "data": {
            "互动标识": "first", "completed": False}})
        self.panel._poll()
        self.assertEqual(self.panel.interaction_user.get(), "")
        self.assertEqual(self.panel.interaction_message.get(), "")

    def test_all_stateful_options_use_the_same_switch_group(self):
        self.assertEqual(len(self.panel.toggle_widgets), 10)
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
        self.panel.toggle_widgets["独立窗口"].invoke()
        window = self.panel.broadcast_window
        window.withdraw()
        self.assertTrue(self.panel.broadcast_visible.get())
        self.root.tk.call(window.protocol("WM_DELETE_WINDOW"))
        self.assertFalse(self.panel.broadcast_visible.get())
        self.assertIs(self.panel.broadcast_window, window)
        self.assertEqual(window.state(), "withdrawn")
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
            self.assertEqual(self.panel.interaction_button.cget("text"), "开启弹幕互动")
            self.panel.interaction_button.invoke()
            self.assertEqual(self.panel.interaction_button.cget("text"), "暂停弹幕互动")
        start_board.assert_not_called()
        self.assertIs(self.panel.interaction_button.master, self.panel.live_connect_button.master)
        self.assertIsNot(self.panel.start_button.master, self.panel.live_connect_button.master)

    def test_top_only_keeps_start_and_pause_and_other_actions_live_in_settings(self):
        top_buttons = {widget for widget in self.panel._walk_widgets(self.panel.controls)
                       if isinstance(widget, (tk.ttk.Button, tk.ttk.Menubutton))}
        self.assertEqual(top_buttons, {self.panel.start_button, self.panel.stop_button})
        self.assertIn("开始下棋", self.panel.start_button.cget("text"))
        self.assertEqual(self.panel.stop_button.cget("text"), "暂停下棋")
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
        for theme in ("清晰蓝白", "午夜蓝", "云白青黛"):
            self.panel.theme_name.set(theme)
            self.root.update_idletasks()
        self.assertIn("当前台词。", self.panel.live_log.get("1.0", "end"))
        self.panel.broadcast_view.destroy()
        self.root.update_idletasks()
        self.assertFalse(errors)

    def test_settings_control_both_pages_preview_without_hiding_the_capture_window(self):
        self.panel.preview_toggle.invoke()
        self.panel.open_broadcast_window()
        window = self.panel.broadcast_window
        self.panel.interaction_user.set("真实观众")
        self.panel.interaction_message.set("原始弹幕")
        self.panel.pages.select(1)
        self.root.update()
        self.assertEqual(self.panel.broadcast_view.winfo_manager(), "pack")
        self.assertTrue(self.panel.preview_visible)
        self.assertTrue(self.panel.preview_enabled.get())
        self.assertEqual(window.state(), "normal")
        self.panel.preview_toggle.invoke()
        self.assertEqual(self.panel.broadcast_view.winfo_manager(), "")
        self.assertFalse(self.panel.preview_enabled.get())
        self.panel.pages.select(0)
        self.root.update()
        self.assertEqual(self.panel.broadcast_view.winfo_manager(), "")
        self.panel.pages.select(1)
        self.panel.preview_toggle.invoke()
        self.assertEqual(self.panel.broadcast_view.winfo_manager(), "pack")
        self.assertIs(self.panel.broadcast_window, window)
        self.assertEqual(self.panel.interaction_user.get(), "真实观众")
        self.assertEqual(self.panel.interaction_message.get(), "原始弹幕")
        self.panel.toggle_widgets["独立窗口"].invoke()
        self.assertEqual(window.state(), "withdrawn")
        self.panel.pages.select(1)
        self.panel.toggle_widgets["独立窗口"].invoke()
        self.assertIs(self.panel.broadcast_window, window)
        self.assertEqual(window.state(), "normal")

    def test_enabling_interaction_after_stop_displays_replies_without_reviving_board_workers(self):
        canceled = self.panel.task_cancel
        canceled.set()
        with patch.object(self.panel, "save_live_settings", return_value=True), patch.object(
                self.panel, "prepare_and_wait") as start_board:
            self.panel.enable_live()
        self.assertTrue(canceled.is_set())
        self.assertIs(self.panel.task_cancel, canceled)
        self.assertTrue(self.panel.task_cancel.is_set())
        start_board.assert_not_called()
        self.panel._receive_live_event({"kind": "reply", "text": "小王，晚上好。", "data": {
            "kind": "弹幕回复", "观众": "小王", "弹幕": "主播晚上好", "互动标识": "resumed"}})
        self.panel._poll()
        self.assertIn("小王，晚上好。", self.panel.live_log.get("1.0", "end"))
        self.assertEqual(self.panel.interaction_user.get(), "小王")

    def test_pausing_chess_keeps_interaction_and_current_display(self):
        self.controller.enabled = True
        self.panel.interaction_user.set("小王")
        self.panel.interaction_message.set("主播晚上好")
        self.panel._sync_interaction_button()
        with tempfile.TemporaryDirectory() as folder, patch(
                "control_panel.STOP_FILE", str(Path(folder) / "stop.request")):
            self.panel.stop_button.invoke()
        self.controller.stop_chess_tasks.assert_called_once()
        self.controller.pause.assert_not_called()
        self.controller.disconnect.assert_not_called()
        self.assertEqual(self.panel.interaction_button.cget("text"), "暂停弹幕互动")
        self.assertEqual(self.panel.interaction_user.get(), "小王")
        self.assertEqual(self.panel.interaction_message.get(), "主播晚上好")
        self.assertTrue(self.panel.task_cancel.is_set())

    def test_voice_switch_does_not_start_interaction_or_clear_board_pause(self):
        self.panel.task_cancel.set()
        canceled = self.panel.task_cancel
        self.controller.reset_mock()
        with patch.object(self.panel, "save_live_settings", return_value=True):
            self.panel.live_speech.set(True)
            self.panel.toggle_live_speech()
        self.assertIs(self.panel.task_cancel, canceled)
        self.assertTrue(canceled.is_set())
        self.controller.enable.assert_not_called()
        self.controller.resume_chess_tasks.assert_not_called()

    def test_pausing_interaction_keeps_the_active_chess_session(self):
        process = Mock()
        process.poll.return_value = None
        self.panel.process = process
        self.panel.auto_retry = True
        self.panel.waiting_for_board = True
        canceled = self.panel.task_cancel
        self.panel.pause_live()
        self.controller.pause.assert_called_once()
        self.controller.stop_chess_tasks.assert_not_called()
        self.assertIs(self.panel.process, process)
        self.assertIs(self.panel.task_cancel, canceled)
        self.assertFalse(canceled.is_set())
        self.assertTrue(self.panel.auto_retry)
        self.assertTrue(self.panel.waiting_for_board)

    def test_starting_chess_does_not_enable_interaction(self):
        self.controller.enabled = False
        self.controller.reset_mock()
        with patch("control_panel.threading.Thread"):
            self.panel.prepare_and_wait()
        self.controller.resume_chess_tasks.assert_called_once()
        self.controller.enable.assert_not_called()
        self.assertFalse(self.controller.enabled)

    def test_late_spoken_event_does_not_restore_the_display_after_pausing_interaction(self):
        self.controller.pipeline.chat_epoch = 2
        self.panel.pause_live()
        self.panel._receive_live_event({"kind": "reply", "text": "小王，晚上好。", "data": {
            "kind": "弹幕回复", "观众": "小王", "弹幕": "晚上好", "互动标识": "old", "_chat_epoch": 1}})
        self.panel._poll()
        self.assertEqual(self.panel.interaction_user.get(), "")
        self.assertEqual(self.panel.interaction_message.get(), "")
        self.assertIn("小王，晚上好。", self.panel.live_log.get("1.0", "end"))

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

    def test_test_input_cannot_replace_a_real_comment_or_enter_the_spoken_log(self):
        for user, message, is_test in (("真实昵称", "主播晚上好", False), ("模拟观众", "测试台词", True)):
            self.panel._receive_live_event({"kind": "reply", "text": message, "data": {
                "kind": "弹幕回复", "观众": user, "弹幕": message,
                "测试输入": is_test, "互动标识": user}})
        self.panel._poll()
        self.assertEqual(self.panel.interaction_user.get(), "真实昵称")
        self.assertEqual(self.panel.interaction_message.get(), "主播晚上好")
        self.assertNotIn("模拟观众", self.panel.live_log.get("1.0", "end"))
        self.assertIsNone(self.panel._speech_log_entry({"kind": "reply", "text": "测试", "data": {"测试输入": True}}))

    def test_replacing_area_a_avatar_keeps_comment_and_popout_geometry(self):
        self.panel.open_broadcast_window()
        window = self.panel.broadcast_window
        window.withdraw()
        self.root.update_idletasks()
        self.assertEqual(window.title(), AREA_A_TITLE)
        self.panel.interaction_user.set("直播间的真实昵称")
        self.panel.interaction_message.set("这条弹幕要保持原样")
        geometry = window.geometry()
        with tempfile.TemporaryDirectory() as folder:
            new_avatar = str(Path(folder) / "custom-avatar.png")
            shutil.copyfile(DEFAULT_AVATAR, new_avatar)
            uploaded = Path(folder) / "saved-avatars"
            with patch("avatar_picker.UPLOAD_DIRECTORY", uploaded), patch(
                    "control_panel.filedialog.askopenfilename", return_value=new_avatar):
                self.panel.choose_area_a_avatar()
            saved = Path(self.panel.area_a_avatar_path)
            self.assertEqual(saved.parent, uploaded)
            self.assertEqual(saved.read_bytes(), Path(new_avatar).read_bytes())
            Path(new_avatar).unlink()
            self.assertTrue(saved.is_file())
            capture = window.winfo_children()[0]
            self.assertIs(capture._avatar_source_image, self.panel.broadcast_view._avatar_source_image)
            self.assertEqual(window.geometry(), geometry)
            self.assertEqual(self.panel.interaction_user.get(), "直播间的真实昵称")
            self.assertEqual(self.panel.interaction_message.get(), "这条弹幕要保持原样")
            self.panel.reset_area_a_avatar()
            self.assertEqual(self.panel.area_a_avatar_path, "")
            self.assertEqual(window.geometry(), geometry)

    def test_all_built_in_avatars_can_be_selected_without_changing_current_comment(self):
        self.panel.interaction_user.set("真实观众")
        self.panel.interaction_message.set("请保留这条原始弹幕")
        self.panel.avatar_presets_button.invoke()
        picker = self.panel.avatar_picker_window
        self.assertEqual(len(AVATAR_PRESETS), 10)
        self.assertEqual(len(picker._buttons), len(AVATAR_PRESETS))
        for (button, preset), (_, expected) in zip(picker._buttons, AVATAR_PRESETS):
            self.assertEqual(preset, expected)
            button.invoke()
            self.assertEqual(self.panel.area_a_avatar_path, str(expected))
            self.assertEqual(self.panel.interaction_user.get(), "真实观众")
            self.assertEqual(self.panel.interaction_message.get(), "请保留这条原始弹幕")
        picker.destroy()

    def test_canceling_or_rejecting_upload_preserves_current_avatar(self):
        original = self.panel.broadcast_view._avatar_source_image
        with patch("control_panel.filedialog.askopenfilename", return_value=""):
            self.panel.avatar_upload_button.invoke()
        with patch("control_panel.filedialog.askopenfilename", return_value="missing.png"), patch(
                "control_panel.messagebox.showerror") as error:
            self.panel.avatar_upload_button.invoke()
            error.assert_called_once()
        self.assertIs(self.panel.broadcast_view._avatar_source_image, original)

    def test_custom_background_and_transparency_persist_without_changing_window_opacity(self):
        import control_panel
        from app_wallpaper import DEFAULT_BACKGROUND
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "selected.png"
            shutil.copyfile(DEFAULT_BACKGROUND, source)
            uploads = Path(folder) / "backgrounds"
            with patch("control_panel.filedialog.askopenfilename", return_value=str(source)), patch(
                    "control_panel.BACKGROUND_DIRECTORY", uploads):
                self.panel.background_upload_button.invoke()
            selected = Path(self.panel.background_image_path)
            self.assertEqual(selected.parent, uploads)
            source.unlink()
            self.assertTrue(selected.is_file())
            self.panel.background_transparency.set(65)
            self.panel.change_background_transparency()
            self.assertEqual(self.panel.wallpaper.transparency, 65)

            self.assertEqual(float(self.root.attributes("-alpha")), 1.0)
            with patch("control_panel.UI_CFG", str(Path(folder) / "ui.json")):
                self.panel._save_ui_settings()
                restored = control_panel.load_ui_settings()
            self.assertEqual(restored["background_image_path"], str(selected))
            self.assertEqual(restored["background_transparency"], 65)
            self.panel.reset_background_image()
            self.assertEqual(self.panel.background_image_path, "")
            self.assertEqual(self.panel.wallpaper.transparency, 65)

    def test_wallpaper_covers_controls_settings_and_all_three_logs(self):
        surfaces = self.panel.wallpaper._surfaces
        self.assertIn(self.panel.controls, surfaces)
        self.assertIn(self.panel.settings_form, surfaces)
        for pane in self.panel.log_panels.values():
            self.assertIn(pane.content, surfaces)
            self.assertEqual(pane.canvas.winfo_manager(), "")
        for text in (self.panel.log, self.panel.live_log, self.panel.room_log, self.panel.persona_text):
            self.assertIn(text._display, surfaces)
        self.assertNotIn(self.panel.broadcast_view, surfaces)


if __name__ == "__main__":
    unittest.main()
