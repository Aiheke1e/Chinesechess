"""Conservative names for verified one-move wins, independent of JJ animations.

The 17-name reference catalog follows a community JJ effects collection;
these are chess-shape rules, not JJ's private recognition or priority rules.
"""
from board import Board, RED, BLACK
from notation import move_description


PATTERNS = {
    "马后炮": "马封住将边的路口，也给身后的炮当架。马管去路，炮管这一将，最后合在同一条线上。",
    "重炮": "两门炮一前一后，前炮当架，后炮将军。宫里的应对已经走不通，这一线由双炮接力收口。",
    "钓鱼马": "马守住将边的落脚点，车负责最后这一将。看着是一辆车出手，其实马早把路口占好了。",
    "列马车": "车贴近将门，马护住这辆车。将想吃车，马不答应；再想换条路，这个局面也没留出口。",
    "双车错": "两辆车交错着将军，一辆管这一线，另一辆接下一线。车轮着上班，将却找不到下班的路。",
    "铁门栓": "炮镇住中路，车或兵把将门封住。门口这一关，宫里的应对也被牵住，像给九宫上了一道门栓。",
    "天地炮": "一路中炮，一路底炮，两边互相配合。一个盯住宫里的应对，一个负责最后将军，把这片九宫收住。",
    "夹车炮": "两门炮和车列在一条线上，互相接应。不是单个棋子硬闯，是这一组车炮把最后的路封好。",
    "卧槽马": "马跳到卧槽点，直接向将门招呼。别看它只走一个日字，这一跳正好把最后的应对问住。",
    "八角马": "马与将隔着九宫成对角，先管住将边的要点，再由车完成最后一将。位置站得准，比跑得多更管用。",
    "侧面虎": "马从九宫侧面看住将门，车在同一侧收口。没有大喊大叫，这只虎就站在旁边，退路却已经没了。",
    "对面笑": "咱们的将帅也在帮忙，隔着棋盘守住一条直线。对面不能走到这条线上碰面，最后这一将就有了着落。",
    "闷宫": "将身边的士把宫门挤住，炮借着这道炮架将军。平时是护驾的阵形，这个局面反倒把出口占满了。",
    "闷杀": "将旁边的格子被自家棋子挤住，外面的将军又解不开。看着身边人不少，这一刻却谁也腾不出地方。",
    "双将": "两个棋子同时将军，挡住一路，另一路仍在。宫里顾得了这边，顾不了那边，两路火力一同给这盘棋画个句号。",
    "困毙": "这一步没有将军，但对面所有棋子都没有合法着法。棋盘还摆着，人却无路可走，这就是困毙取胜。",
    "海底捞月": "车绕到将的背后，底线上的炮又护住这辆车。正面绕不开的门，这一回从身后把它关上。",
}
ALIASES = {"列马车": ("立马车",), "铁门栓": ("铁门闩",),
           "对面笑": ("白脸将",), "侧面虎": ("高钓马",)}


def _between(a, b):
    ar, ac = divmod(a, 9)
    br, bc = divmod(b, 9)
    if ar == br:
        return [ar * 9 + c for c in range(min(ac, bc) + 1, max(ac, bc))]
    if ac == bc:
        return [r * 9 + ac for r in range(min(ar, br) + 1, max(ar, br))]
    return None


def _controls(board, source, target):
    if source == target:
        return False
    probe = board.copy()
    probe.sq[target] = "p"
    return any(to == target for _, to in probe.gen_moves(RED, idx_filter=source))


def _canonical(board):
    if board.turn == BLACK:
        return board.copy()
    return Board([p.swapcase() if p else None for p in reversed(board.sq)], BLACK)


def identify_pattern(final, alternating_rooks=False):
    """Only name terminal positions; uncertain combinations retain a generic name."""
    attacker = RED if final.turn == BLACK else BLACK
    if (final.king_pos(RED) < 0 or final.king_pos(BLACK) < 0
            or final.in_check(attacker) or final.legal_moves()):
        return None
    board = _canonical(final)
    king = board.king_pos(BLACK)
    kr, kc = divmod(king, 9)
    if not board.in_check(BLACK):
        return "困毙"
    pieces = {kind: [i for i, p in enumerate(board.sq) if p == kind] for kind in "RCNKP"}
    checkers = {source for source, to in board.gen_moves(RED) if to == king}
    needed_cache = {}
    def needed(index):
        if index not in needed_cache:
            probe = board.copy()
            probe.sq[index] = None
            needed_cache[index] = not probe.in_check(BLACK) or bool(probe.legal_moves())
        return needed_cache[index]
    def screen(source):
        line = _between(source, king)
        return [i for i in (line or []) if board.sq[i]]

    # A conservative aligned form of 夹车炮: both cannons and the intervening
    # rook must actually contribute to the mate, not merely be present.
    for first, second in ((a, b) for a in pieces["C"] for b in pieces["C"] if a < b):
        line = _between(first, second)
        if line and needed(first) and needed(second) and any(
                rook in line and needed(rook) for rook in pieces["R"]):
            return "夹车炮"
    for cannon in pieces["C"]:
        if cannon not in checkers:
            continue
        blockers = screen(cannon)
        if len(blockers) != 1:
            continue
        support = blockers[0]
        if board.sq[support] == "C":
            return "重炮"
        if board.sq[support] == "N":
            sr, sc = divmod(support, 9)
            if abs(sr - kr) + abs(sc - kc) == 2 and (
                    (sr == kr or sc == kc) and any(
                        _controls(board, support, r * 9 + c)
                        for r, c in ((kr - 1, kc), (kr + 1, kc), (kr, kc - 1), (kr, kc + 1))
                        if 0 <= r <= 2 and 3 <= c <= 5)):
                return "马后炮"
        if kr == 0 and cannon // 9 == 0:
            if any(c % 9 == 4 and c // 9 > 0 and needed(c) for c in pieces["C"] if c != cannon):
                return "天地炮"
            if king == 4 and board.sq[13] == "a" and (board.sq[3] == "a" or board.sq[5] == "a"):
                return "闷宫"

    for rook in pieces["R"]:
        if rook not in checkers:
            continue
        rr, rc = divmod(rook, 9)
        for horse in pieces["N"]:
            nr, nc = divmod(horse, 9)
            if (rr == kr == 0 and abs(rc - kc) == 1 and abs(nr - kr) == 2 and abs(nc - kc) == 2
                    and _controls(board, horse, rook) and needed(horse)):
                return "列马车"
        if rr == 0 and kr > 0 and rc == kc and any(
                c // 9 == 0 and _controls(board, c, rook) for c in pieces["C"]):
            return "海底捞月"
    if kr == 0 and kc == 4 and any(
            i in checkers and i // 9 == 0 and i % 9 in (3, 5)
            for i in pieces["R"] + pieces["P"]):
        if any(c % 9 == 4 and needed(c) for c in pieces["C"]):
            return "铁门栓"
    if alternating_rooks and any(r in checkers for r in pieces["R"]) and len(pieces["R"]) >= 2:
        return "双车错"
    if len(checkers) >= 2:
        return "双将"
    for horse in pieces["N"]:
        nr, nc = divmod(horse, 9)
        if horse in checkers and nr == 1 and nc in (2, 6):
            return "卧槽马"
        if not any(r in checkers for r in pieces["R"]) or not needed(horse):
            continue
        if nr in (0, 2) and nc in (3, 5) and abs(nr - kr) == abs(nc - kc) == 2:
            return "八角马"
        if nr == 2 and nc in (2, 6):
            return "钓鱼马"
        if nr == 3 and ((kc == 3 and nc == 2) or (kc == 5 and nc == 6)):
            return "侧面虎"
    if any(needed(k) for k in pieces["K"]):
        return "对面笑"
    neighbors = [(kr + dr) * 9 + kc + dc for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1))
                 if 0 <= kr + dr <= 2 and 3 <= kc + dc <= 5]
    if neighbors and all(board.sq[i] and board.sq[i].islower() for i in neighbors):
        return "闷杀"
    return "组合将杀"


def _alternating_rooks(event, previous, reply):
    if not previous or not reply:
        return False
    try:
        if int(event["number"]) != int(previous["number"]) + 1:
            return False
        board = Board.from_fen(previous["fen_before"])
        first, response, current = [Board.from_ucci(x) for x in
                                   (previous["ucci"], reply["ucci"], event["ucci"])]
        if board.sq[first[0]].upper() != "R" or first not in board.legal_moves():
            return False
        board.apply(first)
        checked = board.in_check(board.turn)
        if response not in board.legal_moves():
            return False
        board.apply(response)
        observed = Board.from_fen(event["fen_before"])
        return (checked and board.sq == observed.sq and board.turn == observed.turn
                and board.sq[current[0]] is not None and board.sq[current[0]].upper() == "R"
                and first[1] != current[0])
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def build_finisher(event, previous=None, reply=None):
    """Verify the selected legal move itself ends the game, without another search."""
    try:
        board = Board.from_fen(event["fen_before"])
        move = Board.from_ucci(event["ucci"])
        defender = BLACK if board.turn == RED else RED
        if (board.king_pos(RED) < 0 or board.king_pos(BLACK) < 0
                or event.get("side", board.turn) != board.turn
                or board.in_check(defender) or move not in board.legal_moves()):
            return None
        notation = move_description(board, move)
        final = board.copy()
        final.apply(move)
        if final.king_pos(final.turn) < 0 or final.legal_moves():
            return None
        name = identify_pattern(final, _alternating_rooks(event, previous, reply))
        if not name:
            return None
        explanation = PATTERNS.get(name, "这不是一个棋子在忙，是几路子力合着把将门收住。挡、吃、挪将，都已经没有解围的路。")
        openings = (
            "棋友们，咱们轻一点，别把对面的将吵醒了。它还在九宫里找出口，咱们这边已经把收尾安排上了。",
            "这盘棋下到这里，棋子们该领盒饭下班了。不过咱们办事讲究，最后这班岗站好，再给对面的将办退房。",
            "先把掌声存一存，棋还差最后这一手。对面的将倒是想换个座位，可九宫就这么大，咱们已经把空位占齐了。",
        )
        spoken = notation.split(" ", 1)[-1].replace("，吃", "吃")
        number = int(event.get("number") or 1)
        ending = (f"接下来走{spoken}，落下就是困毙，对面无棋可走。" if name == "困毙" else
                  f"接下来走{spoken}，这一手落下，就是{name}。")
        text = openings[(number - 1) % len(openings)] + explanation + ending
        return {"name": name, "notation": notation, "text": text,
                "terminal_fen": final.to_fen(), "checkmate": final.in_check(final.turn)}
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return None


def confirmed_finisher_note(event, last_move):
    """A short landing confirmation, only for the exact predicted terminal board."""
    if event.get("result") != "won" or not last_move:
        return ""
    plan = last_move.get("finisher")
    if not plan or not event.get("fen"):
        return ""
    try:
        observed = Board.from_fen(event["fen"])
        expected = Board.from_fen(plan["terminal_fen"])
        verified = build_finisher(last_move)
        if (not verified or (verified["name"] != plan["name"] and plan["name"] != "双车错")
                or expected.sq != observed.sq or expected.turn != observed.turn
                or verified["terminal_fen"] != expected.to_fen()):
            return ""
        if plan["name"] == "困毙":
            return "落子确认，对面无棋可走，这盘拿下。"
        return f"落子确认，{plan['name']}成了，这盘拿下。"
    except (KeyError, TypeError, ValueError, AttributeError):
        return ""
