"""Occasional live time calls based on completed real games."""
from datetime import datetime, timedelta, timezone


BEIJING = timezone(timedelta(hours=8))
CLOCK_KIND = "直播报时"


def beijing_now():
    return datetime.now(BEIJING)


def minute_key(moment):
    return moment.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")


def clock_line(moment):
    moment = moment.astimezone(BEIJING)
    return f"现在是北京时间{moment.hour}点{moment.minute:02d}分，你看到的不是直播录像。"


class GameClockCadence:
    def __init__(self, every=3):
        self.every = max(1, int(every))
        self.completed = 0
        self.active = False
        self.dry_run = False

    def observe(self, event):
        kind = event.get("event")
        if kind == "start":
            self.active = False
            self.dry_run = bool(event.get("dry_run"))
        if self.dry_run or event.get("dry_run"):
            return False
        if kind in ("move_applied", "opponent_move"):
            self.active = True
        if kind != "game_over" or not self.active:
            return False
        self.active = False
        if (event.get("result") not in ("won", "lost", "draw", "finished") or
                event.get("reason") not in ("no_legal_moves", "jj_result_screen",
                    "delayed_jj_result_screen", "rank_promotion_screen")):
            return False
        self.completed += 1
        return self.completed % self.every == 0
