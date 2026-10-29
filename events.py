from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone


class EventLog:
    """Append-only JSONL event stream for TTS and live-stream integrations."""

    def __init__(self, path: str | None):
        self.path = path
        self._lock = threading.Lock()

    def emit(self, event: str, **data):
        if not self.path:
            return
        record = {
            "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
            "event": event,
            **data,
        }
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock, open(self.path, "a", encoding="utf-8") as stream:
            stream.write(line + "\n")

