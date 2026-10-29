"""Strictly scoped cleanup for generated chess-assistant artifacts."""
from __future__ import annotations

from pathlib import Path


HERE = Path(__file__).resolve().parent
WORK = HERE / "work"
DIAGNOSTICS = WORK / "diagnostics"
TTS_CACHE = Path(r"D:\environment\shuzhiren\audio\chess-live")


def _generated_candidates():
    if DIAGNOSTICS.is_dir():
        yield from (path for path in DIAGNOSTICS.rglob("*") if path.is_file())
    if WORK.is_dir():
        yield from WORK.glob("*.png")
        yield from WORK.glob("*.tmp")
    if TTS_CACHE.is_dir():
        yield from TTS_CACHE.glob("speech_*.wav")


def scan_generated_cache():
    files = []
    total = 0
    for path in dict.fromkeys(_generated_candidates()):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files.append(path)
        total += size
    return {"files": files, "bytes": total}


def _is_allowed(path):
    path = Path(path).resolve()
    if path.parent == WORK.resolve() and path.suffix.lower() in (".png", ".tmp"):
        return True
    try:
        path.relative_to(DIAGNOSTICS.resolve())
        return True
    except ValueError:
        pass
    return (path.parent == TTS_CACHE.resolve() and
            path.name.startswith("speech_") and path.suffix.lower() == ".wav")


def cleanup_generated_cache(files=None):
    chosen = scan_generated_cache()["files"] if files is None else files
    deleted = 0
    freed = 0
    failed = []
    for candidate in chosen:
        path = Path(candidate)
        if not _is_allowed(path):
            failed.append((str(path), "不在允许清理的缓存范围内"))
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            deleted += 1
            freed += size
        except FileNotFoundError:
            continue
        except OSError as error:
            failed.append((str(path), str(error)))
    return {"deleted": deleted, "bytes": freed, "failed": failed}
