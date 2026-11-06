import io
import json
import queue
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import douyin_bridge
from control_panel import ControlPanel
from live_host import LiveController, LiveSettings, normalize_room, room_input
from health_check import collect_readiness


SHARE = ("0- #在抖音，记录美好生活#【陕西棋王】正在直播，来和我一起支持Ta吧。"
         "复制下方链接，打开【抖音】，直接观看直播！ "
         "https://v.douyin.com/4MvomQ_qpB0/ 7@7.com :1pm")


class LiveConnectionTests(unittest.TestCase):
    def panel(self):
        panel = object.__new__(ControlPanel)
        panel.live_controller = Mock()
        panel.live_room = Mock(get=Mock(return_value=SHARE))
        panel.live_status = Mock()
        panel.live_connect_button = Mock()
        panel.save_live_settings = Mock(return_value=True)
        panel.append_live = Mock()
        panel.output = queue.Queue()
        panel.root = Mock()
        panel.task_cancel = threading.Event()
        panel.closing = False
        return panel

    def controller(self):
        controller = object.__new__(LiveController)
        controller.connector = None
        controller.connector_epoch = 0
        controller.connector_lock = threading.RLock()
        controller.settings = LiveSettings()
        controller._notify = Mock()
        controller.receive = Mock()
        return controller

    def test_exact_user_share_text_resolves_to_web_room(self):
        response = Mock()
        response.url = "https://webcast.amemv.com/douyin/webcast/reflow/7703461520374909722"
        response.read.return_value = b'{"webRid\\":\\"216845906630\\"}'
        context = Mock(__enter__=Mock(return_value=response), __exit__=Mock(return_value=False))
        with patch("live_host.urlopen", return_value=context):
            self.assertEqual(normalize_room(SHARE), "216845906630")
        self.assertEqual(room_input(SHARE), "https://v.douyin.com/4MvomQ_qpB0/")

    def test_valid_share_is_accepted_by_offline_health_check(self):
        with patch("live_host.urlopen") as network:
            items = collect_readiness({"room": SHARE}, device_provider=lambda: [],
                                      llm_key_loader=lambda: "", tts_key_loader=lambda: "")
            network.assert_not_called()
        self.assertEqual(next(item for item in items if item["key"] == "room")["level"], "ok")
        for value in ("https://live.douyin.com/abc", "https://v.douyin.com/", "https://example.com/123456789"):
            with self.assertRaises(ValueError):
                room_input(value)

    def test_connect_button_returns_while_network_is_blocked_and_prevents_duplicate_clicks(self):
        panel = self.panel()
        started, release = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        worker_threads = []

        def connect(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            started.set()
            release.wait(2)

        panel.live_controller.connect.side_effect = connect
        try:
            before = time.monotonic()
            panel.connect_live()
            self.assertLess(time.monotonic() - before, 0.2)
            self.assertTrue(started.wait(1))
            panel.live_connect_button.configure.assert_called_with(state="normal", text="取消连接")
            panel.connect_live()
            panel.live_controller.connect.assert_called_once()
            self.assertNotEqual(worker_threads[0], main_thread)
        finally:
            release.set()
        result = panel.output.get(timeout=1)
        panel.output.put(result)
        panel._poll()
        panel.live_connect_button.configure.assert_called_with(state="normal", text="取消连接")

    def test_parse_failure_is_shown_on_the_page_and_button_becomes_available(self):
        panel = self.panel()
        panel.live_controller.connect.side_effect = ValueError("短链接解析超时")
        panel.connect_live()
        result = panel.output.get(timeout=1)
        panel.output.put(result)
        panel._poll()
        panel.live_status.set.assert_called_with("连接失败：短链接解析超时")
        panel.append_live.assert_called_with("连接失败", "短链接解析超时")
        self.assertIsNone(panel.live_connect_request)

    def test_same_connection_control_cancels_an_inflight_request_and_ignores_its_late_result(self):
        panel = self.panel()
        started, release = threading.Event(), threading.Event()
        panel.live_controller.connect.side_effect = lambda *args, **kwargs: (started.set(), release.wait(2))
        try:
            panel.toggle_live_connection()
            self.assertTrue(started.wait(1))
            panel.toggle_live_connection()
            panel.live_controller.disconnect.assert_called_once()
            self.assertIsNone(panel.live_connect_request)
            panel.live_connect_button.configure.assert_called_with(state="normal", text="连接直播间")
        finally:
            release.set()
        result = panel.output.get(timeout=1)
        panel.output.put(result)
        panel._poll()
        self.assertEqual(panel.connection_state, "disconnected")
        panel.live_connect_button.configure.assert_called_with(state="normal", text="连接直播间")

    def test_connected_control_disconnects_without_creating_another_connection(self):
        panel = self.panel()
        panel.connection_state = "connected"
        panel._sync_connection_button()
        panel.live_connect_button.configure.assert_called_with(state="normal", text="断开直播间")
        panel.toggle_live_connection()
        panel.live_controller.disconnect.assert_called_once()
        panel.live_controller.connect.assert_not_called()
        self.assertEqual(panel.connection_state, "disconnected")
        panel.live_connect_button.configure.assert_called_with(state="normal", text="连接直播间")

    def test_disconnect_during_resolution_prevents_a_late_connector_process(self):
        controller = self.controller()
        started, release = threading.Event(), threading.Event()

        def resolve(room):
            started.set()
            release.wait(2)
            return "216845906630"

        with patch("live_host.normalize_room", side_effect=resolve), patch(
                "live_host.Path.is_file", return_value=True), patch("live_host.subprocess.Popen") as popen:
            worker = threading.Thread(target=controller.connect, args=(SHARE,))
            worker.start()
            try:
                self.assertTrue(started.wait(1))
                controller.disconnect()
            finally:
                release.set()
                worker.join(1)
            self.assertFalse(worker.is_alive())
            popen.assert_not_called()

    def test_cancel_before_worker_start_does_not_resolve_or_connect(self):
        controller = self.controller()
        with patch("live_host.normalize_room") as resolve, patch("live_host.subprocess.Popen") as popen:
            controller.connect(SHARE, cancelled=lambda: True)
            resolve.assert_not_called()
            popen.assert_not_called()

    def test_cancelled_ui_results_and_previous_connection_status_are_ignored(self):
        panel = self.panel()
        request = object()
        panel.live_connect_request = request
        panel.disconnect_live()
        panel.live_status.reset_mock()
        panel.live_controller.connector_epoch = 2
        panel.output.put(("live_connect_result", request, "旧请求出错"))
        panel.output.put(("live_event", {"kind": "connection", "text": "旧连接已连上",
                                         "data": {"connector_epoch": 1}}))
        panel._poll()
        panel.live_status.set.assert_not_called()
        panel.append_live.assert_not_called()

    def test_connector_exit_preserves_useful_failure_reason(self):
        controller = self.controller()
        controller.connector_epoch = 3
        event = {"type": "status", "state": "error", "text": "连接器缺少文件 sign.js"}
        process = Mock(stdout=io.StringIO("CHESS_LIVE_EVENT " + json.dumps(event) + "\n"))
        controller._read_connector(process, 3)
        self.assertIn("sign.js", controller._notify.call_args.args[1])
        self.assertEqual(controller._notify.call_args.args[2]["state"], "error")
        self.assertEqual(controller.receive.call_args.args[0]["connector_epoch"], 3)

    def test_connector_exit_preserves_ended_room_explanation(self):
        controller = self.controller()
        controller.connector_epoch = 3
        event = {"type": "status", "state": "ended", "text": "该场直播已结束，请更换正在开播的直播间链接"}
        process = Mock(stdout=io.StringIO("CHESS_LIVE_EVENT " + json.dumps(event) + "\n"))
        controller._read_connector(process, 3)
        self.assertEqual(controller._notify.call_args.args[1], event["text"])
        self.assertEqual(controller._notify.call_args.args[2]["state"], "ended")

    def test_room_snapshot_reads_room_status_instead_of_unrelated_page_status(self):
        page = json.dumps({"status": 2, "roomInfo": {}, "roomStore": {"roomInfo": {
            "room": {"id_str": "7703480882075454218", "status": 4}}}})
        for source in (page, page.replace('"', '\\"')):
            self.assertEqual(douyin_bridge.room_snapshot(source)["status"], 4)
        self.assertEqual(douyin_bridge.room_snapshot('{"status":4,"roomInfo":{}}'), {})

    def test_adapter_binds_signature_asset_to_absolute_installation_path(self):
        module = types.ModuleType("liveMan")
        class Fetcher:
            def __init__(self, room, **kwargs):
                self.session = Mock()
                self.kwargs = kwargs
                self.live_id = room
                self.live_url = "https://live.douyin.com/"
                self.headers = {"User-Agent": "test"}
                self.session.get.return_value.text = json.dumps({"roomInfo": {
                    "room": {"id_str": "7703480882075454218", "status": 4}}})
            @property
            def ttwid(self):
                return "test-cookie"
            def start(self):
                self.started = True
        module.DouyinLiveWebFetcher = Fetcher
        signature = module.generateSignature = Mock(return_value="signature")
        proto = types.ModuleType("protobuf.douyin")
        for name in ("ChatMessage", "ControlMessage", "GiftMessage", "MemberMessage",
                     "PushFrame", "Response", "SocialMessage", "RoomUserSeqMessage", "LikeMessage",
                     "EmojiChatMessage"):
            setattr(proto, name, Mock())
        with patch.dict("sys.modules", {"liveMan": module, "protobuf": types.ModuleType("protobuf"),
                                       "protobuf.douyin": proto, "websocket": Mock()}), patch.object(
                douyin_bridge, "load_cookie", return_value=""):
            fetcher = douyin_bridge.create_fetcher("216845906630")
            module.generateSignature("wss://example.test")
        expected = str(douyin_bridge.UPSTREAM / "sign.js")
        signature.assert_called_once_with("wss://example.test", script_file=expected)
        self.assertEqual(fetcher.kwargs["abogus_file"], str(douyin_bridge.UPSTREAM / "a_bogus.js"))
        # The adapter's sink is emit by default; intercept stdout to verify the
        # real start path refuses an ended room before opening a websocket.
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            fetcher.start()
        self.assertTrue(fetcher.ended)
        self.assertFalse(getattr(fetcher, "started", False))
        self.assertIn('"ended"', output.getvalue())
        fetcher.room_details["status"] = 2
        fetcher.start()
        self.assertTrue(fetcher.started)

    def test_connection_errors_are_specific_without_exposing_request_secrets(self):
        self.assertIn("sign.js", douyin_bridge.connection_error(FileNotFoundError(2, "missing", "sign.js")))
        self.assertIn("websocket", douyin_bridge.connection_error(ModuleNotFoundError("missing", name="websocket")))
        self.assertIn("超时", douyin_bridge.connection_error(TimeoutError("private request")))
        self.assertNotIn("private", douyin_bridge.connection_error(RuntimeError("private cookie")))


if __name__ == "__main__":
    unittest.main()
