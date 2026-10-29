"""Build a compact local opening index from the CC BY 4.0 CCPD dataset."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re

from opening_library import normalize_notation


HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = HERE / "data" / "ccpd_source"
DEFAULT_OUTPUT = HERE / "data" / "opening_book.json.gz"
MOVE_RE = re.compile(r"^(?:前|中|后)?[车马炮兵卒相象仕士帅将][一二三四五六七八九1-9][进退平][一二三四五六七八九1-9]$")


def decode_pgn(path):
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp950", "big5", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_pgn(path):
    text = decode_pgn(path)
    headers = dict(re.findall(r'^\[([^\s]+)\s+"(.*)"\]\s*$', text,
                              flags=re.MULTILINE))
    body = re.sub(r"^\[.*?\]\s*$", " ", text, flags=re.MULTILINE)
    body = re.sub(r"\{.*?\}", " ", body, flags=re.DOTALL)
    body = re.sub(r"\([^()]*(?:\([^()]*\)[^()]*)*\)", " ", body)
    body = re.sub(r"\d+\.(?:\.\.)?", " ", body)
    moves = []
    for token in body.split():
        if token in ("1-0", "0-1", "1/2-1/2", "*"):
            continue
        move = normalize_notation(token.strip("!?+#"))
        if MOVE_RE.fullmatch(move):
            moves.append(move)
    return headers, moves


def opening_name(path, root):
    relative = Path(path).relative_to(root)
    parts = relative.parts
    for marker in ("以開局分類", "以开局分类"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts) - 1:
                return parts[index + 1]
    for marker in ("開局", "开局"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts) - 1:
                return parts[index + 1]
    return Path(path).parent.name or "未分类开局"


def discover_roots(source):
    source = Path(source)
    dataset = source / "Dataset"
    if not dataset.is_dir():
        candidates = list(source.glob("*/Dataset"))
        dataset = candidates[0] if candidates else dataset
    roots = []
    for path in dataset.rglob("*"):
        if path.is_dir() and path.name in ("以開局分類", "以开局分类", "開局", "开局"):
            roots.append(path)
    return dataset, roots


def build_index(source, output, max_plies=24, min_games=2):
    dataset, roots = discover_roots(source)
    if not roots:
        raise FileNotFoundError("没有找到 CCPD 的开局或按开局分类目录")
    files = sorted({path for root in roots for path in root.rglob("*.pgn")})
    counts = defaultdict(Counter)
    ecco_counts = defaultdict(Counter)
    examples = defaultdict(list)
    accepted = 0
    for path in files:
        try:
            headers, moves = parse_pgn(path)
        except OSError:
            continue
        if len(moves) < 4:
            continue
        accepted += 1
        name = opening_name(path, dataset)
        ecco = headers.get("ECCO", "").strip()
        example = headers.get("Event", "").strip()
        for depth in range(4, min(int(max_plies), len(moves)) + 1):
            key = "|".join(moves[:depth])
            counts[key][name] += 1
            if ecco:
                ecco_counts[key][ecco] += 1
            if example and len(examples[key]) < 2:
                examples[key].append(example)

    entries = {}
    for key, names in counts.items():
        total = sum(names.values())
        name, top = names.most_common(1)[0]
        if total < int(min_games):
            continue
        ecco = ecco_counts[key].most_common(1)
        entries[key] = {
            "name": name,
            "games": total,
            "agreement": round(top / total, 3),
            "ecco": ecco[0][0] if ecco else "",
            "examples": examples[key],
        }
    payload = {
        "metadata": {
            "format": 1,
            "source": "Chinese Chess Practical Dataset (CCPD)",
            "source_url": "https://github.com/Yvonne761/Chinese-Chess-Practical-Dataset",
            "license": "CC BY 4.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_games": accepted,
            "entries": len(entries),
            "max_plies": int(max_plies),
        },
        "entries": entries,
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8", compresslevel=9) as stream:
        json.dump(payload, stream, ensure_ascii=False,
                  separators=(",", ":"))
    return payload["metadata"]


def main():
    parser = argparse.ArgumentParser(description="导入 CCPD 象棋开局棋谱")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-plies", type=int, default=24)
    parser.add_argument("--min-games", type=int, default=2)
    args = parser.parse_args()
    metadata = build_index(
        args.source, args.output, args.max_plies, args.min_games)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

