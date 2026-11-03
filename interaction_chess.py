"""Read-only, bounded chess facts for selected comments; never choose a move."""
from collections import deque
import re
import threading
import time

from board import Board
from notation import move_description
from queen_commentary import move_effects


PIECES = "车马炮兵卒相象仕士帅将"
MOVE = re.compile(rf"[{PIECES}][一二三四五六七八九1-9][进退平][一二三四五六七八九1-9]")
CHESS = re.compile(rf"[{PIECES}][一二三四五六七八九1-9]|[打吃跳进出回退平飞补架拱][{PIECES}]|"
                   r"绝杀|漏杀|将死|闷宫|重炮|双杯|钓鱼马|这[盘局手步]|棋[局盘招力]|谁[赢输]|局势")


def chess_comment(text):
    return bool(CHESS.search(str(text)))


def incomplete_comment(text):
    return bool(re.fullmatch(rf"\s*(?:[1-9一二三四五六七八九]路|[{PIECES}][1-9]{{1,2}}|"
                             rf"[打吃跳进出回退平飞补架拱][{PIECES}])\s*[。！!]?", str(text)))


def _normalize(move):
    return str(move).translate(str.maketrans("一二三四五六七八九", "123456789"))


class ChessChatContext:
    """A snapshot of confirmed events, separate from commentary enable/pause."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with self.lock:
            self.analyses = {}
            self.moves = deque(maxlen=6)
            self.fen = ""
            self.side = ""
            self.updated = 0

    def observe(self, event):
        kind = event.get("event")
        with self.lock:
            if kind in ("start", "game_over"):
                self.reset()
                return
            if kind == "engine_analysis":
                token = event.get("speech_token")
                if token:
                    self.analyses[token] = dict(event)
                    while len(self.analyses) > 4:
                        self.analyses.pop(next(iter(self.analyses)))
                if event.get("side") in ("r", "b"):
                    self.side = event["side"]
                return
            if kind in ("move_rejected", "move_preview", "speech_cancelled"):
                self.analyses.pop(event.get("speech_token"), None)
                return
            if kind not in ("move_applied", "opponent_move") or not event.get("fen"):
                return
            try:
                board = Board.from_fen(event["fen"])
                if min(board.king_pos("r"), board.king_pos("b")) < 0:
                    return
                source = (self.analyses.pop(event.get("speech_token"), {}) if kind == "move_applied"
                          else {"fen_before": self.fen, "ucci": event.get("ucci")})
                effects = []
                if source.get("fen_before") and source.get("ucci"):
                    before = Board.from_fen(source["fen_before"])
                    move = Board.from_ucci(source["ucci"])
                    if move in before.legal_moves():
                        probe = before.copy()
                        probe.apply(move)
                        if probe.to_fen() == board.to_fen():
                            effects = move_effects(before, move)
                self.fen, self.updated = board.to_fen(), self.clock()
                self.moves.append({"notation": event.get("notation", ""), "at": self.clock(),
                    "own": kind == "move_applied", "effects": effects})
            except (ValueError, KeyError, TypeError, IndexError):
                return

    def snapshot(self):
        with self.lock:
            if not self.fen or self.clock() - self.updated > 25:
                return {}
            return {"fen": self.fen, "side": self.side,
                    "moves": [dict(row, effects=list(row["effects"])) for row in self.moves
                              if self.clock() - row["at"] <= 45]}


def grounded_reply(comment, snapshot=None):
    """Return a factual answer for chess, or None for ordinary social chat."""
    comment = str(comment)
    if re.fullmatch(r"\s*(?:听我的|按我说的走|跟我走)\s*[。！!]?", comment):
        return "建议收到，招法还得按棋局核对，咱们不靠嗓门选招。"
    if not chess_comment(comment):
        return None
    snapshot = snapshot or {}
    suggestion = MOVE.search(comment)
    if suggestion and snapshot.get("fen"):
        try:
            board = Board.from_fen(snapshot["fen"])
            move = next((move for move in board.legal_moves() if _normalize(
                move_description(board, move).split(" ", 1)[-1].split("，", 1)[0]) == _normalize(suggestion.group())), None)
            if move is not None:
                probe = board.copy()
                probe.apply(move)
                mate = (probe.king_pos(probe.turn) >= 0 and probe.in_check(probe.turn) and not probe.legal_moves())
                if mate:
                    return f"按刚确认的局面，{suggestion.group()}确实是一步将死，这次有棋盘作证。"
                if re.search(r"绝杀|将死|闷宫|重炮|双杯|钓鱼马", comment):
                    return f"{suggestion.group()}这手能走，但对面还有合法应法，不能直接喊一步绝杀。"
                effects = move_effects(board, move)
                if effects:
                    fact = effects[0].replace("现在", "走完后").replace("过来", "走完后会过来")
                    if snapshot.get("side") and board.turn != snapshot["side"]:
                        fact = fact.replace("咱们", "__OTHER__").replace("对面", "咱们").replace("__OTHER__", "对手")
                    return f"如果当前行棋方走{suggestion.group()}，{fact}"
                return f"{suggestion.group()}是当前可走的一手，具体是不是最好，还不能只看招名拍板。"
            return f"刚确认的局面里，当前行棋方没有{suggestion.group()}这手，可能说的是前面的局面。"
        except (ValueError, TypeError, IndexError):
            pass
    if re.search(r"漏杀|绝杀|将死|闷宫|重炮|双杯|钓鱼马", comment):
        return "这条杀法得核对当时的局面，不能听见绝杀就跟着喊，豆包先把嘴刹住。"
    piece = "相象" if re.search(r"飞[象相]", comment) else "马" if re.search(r"跳马|进马|回马", comment) else ""
    for row in reversed(snapshot.get("moves") or []):
        notation = re.sub(r"^(?:红方|黑方)\s+", "", row["notation"])
        if piece and notation[:1] in piece:
            actor = "咱们" if row["own"] else "对手"
            effects = row.get("effects") or []
            if effects:
                fact = effects[0]
                if not row["own"]:
                    fact = fact.replace("咱们", "__OTHER__").replace("对面", "咱们").replace("__OTHER__", "对手")
                return f"刚才{actor}走的是{notation.split('，')[0]}，{fact}"
            return f"刚才{actor}走的是{notation.split('，')[0]}，更具体的作用还没核实，不能硬夸成高招。"
    if re.search(r"先?[打吃]马", comment):
        return "先打马这个建议收到，不过能吃和该先吃是两回事，得对着具体局面看。"
    return "这条得对着具体棋形讲，现有记录还不能确认你的意思，豆包不隔空封高招。"
