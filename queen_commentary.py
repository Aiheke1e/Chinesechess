"""QUEEN-style evidence builder for Xiangqi commentary.

The language model never chooses a move here.  It receives three alternatives
already searched by Pikafish and factual features calculated from the board.
This mirrors QUEEN's compare-then-consolidate idea without adding another
engine search to the move-critical path.
"""
from __future__ import annotations

from board import BLACK, RED, Board, color_of
from notation import PIECE_NAMES, file_name, move_description


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


def _legal_from(board, origin):
    """Probe one piece only; no engine search or mutation of the live board."""
    piece = board.sq[origin]
    if not piece:
        return []
    side = color_of(piece)
    result = []
    for move in board.gen_moves(side, origin):
        probe = board.copy()
        probe.apply(move)
        if not probe.in_check(side):
            result.append(move)
    return result


def _defended(board, origin):
    side = color_of(board.sq[origin])
    targets = set()
    for index, piece in enumerate(board.sq):
        if index == origin or not piece or color_of(piece) != side or piece.upper() not in "RNC":
            continue
        probe = board.copy()
        probe.sq[index] = piece.swapcase()
        if (origin, index) in _legal_from(probe, origin):
            targets.add(index)
    return targets


def _attackers(board, target, enemy):
    return {move[0] for move in board.gen_moves(enemy)
            if move[1] == target and move in _legal_from(board, move[0])}


def _attacked(board, target, enemy):
    return bool(_attackers(board, target, enemy))


def _important_attack(board, target, enemy):
    """Do not call a normal defended, equal-or-better exchange an escape."""
    attackers = _attackers(board, target, enemy)
    if not attackers:
        return False
    piece = board.sq[target]
    if any(PIECE_VALUE[board.sq[source].upper()] < PIECE_VALUE[piece.upper()]
           for source in attackers):
        return True
    probe = board.copy()
    probe.sq[target] = piece.swapcase()
    return not _attacked(probe, target, color_of(piece))


def _has_defender(board, target):
    piece = board.sq[target]
    probe = board.copy()
    probe.sq[target] = piece.swapcase()
    return _attacked(probe, target, color_of(piece))


def _piece_label(board, index):
    piece = board.sq[index]
    number = file_name(color_of(piece), index % 9).translate(
        str.maketrans("123456789", "一二三四五六七八九"))
    return number + "路" + PIECE_NAMES[piece]


def move_effects(board, move):
    """Describe verified changes caused by this move, without predicting a reply."""
    origin, destination = move
    piece = board.sq[origin]
    if not piece or color_of(piece) != board.turn or move not in _legal_from(board, origin):
        return []
    after = board.copy()
    after.apply(move)
    side, enemy = board.turn, after.turn
    subject = {"R": "这辆车", "C": "这门炮", "N": "这匹马", "B": "这枚相" if side == RED else "这枚象",
               "A": "这枚仕" if side == RED else "这枚士", "P": "这枚兵" if side == RED else "这枚卒",
               "K": "咱们的帅" if side == RED else "咱们的将"}[piece.upper()]
    effects = []
    before_moves, after_moves = _legal_from(board, origin), _legal_from(after, destination)
    old_targets = {target for _, target in before_moves if board.sq[target]}
    new_targets = [target for _, target in after_moves if after.sq[target] and
                   after.sq[target].upper() in "RNC" and target not in old_targets
                   and (PIECE_VALUE[piece.upper()] < PIECE_VALUE[after.sq[target].upper()]
                        or not _has_defender(after, target))]
    if new_targets:
        target = max(new_targets, key=lambda index: PIECE_VALUE[after.sq[index].upper()])
        effects.append(f"{subject}盯住了对面的{_piece_label(after, target)}。")
    protected = _defended(after, destination) - _defended(board, origin)
    # A new geometric connection to an unthreatened piece is not a reason to
    # speak.  Explain protection only when a real attack makes it relevant.
    protected = {target for target in protected if _important_attack(board, target, enemy)
                 and _attacked(after, target, enemy)}
    if protected:
        target = max(protected, key=lambda index: PIECE_VALUE[after.sq[index].upper()])
        effects.insert(0, f"{subject}给正在受攻的{_piece_label(after, target)}补上保护。")
    if piece.upper() in "RNC" and _important_attack(board, origin, enemy) and not _attacked(after, destination, enemy):
        effects.insert(0, f"{subject}避开了对面的攻击。")
    from_row, to_row = origin // 9, destination // 9
    if piece.upper() == "P" and (from_row == 5 and to_row == 4 if side == RED else from_row == 4 and to_row == 5):
        effects.append(f"{subject}刚过河，之后也能横着走了。")
    return effects


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
            "回应坐标": pv[1] if len(line) >= 2 else None,
            "引擎值": _score(analysis),
        })

    chosen_notation = move_description(board, chosen_move)
    chosen_row = next((row for row in candidate_rows
                       if row["走法"] == chosen_notation), None)
    best_score = chosen_row["引擎值"] if chosen_row else None
    alternatives = []
    for row in candidate_rows:
        if row is chosen_row:
            continue
        gap = None if best_score is None or row["引擎值"] is None else best_score - row["引擎值"]
        if gap is None:
            comparison = "作为备选，但缺少足够证据判断差距"
        elif gap <= -60:
            comparison = "此候选评价更高，当前排序需要复核"
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
    reply = chosen_row["后续路线"][0] if (
        chosen_row and chosen_row["后续路线"]) else ""
    reply_recaptures = False
    if chosen_row and chosen_row["回应坐标"]:
        response = Board.from_ucci(chosen_row["回应坐标"])
        reply_recaptures = response[1] == chosen_move[1]
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
        "本手作用": move_effects(board, chosen_move),
        "最可能应手": reply,
        "应手回吃本子": reply_recaptures,
        "候选取舍": alternatives,
        "主线参考": chosen_row["后续路线"] if chosen_row else [],
        "值得展开": bool(tactical or score_gap),
        "事实边界": "只能改写以上事实，不得增加杀棋、弃子或战术结论",
    }
