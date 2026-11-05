"""Factual, step-sized sandbox narration and token-scoped playback receipts."""
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from board import PIECE_CN


MARKERS = Path(__file__).resolve().parent / "work/sandbox_speech"


def cue_token(token, cue):
    return f"{token}:sandbox:{cue}"


def marker_path(token):
    return MARKERS / (hashlib.sha256(token.encode("utf-8")).hexdigest() + ".json")


def prepared(token):
    """A token-scoped receipt proves this host understands step playback."""
    try:
        data = json.loads(marker_path(token).read_text(encoding="utf-8"))
        return data.get("token") == token and data.get("status") in ("preparing", "started", "finished")
    except (OSError, ValueError):
        return False


def receipt(payload, status):
    if not payload.get("_sandbox_cue"):
        return False
    token = str(payload.get("speech_token") or "")
    if not token:
        return True
    path = marker_path(token)
    temporary = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps({"token": token, "status": status,
                                         "created": time.time()}), encoding="utf-8")
        for attempt in range(12):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows readers can briefly hold the previous receipt open.
                # Losing the final acknowledgement would stall an entire cue.
                if attempt == 11:
                    raise
                threading.Event().wait(.005)
    except OSError:
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def wait_receipt(token, *, finished=False, deadline, cancelled):
    while time.monotonic() < deadline and not cancelled():
        try:
            data = json.loads(marker_path(token).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if data.get("token") == token:
            status = data.get("status")
            if status in ("failed", "cancelled"):
                return False
            if status == "finished" or not finished and status == "started":
                return True
        time.sleep(.03)
    return False


def _pieces(pieces):
    return "、".join(f"{pieces.count(piece)}枚{PIECE_CN[piece]}"
                    for piece in dict.fromkeys(pieces))


def result_note(route, own, mate=False, pattern="组合将杀"):
    if mate:
        return f"这路{pattern}摆到这里，吃将收尾。回到实战。"
    ours = [item["capture"] for item in route if item.get("capture") and item.get("side") == own]
    theirs = [item["capture"] for item in route if item.get("capture") and item.get("side") != own]
    if ours and theirs:
        return f"沿这条变化，咱们吃到{_pieces(ours)}，对面吃到{_pieces(theirs)}。双方的代价都要算进去。"
    if ours:
        return f"沿这条变化，咱们能吃到{_pieces(ours)}。回到实战，看对面怎么接。"
    if theirs:
        return f"沿这条变化，对面能吃到{_pieces(theirs)}，这个代价得看清。回到实战。"
    if any(item.get("check") or item.get("escape") for item in route):
        return "将军和应将这几步接上了。回到实战，看对面的选择。"
    return "这段接法摆清楚了，回到实战。"


def script(plan):
    route = plan.get("route") or []
    mate = plan.get("kind") == "mate"
    intro = (f"看这路{plan.get('pattern', '组合将杀')}，摆到吃将收尾。" if mate else
             f"看这段{plan.get('reason', '关键接法')}，咱们摆到结果。")
    cues = {"intro": intro}
    first_side = (route[0].get("side") or ("r" if str(route[0].get("notation", "")).startswith("红方") else "b")) if route else "r"
    own = plan.get("own") or (first_side if plan.get("phase") == "before_move" else "b" if first_side == "r" else "r")
    for index, item in enumerate(route, 1):
        text = str(item.get("notation") or "")
        for prefix in ("红方 ", "黑方 ", "演示收尾："):
            text = text.replace(prefix, "")
        side = item.get("side") or ("r" if str(item.get("notation", "")).startswith("红方") else
                                    "b" if str(item.get("notation", "")).startswith("黑方") else own)
        subject = "咱们" if side == own else "对面"
        if item.get("illustration"):
            text = "最后摆一步，" + text
        elif index == 1 and side != own:
            text = "假设对面" + text
        else:
            text = subject + text
        cues[str(index)] = text.rstrip("。") + "。"
    cues["summary"] = plan.get("summary") or result_note(route, own, mate, plan.get("pattern", "组合将杀"))
    return cues


def demo_budget(plan):
    """Include speech, ADB/animation overhead and a visible final position."""
    cues = script(plan)
    seconds = len(plan.get("route") or []) * 1.9 + sum(len(t) for t in cues.values()) / 6 + 3
    return min(42 if plan.get("kind") == "mate" else 30, max(12, seconds))
