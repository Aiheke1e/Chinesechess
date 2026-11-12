"""End an already-running Douyin Companion broadcast; never launch/go live.

Desktop input is isolated behind a small backend so verification uses fixtures.
Unknown labels, ambiguous windows, moved controls, or lost focus stop the action.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time


END_LABELS = {"结束直播", "关闭直播"}
START_LABELS = {"开始直播"}


@dataclass(frozen=True)
class Control:
    text: str
    x: float
    y: float
    width: float
    height: float
    button: bool = False

    @property
    def point(self):
        return (int(self.x + self.width / 2), int(self.y + self.height / 2))


@dataclass
class Snapshot:
    identity: tuple
    width: int
    height: int
    controls: list
    captured: float


def clean(text):
    return re.sub(r"\s+", "", str(text))


def classify(frame):
    """Only toolbar buttons or a corroborated end-live dialog are actionable."""
    text = "".join(clean(item.text) for item in frame.controls)
    dialog = any(term in text for term in (
        "确认结束直播", "确定结束直播", "是否结束直播", "确定要结束直播", "结束本场直播"))
    candidates = []
    for item in frame.controls:
        name = clean(item.text)
        x, y = item.point
        if not item.button:
            continue
        if dialog and name in END_LABELS | {"确定", "确认", "确认结束", "确定结束"}:
            if .15 * frame.width < x < .9 * frame.width and .25 * frame.height < y < .9 * frame.height:
                candidates.append(("confirm", item))
        elif not dialog and .5 * frame.width < x < .97 * frame.width and .75 * frame.height < y < .97 * frame.height:
            if name in START_LABELS:
                candidates.append(("idle", item))
            elif name in END_LABELS:
                candidates.append(("live", item))
    return candidates[0] if len(candidates) == 1 else ("unknown", None)


class CompanionShutdown:
    def __init__(self, backend=None, clock=time.monotonic):
        self.backend = backend or WindowsCompanionBackend()
        self.clock = clock

    def end_live(self, cancel=None):
        cancel = cancel or threading.Event()
        clicked = set()
        previous = None
        idle_frames = 0
        deadline = self.clock() + 30
        while self.clock() < deadline and not cancel.is_set():
            frame = self.backend.snapshot()
            state, button = classify(frame)
            if state == "idle":
                idle_frames += 1
                if idle_frames >= 2:
                    return True, "已确认直播伴侣未开播" if not clicked else "直播已结束 · 已确认直播伴侣回到未开播状态"
            else:
                idle_frames = 0
            if state == "unknown":
                # Never click a fixed coordinate or fall back to closing the
                # application: that does not prove the server stream ended.
                if clicked:
                    previous = None  # Dialog/transition may still be rendering.
                else:
                    return False, "未识别到明确的结束直播按钮，未操作；请在直播伴侣手动下播"
            elif state in ("live", "confirm") and state not in clicked:
                signature = (frame.identity, state, button.point)
                if previous and previous[:2] == signature[:2] and max(
                        abs(a-b) for a, b in zip(previous[2], signature[2])) <= 6:
                    if cancel.is_set():
                        break
                    self.backend.click(frame, button, cancel)
                    clicked.add(state)
                    previous = None
                else:
                    previous = signature
            if cancel.wait(.25):
                break
        if cancel.is_set():
            return False, "下播操作已取消；如已确认结束，请以直播伴侣状态为准"
        return False, "未确认直播已结束，请检查直播伴侣；未重复点击"


class WindowsCompanionBackend:
    """Runtime-only UI adapter. Nothing is launched and START is never clicked."""
    def __init__(self):
        self.window = None

    @staticmethod
    def _executable(pid):
        import win32api
        import win32process
        handle = win32api.OpenProcess(0x0410, False, pid)
        try:
            return Path(win32process.GetModuleFileNameEx(handle, 0)).name
        finally:
            handle.Close()

    def _window(self):
        from pywinauto import Desktop
        candidates = [w for w in Desktop(backend="win32").windows(title="直播伴侣", visible_only=True)
                      if self._executable(w.process_id()) == "直播伴侣.exe"]
        if len(candidates) != 1:
            raise RuntimeError("请保持一个已打开的抖音直播伴侣主窗口；不会自动启动或开播")
        window = candidates[0]
        if self.window is not None and window.handle != self.window.handle:
            raise RuntimeError("直播伴侣窗口已变化，已取消操作")
        self.window = window
        return window

    @staticmethod
    def _rect(window):
        r = window.rectangle()
        return (r.left, r.top, r.right, r.bottom)

    def snapshot(self):
        import win32gui
        window = self._window()
        window.set_focus()
        if win32gui.GetForegroundWindow() != window.handle:
            raise RuntimeError("直播伴侣没有处于前台，未操作")
        rect = self._rect(window)
        image = window.capture_as_image().convert("RGB")
        if image.width < 600 or image.height < 350:
            raise RuntimeError("直播伴侣窗口过小，请展开主窗口")
        # Only a temporary local image is used by the bundled CPU OCR. No
        # screenshot, account nickname, or raw OCR is retained in the log.
        with tempfile.TemporaryDirectory(prefix="chess_companion_") as folder:
            path = Path(folder) / "frame.png"
            image.save(path)
            result = subprocess.run([
                sys.executable, "-X", "utf8", str(Path(__file__).with_name("companion_ocr.py")), str(path)],
                capture_output=True, encoding="utf-8", timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        lines = json.loads(result.stdout or "[]")
        controls = []
        for line in lines:
            words = line.get("words") or []
            # Accept exact text bounds only; never estimate a button location
            # from nearby help text. Deduplicate repeated OCR spans below.
            for begin in range(len(words)):
                for end in range(begin+1, min(len(words), begin+12)+1):
                    span = words[begin:end]
                    name = clean("".join(w["text"] for w in span))
                    if name not in END_LABELS | START_LABELS | {"确定", "确认", "确认结束", "确定结束"}:
                        continue
                    left = min(w["x"] for w in span)
                    top = min(w["y"] for w in span)
                    right = max(w["x"]+w["width"] for w in span)
                    bottom = max(w["y"]+w["height"] for w in span)
                    controls.append(Control(name, left, top, right-left, bottom-top,
                                            self._button_surface(image, (left, top, right, bottom))))
            controls.append(Control(line.get("text", ""), 0, 0, 0, 0))
        controls = list(dict.fromkeys(controls))
        if win32gui.GetForegroundWindow() != window.handle or rect != self._rect(window):
            raise RuntimeError("识别期间直播伴侣窗口移动或失去焦点，未操作")
        # Capture a fresh frame after OCR; a changing dialog/button invalidates
        # the recognition rather than using seconds-old coordinates.
        fresh = window.capture_as_image().convert("RGB")
        from PIL import ImageChops, ImageStat
        for item in controls:
            if item.button:
                box = (int(item.x)-4, int(item.y)-4,
                       int(item.x+item.width)+4, int(item.y+item.height)+4)
                if max(ImageStat.Stat(ImageChops.difference(image.crop(box), fresh.crop(box))).mean) > 3:
                    raise RuntimeError("直播伴侣按钮画面正在变化，未操作")
        return Snapshot((window.handle, *rect), image.width, image.height, controls, time.monotonic())

    @staticmethod
    def _button_surface(image, box):
        """Require a distinct, flat filled area around text, not preview text."""
        import numpy as np
        x1, y1, x2, y2 = map(int, box)
        if x1 < 12 or x2+12 >= image.width or y1 < 5 or y2+5 >= image.height:
            return False
        pixels = np.asarray(image)
        samples = [pixels[y, x].astype(float) for x in (x1-7, x2+7)
                   for y in (y1, (y1+y2)//2, y2)]
        # Require a filled pink/red action surface. Other themes/skins remain
        # unsupported rather than treating ordinary grey text as a button.
        return all(p[0] > 130 and p[0] > p[1]*1.35 and p[0] > p[2]*1.1 for p in samples)

    def click(self, frame, button, cancel):
        import win32gui
        window = self._window()
        if (cancel.is_set() or time.monotonic()-frame.captured > .7
                or (window.handle, *self._rect(window)) != frame.identity
                or win32gui.GetForegroundWindow() != window.handle
                or clean(button.text) in START_LABELS):
            raise RuntimeError("下播按钮证据已失效，未操作")
        x, y = button.point
        # Also ensure another top-level window has not covered the point.
        left, top, _, _ = self._rect(window)
        under = win32gui.GetAncestor(win32gui.WindowFromPoint((left+x, top+y)), 2)
        if under != window.handle:
            raise RuntimeError("下播按钮被其他窗口遮挡，未操作")
        window.click_input(coords=(x, y))
