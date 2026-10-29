"""Compatibility bridge to Pikafish.

The former Python minimax/alpha-beta implementation was intentionally removed.
All move decisions now come from the bundled Pikafish engine.
"""
from __future__ import annotations

from board import Board
from pikafish import DEFAULT_EXE, Pikafish


def choose_move(board: Board, depth: int = 8, mode: str = "pikafish",
                engine_path: str = DEFAULT_EXE, **_kwargs):
    if mode not in ("pikafish", "ucci"):
        raise ValueError("内置 Alpha-Beta 已删除；仅支持 Pikafish")
    engine = Pikafish(engine_path)
    try:
        best = engine.bestmove(board.to_fen(), depth=depth)
    finally:
        engine.quit()
    if not best:
        return None, None
    move = Board.from_ucci(best)
    if move not in board.legal_moves():
        raise RuntimeError(f"Pikafish 返回非法着法: {best}")
    return move, depth
