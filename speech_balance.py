"""Bounded airtime for a chess stream; standalone chat keeps its own cadence."""
from collections import deque
import threading
import time


class SpeechBalance:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.entries = deque(maxlen=60)
        self.chess_active = False
        self.protected_until = 0.0
        self.sandbox_until = 0.0
        self.last_chess = self.clock()
        self.last_reservation = -1e9

    def set_chess_active(self, active):
        with self.lock:
            if active and not self.chess_active:
                self.last_chess = self.clock()
            self.chess_active = bool(active)
            if not active:
                self.protected_until = 0.0
                self.sandbox_until = 0.0

    def can_chat(self):
        with self.lock:
            now = self.clock()
            while self.entries and now - self.entries[0][0] >= 60:
                self.entries.popleft()
            if not self.chess_active:
                return True
            chats = [row for row in self.entries if row[1] == "chat"]
            return (now >= self.protected_until and len(chats) < 3
                    and (not chats or now - chats[-1][0] >= 10)
                    and sum(row[2] for row in chats) < 20)

    def chat_may_finish(self):
        with self.lock:
            return not self.chess_active or self.clock() >= self.sandbox_until

    def reserve_chess(self, seconds=6, force=False, sandbox=False):
        with self.lock:
            now = self.clock()
            if not self.chess_active:
                return False
            if not force and (now - self.last_chess < 15 or now - self.last_reservation < 15):
                return False
            self.protected_until = max(self.protected_until, now + min(15, max(0, seconds)))
            if sandbox:
                self.sandbox_until = self.protected_until
            self.last_reservation = now
            return True

    def record(self, kind, seconds):
        with self.lock:
            now = self.clock()
            category = ("chat" if kind in ("弹幕回复", "礼物答谢", "欢迎观众") else
                        "other" if kind == "自我介绍" else "chess")
            self.entries.append((now, category, max(0.0, seconds)))
            if category == "chess":
                self.last_chess = now
