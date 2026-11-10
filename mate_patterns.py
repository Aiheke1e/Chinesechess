"""Conservative names for verified one-move wins, independent of JJ animations.

The 17-name reference catalog follows a community JJ effects collection;
these are chess-shape rules, not JJ's private recognition or priority rules.
"""
from board import Board, RED, BLACK
from notation import move_description, move_notation, PIECE_NAMES


PATTERNS = {
    "马后炮": "马封住将边的路，也给身后的炮当架。马管去路，炮来将军，最后在同一条线上收口。",
    "重炮": "两门炮一前一后，前炮当架，后炮将军。双炮配合，把将的应对都收住了。",
    "钓鱼马": "马看住将边的落脚点，车来完成最后一将。这辆车能收口，靠的是马先站住位置。",
    "列马车": "车贴着将门，马在身后护车。将吃不了车，换条路也找不到出口。",
    "双车错": "两辆车交错将军，轮流接力。前一辆刚管住一线，后一辆又把另一线接上。",
    "铁门栓": "炮镇住中路，车或兵封住将门。宫里被牵住，门口也被锁住。",
    "天地炮": "中炮和底炮一起配合，一门牵住宫里的应对，一门负责最后将军。",
    "夹车炮": "车和两门炮排在一条线上，前后接应，一起把最后的路封住。",
    "卧槽马": "马跳到卧槽点，直接将军。这个落点正好卡住最后的解围办法。",
    "八角马": "马与将在九宫两头成对角，先看住要点，再让车完成最后一将。",
    "侧面虎": "马在九宫侧面看住将门，车在同一侧将军。马管住退路，车来收口。",
    "对面笑": "咱们的将帅隔着棋盘牵住一条直线。对面不能沿这条线走，最后这一将就有了帮手。",
    "闷宫": "炮隔着对面的士将军，宫里的棋子又挡住了将的去路。将挪不开，这声将军也解不了。",
    "闷杀": "将旁边的格子被自家棋子占着，外面的将军又解不开。自家棋子把最后的路堵住了。",
    "双将": "两个棋子同时将军，挡住一路，还有另一路。将的其他解围办法也已经被封住。",
    "困毙": "这步没有将军，可对面所有棋子都没有合法着法。轮到它走，却一步也走不了，这就是困毙取胜。",
    "海底捞月": "车绕到将的背后，底线上的炮护住这辆车。车炮从背后配合，把最后的路收住。",
}
ALIASES = {"列马车": ("立马车",), "铁门栓": ("铁门闩",),
           "对面笑": ("白脸将",), "侧面虎": ("高钓马",)}

MATE_OPENINGS = (
    ("门", "将军想出门，咱们没给批假。"),
    ("下班", "棋子们到岗了，将军该下班了。"),
    ("收口", "这下门都关上了，钥匙咱们收着。"),
    ("座位", "九宫这么大，愣是没给他留座。"),
)
MATE_PUNCHLINES = {
    "马后炮": "这回的马后炮，可是真有炮在后面撑腰。",
    "重炮": "前面一门查票，后面一门收场，配合得明明白白。",
    "钓鱼马": "马把位置坐稳，车来收尾，搭档这回没白请。",
    "列马车": "车敢贴这么近，身后的马可不是来合影的。",
    "双车错": "两辆车轮班，将军却排不上休息号。",
    "铁门栓": "九宫这道门，咱们从外面把栓插上了。",
    "天地炮": "上面一路，下面一路，这个包围圈还分楼层。",
    "夹车炮": "车炮站成一排，这份团队作业谁也没偷懒。",
    "卧槽马": "马儿这一跳，压轴的镜头就归它了。",
    "八角马": "这匹马站位讲究，妥妥的关键岗位。",
    "侧面虎": "这只虎话不多，站在旁边就把事办了。",
    "对面笑": "咱们的老大也参与了，这回真没当甩手掌柜。",
    "闷宫": "护驾的多，也不一定就安全。",
    "闷杀": "身边同事倒是不少，可惜谁也腾不出个位。",
    "双将": "两边同时敲门，一双手可真忙不过来。",
    "困毙": "没有一声将军，照样把这道题做完了。",
    "海底捞月": "正面进不去，车绕个背后，把收尾办了。",
    "组合将杀": "几位棋子一起办事，谁也没白来这趟。",
}


def finisher_copy(name, notation, number=1, *, opening=None, playful=True, detail=""):
    spoken = notation.split(" ", 1)[-1].replace("，吃", "吃")
    # Keep the full verified escape details in build_finisher's facts, while
    # the live voice calls the move, names the finish and adds ONE short joke.
    if name == "困毙":
        line = f"走{spoken}，困毙，对面无棋可走。"
        joke = ("全队站得挺齐，可惜谁也动不了。", "轮到对面走，全队一起请假了。")[(int(number or 1)-1) % 2]
    else:
        line = f"走{spoken}，{name}收口。"
        joke = (opening or MATE_OPENINGS[(int(number or 1)-1) % len(MATE_OPENINGS)][1]
                if int(number or 1) % 3 else MATE_PUNCHLINES.get(name, "这下将军也没处打卡了。"))
    return line + (joke if playful else "")


def _escape_detail(final):
    """Name concrete king exits controlled in the verified terminal position."""
    loser, king = final.turn, final.king_pos(final.turn)
    attacker = RED if loser == BLACK else BLACK
    if king < 0 or not final.in_check(loser):
        return ""
    details = []
    for move in final.gen_moves(loser, king):
        if abs(move[0] // 9 - move[1] // 9) + abs(move[0] % 9 - move[1] % 9) != 1:
            continue
        probe = final.copy()
        probe.apply(move)
        sources = [a for a, b in probe.gen_moves(attacker) if b == move[1]]
        if sources:
            piece = PIECE_NAMES[probe.sq[sources[0]]]
            action = move_notation(final, move, include_side=False)
            details.append(f"想{action}，那个落点有咱们的{piece}看着。")
        if len(details) == 2:
            break
    return "".join(details)


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
        detail = _escape_detail(final)
        text = finisher_copy(name, notation, event.get("number", 1), detail=detail)
        return {"name": name, "notation": notation, "text": text,
                "detail": detail, "terminal_fen": final.to_fen(), "checkmate": final.in_check(final.turn)}
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
            return "对面无棋可走，这盘拿下！"
        return f"{plan['name']}成了，这盘拿下！"
    except (KeyError, TypeError, ValueError, AttributeError):
        return ""
