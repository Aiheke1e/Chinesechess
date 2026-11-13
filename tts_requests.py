"""Paced, cancellable TTS requests shared by all speech producers."""
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time


class SynthesisCancelled(Exception):
    pass


class TTSRateLimitError(RuntimeError):
    def __init__(self, code, request_id=None):
        super().__init__(f"百炼语音请求过于频繁，请稍后再试（{code}）")
        self.code, self.request_id = code, request_id


_context = ContextVar("tts_request_context", default={})


@contextmanager
def synthesis_context(**options):
    token = _context.set(options)
    try:
        yield
    finally:
        _context.reset(token)


def check_synthesis_valid():
    valid = _context.get().get("valid")
    if valid is not None and not valid():
        raise SynthesisCancelled()


class RequestLimiter:
    """One API request at a time; priority applies to requests not yet sent."""
    def __init__(self, interval=.5, backoffs=(1., 2.), clock=time.monotonic):
        self.interval, self.backoffs, self.clock = interval, backoffs, clock
        self.condition = threading.Condition()
        self.pending = []
        self.sequence = 0
        self.busy = False
        self.next_start = 0.

    def run(self, request, *, priority=(2, 0), valid=lambda: True, on_retry=None):
        with self.condition:
            self.sequence += 1
            ticket = (priority, self.sequence)
            self.pending.append(ticket)
        attempts = 0
        try:
            while True:
                with self.condition:
                    while True:
                        if not valid():
                            raise SynthesisCancelled()
                        now = self.clock()
                        if not self.busy and ticket == min(self.pending) and now >= self.next_start:
                            self.busy = True
                            self.next_start = now + self.interval
                            break
                        self.condition.wait(.03)
                retry = False
                try:
                    return request()
                except TTSRateLimitError:
                    if attempts >= len(self.backoffs):
                        raise
                    delay = self.backoffs[attempts]
                    attempts += 1
                    with self.condition:
                        self.next_start = max(self.next_start, self.clock() + delay)
                    retry = True
                finally:
                    with self.condition:
                        self.busy = False
                        self.condition.notify_all()
                if retry and on_retry and valid():
                    on_retry(attempts)
        finally:
            with self.condition:
                self.pending.remove(ticket)
                self.condition.notify_all()


_limiter = RequestLimiter()


def limited_request(request):
    return _limiter.run(request, **_context.get())
