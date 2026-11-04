"""Small live control channel shared by the desktop and its player process."""
import json
import os
from pathlib import Path
import tempfile


CONTROL_PATH = Path(__file__).resolve().parent / "work" / f"chess_controls_{os.getpid()}.json"


def write_auto_continue(enabled, path=CONTROL_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="chess_controls_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"auto_continue": bool(enabled)}, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def auto_continue_enabled(path=None, *, default=True):
    if path is None:
        return bool(default)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return isinstance(data, dict) and data.get("auto_continue") is True
    except (OSError, ValueError, TypeError):
        # A controlled child must never fall back to an older enabled value
        # when it cannot confirm its owner's current setting.
        return False
