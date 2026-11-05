"""Only confirmed real-game moves may enter the engine's repetition history."""
from board import Board
from collections import Counter


class PositionHistory:
    def __init__(self, board):
        self.root_fen = board.to_fen()
        self.moves = []
        self.board = board.copy()
        self.positions = Counter({self.root_fen: 1})

    @classmethod
    def restore(cls, board, session=None):
        fallback = cls(board)
        session = session or {}
        stored = session.get("history")
        if not isinstance(stored, dict):
            return fallback
        try:
            root = Board.from_fen(stored["root_fen"])
            moves = stored["moves"]
            if not isinstance(moves, list) or len(moves) > 4096:
                return fallback
            history = cls(root)
            sequence = moves + [Board.ucci(move) for move in session.get("inferred_moves", ())]
            for ucci in sequence:
                move = Board.from_ucci(ucci)
                if move not in history.board.legal_moves():
                    return fallback
                history.board.apply(move)
                history.moves.append(ucci)
                history.positions[history.board.to_fen()] += 1
            if history.board.to_fen() != board.to_fen():
                return fallback
            return history
        except (KeyError, TypeError, ValueError, IndexError):
            return fallback

    def append(self, move, confirmed_board):
        probe = self.board.copy()
        if move in probe.legal_moves():
            probe.apply(move)
            if probe.to_fen() == confirmed_board.to_fen():
                self.moves.append(Board.ucci(move))
                self.board = probe
                self.positions[probe.to_fen()] += 1
                return
        # Never attach an unrelated/endgame board to an old game's move list.
        self.root_fen = confirmed_board.to_fen()
        self.board = confirmed_board.copy()
        self.moves = []
        self.positions = Counter({self.root_fen: 1})

    def repeated_chases(self, board):
        """Avoid a fourth occurrence caused by our repeated check/chase.

        Quiet drawing repetitions and captures remain engine decisions.  This
        only filters a non-capture that repeats a confirmed position and again
        attacks the enemy king or a rook/horse/cannon with the moved piece.
        """
        if board.to_fen() != self.board.to_fen() or board.in_check(board.turn):
            return set()
        excluded = set()
        for move in board.legal_moves():
            if board.sq[move[1]] is not None:
                continue
            after = board.copy()
            after.apply(move)
            if self.positions[after.to_fen()] < 3:
                continue
            chasing = after.in_check(after.turn) or any(
                attack[0] == move[1] and (after.sq[attack[1]] or "").upper() in ("R", "N", "C")
                for attack in after.legal_moves(board.turn))
            if chasing:
                excluded.add(Board.ucci(move))
        return excluded

    def export(self):
        return {"root_fen": self.root_fen, "moves": list(self.moves)}
