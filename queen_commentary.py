"""QUEEN-style evidence builder for Xiangqi commentary.

The language model never chooses a move here.  It receives three alternatives
already searched by Pikafish and factual features calculated from the board.
This mirrors QUEEN's compare-then-consolidate idea without adding another
engine search to the move-critical path.
"""
from __future__ import annotations

from board import BLACK, RED, Board, color_of
from notation import PIECE_NAMES, move_description


PIECE_VALUE = {"K": 0, "A": 2, "B": 2, "N": 4, "R": 9, "C": 4.5, "P": 1}


def _score(analysis):
    if not analysis:
        return None
    try:
        value = int(analysis.get("score", 0))
    except (TypeError, ValueError):
        return None
    if analysis.get("score_type") == "mate":
        return 100000 if value > 0 else -100000
    return value if analysis.get("score_type") == "cp" else None


def _material(board, side):
    own = other = 0.0
    for piece in board.sq:
        if not piece:
            continue
        value = PIECE_VALUE.get(piece.upper(), 0)
        if color_of(piece) == side:
            own += value
        else:
            other += value
    gap = own - other
    if gap >= 4:
        return "咱们子力明显占优"
    if gap >= 1:
        return "咱们多一点子力"
    if gap <= -4:
        return "咱们子力明显吃亏"
    if gap <= -1:
        return "咱们少一点子力"
    return "双方子力大致相当"


def _line(board, pv, limit=4):
    probe = board.copy()
    result = []
    for ucci in list(pv or [])[:limit]:
        try:
            move = Board.from_ucci(ucci)
        except (TypeError, ValueError):
            break
        if move not in probe.legal_moves():
            break
        result.append(move_description(probe, move))
        probe.apply(move)
    return result


def _move_facts(board, move):
    side = board.turn
    piece = board.sq[move[0]]
    captured = board.sq[move[1]]
    before_check = board.in_check(side)
    after = board.copy()
    after.apply(move)
    opponent = after.turn
    facts = []
    if captured:
        facts.append("吃掉" + PIECE_NAMES.get(captured, captured))
    if before_check:
        facts.append("先解除将军")
    if after.in_check(opponent):
        facts.append("形成将军")
    from_row, from_col = divmod(move[0], 9)
    to_row, to_col = divmod(move[1], 9)
    piece_name = PIECE_NAMES.get(piece, piece) if piece else "棋子"
    forward = to_row < from_row if side == RED else to_row > from_row
    if (piece and piece.upper() in "NRCP" and
            to_col == 4 and from_col != 4):
        facts.append("争夺中路")
    if piece and piece.upper() in "NRC":
        home_row = 9 if side == RED else 0
        if divmod(move[0], 9)[0] == home_row:
            facts.append("调动后排大子")
    # Positional facts below only describe geometry that is visible on the
    # board.  They deliberately avoid invented claims such as "施压" or
    # "制造威胁" when the search result does not prove one.
    if piece and piece.upper() in "RC":
        if from_row == to_row:
            facts.append(piece_name + "横向换线")
        elif forward:
            facts.append(piece_name + "向前推进")
        else:
            facts.append(piece_name + "回撤调整")
    elif piece and piece.upper() == "N":
        if abs(to_col - 4) < abs(from_col - 4):
            facts.append("马向中路靠拢")
        elif forward:
            facts.append("马向前跃进")
        else:
            facts.append("马回撤调整")
    elif piece and piece.upper() == "P":
        crossed_before = from_row <= 4 if side == RED else from_row >= 5
        crossed_after = to_row <= 4 if side == RED else to_row >= 5
        if from_row == to_row:
            if abs(to_col - 4) < abs(from_col - 4):
                facts.append("过河兵向中路靠拢")
            else:
                facts.append("过河兵横向换线")
        elif not crossed_before and crossed_after:
            facts.append(piece_name + "刚刚过河")
        elif crossed_after:
            facts.append("让过河兵参与进攻")
        else:
            facts.append(piece_name + "向前推进")
    elif piece and piece.upper() == "B":
        facts.append("飞" + piece_name + "调整防线")
    elif piece and piece.upper() == "A":
        facts.append("补" + piece_name + "守住宫门")
    elif piece and piece.upper() == "K":
        facts.append(piece_name + "调整位置")
    return list(dict.fromkeys(facts))


def build_queen_evidence(board, chosen_move, candidates):
    """Build compact, fully grounded evidence from an existing MultiPV result."""
    side = board.turn
    candidate_rows = []
    for rank, analysis in enumerate(list(candidates or [])[:3], 1):
        pv = list(analysis.get("pv") or [])
        if not pv:
            continue
        try:
            move = Board.from_ucci(pv[0])
        except (TypeError, ValueError):
            continue
        if move not in board.legal_moves():
            continue
        line = _line(board, pv)
        candidate_rows.append({
            "顺位": rank,
            "走法": move_description(board, move),
            "直接作用": _move_facts(board, move),
            "后续路线": line[1:4],
            "引擎值": _score(analysis),
        })

    best_score = candidate_rows[0]["引擎值"] if candidate_rows else None
    alternatives = []
    for row in candidate_rows[1:]:
        gap = None if best_score is None or row["引擎值"] is None else best_score - row["引擎值"]
        if gap is None:
            comparison = "作为备选，但缺少足够证据判断差距"
        elif gap >= 180:
            comparison = "明显不如首选稳妥"
        elif gap >= 60:
            comparison = "也能走，但首选更舒服"
        else:
            comparison = "与首选接近，属于另一种计划"
        alternatives.append({
            "走法": row["走法"],
            "作用": row["直接作用"],
            "取舍": comparison,
        })

    chosen_facts = _move_facts(board, chosen_move)
    reply = candidate_rows[0]["后续路线"][0] if (
        candidate_rows and candidate_rows[0]["后续路线"]) else ""
    tactical = any(word in "、".join(chosen_facts)
                   for word in ("吃掉", "将军", "解除"))
    score_gap = False
    if len(candidate_rows) > 1 and best_score is not None:
        score_gap = any(row["引擎值"] is not None and
                        best_score - row["引擎值"] >= 90
                        for row in candidate_rows[1:])
    return {
        "方法": "比较Pikafish前三候选后归纳，不允许模型自行猜招",
        "主播视角": "咱们" if side in (RED, BLACK) else "",
        "当前子力": _material(board, side),
        "最终选择": move_description(board, chosen_move),
        "选择理由": chosen_facts,
        "最可能应手": reply,
        "候选取舍": alternatives,
        "主线参考": (candidate_rows[0]["后续路线"] if candidate_rows else []),
        "值得展开": bool(tactical or score_gap),
        "事实边界": "只能改写以上事实，不得增加杀棋、弃子或战术结论",
    }
