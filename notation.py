from __future__ import annotations

from board import Board, color_of


RED_NUMBERS = "一二三四五六七八九"
PIECE_NAMES = {
    "K": "帅", "A": "仕", "B": "相", "N": "马",
    "R": "车", "C": "炮", "P": "兵",
    "k": "将", "a": "士", "b": "象", "n": "马",
    "r": "车", "c": "炮", "p": "卒",
}


def file_name(color: str, column: int) -> str:
    """Traditional notation: red uses Chinese reversed files, black Arabic."""
    return RED_NUMBERS[8 - column] if color == "r" else str(column + 1)


def step_name(color: str, amount: int) -> str:
    return RED_NUMBERS[amount - 1] if color == "r" else str(amount)


def move_notation(board: Board, move: tuple[int, int], include_side=True) -> str:
    """Return readable Chinese chess notation, e.g. 红方 炮二平五."""
    frm, to = move
    piece = board.sq[frm]
    if piece is None:
        return Board.ucci(move)
    color = color_of(piece)
    from_row, from_col = divmod(frm, 9)
    to_row, to_col = divmod(to, 9)
    prefix = ("红方 " if color == "r" else "黑方 ") if include_side else ""
    name = PIECE_NAMES.get(piece, piece)
    source_file = file_name(color, from_col)

    if from_row == to_row:
        action = "平"
        destination = file_name(color, to_col)
    else:
        forward = to_row < from_row if color == "r" else to_row > from_row
        action = "进" if forward else "退"
        if piece.upper() in "NAB":
            destination = file_name(color, to_col)
        else:
            destination = step_name(color, abs(to_row - from_row))
    return f"{prefix}{name}{source_file}{action}{destination}"


def move_description(board: Board, move: tuple[int, int], include_side=True) -> str:
    captured = board.sq[move[1]]
    text = move_notation(board, move, include_side=include_side)
    if captured:
        text += f"，吃{PIECE_NAMES.get(captured, captured)}"
    return text

