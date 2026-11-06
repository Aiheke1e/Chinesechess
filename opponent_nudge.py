"""Select JJ's free first cloud emoji during a confirmed opponent wait.

Runs synchronously in the player, never in a competing input worker. No
coordinate fallback, repeat send, or Android BACK (a game exit) is allowed.
"""
from functools import lru_cache
from pathlib import Path
import subprocess
import time

import cv2
import numpy as np

from adb_bridge import TaskCancelled, screenshot, tap
from game_sandbox import _detect_sandbox_exit
from recognizer import recognize_occupancy, recognize_presence_occupancy


ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=4)
def _template(name):
    try:
        return cv2.imdecode(np.frombuffer(
            (ROOT / "assets" / (name + ".png")).read_bytes(), np.uint8),
            cv2.IMREAD_GRAYSCALE)
    except (OSError, cv2.error):
        return None


def _match(image, name, bounds, threshold=.86):
    if (image is None or getattr(image, "ndim", 0) != 3
            or image.shape[0] < 800 or image.shape[1] < 450
            or not .50 <= image.shape[1] / image.shape[0] <= .60):
        return None
    template = _template(name)
    if template is None:
        return None
    gray = cv2.cvtColor(cv2.resize(image, (1080, 1920)), cv2.COLOR_BGR2GRAY)
    x0, y0, x1, y1 = bounds
    area = gray[y0:y1, x0:x1]
    if min(template.shape) < 1 or any(a < b for a, b in zip(area.shape, template.shape)):
        return None
    # Match both the older 540p fixtures and the running 1080p emulator.
    area = cv2.GaussianBlur(area, (3, 3), 0)
    template = cv2.GaussianBlur(template, (3, 3), 0)
    _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(
        area, template, cv2.TM_CCOEFF_NORMED))
    if score < threshold:
        return None
    height, width = image.shape[:2]
    return (round((x0 + point[0] + template.shape[1] / 2) * width / 1080),
            round((y0 + point[1] + template.shape[0] / 2) * height / 1920))


def emoji_button(image):
    return _match(image, "jj-emoji-smile", (870, 1710, 1040, 1880))


def emoji_panel_visible(image):
    return _match(image, "jj-emoji-tabs", (45, 1530, 1035, 1650), .94) is not None


def cloud_button(image):
    # Require the emoji tab AND the cloud in the first slot. Adjacent paid
    # watering-can/glove/basket tiles are never used as fallbacks.
    if not emoji_panel_visible(image):
        return None
    if _match(image, "jj-emoji-coin", (85, 1765, 280, 1830), .86) is not None:
        return None
    return _match(image, "jj-emoji-cloud", (90, 1650, 275, 1815), .90)


def _unchanged(board, image, orientation):
    if image is None or _detect_sandbox_exit(image) is not None:
        return False
    for read in (recognize_occupancy, recognize_presence_occupancy):
        observed = read(image, orientation)
        if observed is not None and len(observed) == 90 and all(
                (wanted is None and seen is None) or
                (wanted is not None and seen is not None and
                 wanted.isupper() == seen.isupper())
                for wanted, seen in zip(board.sq, observed)):
            return True
    return False


def send_wait_cloud(device, board, orientation, my_color, stop_requested=lambda: False):
    """One bounded attempt; the caller owns the wait/cooldown and dry-run gate.

    ``sent`` means JJ closed the picker after its first emoji was selected;
    it is not a claim of server delivery. Never mutate the trusted position.
    """
    def result(sent=False, reason="未发送", changed=False):
        return {"sent": sent, "reason": reason, "board_changed": changed}

    opened = False
    io_errors = (OSError, subprocess.SubprocessError, cv2.error)
    try:
        if stop_requested() or my_color not in ("r", "b") or board.turn == my_color:
            return result(reason="已暂停或轮到我方")
        image = screenshot(device)
        button = emoji_button(image)
        if (emoji_panel_visible(image) or button is None
                or not _unchanged(board, image, orientation)):
            return result(reason="实战棋盘或表情入口未确认", changed=True)
        if stop_requested():
            return result(reason="已暂停")
        opened = True
        tap(*button, device=device)
        time.sleep(.12)
        panel = screenshot(device)
        cloud = cloud_button(panel)
        if cloud is None:
            return result(reason="未确认乌云面板，不再点击")
        if stop_requested():
            return result(reason="已暂停")
        if not _unchanged(board, panel, orientation):
            return result(reason="对手已落子或棋盘有变化", changed=True)
        # Catch a move arriving during picker animation/recognition.
        panel = screenshot(device)
        cloud = cloud_button(panel)
        if cloud is None or not _unchanged(board, panel, orientation):
            return result(reason="发送前棋盘或面板有变化", changed=True)
        if stop_requested():
            return result(reason="已暂停")
        tap(*cloud, device=device)
        time.sleep(.12)
        after = screenshot(device)
        accepted = not emoji_panel_visible(after) and emoji_button(after) is not None
        opened = not accepted
        return result(sent=accepted, reason="已选择乌云表情" if accepted else "未确认发送，不重试")
    except io_errors:
        return result(reason="表情操作未完成，略过本次催棋", changed=True)
    finally:
        # Only dismiss a freshly confirmed picker in the non-board header.
        # BACK opens a forfeiture dialog, and is deliberately never used.
        if opened and not stop_requested():
            try:
                frame = screenshot(device)
                if emoji_panel_visible(frame) and not stop_requested():
                    height, width = frame.shape[:2]
                    tap(round(width * .5), round(height * .175), device=device)
            except (*io_errors, TaskCancelled):
                pass
