"""Bounded, local selection of fresh comments; no model calls per incoming chat."""
from collections import deque
import random
import re
import threading
import time


class ChatSelector:
    MAX_PENDING = 60
    MAX_AGE = 25.0

    def __init__(self, clock=time.monotonic, rng=None):
        self.clock = clock
        self.rng = rng or random.Random()
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with self.lock:
            self.online = None
            self.online_at = 0.0
            self.arrivals = deque(maxlen=16)  # Per-second counts, including large bursts.
            self.pending = {}
            self.recent_text = {}
            self.recent_users = {}
            self.next_pick = 0.0

    def clear(self):
        with self.lock:
            self.pending.clear()

    def update_online(self, count):
        try:
            count = int(count)
        except (TypeError, ValueError, OverflowError):
            return False
        if count < 0:
            return False
        with self.lock:
            self.online, self.online_at = count, self.clock()
        return True

    def _prune(self, now):
        while self.arrivals and self.arrivals[0][0] <= now - 15:
            self.arrivals.popleft()
        self.pending = {key: row for key, row in self.pending.items()
                        if now - row["received"] <= self.MAX_AGE}

    def _stats(self, now):
        self._prune(now)
        online = self.online if now - self.online_at <= 45 else None
        rate = sum(count for _, count in self.arrivals) / 15
        interval = 8 if online is None or online < 50 else (
            12 if online < 500 else 20 if online < 5000 else 30)
        if rate >= 10:
            interval = max(interval, 30)
        elif rate >= 2:
            interval = max(interval, 20)
        return {"online": online, "chat_rate": round(rate, 1), "interval": interval}

    def stats(self):
        with self.lock:
            return self._stats(self.clock())

    def offer(self, event, eligible=True):
        now = self.clock()
        text = " ".join(str(event.get("text") or "").split())
        with self.lock:
            second = int(now)
            if self.arrivals and self.arrivals[-1][0] == second:
                bucket, count = self.arrivals.pop()
                self.arrivals.append((bucket, count + 1))
            else:
                self.arrivals.append((second, 1))
            self._prune(now)
            if not eligible:
                return False
            # Keep meaningful text, not long advertisements or pure emoji/666.
            meaningful = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
            if (len(meaningful) < 2 or len(text) > 160
                    or re.search(r"https?://|www\.", text, re.I)
                    or re.fullmatch(r"[哈呵嘿哦啊嗯好棒赞689]+", meaningful)
                    or re.search(r"(.)\1{5,}", meaningful)):
                return False
            normalized = meaningful.casefold()
            if normalized in self.recent_text and now - self.recent_text[normalized] < 60:
                return False
            self.recent_text[normalized] = now
            if len(self.recent_text) > 300:
                self.recent_text.pop(next(iter(self.recent_text)))
            user = str(event.get("user") or "观众")[:30]
            key = str(event.get("user_id") or user)
            self.pending.pop(key, None)
            self.pending[key] = {"user": user, "user_id": key, "text": text,
                                 "received": now, "id": str(event.get("id") or "")}
            while len(self.pending) > self.MAX_PENDING:
                self.pending.pop(next(iter(self.pending)))
        return True

    def take(self):
        now = self.clock()
        with self.lock:
            stats = self._stats(now)
            if now < self.next_pick:
                return None
            rows = [row for key, row in self.pending.items()
                    if now - row["received"] >= 1.5 and
                    now - self.recent_users.get(key, -1e9) >= min(90, stats["interval"] * 2)]
            if not rows:
                return None
            weights = [2 if re.search(r"[?？]|为什么|怎么|能不能|请问", row["text"]) else 1
                       for row in rows]
            picked = self.rng.choices(rows, weights=weights, k=1)[0]
            self.pending.clear()  # Unselected comments never accumulate into a speech backlog.
            self.recent_users[picked["user_id"]] = now
            if len(self.recent_users) > 300:
                self.recent_users.pop(next(iter(self.recent_users)))
            self.next_pick = now + stats["interval"]
            return picked
