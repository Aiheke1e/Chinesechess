"""Read-only opponent presence cues from the existing game screenshots."""
from functools import lru_cache
from pathlib import Path
import time

import cv2
import numpy as np


@lru_cache(maxsize=1)
def _offline_template():
    path = Path(__file__).resolve().parent / "assets/jj-opponent-offline.png"
    try:
        return cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
    except (OSError, cv2.error):
        return None


def opponent_disconnected(image):
    """Match only JJ's explicit 断线 label on the top opponent avatar.

    A running countdown, a stationary board or an absent screenshot is not
    evidence of disconnection.  This detector never sends input to the game.
    """
    if image is None or getattr(image, "ndim", 0) != 3:
        return False
    height, width = image.shape[:2]
    if height < 800 or width < 450 or not .48 <= width / height <= .65:
        return False
    template = _offline_template()
    if template is None:
        return False
    gray = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (1080, 1920))
    area = cv2.inRange(gray[75:165, 460:625], 190, 255)
    scores = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
    return cv2.minMaxLoc(scores)[1] >= .76


def countdown_side(image):
    """Read JJ's active large digit clock; never infer a turn from stillness.

    The running clock has a saturated yellow progress arc and large outlined
    digits. Inactive portraits, the small total-time label and 断线 do not.
    Ambiguous or missing evidence is intentionally unknown.
    """
    if (image is None or getattr(image, "ndim", 0) != 3 or image.shape[0] < 800
            or image.shape[1] < 450 or not .50 <= image.shape[1] / image.shape[0] <= .60):
        return None
    hsv = cv2.cvtColor(cv2.resize(image, (1080, 1920)), cv2.COLOR_BGR2HSV)
    yy, xx = np.ogrid[:205, :220]
    radius = (xx - 110) ** 2 + (yy - 90) ** 2
    ring = (radius >= 70 ** 2) & (radius <= 102 ** 2)
    active = []
    for side, y in (("opponent", 20), ("self", 1650)):
        face = hsv[y:y + 205, 430:650]
        yellow = cv2.inRange(face, (25, 150, 150), (40, 255, 255))
        if np.count_nonzero(yellow[ring]) < 600:
            continue
        digits = cv2.inRange(face[45:140, 35:185], (0, 0, 170), (179, 110, 255))
        _, _, components, _ = cv2.connectedComponentsWithStats(digits)
        if any(10 <= w <= 65 and 45 <= h <= 70 and area >= 180
               for _, _, w, h, area in components[1:]):
            active.append(side)
    return active[0] if len(active) == 1 else None


class OpponentWaitMonitor:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.last_long_notice = float("-inf")
        self.begin_wait()

    def begin_wait(self):
        self.started = self.clock()
        self.matches = 0
        self.clears = 0
        self.stable_frames = 0
        self.visual = None
        self.offline = False
        self.offline_notified = False
        self.long_notified = False

    def observe_screen(self, image):
        if image is None or getattr(image, "ndim", 0) != 3:
            self.matches = self.clears = 0
            self.visual = None
            return None
        self.visual = opponent_disconnected(image)
        self.matches = self.matches + 1 if self.visual else 0
        if self.visual:
            self.clears = 0
        if self.matches >= 2 and not self.offline_notified:
            self.offline = self.offline_notified = True
            return "disconnected"
        return None

    def observe_board(self, unchanged):
        if not unchanged or self.visual is None:
            self.stable_frames = self.clears = 0
            return None
        self.stable_frames += 1
        if self.offline and not self.visual:
            self.clears += 1
            if self.clears >= 2:
                self.offline = False
                return "cleared"  # Cancel stale speech; do not claim a reconnection.
        now = self.clock()
        if (not self.offline_notified and not self.long_notified
                and self.stable_frames >= 2 and now - self.started >= 20
                and now - self.last_long_notice >= 180):
            self.long_notified = True
            self.last_long_notice = now
            return "waiting_long"
        return None
