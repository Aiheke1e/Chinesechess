from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "work", "runtime_status.json")


def write_runtime_status(stage, message="", *, details=None, path=DEFAULT_PATH):
    """Atomically publish the player heartbeat for the desktop watchdog."""
    payload = {
        "pid": os.getpid(),
        "stage": str(stage),
        "message": str(message),
        "updated_at": time.time(),
        "updated_at_text": datetime.now().astimezone().isoformat(timespec="seconds"),
        "details": dict(details or {}),
    }
    folder = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(folder, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            prefix="runtime_status_", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        return True
    except OSError:
        return False


def read_runtime_status(path=DEFAULT_PATH):
    try:
        with open(path, encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            return None
        payload["updated_at"] = float(payload["updated_at"])
        payload["pid"] = int(payload["pid"])
        return payload
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def heartbeat_age(payload, now=None):
    if not payload:
        return None
    return max(0.0, float(now if now is not None else time.time()) -
               float(payload["updated_at"]))


def rotate_log(path, *, max_bytes=2 * 1024 * 1024, backups=2):
    """Rotate a text log before appending; never touches unrelated files."""
    try:
        if os.path.getsize(path) < max_bytes:
            return False
    except OSError:
        return False
    try:
        oldest = f"{path}.{backups}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for index in range(backups - 1, 0, -1):
            source = f"{path}.{index}"
            if os.path.exists(source):
                os.replace(source, f"{path}.{index + 1}")
        os.replace(path, f"{path}.1")
        return True
    except OSError:
        return False
