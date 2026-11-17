"""Cancellable, session-local end-of-stream choreography; no desktop input here."""
from datetime import datetime, timedelta
import re
import time

from broadcast_flow import BroadcastFlow


PRELUDE_KIND = "下播预告"
FAREWELL_KIND = "下播告别"
SHUTDOWN_KINDS = {PRELUDE_KIND, FAREWELL_KIND}
PRELUDES = (
    ("这盘下完咱们今天就收工，兄弟们陪豆包把这盘走完。",
     "收棋前问问大家，今天哪步棋最有意思？咱们边下边聊。"),
    ("跟兄弟们打个招呼，今天最后一盘啦，咱们认真收个尾。",
     "今天有没有哪步棋让你拍大腿？趁棋盘还热，咱们聊两句。"),
    ("这盘就是今天的收官局，棋要下完，陪伴也得有头有尾。",
     "临收工问一句，今天是看棋过瘾，还是听豆包唠嗑过瘾？"),
)
FAREWELLS = (
    ("今天就陪大家下到这里啦。谢谢每一位来看棋、聊天、陪着豆包的朋友，辛苦大家了。",
     "棋盘先收起来，快乐留给大家。爱你们，咱们下次直播见，拜拜！"),
    ("今天的棋桌要收摊啦。谢谢大家的陪伴，来聊一句也好，安静看棋也好，豆包都很开心。",
     "车马炮下班，豆包也跟大家道个别。爱你们，下次开播再聚，拜拜！"),
    ("感谢兄弟们今天一起看棋，一起琢磨。豆包今天先收工了，谢谢你们把时间留在这里。",
     "棋可以明天再下，今天先把自己照顾好。爱你们，咱们下回棋桌见，拜拜！"),
)


def next_shutdown_time(value, now=None):
    now = now or datetime.now().astimezone()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value)):
        raise ValueError("请输入24小时制时间，例如23:30")
    hour, minute = map(int, value.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


class StreamShutdown:
    """Driven on the UI thread. Only completed AUDIO receipts permit closing."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.epoch = 0
        self.state = "idle"
        self.target = None
        self.pending = None
        self.message = ""
        self.sequence = 0
        self.entered = 0
        self.preludes = 0
        self.next_prelude = 0
        self.farewells = 0
        self.round = -1

    @property
    def active(self):
        return self.state in ("waiting_game", "draining", "farewell", "closing")

    @property
    def finalizing(self):
        return self.state in ("draining", "farewell", "closing")

    def schedule(self, value, now=None):
        if self.active:
            raise ValueError("正在准备下播，请先取消")
        self.epoch += 1
        self.target = next_shutdown_time(value, now)
        self.state = "scheduled"
        self.message = f"{self.target:%m月%d日 %H:%M}起，打完当局后下播"

    def due(self, now=None):
        return self.state == "scheduled" and (now or datetime.now().astimezone()) >= self.target

    def arm(self, game_active):
        self.epoch += 1
        self.round += 1
        self.target = self.pending = None
        self.preludes = self.farewells = 0
        self.next_prelude = self.entered = self.clock()
        self.state = "waiting_game" if game_active else "draining"
        self.message = "本局后下播 · 本局照常下完" if game_active else "准备告别 · 不再开启新局"

    def cancel(self):
        self.epoch += 1
        self.state = "idle"
        self.target = self.pending = None
        self.message = "已取消下播；自动连局保持关闭"

    def fail(self, message):
        self.epoch += 1
        self.state = "error"
        self.pending = None
        self.message = message

    def observe_game(self, event):
        if not self.active or self.state == "closing" or event.get("dry_run"):
            return
        if event.get("event") == "game_over" and BroadcastFlow.is_terminal(event):
            if self.state == "waiting_game":
                self.state = "draining"
                self.entered = self.clock()
                self.message = "本局已结束 · 等当前播报说完"
        elif event.get("event") in ("start", "position") and self.finalizing:
            # A game already accepted by JJ at the activation boundary must
            # still be played out. Invalidate every old farewell receipt.
            self.epoch += 1
            self.pending = None
            self.state = "waiting_game"
            self.farewells = 0
            self.message = "已接上当前棋局 · 下完再下播"

    def speech_done(self, data):
        if not self.pending or data.get("_shutdown_token") != self.pending:
            return
        self.pending = None
        if data.get("kind") == PRELUDE_KIND:
            return  # A missed safe-gap prelude never blocks real play.
        if self.state != "farewell":
            return
        if not data.get("completed") or data.get("_speech_mode") != "audio":
            self.fail("告别语音未完整播完，未操作直播伴侣；检查语音后重试")
            return
        self.farewells += 1
        self.entered = self.clock()

    def _speech(self, kind, line):
        self.sequence += 1
        self.pending = f"shutdown-{self.epoch}-{self.sequence}"
        self.sent_at = self.clock()
        payload = {"_shutdown_token": self.pending, "_shutdown_epoch": self.epoch}
        if kind == PRELUDE_KIND:
            payload["_expires_at"] = self.clock() + 12
        return ("speak", kind, line, payload)

    def tick(self, *, idle, prelude_safe=False):
        now = self.clock()
        if self.pending:
            if now - self.sent_at > 90:
                if self.state == "waiting_game":
                    self.pending = None
                else:
                    self.fail("告别播报等待超时，未操作直播伴侣")
            return None
        if self.state == "waiting_game" and idle and prelude_safe and self.preludes < 2 and now >= self.next_prelude:
            lines = PRELUDES[self.round % len(PRELUDES)]
            line = lines[self.preludes]
            self.preludes += 1
            self.next_prelude = now + 45
            return self._speech(PRELUDE_KIND, line)
        if self.state == "draining":
            if now - self.entered > 90:
                self.fail("当前播报未结束，未操作直播伴侣")
            elif idle and now - self.entered >= 2:
                self.state = "farewell"
                self.entered = now
                self.message = "正在告别 · 说完后结束直播"
        if self.state == "farewell" and idle:
            lines = FAREWELLS[self.round % len(FAREWELLS)]
            if self.farewells < len(lines):
                return self._speech(FAREWELL_KIND, lines[self.farewells])
            self.state = "closing"
            self.message = "告别已播完 · 正在结束直播伴侣的直播"
            return ("close", self.epoch)
        if self.state == "farewell" and now - self.entered > 90:
            self.fail("告别流程等待超时，未操作直播伴侣")
        return None

    def closed(self, epoch, *, success, message):
        if epoch != self.epoch or self.state != "closing":
            return False
        self.state = "done" if success else "error"
        self.message = message
        return True
