# board.py —— 中国象棋棋盘状态机 / FEN / 走法生成
# 索引: idx = row * 9 + col, row 0 在顶部(黑方底线), row 9 在底部(红方底线)
from __future__ import annotations

ROWS, COLS = 10, 9
RED, BLACK = "r", "b"

START_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"

PIECE_CN = {
    "K": "帅", "A": "仕", "B": "相", "N": "马", "R": "车", "C": "炮", "P": "兵",
    "k": "将", "a": "士", "b": "象", "n": "马", "r": "车", "c": "炮", "p": "卒",
}
CN_TO_CODE = {
    "帅": "K", "帥": "K", "仕": "A", "士": "A", "相": "B", "象": "B",
    "马": "N", "馬": "N", "车": "R", "車": "R", "炮": "C", "砲": "C", "包": "C",
    "兵": "P", "卒": "P", "将": "k", "將": "k",
}
RED_CHARS = set("帅帥仕相馬车車炮兵")
BLACK_CHARS = set("将士象馬車砲卒包將")

def color_of(p: str) -> str:
    return RED if p.isupper() else BLACK


def in_board(r: int, c: int) -> bool:
    return 0 <= r < ROWS and 0 <= c < COLS


def inside_palace(color: str, r: int, c: int) -> bool:
    if not (3 <= c <= 5):
        return False
    return (7 <= r <= 9) if color == RED else (0 <= r <= 2)


class Board:
    __slots__ = ("sq", "turn")

    def __init__(self, sq=None, turn: str = RED):
        if turn not in (RED, BLACK):
            raise ValueError("turn must be 'r' or 'b'")
        if sq is not None and len(sq) != 90:
            raise ValueError(f"board must contain 90 squares, got {len(sq)}")
        self.sq = sq if sq is not None else [None] * 90
        self.turn = turn

    @classmethod
    def from_fen(cls, fen: str) -> "Board":
        parts = fen.strip().split()
        if not parts:
            raise ValueError("empty FEN")
        rows = parts[0].split("/")
        if len(rows) != 10:
            raise ValueError(f"FEN must contain 10 rows, got {len(rows)}")
        sq = [None] * 90
        allowed = set("KABNRCPkabnrcp")
        for r, rs in enumerate(rows):
            c = 0
            for ch in rs:
                if ch in "123456789":
                    c += int(ch)
                else:
                    if ch not in allowed:
                        raise ValueError(f"invalid FEN piece: {ch}")
                    if c >= 9:
                        raise ValueError(f"FEN row {r + 1} is too wide")
                    sq[r * 9 + c] = ch
                    c += 1
            if c != 9:
                raise ValueError(f"FEN row {r + 1} has width {c}, expected 9")
        if len(parts) >= 2 and parts[1] not in ("w", "b"):
            raise ValueError("FEN side to move must be 'w' or 'b'")
        turn = RED if len(parts) < 2 or parts[1] == "w" else BLACK
        return cls(sq, turn)

    def to_fen(self) -> str:
        out = []
        for r in range(ROWS):
            empty = 0
            line = ""
            for c in range(COLS):
                p = self.sq[r * 9 + c]
                if p is None:
                    empty += 1
                else:
                    if empty:
                        line += str(empty)
                        empty = 0
                    line += p
            if empty:
                line += str(empty)
            out.append(line)
        return "/".join(out) + (" w" if self.turn == RED else " b") + " - - 0 1"

    def copy(self) -> "Board":
        return Board(self.sq[:], self.turn)

    def king_pos(self, color: str) -> int:
        k = "K" if color == RED else "k"
        for i in range(90):
            if self.sq[i] == k:
                return i
        return -1

    def _slide(self, idx, color, dirs, moves, cannon=False):
        r0, c0 = divmod(idx, 9)
        for dr, dc in dirs:
            r, c = r0 + dr, c0 + dc
            jumped = False
            while in_board(r, c):
                t = self.sq[r * 9 + c]
                if not cannon:
                    if t is None:
                        moves.append((idx, r * 9 + c))
                    else:
                        if color_of(t) != color:
                            moves.append((idx, r * 9 + c))
                        break
                else:
                    if not jumped:
                        if t is None:
                            moves.append((idx, r * 9 + c))
                        else:
                            jumped = True
                    else:
                        if t is not None:
                            if color_of(t) != color:
                                moves.append((idx, r * 9 + c))
                            break
                r += dr
                c += dc

    def gen_moves(self, color: str | None = None, idx_filter: int | None = None):
        color = color or self.turn
        out = []
        for idx in range(90):
            p = self.sq[idx]
            if p is None or color_of(p) != color:
                continue
            if idx_filter is not None and idx != idx_filter:
                continue
            r0, c0 = divmod(idx, 9)
            up = p.upper()
            if up == "R":
                self._slide(idx, color, ((1, 0), (-1, 0), (0, 1), (0, -1)), out)
            elif up == "C":
                self._slide(idx, color, ((1, 0), (-1, 0), (0, 1), (0, -1)), out, cannon=True)
            elif up == "N":
                legs = (((-1, 0), (-2, -1)), ((-1, 0), (-2, 1)),
                        ((1, 0), (2, -1)), ((1, 0), (2, 1)),
                        ((0, -1), (-1, -2)), ((0, -1), (1, -2)),
                        ((0, 1), (-1, 2)), ((0, 1), (1, 2)))
                for (lr, lc), (dr, dc) in legs:
                    lr_, lc_ = r0 + lr, c0 + lc
                    r, c = r0 + dr, c0 + dc
                    if not in_board(r, c) or not in_board(lr_, lc_):
                        continue
                    if self.sq[lr_ * 9 + lc_] is not None:
                        continue
                    t = self.sq[r * 9 + c]
                    if t is None or color_of(t) != color:
                        out.append((idx, r * 9 + c))
            elif up == "B":
                for dr, dc in ((-2, -2), (-2, 2), (2, -2), (2, 2)):
                    r, c = r0 + dr, c0 + dc
                    er, ec = r0 + dr // 2, c0 + dc // 2
                    if not in_board(r, c):
                        continue
                    if self.sq[er * 9 + ec] is not None:
                        continue
                    if color == RED and r < 5:
                        continue
                    if color == BLACK and r > 4:
                        continue
                    t = self.sq[r * 9 + c]
                    if t is None or color_of(t) != color:
                        out.append((idx, r * 9 + c))
            elif up == "A":
                for dr, dc in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
                    r, c = r0 + dr, c0 + dc
                    if inside_palace(color, r, c):
                        t = self.sq[r * 9 + c]
                        if t is None or color_of(t) != color:
                            out.append((idx, r * 9 + c))
            elif up == "K":
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    r, c = r0 + dr, c0 + dc
                    if inside_palace(color, r, c):
                        t = self.sq[r * 9 + c]
                        if t is None or color_of(t) != color:
                            out.append((idx, r * 9 + c))
            elif up == "P":
                forward = -1 if color == RED else 1
                cand = [(r0 + forward, c0)]
                crossed = (r0 <= 4) if color == RED else (r0 >= 5)
                if crossed:
                    cand += [(r0, c0 - 1), (r0, c0 + 1)]
                for r, c in cand:
                    if in_board(r, c):
                        t = self.sq[r * 9 + c]
                        if t is None or color_of(t) != color:
                            out.append((idx, r * 9 + c))
        return out

    def apply(self, mv):
        f, t = mv
        cap = self.sq[t]
        self.sq[t] = self.sq[f]
        self.sq[f] = None
        self.turn = BLACK if self.turn == RED else RED
        return cap

    def undo(self, mv, cap):
        f, t = mv
        self.sq[f] = self.sq[t]
        self.sq[t] = cap
        self.turn = BLACK if self.turn == RED else RED

    def in_check(self, color: str) -> bool:
        kp = self.king_pos(color)
        if kp < 0:
            return True
        opp = BLACK if color == RED else RED
        for f, t in self.gen_moves(opp):
            if t == kp:
                return True
        ok = self.king_pos(RED)
        bk = self.king_pos(BLACK)
        if ok >= 0 and bk >= 0 and ok % 9 == bk % 9:
            col = ok % 9
            between = [i for i in range(min(ok, bk) + 9, max(ok, bk), 9)
                       if self.sq[i] is not None]
            if not between:
                return True
        return False

    def legal_moves(self, color: str | None = None):
        color = color or self.turn
        res = []
        for f, t in self.gen_moves(color):
            cap = self.apply((f, t))
            bad = self.in_check(color)
            self.undo((f, t), cap)
            if not bad:
                res.append((f, t))
        return res

    @staticmethod
    def ucci(mv) -> str:
        f, t = mv

        def conv(idx):
            r, c = divmod(idx, 9)
            return chr(ord("a") + c) + str(9 - r)

        return conv(f) + conv(t)

    @staticmethod
    def from_ucci(s: str):
        if (not isinstance(s, str) or len(s) != 4 or
                s[0] not in "abcdefghi" or s[2] not in "abcdefghi" or
                s[1] not in "0123456789" or s[3] not in "0123456789"):
            raise ValueError(f"invalid UCCI move: {s!r}")
        def conv(s2):
            c = ord(s2[0]) - ord("a")
            r = 9 - int(s2[1])
            return r * 9 + c

        return conv(s[0:2]), conv(s[2:4])

    def pretty(self) -> str:
        lines = []
        for r in range(ROWS):
            row = self.sq[r * 9:(r + 1) * 9]
            lines.append(" ".join((PIECE_CN.get(p, "?") if p else "·") for p in row))
        return "\n".join(lines)

    def attacker_count(self) -> int:
        return sum(1 for p in self.sq if p is not None)
