"""Emit normalized Douyin live events from the external connector."""
from __future__ import annotations

import argparse
from functools import partial
import gzip
import json
import re
from pathlib import Path
import sys
import threading
import time


UPSTREAM = Path(r"D:\environment\shuzhiren\connectors\DouyinLiveWebFetcher-main")
sys.path.insert(0, str(UPSTREAM))


def emit(event):
    print("CHESS_LIVE_EVENT " + json.dumps(event, ensure_ascii=False), flush=True)


def connection_error(error):
    """Useful diagnostics without publishing cookies or signed request URLs."""
    if isinstance(error, FileNotFoundError):
        filename = Path(error.filename or "连接器文件").name
        return f"连接器缺少文件 {filename}，请检查连接器安装目录"
    if isinstance(error, ModuleNotFoundError):
        return f"连接器运行环境缺少依赖：{error.name}"
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return f"直播连接被服务器拒绝（HTTP {status}），请稍后重试"
    if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower():
        return "连接直播间超时，请检查网络后重试"
    return f"直播连接失败（{type(error).__name__}），请确认正在开播后重试"


def room_snapshot(page):
    """Read the actual room object, ignoring unrelated status fields on the page."""
    decoder = json.JSONDecoder()
    for candidate in (page, page.replace('\\"', '"')):
        for match in re.finditer(r'"roomInfo"\s*:\s*(\{)', candidate):
            try:
                info, _ = decoder.raw_decode(candidate, match.start(1))
            except ValueError:
                continue
            room = info.get("room")
            if isinstance(room, dict) and str(room.get("id_str", "")).isdigit():
                return room
    return {}


def create_fetcher(room, sink=emit):
    import liveMan
    from liveMan import DouyinLiveWebFetcher
    from protobuf.douyin import (ChatMessage, ControlMessage, GiftMessage,
                                 MemberMessage, PushFrame, Response,
                                 SocialMessage, RoomUserSeqMessage, LikeMessage,
                                 EmojiChatMessage)
    import websocket

    # Upstream resolves this default against cwd. Our child runs in the chess
    # project, so bind the asset explicitly instead of changing global cwd.
    liveMan.generateSignature = partial(
        liveMan.generateSignature, script_file=str(UPSTREAM / "sign.js"))

    class Fetcher(DouyinLiveWebFetcher):
        def __init__(self):
            super().__init__(room, abogus_file=str(UPSTREAM / "a_bogus.js"))
            self.ended = False
            original_get = self.session.get

            def bounded_get(*args, **kwargs):
                kwargs.setdefault("timeout", (8, 15))
                return original_get(*args, **kwargs)
            self.session.get = bounded_get

        @property
        def room_id(self):
            if not hasattr(self, "room_details"):
                response = self.session.get(self.live_url + self.live_id, headers={
                    **self.headers, "cookie": f"ttwid={self.ttwid}"})
                response.raise_for_status()
                self.room_details = room_snapshot(response.text)
            return self.room_details.get("id_str") or super().room_id

        def start(self):
            # A websocket also opens for an ended room, but delivers no chat.
            # Check the room itself before reporting a successful connection.
            self.room_id
            if str(self.room_details.get("status")) == "4":
                self.ended = True
                sink({"type": "status", "state": "ended",
                      "text": "该场直播已结束，请更换正在开播的直播间链接"})
                return
            return super().start()

        def _wsOnOpen(self, ws):
            sink({"type": "status", "state": "connected", "text": "已连接抖音直播间"})
            threading.Thread(target=self._sendHeartbeat, daemon=True).start()

        def _wsOnError(self, ws, error):
            sink({"type": "status", "state": "error", "text": connection_error(error)})

        def _wsOnClose(self, ws, *args):
            sink({"type": "status", "state": "ended" if self.ended else "disconnected",
                  "text": "直播已结束" if self.ended else "抖音连接已断开"})

        def _wsOnMessage(self, ws, message):
            try:
                package = PushFrame().parse(message)
                response = Response().parse(gzip.decompress(package.payload))
                if response.need_ack:
                    ack = PushFrame(log_id=package.log_id, payload_type="ack",
                                    payload=response.internal_ext.encode())
                    ws.send(bytes(ack), websocket.ABNF.OPCODE_BINARY)
                handlers = {
                    "WebcastChatMessage": (ChatMessage, "chat"),
                    "WebcastGiftMessage": (GiftMessage, "gift"),
                    "WebcastMemberMessage": (MemberMessage, "member"),
                    "WebcastSocialMessage": (SocialMessage, "social"),
                    "WebcastLikeMessage": (LikeMessage, "like"),
                    "WebcastEmojiChatMessage": (EmojiChatMessage, "chat"),
                }
                for item in response.messages_list:
                    if item.method == "WebcastControlMessage":
                        if ControlMessage().parse(item.payload).status == 3:
                            self.ended = True
                            sink({"type": "status", "state": "ended", "text": "直播已结束"})
                            self.stop()
                        continue
                    if item.method == "WebcastRoomUserSeqMessage":
                        stats = RoomUserSeqMessage().parse(item.payload)
                        sink({"type": "audience", "online": int(stats.total)})
                        continue
                    if item.method not in handlers:
                        continue
                    message_class, kind = handlers[item.method]
                    parsed = message_class().parse(item.payload)
                    event = {"type": kind,
                             "id": str(parsed.common.msg_id or ""),
                             "user": (parsed.user.nick_name or "观众")[:256],
                             "user_id": str(parsed.user.id or "")}
                    if kind == "chat":
                        event["text"] = (parsed.default_content if item.method == "WebcastEmojiChatMessage"
                                         else parsed.content)[:500]
                    elif kind == "gift":
                        count = parsed.combo_count or parsed.repeat_count or 1
                        event.update({"gift": parsed.gift.name, "count": count,
                                      "text": f"送出 {parsed.gift.name} × {count}"})
                    elif kind == "member":
                        event["text"] = "进入直播间"
                    elif kind == "like":
                        event["text"] = f"点赞 × {parsed.count or 1}"
                    else:
                        event["text"] = ("关注了主播" if parsed.follow_count else
                                         "分享了直播间" if parsed.share_target or parsed.share_type
                                         else "参与直播互动")
                    sink(event)
            except Exception:
                sink({"type": "status", "state": "error",
                      "text": "收到无法解析的直播数据，连接器可能需要更新"})

    return Fetcher()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--room", required=True)
    args = parser.parse_args()
    if not args.room.isdigit() or not 6 <= len(args.room) <= 25:
        raise SystemExit("Invalid room id")
    for attempt in range(5):
        emit({"type": "status", "state": "connecting",
              "text": f"正在连接直播间（第 {attempt + 1} 次）"})
        try:
            fetcher = create_fetcher(args.room)
            fetcher.start()
            if fetcher.ended:
                return
        except Exception as error:
            emit({"type": "status", "state": "error",
                  "text": connection_error(error)})
        if attempt < 4:
            time.sleep(min(2 ** (attempt + 1), 20))


if __name__ == "__main__":
    main()
