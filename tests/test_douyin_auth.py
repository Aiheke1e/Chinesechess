import io
import json
from pathlib import Path
import queue
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import douyin_auth as auth
import douyin_bridge
from control_panel import ControlPanel


class DouyinAuthTests(unittest.TestCase):
    def test_saved_login_is_encrypted_and_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(auth, "COOKIE_PATH", Path(folder) / "login.dpapi"):
            auth.save_cookie("sessionid=private-login; ttwid=browser-id")
            self.assertNotIn(b"private-login", auth.COOKIE_PATH.read_bytes())
            self.assertEqual(auth.load_cookie(), "sessionid=private-login; ttwid=browser-id")
            auth.clear_cookie()
            self.assertEqual(auth.load_cookie(), "")

    def test_only_douyin_cookies_from_the_dedicated_browser_are_selected(self):
        header = auth.douyin_cookie_header([
            {"domain": ".douyin.com", "name": "sessionid", "value": "valid"},
            {"domain": "live.douyin.com", "name": "ttwid", "value": "id"},
            {"domain": "notdouyin.com", "name": "other", "value": "private"},
            {"domain": "douyin.com.example.com", "name": "other2", "value": "private"},
            {"domain": ".douyin.com", "name": "bad", "value": "value\r\ninjected"}])
        self.assertEqual(header, "sessionid=valid; ttwid=id")
        self.assertTrue(auth.has_login(header))
        self.assertFalse(auth.has_login("ttwid=visitor"))
        with self.assertRaises(ValueError): auth.cookie_items("sessionid=one\r\nInjected: yes")

    def test_login_cookie_overrides_only_duplicate_cookie_names(self):
        self.assertEqual(auth.cookie_items(auth.merge_cookie("ttwid=guest; nonce=one", "sessionid=login; ttwid=logged")),
                         {"ttwid": "logged", "nonce": "one", "sessionid": "login"})

    def fake_fetcher(self, login):
        module = types.ModuleType("liveMan")
        socket = Mock()
        class Base:
            def __init__(self, room, **kwargs):
                self.session = Mock()
                self.http_get = self.session.get
                self.live_id, self.live_url = room, "https://live.douyin.com/"
                self.headers = {"User-Agent": "test"}
            @property
            def ttwid(self): return "guest"
            def _connectWebSocket(self):
                return socket.WebSocketApp("wss://douyin.example", header={"cookie": "ttwid=guest", "user-agent": "test"})
            def _sendHeartbeat(self): pass
        module.DouyinLiveWebFetcher, module.generateSignature = Base, Mock()
        proto = types.ModuleType("protobuf.douyin")
        for name in ("ChatMessage", "ControlMessage", "GiftMessage", "MemberMessage", "PushFrame", "Response",
                     "SocialMessage", "RoomUserSeqMessage", "LikeMessage", "EmojiChatMessage"):
            setattr(proto, name, Mock())
        events = []
        modules = {"liveMan": module, "protobuf": types.ModuleType("protobuf"), "protobuf.douyin": proto, "websocket": socket}
        with patch.dict("sys.modules", modules), patch.object(douyin_bridge, "load_cookie", return_value=login):
            fetcher = douyin_bridge.create_fetcher("123456789", events.append)
        return fetcher, socket, events

    def test_account_login_reaches_both_http_and_websocket_not_just_session(self):
        fetcher, socket, events = self.fake_fetcher("sessionid=private-login; ttwid=logged")
        request = fetcher.http_get
        self.assertEqual(fetcher.ttwid, "logged")
        fetcher.session.get("https://live.douyin.com/123456789", headers={"cookie": "ttwid=guest"})
        self.assertIn("sessionid=private-login", request.call_args.kwargs["headers"]["cookie"])
        self.assertIn("ttwid=logged", request.call_args.kwargs["headers"]["cookie"])
        fetcher._connectWebSocket()
        sent = socket.WebSocketApp.call_args
        self.assertIn("sessionid=private-login", sent.kwargs["header"]["cookie"])
        self.assertNotIn("private-login", sent.args[0])
        with patch("douyin_bridge.threading.Thread"):
            fetcher._wsOnOpen(Mock())
        self.assertIn("已载入登录信息", events[-1]["text"])
        self.assertNotIn("private-login", json.dumps(events))

    def test_guest_mode_is_explicit_about_gift_limitations(self):
        fetcher, socket, events = self.fake_fetcher("")
        fetcher._connectWebSocket()
        self.assertEqual(socket.WebSocketApp.call_args.kwargs["header"]["cookie"], "ttwid=guest")
        with patch("douyin_bridge.threading.Thread"):
            fetcher._wsOnOpen(Mock())
        self.assertIn("游客接入", events[-1]["text"])
        self.assertIn("礼物消息需登录", events[-1]["text"])

    def panel(self):
        panel = object.__new__(ControlPanel)
        panel._douyin_login_request = object()
        panel.live_status, panel.live_room = Mock(), Mock(get=Mock(return_value="123456789"))
        panel.disconnect_live, panel.connect_live = Mock(), Mock()
        return panel

    def test_saved_login_reconnects_current_room_and_cancel_does_not_disconnect(self):
        panel = self.panel()
        panel._handle_douyin_login(panel._douyin_login_request, {"state": "saved", "text": "已保存"})
        panel.disconnect_live.assert_called_once()
        panel.connect_live.assert_called_once()
        self.assertIsNone(panel._douyin_login_request)
        panel = self.panel()
        panel._handle_douyin_login(panel._douyin_login_request, {"state": "cancelled", "text": "已取消"})
        panel.disconnect_live.assert_not_called()
        panel.connect_live.assert_not_called()

    def test_stale_login_result_cannot_reconnect(self):
        panel = self.panel()
        panel._handle_douyin_login(object(), {"state": "saved", "text": "旧结果"})
        panel.connect_live.assert_not_called()

    def test_login_helper_never_puts_account_cookie_on_command_line_or_in_events(self):
        panel = self.panel()
        panel._douyin_login_request = None
        panel.output = queue.Queue()
        process = Mock(stdout=io.StringIO('CHESS_DOUYIN_LOGIN {"state":"saved","text":"已保存"}\n'))
        with patch("control_panel.os.path.isfile", return_value=True), patch("control_panel.threading.Thread") as thread, patch(
                "control_panel.subprocess.Popen", return_value=process) as popen:
            panel.login_douyin()
            target = thread.call_args.kwargs["target"]
            panel.login_douyin()
            self.assertEqual(thread.call_count, 1)
            target()
        self.assertEqual(len(popen.call_args.args[0]), 3)
        self.assertTrue(popen.call_args.args[0][-1].endswith("douyin_login.py"))
        event = panel.output.get_nowait()
        self.assertEqual(event[2], {"state": "saved", "text": "已保存"})


if __name__ == "__main__":
    unittest.main()
