"""Remember only verified demonstration positions, without controlling play."""
import time

from board import Board
from commentary_text import spoken_move


class SandboxStory:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.plans = {}
        self.shown = {}
        self.forecast = None
        self.branch = None
        self.sequence = 0
        self.branch_sequence = 0

    def clear(self):
        self.plans.clear()
        self.shown.clear()
        self.forecast = self.branch = None

    def prepare(self, token, plan):
        plan = {**plan, "narration_variant":self.sequence % 8}
        self.sequence += 1
        self.plans[token] = plan
        while len(self.plans) > 4:
            self.plans.pop(next(iter(self.plans)))
        return plan

    def finished(self, token, event):
        plan = self.plans.pop(token, None)
        if not plan or not event.get("entered") or not event.get("safe_exit", True):
            return
        try:
            board = Board.from_fen(plan["fen"])
            route = plan.get("route") or []
            offset = int(bool(event.get("observed_reply")))
            completed = int(event.get("completed") or 0)
            for index, item in enumerate(route):
                if item.get("illustration"):
                    break
                move = Board.from_ucci(item["ucci"])
                if move not in board.legal_moves():
                    return
                if offset <= index < offset + completed and item.get("side") == plan.get("own"):
                    self.shown[(board.to_fen(), item["ucci"])] = self.clock()
                board.apply(move)
            if completed and plan.get("phase") == "after_move":
                self.forecast = {"fen":plan["fen"], "ucci":route[0]["ucci"], "at":self.clock()}
            elif completed >= 2 and plan.get("phase") == "before_move" and not route[1].get("illustration"):
                before = Board.from_fen(plan["fen"])
                before.apply(Board.from_ucci(route[0]["ucci"]))
                self.forecast = {"fen":before.to_fen(), "ucci":route[1]["ucci"], "at":self.clock()}
        except (KeyError, ValueError, TypeError, IndexError):
            return

    def reply(self, event):
        forecast, self.forecast = self.forecast, None
        if not forecast or self.clock() - forecast["at"] > 90:
            return
        try:
            board = Board.from_fen(forecast["fen"])
            move = Board.from_ucci(event["ucci"])
            if move not in board.legal_moves():
                return
            board.apply(move)
            if board.to_fen() != Board.from_fen(event["fen"]).to_fen():
                return
            if event["ucci"] != forecast["ucci"]:
                self.branch = {"fen":board.to_fen(), "at":self.clock(),
                               "move":spoken_move(event.get("notation"))}
        except (KeyError, ValueError, TypeError, IndexError):
            return

    def move_call(self, event):
        now = self.clock()
        self.shown = {key:at for key, at in self.shown.items() if now - at < 150}
        try:
            fen = Board.from_fen(event["fen_before"]).to_fen()
        except (KeyError, ValueError, TypeError):
            return ""
        move = spoken_move(event.get("notation"))
        branch, self.branch = self.branch, None
        if branch and branch["fen"] == fen and now - branch["at"] < 30:
            actual = branch["move"]
            variants = (f"对面{actual}，换了条线。", f"实战对面选了{actual}，这次另接一招。",
                        f"对面改走{actual}，咱们跟着新局面接。", f"对面另有主意，走了{actual}。")
            text = variants[self.branch_sequence % len(variants)]
            self.branch_sequence += 1
            return f"{text}{move}。"
        if (fen, event.get("ucci")) in self.shown:
            return f"实战走{move}。"
        return ""
