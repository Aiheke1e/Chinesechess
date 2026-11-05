"""Recognize explicit in-game JJ rule warnings, never result controls."""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


RULE_REJECTED = "RULE_REJECTED:"


@lru_cache(maxsize=1)
def _chase_template():
    try:
        data = (Path(__file__).resolve().parent / "assets/jj-rule-long-chase.png").read_bytes()
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    except (OSError, cv2.error):
        return None


def repetition_warning(image):
    """Read the observed '长捉违规请变招' text, independent of board theme."""
    if image is None or getattr(image, "ndim", 0) != 3:
        return False
    height, width = image.shape[:2]
    if height < 800 or width < 450 or not .48 <= width / height <= .65:
        return False
    template = _chase_template()
    if template is None:
        return False
    gray = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (1080, 1920))
    scores = cv2.matchTemplate(gray[865:1050, 330:750], template, cv2.TM_CCOEFF_NORMED)
    return cv2.minMaxLoc(scores)[1] >= .86
