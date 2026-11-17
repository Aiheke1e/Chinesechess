"""Rare audience-facing questions over an already verified engine sandbox."""
import time

from notation import PIECE_NAMES
from queen_commentary import PIECE_VALUE


QUESTIONS = (
    "兄弟们，这段换子，后手在哪儿？",
    "别只看眼前这一口，后面怎么接？",
    "这段交换，咱们要算到哪一步？",
    "兄弟们来搭把手，这里怎么接？",
)


class AudienceSceneDirector:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.last_at = float("-inf")
        self.used = False
        self.last_number = 0
        self.sequence = 0

    def end_game(self):
        self.used = False
        self.last_number = 0

    def choose(self, event, plan):
        if event.get("event") != "engine_analysis":
            return None
        try:
            number = int(event.get("number") or 0)
            score = int((event.get("analysis") or {}).get("score", 0))
        except (TypeError, ValueError):
            return None
        if number and number < self.last_number:
            self.used = False
        self.last_number = number
        if (not plan or self.used or number < 8 or self.clock() - self.last_at < 180
                or event.get("dry_run") or plan.get("kind") != "tactic"
                or plan.get("phase") != "after_move" or "解将" in str(plan.get("reason"))):
            return None
        analysis = event.get("analysis") or {}
        if analysis.get("score_type") != "cp" or not -150 <= score <= 500:
            return None
        route, own = plan.get("route") or [], plan.get("own")
        ours = [item for item in route if item.get("side") == own]
        theirs = [item for item in route if item.get("side") != own]
        captures = [item for item in ours if str(item.get("capture") or "").upper() in ("R", "N", "C")]
        if (not 3 <= len(route) <= 5 or len(ours) < 2 or not captures
                or not any(item.get("capture") for item in theirs)
                or any(item.get("illustration") for item in route)):
            return None
        gain = sum(PIECE_VALUE.get(str(item.get("capture") or "").upper(), 0) for item in ours)
        cost = sum(PIECE_VALUE.get(str(item.get("capture") or "").upper(), 0) for item in theirs)
        if gain < cost:
            return None
        variant = self.sequence % len(QUESTIONS)
        target = str(captures[0].get("notation") or "").split(" ", 1)[-1].split("，", 1)[0]
        scene = {"variant": variant, "question": QUESTIONS[variant], "lead_move": target,
                 "lead_piece": PIECE_NAMES[captures[0]["capture"]], "profitable": gain > cost}
        self.sequence += 1
        self.used, self.last_at = True, self.clock()
        return scene
