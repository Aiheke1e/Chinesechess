"""Short broadcast notes grounded in confirmed moves and existing engine lines."""
import re

from board import Board
from notation import PIECE_NAMES, move_notation
from commentary_style import piece_amount


def spoken_move(notation):
    return re.sub(r"^(?:红方|黑方)\s+", "", str(notation or "").strip())


def complete_note(text, limit=56):
    """Keep at most two complete sentences, without cutting a sentence in half."""
    result = ""
    for part in re.findall(r"[^。！？!?]+[。！？!?]?", str(text or ""))[:2]:
        part = part.strip()
        if not part:
            continue
        if part[-1] not in "。！？!?":
            part += "。"
        if len(result + part) > limit:
            break
        result += part
    return result


def waiting_note(evidence, used=(), *, allow_reply=False):
    """Add one new consequence, instead of explaining a capture as 'to capture'."""
    evidence = evidence or {}
    for effect in evidence.get("本手作用") or ():
        if effect not in used:
            return complete_note(effect)
    if not allow_reply:
        return ""
    route = list(evidence.get("主线参考") or [])
    reply = spoken_move(evidence.get("最可能应手"))
    if route and spoken_move(route[0]) == reply and len(route) >= 2:
        next_move = spoken_move(route[1])
        if "，吃" in next_move:
            response = reply.replace("，吃", "吃")
            plan = next_move.replace("，吃", "吃")
            return complete_note(f"如果对面{response}，咱们可以{plan}。")
    if reply and "，吃" in reply:
        move, target = reply.rsplit("，吃", 1)
        if target[:1] not in "车马炮兵卒仕士相象帅将":
            return ""
        if evidence.get("应手回吃本子"):
            return complete_note(f"对手可能{move}，吃回这{piece_amount(target[:1])[1:]}，这轮交换要一起看。")
        return complete_note(f"对手可能{move}，吃掉咱们{piece_amount(target[:1])}。")
    # Spectators follow the played line. Engine alternatives are never a
    # reason to introduce a second choice into the broadcast.
    return ""


def opponent_wait_note(status, number=0, *, playful=True):
    if status == "disconnected":
        if not playful:
            return "对面显示断线了，先等他重连。"
        lines = (
            "对面显示断线了，信号先溜了。咱们等他连回来。",
            "对面断线了，别是这步棋把信号吓跑了。先给他一点重连时间。",
            "对面掉线了，咱们只下棋，可没碰他网线。等他回来再接着下。",
        )
        return lines[int(number) % len(lines)]
    if status == "waiting_long":
        lines = ("对面这手还没走，咱们先等等。手别急，棋盘又不会自己跑。",
                 "这会儿还没新落子，兄弟们也歇口气。",
                 "对面这手还没落，咱们先等一下，别替人家着急。",
                 "棋盘暂时没变化，继续等对面这一手。")
        return lines[int(number)%len(lines)] if playful else "对面暂时还没落子，继续等他这一步。"
    return ""


def opponent_capture_note(event, previous=None):
    """Distinguish a confirmed recapture from an unexplained piece loss."""
    move = spoken_move(event.get("notation"))
    captured = move.rsplit("，吃", 1)[-1][:1] if "，吃" in move else ""
    if not captured:
        return ""
    neutral = f"对面{move.split('，吃', 1)[0]}，吃了咱们{piece_amount(captured)}。"
    if not previous:
        return neutral
    try:
        board = Board.from_fen(previous["fen_before"])
        first, reply = Board.from_ucci(previous["ucci"]), Board.from_ucci(event["ucci"])
        taken, ours = board.sq[first[1]], board.sq[first[0]]
        if not taken or first not in board.legal_moves():
            return neutral
        board.apply(first)
        if reply not in board.legal_moves() or reply[1] != first[1]:
            return neutral
        board.apply(reply)
        observed = Board.from_fen(event["fen"])
        if observed.sq != board.sq or observed.turn != board.turn:
            return neutral
        if taken.upper() == ours.upper():
            kind = "兵卒" if ours.upper() == "P" else PIECE_NAMES[ours]
            return f"对面{move.split('，吃', 1)[0]}，吃回{piece_amount(PIECE_NAMES[ours])}，这轮是兑{kind}。"
        return f"对面吃回{piece_amount(PIECE_NAMES[ours])}，这轮咱们用它换了{piece_amount(PIECE_NAMES[taken])}。"
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return neutral


def confirmed_win_note(event, last_move):
    """Describe the final tactic only when a confirmed terminal board proves it."""
    if event.get("result") != "won" or event.get("reason") != "no_legal_moves" or not last_move:
        return ""
    try:
        board = Board.from_fen(last_move["fen_before"])
        move = Board.from_ucci(last_move["ucci"])
        if move not in board.legal_moves():
            return ""
        captured = board.sq[move[1]]
        board.apply(move)
        final = Board.from_fen(event["fen"])
        if board.sq != final.sq or board.turn != final.turn or board.legal_moves():
            return ""
        if board.in_check(board.turn):
            action = f"吃{PIECE_NAMES[captured]}" if captured else ""
            return f"这手{action}将住了，这盘拿下！"
        return "对面已经无棋可走，这盘拿下。"
    except (KeyError, ValueError, TypeError, IndexError):
        return ""


def connected_move_note(current, previous, reply):
    """Link the same piece only after replaying a confirmed move and real reply."""
    if not previous or not reply:
        return ""
    try:
        if int(current["number"]) != int(previous["number"]) + 1:
            return ""
        board = Board.from_fen(previous["fen_before"])
        first = Board.from_ucci(previous["ucci"])
        response = Board.from_ucci(reply["ucci"])
        move = Board.from_ucci(current["ucci"])
        piece = board.sq[first[0]]
        first_capture = board.sq[first[1]]
        previous_check = False
        for step in (first, response):
            if step not in board.legal_moves():
                return ""
            board.apply(step)
            if step == first:
                previous_check = board.in_check(board.turn)
        observed = Board.from_fen(current["fen_before"])
        if board.sq != observed.sq or board.turn != observed.turn:
            return ""
        if first[1] != move[0] or board.sq[move[0]] != piece or move not in board.legal_moves():
            return ""
        captured = board.sq[move[1]]
        action = move_notation(board, move, include_side=False)
        board.apply(move)
        checking = board.in_check(board.turn)
        names = {"R": "这辆车", "C": "这门炮", "N": "这匹马"}
        name = names.get(piece.upper())
        if not name:
            return ""
        if captured and first_capture:
            target = PIECE_NAMES[captured]
            return f"{action}，再吃{piece_amount(target)}" + ("，同时将军。" if checking else "。")
        if checking and previous_check:
            capture = f"吃掉{piece_amount(PIECE_NAMES[captured])}，" if captured else ""
            return f"{action}，{capture}继续将军。"
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return ""
    return ""
