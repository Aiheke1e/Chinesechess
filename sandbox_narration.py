"""Factual, step-sized sandbox narration and token-scoped playback receipts."""
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from collections import Counter
import math

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
        return data.get("token") == token and data.get("status") in ("preparing", "ready", "started", "finished")
    except (OSError, ValueError):
        return False


def audio_ready(token):
    try:
        data = json.loads(marker_path(token).read_text(encoding="utf-8"))
        return data.get("token") == token and data.get("status") in ("ready", "started", "finished")
    except (OSError, ValueError):
        return False


def question_budget(token):
    """Only this move's prepared question may request a pre-entry scene."""
    try:
        data = json.loads(marker_path(token).read_text(encoding="utf-8"))
        budget = float(data.get("scene_seconds", 0))
        if (data.get("token") == token and data.get("status") in ("preparing", "ready", "started", "finished")
                and 5 <= budget <= 30):
            return budget
    except (OSError, ValueError, TypeError):
        pass
    return None


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
        data = {"token": token, "status": status, "created": time.time()}
        if payload.get("沙盘步骤") == "question" and payload.get("_sandbox_scene_seconds"):
            data["scene_seconds"] = payload["_sandbox_scene_seconds"]
        temporary.write_text(json.dumps(data), encoding="utf-8")
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
    def amount(piece):
        count = pieces.count(piece)
        number = "两" if count == 2 else "一二三四五六七八九十"[count - 1] if 0 < count <= 10 else str(count)
        unit = {"R": "辆", "N": "匹", "C": "门"}.get(piece.upper(), "枚")
        return f"{number}{unit}{PIECE_CN[piece]}"
    return "、".join(amount(piece) for piece in dict.fromkeys(pieces))


def result_note(route, own, mate=False, pattern="组合将杀", variant=0):
    index = int(variant) % 8
    if mate:
        return (f"{pattern}，对面已经无棋可解。", f"这路{pattern}摆到这儿，就是绝杀。")[index % 2]
    ours = [item["capture"] for item in route if item.get("capture") and item.get("side") == own]
    theirs = [item["capture"] for item in route if item.get("capture") and item.get("side") != own]
    if ours and theirs:
        if Counter(p.upper() for p in ours) == Counter(p.upper() for p in theirs):
            trade = "兑" + PIECE_CN[ours[0]] if len(ours) == 1 else f"双方各换掉{_pieces(ours)}"
            return (f"这么接就是{trade}，后手也得一起看。", f"这段{trade}摆清楚了，看实战怎么接。",
                    f"这一来一回是{trade}，可别当成白捡。", f"{trade}这段先看到这儿，接着盯棋盘。",
                    f"按这条接法是{trade}，不是单方面赚子。", f"眼前这口还有回吃，合起来是{trade}。",
                    f"{trade}的来回都摆了，等对面亮实招。", f"这套{trade}的接法看清楚了，继续看棋。")[index]
        # Cancel like-for-like exchanges before describing the balance, rather
        # than reading a long shopping list which hides the point of the line.
        shared = Counter(p.upper() for p in ours) & Counter(p.upper() for p in theirs)
        if shared:
            def subtract(pieces):
                counts, remaining, traded = shared.copy(), [], []
                for piece in pieces:
                    if counts[piece.upper()]:
                        counts[piece.upper()] -= 1
                        traded.append(piece)
                    else:
                        remaining.append(piece)
                return remaining, traded
            gain, traded = subtract(ours)
            cost, _ = subtract(theirs)
            balance = (f"咱们还能拿{_pieces(gain)}" if gain and not cost else
                       f"咱们还要付出{_pieces(cost)}" if cost and not gain else
                       f"咱们用{_pieces(cost)}换{_pieces(gain)}")
            return (f"按这条线，双方先兑掉{_pieces(traded)}，{balance}。",
                    f"把{_pieces(traded)}的互换抵掉，{balance}。",
                    f"这口交换两边都付了{_pieces(traded)}，最后{balance}。",
                    f"这条线先兑掉{_pieces(traded)}，再看剩下的得失：{balance}。",
                    f"{_pieces(traded)}是双方互换的部分，额外算下来{balance}。",
                    f"别只看先吃到的子，兑掉{_pieces(traded)}后，{balance}。",
                    f"这段不是白吃，兑掉{_pieces(traded)}以后，{balance}。",
                    f"把来回的{_pieces(traded)}算平，结果是{balance}。")[index]
        return (f"按这条线，咱们用{_pieces(theirs)}换{_pieces(ours)}。",
                f"这套接法能拿{_pieces(ours)}，代价是{_pieces(theirs)}。",
                f"咱们拿到{_pieces(ours)}，也得交出{_pieces(theirs)}。",
                f"这段交换摆全了：用{_pieces(theirs)}换{_pieces(ours)}。")[index % 4]
    if ours:
        return (f"按这条接法能拿到{_pieces(ours)}，看实战接不接得上。",
                f"这套接法的目标是{_pieces(ours)}，继续看对面的选择。")[index % 2]
    if theirs:
        return f"这么接会让对面拿到{_pieces(theirs)}，这个代价可别漏算。"
    if any(item.get("check") or item.get("escape") for item in route):
        return ("将军和应将串起来了，继续看实战。", "这几步怎么解将摆清楚了，等实战落子。")[index % 2]
    return "这段接法先看到这儿，咱们接着看棋。"


def _step_narration(route, index, own, variant):
    item = route[index]
    move = str(item.get("notation") or "")
    for prefix in ("红方 ", "黑方 ", "演示收尾："):
        move = move.replace(prefix, "")
    side = item.get("side") or (
        "r" if str(item.get("notation", "")).startswith("红方") else
        "b" if str(item.get("notation", "")).startswith("黑方") else own)
    subject = "咱们" if side == own else "对面"
    previous = route[index - 1] if index > 0 else {}
    following = route[index + 1] if index + 1 < len(route) else {}
    recaptures_previous = bool(
        item.get("capture") and previous.get("capture") and
        str(item.get("ucci") or "")[2:] == str(previous.get("ucci") or "")[2:])
    same_square_recapture = bool(
        item.get("capture") and following.get("capture") and
        str(item.get("ucci") or "")[2:] == str(following.get("ucci") or "")[2:])
    if item.get("check") and item.get("escape"):
        lines = (f"{subject}{move}，解将反将，对面得先应这一手。",
                 f"{move}先解将再反将，这条线还得接着看。",
                 f"{subject}这步{move}两件事一起办了：解将、反将。")
    elif item.get("check"):
        lines = (f"{subject}{move}，将军先手，对面得先应这一着。",
                 f"{move}将军了，接下来先看对面怎么解。",
                 f"{subject}这步{move}带将军，路线还得顺着应手看完。")
    elif item.get("escape"):
        lines = (f"{subject}{move}，先解将，这条线才接得下去。",
                 f"{move}，先解将，再看后面的应手。",
                 f"这步{move}先处理将军，不能跳过这层防守。")
    elif recaptures_previous:
        piece = PIECE_CN.get(str(previous.get("capture") or ""), "子")
        lines = (f"{subject}{move}，这是回吃，这口{piece}的交换就算齐了。",
                 f"{move}是回吃，前后两口得放一起算。",
                 f"这步{move}把{piece}接回来，交换账终于完整了。")
    elif same_square_recapture:
        piece = PIECE_CN.get(str(item.get("capture") or ""), "子")
        lines = (f"{subject}{move}吃到{piece}，对面马上能回吃，账得连着算。",
                 f"这步{move}先拿{piece}，下一手就在同一格回吃。",
                 f"{move}吃{piece}之后还有回吃，眼前这口不等于白赚。")
    elif item.get("capture"):
        piece = PIECE_CN.get(str(item.get("capture") or ""), "子")
        lines = (f"{subject}{move}，先拿下{piece}，还要看后手能不能站住。",
                 f"{move}吃到{piece}，这口值不值要把后续接完。",
                 f"{subject}这步{move}拿了{piece}，接下来盯对面的应法。")
    else:
        next_capture = next((future for future in route[index + 1:]
                             if future.get("capture")), None)
        if next_capture:
            piece = PIECE_CN.get(str(next_capture.get("capture") or ""), "子")
            lines = (f"{subject}{move}先把路线接上，后面这口{piece}才是看点。",
                     f"先走{move}，后续变化会落到{piece}的交换上。",
                     f"{move}先落下，别急着下结论，后面还要处理{piece}。")
        else:
            next_move = str(following.get("notation") or "")
            for prefix in ("红方 ", "黑方 ", "演示收尾："):
                next_move = next_move.replace(prefix, "")
            lines = (f"{subject}{move}，这步先调子，后面看应手。",
                     f"先把{move}走出来，攻防还要看下一回合。",
                     f"{move}先落位，这条变化还没到结果。" if not next_move else
                     f"{subject}{move}之后，下一手接{next_move}。")
    return lines[(int(variant) + index) % len(lines)]


def script(plan):
    route = plan.get("route") or []
    mate = plan.get("kind") == "mate"
    intro = (f"这路{plan.get('pattern', '组合将杀')}，用沙盘摆一下。" if mate else
             "摆一下这段连续将军，看应将怎么接。" if plan.get("target_kind") == "连续将军" else
             "这段交换要连着看，咱们摆清后手。" if plan.get("reason") == "关键交换" else
             "这里有后续吃子，咱们摆一下接法。")
    scene = plan.get("audience_scene") if not mate else None
    variant = int(plan.get("narration_variant", 0)) % 8
    if not mate:
        target = next((PIECE_CN[item["capture"]] for item in route
                       if item.get("side") == plan.get("own") and item.get("capture")
                       and item["capture"].upper() in "RNC"), "")
        focus = ("连续将军的应对" if plan.get("target_kind") == "连续将军" else
                 f"吃{target}之后的接法" if target else "这段交换的后手")
        options = (f"用沙盘看看{focus}。", f"咱们摆一条线，看{focus}。",
                   f"这里值得展开：{focus}。", f"先摆清楚{focus}，再看实战。",
                   f"咱们把{focus}连起来看。", f"这一段看{focus}，沙盘里过一遍。",
                   f"顺着这条线，看看{focus}。", f"沙盘推一条参考线，重点看{focus}。")
        intro = options[variant]
        if plan.get("thinking_wait"):
            intro = ("对面还没落子，摆一条可能的接法。", "趁对面还没走，咱们摆一种变化。",
                     "这手还没落定，先用沙盘看看接法。", "实战还没新招，咱们摆一条参考线。")[variant % 4]
    cues = {}
    if scene:
        cues["question"] = scene["question"]
        intro = (f"如果想到吃{scene['lead_piece']}，咱们就摆这条线。" if scene["variant"] % 2 == 0 else
                 f"{scene['lead_move']}，咱们往后看。")
    cues["intro"] = intro
    first_side = (route[0].get("side") or ("r" if str(route[0].get("notation", "")).startswith("红方") else "b")) if route else "r"
    own = plan.get("own") or (first_side if plan.get("phase") == "before_move" else "b" if first_side == "r" else "r")
    for index, item in enumerate(route):
        if item.get("illustration"):
            text = str(item.get("notation") or "")
            for prefix in ("红方 ", "黑方 ", "演示收尾："):
                text = text.replace(prefix, "")
            cues[str(index + 1)] = "最后摆一步，" + text.rstrip("。") + "。"
        else:
            cues[str(index + 1)] = _step_narration(
                route, index, own, plan.get("narration_variant", 0))
    cues["summary"] = result_note(route, own, mate, plan.get("pattern", "组合将杀"), plan.get("narration_variant", 0))
    if scene:
        ours = [item["capture"] for item in route if item.get("capture") and item.get("side") == own]
        theirs = [item["capture"] for item in route if item.get("capture") and item.get("side") != own]
        our_counts = Counter(piece.upper() for piece in ours)
        their_counts = Counter(piece.upper() for piece in theirs)
        if our_counts == their_counts and len(our_counts) == 1:
            kind = next(iter(our_counts))
            facts = f"这是兑{PIECE_CN[kind]}，双方各少{_pieces(ours)}。"
        else:
            facts = f"咱们拿{_pieces(ours)}，对面拿{_pieces(theirs)}。"
        praise = (("妙在后手。", "漂亮，后续才是看点。", "这一下有高手味儿。", "妙，后面的接法也算到了。") if scene["profitable"] else
                  ("高手看的是后手。", "来回的代价都要算。", "看全这条线，才有高手味儿。", "交换的账要算完整。"))
        cues["summary"] = facts + praise[scene["variant"] % len(praise)]
    return cues


def demo_budget(plan):
    """Budget a complete line, including each spoken cue and board animation."""
    cues = script(plan)
    route = plan.get("route") or []
    spoken = sum(len(text) for cue, text in cues.items() if cue != "question") / 5.2
    board_and_ui = len(route) * 2.2 + 4.0
    seconds = math.ceil((spoken + board_and_ui) * 1.18)
    return min(90 if plan.get("kind") == "mate" else 65,
               max(18, seconds))


def question_seconds(scene):
    """Give a short audience prompt its own budget before the route starts."""
    text = str((scene or {}).get("question") or "")
    return min(8, max(5, math.ceil(len(text) / 5.2 + 2)))
