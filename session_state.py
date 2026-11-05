from __future__ import annotations

import json
import os
import tempfile
import time

from adb_bridge import screenshot
from board import Board, START_FEN
from recognizer import recognize_occupancy, recognize_presence_occupancy
from state import (occupancy_difference_count, resolve_legal_sequence_by_occupancy,
                   validate_board)


HERE = os.path.dirname(os.path.abspath(__file__))
SESSION_PATH = os.path.join(HERE, "work", "session_state.json")
BLOCKED_PATH = os.path.join(HERE, "work", "blocked_position.json")
BLOCK_MAX_AGE_SECONDS = 20.0


def infer_turn_from_standard(squares, max_plies=2):
    """Infer the true next side for an early position reached from start."""
    standard = Board.from_fen(START_FEN)
    occupancy = [None if piece is None else
                 ("R" if piece.isupper() else "r") for piece in squares]
    sequence = resolve_legal_sequence_by_occupancy(
        standard, occupancy, max_plies=max_plies)
    if sequence is None:
        return None
    for move in sequence:
        standard.apply(move)
    return standard.turn if standard.sq == squares else None


def save_session(board: Board, orientation: str, my_color: str,
                 tolerant_indices=(), active=True, path=SESSION_PATH, *,
                 own_moves=0, rejected_moves=None):
    data = {
        "version": 1,
        "active": bool(active),
        "fen": board.to_fen(),
        "orientation": orientation,
        "my_color": my_color,
        "tolerant_indices": sorted(set(tolerant_indices)),
        "own_moves": own_moves,
        "rejected_moves": rejected_moves or {},
    }
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="session_", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def load_session(path=SESSION_PATH):
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if (not data.get("active") or data.get("orientation") not in
            ("red_bottom", "black_bottom") or data.get("my_color") not in ("r", "b")):
        return None
    try:
        data["board"] = Board.from_fen(data["fen"])
    except (KeyError, ValueError):
        return None
    if validate_board(data["board"]):
        return None
    count = data.get("own_moves", 0)
    data["own_moves"] = count if type(count) is int and count >= 0 else 0
    rejected = data.get("rejected_moves")
    data["rejected_moves"] = ({fen: moves for fen, moves in rejected.items()
                               if isinstance(fen, str) and isinstance(moves, list)
                               and all(isinstance(move, str) for move in moves)}
                              if isinstance(rejected, dict) else {})
    # A first move may already be visible when an automatic session is created.
    # Recover the true side to move when this early position is uniquely
    # reachable from the standard start in at most two plies.
    inferred_turn = infer_turn_from_standard(data["board"].sq)
    if inferred_turn is not None:
        data["board"].turn = inferred_turn
    return data


def match_live_session(device, path=SESSION_PATH, stable_delay=0.12, *, first_image=None):
    """Confirm two matching frames and recover up to two legal plies."""
    data = load_session(path)
    if data is None:
        return None
    board = data["board"]
    first = screenshot(device) if first_image is None else first_image
    if first is None:
        return None

    def propose(observed, mismatches=0):
        # Two legal plies can change at most four occupancy cells.  Reject a
        # loading screen or unrelated new game before spending another capture.
        if observed is None or occupancy_difference_count(board.sq, observed) > 4 + mismatches:
            return None
        return resolve_legal_sequence_by_occupancy(
            board, observed, max_plies=2, max_mismatches=mismatches)

    observed = recognize_occupancy(first, data["orientation"])
    inferred_moves = propose(observed)
    presence = None
    if inferred_moves is None:
        # A persistent last-move glow can hide a real glyph from the strong
        # matcher.  Presence-only frames are accepted only when stable and a
        # unique 0..2-ply legal sequence explains them with at most one stray
        # hint/highlight intersection.
        presence = recognize_presence_occupancy(first, data["orientation"])
        inferred_moves = propose(presence, mismatches=1)
    if inferred_moves is None:
        return None
    time.sleep(stable_delay)
    second = screenshot(device)
    if second is None or recognize_occupancy(second, data["orientation"]) != observed:
        return None
    if presence is not None and recognize_presence_occupancy(second, data["orientation"]) != presence:
        return None
    for move in inferred_moves:
        if board.turn == data["my_color"]:
            data["own_moves"] = data.get("own_moves", 0) + 1
        board.apply(move)
    data["board"] = board
    data["inferred_moves"] = inferred_moves
    return data


def finish_session(path=SESSION_PATH):
    data = load_session(path)
    if data is None:
        return
    save_session(data["board"], data["orientation"], data["my_color"],
                 data.get("tolerant_indices", ()), active=False, path=path,
                 own_moves=data.get("own_moves", 0), rejected_moves=data.get("rejected_moves"))


def block_position(board: Board, reason: str, path=BLOCKED_PATH):
    """Persist a position that must not be auto-retried after unsafe input."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {"fen": board.to_fen(), "reason": reason, "time": time.time()}
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def clear_blocked_position(path=BLOCKED_PATH):
    """Remove a stale retry guard without touching the trusted session."""
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


def blocked_position_matches(squares, path=BLOCKED_PATH,
                             max_age=BLOCK_MAX_AGE_SECONDS):
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        blocked = Board.from_fen(data["fen"])
        created = float(data.get("time", 0))
    except (OSError, KeyError, ValueError, json.JSONDecodeError, TypeError):
        return False
    # The guard is only a short cooldown after uncertain input.  A permanent
    # guard can deadlock a perfectly valid board when JJ ignores a tap or an
    # animation temporarily hides the moved piece.
    if max_age is not None and time.time() - created > float(max_age):
        return False
    return blocked.sq == squares
