"""Varied, bounded game transitions using only confirmed lifecycle facts."""
import time

GAP_KIND = "局间互动"
TRANSITION_KINDS = {"获胜收官", "结算收官", "新局开场"}


class BroadcastFlow:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.ended_at = None
        self.next_gap = 0
        self.gap_count = 0
        self.gap_sequence = 0
        self.end_sequence = 0
        self.open_sequence = 0
        self.cause_sequences = {}
        self.ended = False
        self.opened = False
        self.dry_run = False

    @staticmethod
    def is_terminal(event):
        return (event.get("result") in ("won","lost","draw") or
                event.get("result") == "finished" and event.get("reason") in (
                    "jj_result_screen","delayed_jj_result_screen","rank_promotion_screen"))

    def start(self, event):
        self.dry_run = bool(event.get("dry_run"))
        self.ended = self.opened = False
        self.ended_at = None

    def position(self, event):
        if self.dry_run or self.opened or not event.get("fen"):
            return ""
        self.opened = True
        self.ended_at = None
        side = {"r":"红","b":"黑"}.get(event.get("my_color"))
        if event.get("own_moves",0):
            return "棋盘接上了，咱们继续当前这盘。"
        variants = ("新一盘接上了", "下一盘来了", "棋盘就绪，咱们继续", "这盘开始，重新布阵")
        lead = variants[self.open_sequence % len(variants)]
        self.open_sequence += 1
        return lead + (f"，这盘咱们执{side}。" if side else "。")

    def end(self, event, *, playful=True):
        if self.dry_run or self.ended or not self.is_terminal(event):
            return ""
        self.ended = True
        self.ended_at = self.clock()
        self.next_gap, self.gap_count = self.ended_at + 12, 0
        options = self.cause_lines(event, playful=playful) or {
            "won":("这盘拿下，收好这一分。", "这局赢了，棋友们歇口气。", "这盘顺利收下，下一盘继续认真走。", "这局拿下了，先给这盘画个句号。"),
            "lost":("这盘输了，记住教训，下一盘再来。", "这局没拿下，咱们整理一下思路。", "这盘先认账，下一局重新布置。", "这局交了学费，后面再接着下。"),
            "draw":("这盘和了，先歇口气。", "这一局和棋收场，下一盘再见高低。", "这盘打成和棋，咱们缓一下。", "和棋收尾了，换盘棋继续。"),
            "finished":("这盘已经结束，咱们等新棋盘。", "这一局告一段落，稍歇一下。", "本局已经结束，下一盘再接着看。", "这盘收尾了，咱们缓一口气。"),
        }[event["result"]]
        cause = event.get("finish_cause") if event.get("finish_evidence") == "jj_result_text" else None
        key = cause or ("disconnected" if event.get("opponent_disconnected") else "ordinary")
        sequence = self.cause_sequences.get(key, 0)
        text = options[sequence % len(options)]
        self.cause_sequences[key] = sequence + 1
        self.end_sequence += 1
        return text

    @staticmethod
    def cause_lines(event, *, playful=True):
        cause = event.get("finish_cause") if event.get("finish_evidence") == "jj_result_text" else None
        result = event.get("result")
        if cause == "opponent_timeout" and result == "won":
            if not playful:
                return ("对面这手超时了，这盘拿下。", "对面用时超时，这局收下，下一盘继续。")
            return (
                "对面这手超时了，这盘收下。又吓跑一个，有请下一位受害者。",
                "对面时间用完了。这盘先记账，下一位棋友请上座。",
                "对面超时判负，咱们拿下。棋还在盘上，闹钟先将军了。",
                "这局对面超时了。这盘拿下，下一位，咱们接着聊棋。",
                "对面这手没赶上时间，这盘收下。下一位棋友，棋桌还热着呢。",
                "对面超时了。棋还在构思，计时器先收卷了，这分咱们收好。",
                "对面时间到，这局拿下。下一位棋友，咱们以棋会友。",
                "这局对面超时了。又吓跑一个？开个玩笑，下一盘继续认真下。",
            )
        if cause == "opponent_resigned" and result == "won":
            if not playful:
                return ("对面认输了，这盘拿下。", "对方投降，这局收下，下一盘继续。")
            return ("对面认输了，这盘收下。又吓跑一个，有请下一位受害者。",
                    "对面投降了。这盘先收好，下一位棋友请入座。",
                    "对面认输了，咱们拿下。棋盘还没凉，下一盘继续。",
                    "对面这盘不接着走了，已经认输。收好这一分，等下一位高手。")
        if cause == "opponent_left" and result == "won":
            return (("对面离开，这局判咱们获胜。又吓跑一个，有请下一位受害者。",
                     "对面退出，这盘收下。下一位棋友请上座。") if playful else
                    ("对面退出，这局判咱们获胜。",))
        if cause == "own_timeout" and result == "lost":
            return ("这次是咱们超时了，先认账，下一盘盯紧时钟。",
                    "这盘咱们没赶上时间，输在计时上。下一局再来。",
                    "咱们这手超时，这盘输了。下一局既看棋，也看钟。",
                    "这次咱们用时超了，这局交学费，下一盘早点落子。")
        if event.get("opponent_disconnected") and result == "finished":
            return ("对面断线后，这局已经结束了。", "对面显示断线，这盘已经结算，咱们等下一局。",
                    "这盘在对面断线后结束了，下一盘再接着看。", "对面断线后已进入结算，这盘先告一段落。")
        return ()

    def gap(self):
        now = self.clock()
        if (self.ended_at is None or self.dry_run or self.gap_count >= 2
                or now < self.next_gap or now-self.ended_at > 90):
            return ""
        lines = (
            "棋友们，平时下棋喜欢抢先手，还是先把阵型站稳？",
            "刚才哪一步最让你犹豫？弹幕里说说。",
            "下一盘想重点看布局、换子，还是残局？",
            "趁这会儿聊一句，车马配合和车炮配合，你更喜欢哪种？",
            "有没有棋友也爱边看边摆？觉得关键的那手可以说一说。",
            "棋友们歇口气，刚才那盘最想重看哪一手？",
            "快棋一上来，你们先看吃子，还是先找将军？",
            "咱们聊点棋：你最拿手的开局是哪一种？",
        )
        text = lines[self.gap_sequence % len(lines)]
        self.gap_sequence += 1
        self.gap_count += 1
        self.next_gap = now + 25
        return text
