"""Defer a short, already verified tactical demo until an opponent thinks."""
from sandbox_narration import demo_budget


def thinking_candidate(plan):
    if not plan or plan.get("kind") != "tactic" or plan.get("phase") != "after_move":
        return plan
    candidate = {**plan, "thinking_wait": True}
    budget = demo_budget(candidate)
    # A complete short exchange fits the waiting window. Longer ordinary
    # demos keep their original timing; mate previews stay before the move.
    if budget <= 20 and 2 <= len(plan.get("route") or []) <= 4:
        candidate["max_seconds"] = budget
        return candidate
    return plan


class ThinkingSandbox:
    def __init__(self):
        self.pending = None

    def arm(self, board, plan, token, number):
        self.pending = ({"fen": board.to_fen(), "plan": plan, "token": token, "number": number}
                        if plan and plan.get("thinking_wait") else None)

    def take(self, board, number, elapsed, *, stable, disconnected=False):
        pending = self.pending
        if not pending:
            return None
        if (pending["fen"] != board.to_fen() or pending["number"] != number or disconnected):
            self.pending = None
            return None
        if not stable or elapsed < 8:
            return None
        self.pending = None
        return pending
