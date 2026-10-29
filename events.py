from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from runtime_status import rotate_log


class EventLog:
    """Append-only JSONL event stream for TTS and live-stream integrations."""

    def __init__(self, path: str | None, *, max_bytes=8 * 1024 * 1024,
                 backups=2):
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
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
        with self._lock:
            rotate_log(self.path, max_bytes=self.max_bytes,
                       backups=self.backups)
            with open(self.path, "a", encoding="utf-8") as stream:
                stream.write(line + "\n")

