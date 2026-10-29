"""Detect the JJ board grid from a standard-start screenshot."""
from __future__ import annotations

from itertools import combinations

import cv2
import numpy as np


def _clusters(values, tolerance=14.0):
    groups = []
    for value in sorted(float(v) for v in values):
        if not groups or value - np.mean(groups[-1]) > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [(float(np.median(group)), len(group)) for group in groups]


def _best_axis(candidates, logical, max_error=10.0):
    """Fit observed clusters to known logical row/column indexes."""
    if len(candidates) < len(logical):
        return None
    best = None
    # Candidate lists are small (normally 9 x-clusters and 6 y-clusters).
    for chosen in combinations(candidates, len(logical)):
        observed = np.array([item[0] for item in chosen], dtype=float)
        counts = sum(item[1] for item in chosen)
        slope, intercept = np.polyfit(np.array(logical, dtype=float), observed, 1)
        residual = float(np.max(np.abs(observed - (intercept + slope * np.array(logical)))))
        score = residual - min(counts, 40) * 0.02
        if slope > 0 and (best is None or score < best[0]):
            best = (score, residual, float(intercept), float(slope))
    if best is None or best[1] > max_error:
        return None
    return best[2], best[3], best[1]


def detect_standard_geometry(image):
    """Return {ox, oy, dx, dy} for a standard 32-piece opening frame.

    The detector uses piece circles, then fits their centers to occupied opening
    rows 0/2/3/6/7/9 and all nine files.  It refuses non-opening or ambiguous
    frames instead of producing approximate click coordinates.
    """
    if image is None or image.ndim != 3:
        return None
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (9, 9), 2)
    scale = width / 1080.0
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1.2,
        minDist=max(45, int(70 * scale)), param1=100, param2=45,
        minRadius=max(25, int(38 * scale)), maxRadius=max(40, int(66 * scale)))
    if circles is None:
        return None
    # The board occupies the central vertical band in the raw portrait frame.
    points = [(float(x), float(y)) for x, y, _ in circles[0]
              if 0.19 * height <= y <= 0.79 * height]
    x_clusters = [item for item in _clusters(x for x, _ in points) if item[1] >= 2]
    # Cannon rows contain only two pieces in the opening, so two detections are
    # sufficient for a y-cluster. The six-row pattern rejects unrelated circles.
    y_clusters = [item for item in _clusters(y for _, y in points) if item[1] >= 2]
    x_fit = _best_axis(x_clusters, list(range(9)), max_error=11 * scale)
    y_fit = _best_axis(y_clusters, [0, 2, 3, 6, 7, 9], max_error=12 * scale)
    if x_fit is None or y_fit is None:
        return None
    ox, dx, x_error = x_fit
    oy, dy, y_error = y_fit
    if not (0.08 * width < dx < 0.15 * width and
            0.045 * height < dy < 0.075 * height):
        return None
    return {"ox": round(ox, 3), "oy": round(oy, 3),
            "dx": round(dx, 3), "dy": round(dy, 3),
            "x_error": round(x_error, 3), "y_error": round(y_error, 3)}
