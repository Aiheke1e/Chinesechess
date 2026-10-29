"""Save a complete visual snapshot when automatic play cannot continue."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

import cv2

from adb_bridge import current_package, screenshot


HERE = os.path.dirname(os.path.abspath(__file__))
DIAGNOSTIC_DIR = os.path.join(HERE, "work", "diagnostics")


def _safe_name(value):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value)).strip("_")
    return cleaned[:50] or "unknown"


def _write_png(path, image):
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise OSError("PNG encoding failed")
    encoded.tofile(path)


def _prune(folder, keep=80):
    images = sorted(
        (os.path.join(folder, name) for name in os.listdir(folder)
         if name.lower().endswith(".png")),
        key=os.path.getmtime,
        reverse=True,
    )
    for image_path in images[keep:]:
        for path in (image_path, os.path.splitext(image_path)[0] + ".json"):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def capture_diagnostic(device, reason, *, extra=None, image=None,
                       folder=DIAGNOSTIC_DIR):
    """Save screenshot + JSON context and return the PNG path.

    Diagnostics must never crash or alter the chess controller, so failures are
    deliberately converted to ``None``.
    """
    try:
        frame = screenshot(device) if image is None else image
        if frame is None:
            return None
        os.makedirs(folder, exist_ok=True)
        now = datetime.now().astimezone()
        stem = now.strftime("%Y%m%d_%H%M%S_%f_") + _safe_name(reason)
        image_path = os.path.join(folder, stem + ".png")
        json_path = os.path.join(folder, stem + ".json")
        _write_png(image_path, frame)
        package, activity = current_package(device)
        payload = {
            "time": now.isoformat(timespec="milliseconds"),
            "reason": str(reason),
            "device": device,
            "package": package,
            "activity": activity,
            "image": os.path.basename(image_path),
            "image_size": [int(frame.shape[1]), int(frame.shape[0])],
            "extra": extra or {},
        }
        with open(json_path, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        _prune(folder)
        return image_path
    except Exception:
        return None
