"""Compact CCPD opening-book lookup used by the live commentator."""
from __future__ import annotations

from functools import lru_cache
import gzip
import json
from pathlib import Path
import re


HERE = Path(__file__).resolve().parent
DEFAULT_BOOK = HERE / "data" / "opening_book.json.gz"

_TRANSLATION = str.maketrans({
    "車": "车", "馬": "马", "傌": "马", "帥": "帅", "將": "将",
    "進": "进", "後": "后", "砲": "炮", "紅": "红",
    "１": "1", "２": "2",
    "３": "3", "４": "4", "５": "5", "６": "6", "７": "7",
    "８": "8", "９": "9", "０": "0",
})
_NAME_TRANSLATION = str.maketrans({
    "開": "开", "傌": "马", "馬": "马", "過": "过", "宮": "宫",
    "飛": "飞", "鉤": "钩", "門": "门", "佈": "布", "風": "风",
    "順": "顺", "邊": "边", "進": "进", "車": "车", "後": "后",
})


def normalize_notation(value):
    """Normalize traditional/full-width WXF text to the assistant's format."""
    text = str(value or "").translate(_TRANSLATION)
    text = re.sub(r"^(红方|黑方)\s*", "", text.strip())
    return re.sub(r"\s+", "", text)


def normalize_opening_name(value):
    return str(value or "").translate(_NAME_TRANSLATION).strip()


class OpeningLibrary:
    def __init__(self, entries=None, metadata=None):
        self.entries = entries or {}
        self.metadata = metadata or {}
        self.max_plies = int(self.metadata.get("max_plies", 24))

    @classmethod
    def from_path(cls, path=DEFAULT_BOOK):
        path = Path(path)
        if not path.is_file():
            return cls(metadata={"available": False, "path": str(path)})
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        metadata = dict(payload.get("metadata") or {})
        metadata.update({"available": True, "path": str(path)})
        return cls(payload.get("entries") or {}, metadata)

    @property
    def available(self):
        return bool(self.metadata.get("available", bool(self.entries)))

    def lookup(self, moves, min_plies=4):
        normalized = [normalize_notation(move) for move in moves]
        normalized = [move for move in normalized if move]
        upper = min(len(normalized), self.max_plies)
        for depth in range(upper, max(0, int(min_plies)) - 1, -1):
            key = "|".join(normalized[:depth])
            entry = self.entries.get(key)
            if entry:
                return {**entry, "matched_plies": depth}
        return None


@lru_cache(maxsize=1)
def load_opening_library(path=None):
    return OpeningLibrary.from_path(path or DEFAULT_BOOK)

