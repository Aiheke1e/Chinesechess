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
                   rf"[打吃拿](?:你|我|他|她|咱们|对面|对手|的|这|那|枚|匹|门|辆){{0,4}}[{PIECES}]|破[相象]|"
                   rf"[抓捉抽兑弃丢冲挺推压挡封卧上落](?:你|他|这|那|中|边|路|河|的|[1-9]|[上下左右前后]){{0,3}}[{PIECES}]|"
                   r"绝杀|漏杀|将死|叫杀|闷宫|重炮|双杯|钓鱼马|卧槽马|马后炮|"
                   r"这[盘局手步招]|棋[局盘招力规风]|谁[赢输]|局势|均势|优势|劣势|"
                   r"丢子|弃子|送子|兑子|蹩马腿|马.{0,4}(?:方向|难受|卧错)|"
                   r"(?:红|黑)(?:方|棋)?.{0,2}(?:亏|赢|输|优)|"
                   r"(?:要|能|会|快|包|稳|终于).{0,3}(?:赢|输)|开[胡和]|"
                   r"三步虎|列炮|两头蛇|双头蛇|屏风马|中炮|仙人指路|一[杀将捉吃].{0,3}一[杀将捉吃]")


def chess_comment(text):
    return bool(CHESS.search(str(text)))


def incomplete_comment(text):
    text = re.sub(r"\[[^\]\n]{1,12}\]", "", str(text)).strip()
    return bool(re.fullmatch(rf"\s*(?:[1-9一二三四五六七八九]路|[{PIECES}][1-9]{{1,2}}|"
                             rf"[打吃跳进出回退平飞补架拱][{PIECES}]|[冲挺拱推](?:中|边)?[兵卒]|"
                             rf"[上下左右前后]边?[{PIECES}]|均势|[推拉走跳]起来|推演[\u4e00-\u9fff]?|[一二三四五六七八九1-9]+把手|准备[a-zA-Z]+)\s*[。！!]?", text))


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
    if re.search(r"棋规|规则|违规|一[杀将捉吃].{0,3}一[杀将捉吃]", comment):
        return "是否违规得看完整往返棋谱和平台规则，只凭这几个字还不能判。"
    if re.search(r"三步虎|列炮|两头蛇|双头蛇|屏风马|仙人指路", comment) and re.search(r"亏|优|难受|好坏", comment):
        return "布局好坏还得看具体变化，不能只凭开局名字说哪边吃亏。"
    suggestion = MOVE.search(comment)
    if suggestion and snapshot.get("fen"):
        try:
            board = Board.from_fen(snapshot["fen"])
            move = next((move for move in board.legal_moves() if _normalize(
                move_description(board, move).split(" ", 1)[-1].split("，", 1)[0]) == _normalize(suggestion.group())), None)
            if move is not None:
                probe = board.copy()
                probe.apply(move)
                terminal = probe.king_pos(probe.turn) >= 0 and not probe.legal_moves()
                if terminal:
                    if probe.in_check(probe.turn):
                        return f"按刚确认的局面，{suggestion.group()}确实是一步将死，这次有棋盘作证。"
                    return f"按刚确认的局面，{suggestion.group()}走完对面无棋可走，是困毙取胜。"
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
    if suggestion and not snapshot.get("fen"):
        return f"{suggestion.group()}这手我收到了，还得核对当前棋形才能讲作用。"
    if re.search(r"谁[赢输]|优势|劣势|均势|[红黑].{0,3}亏|(?:要|能|会|快|包|稳|终于).{0,3}(?:赢|输)|开[胡和]", comment):
        return "这盘的最新棋形还没核实，先不急着判输赢。" if not snapshot.get("fen") else "胜负得看后续实招，刚才那份棋形还不足以保证谁赢。"
    if re.search(r"漏杀|绝杀|将死|闷宫|重炮|双杯|钓鱼马", comment):
        return "这条杀法得核对当时的局面，先不能直接认定是绝杀。"
    if re.search(r"丢子|弃子|送子|兑子", comment):
        return "现在还不能确认是丢子还是交换，得把前后的走法一起看。"
    if re.search(r"抽.{0,2}车|抓.{0,2}炮|叫杀", comment):
        return "这条威胁得看双方子的位置，我还不能只凭这句话定应法。"
    if re.search(r"破[相象]", comment):
        return "你说的是破相这条思路。能否拆开防守，还得核对后面的接法。"
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
    capture = re.search(rf"[打吃拿](?:你|我|他|她|咱们|对面|对手|的|这|那|枚|匹|门|辆){{0,4}}([{PIECES}])", comment)
    if capture:
        return f"涉及吃{capture.group(1)}的这条线，还得把保护和回吃核对清楚，不能只看眼前这一口。"
    return "这手得看完整棋形，你指的是哪枚子、哪一步？"
