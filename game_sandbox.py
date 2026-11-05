"""Drive JJ's built-in in-game sandbox without corrupting the live position."""
from __future__ import annotations

import json
import os
import re
import time

import cv2
import numpy as np

from adb_bridge import adb, screenshot, tap
from board import Board
from notation import move_description
from mover import pt
from recognizer import (recognize_indices, recognize_occupancy,
                        recognize_presence_indices,
                        recognize_presence_occupancy)
from state import (resolve_legal_sequence_by_occupancy,
                   trusted_position_matches)


HERE = os.path.dirname(os.path.abspath(__file__))
LIVE_CONFIG = os.path.join(HERE, "live_config.json")
MAX_MATE_PLIES = 14
MAX_TACTIC_PLIES = 8


class SandboxCadence:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.last_move, self.last_at = -100, -1e9
        self.recent = {}

    def allowed(self, plan, number):
        signature = tuple(item.get("ucci") for item in plan.get("route", []))
        return (plan.get("kind") == "mate" or
                number - self.last_move >= 3 and self.clock() - self.last_at >= 25
                and (not signature or self.clock() - self.recent.get(signature, -1e9) >= 150))

    def record(self, number, plan=None):
        self.last_move, self.last_at = number, self.clock()
        signature = tuple(item.get("ucci") for item in (plan or {}).get("route", []))
        self.recent = {key: at for key, at in self.recent.items() if self.clock() - at < 150}
        if signature:
            self.recent[signature] = self.last_at


def king_capture_finish(board):
    """An illustration AFTER checkmate, never a legal move for the live game."""
    loser = board.turn
    if not board.in_check(loser) or board.legal_moves():
        return None
    target = board.king_pos(loser)
    if target < 0:
        return None
    attacker = "b" if loser == "r" else "r"
    captures = [move for move in board.legal_moves(attacker) if move[1] == target]
    if not captures:
        return None
    move = sorted(captures)[0]
    return {"ucci": Board.ucci(move), "notation": "演示收尾：" + move_description(board, move),
            "illustration": "king_capture"}


def validated_route(board, route, mate=False):
    probe, result = board.copy(), []
    if not route or len(route) > (MAX_MATE_PLIES if mate else MAX_TACTIC_PLIES):
        return []
    for index, item in enumerate(route):
        try:
            move = Board.from_ucci(item["ucci"])
            if item.get("illustration"):
                finish = king_capture_finish(probe)
                if (not mate or index != len(route) - 1 or not finish or
                        item.get("illustration") != "king_capture" or item["ucci"] != finish["ucci"]):
                    return []
            elif move not in probe.legal_moves() or (probe.sq[move[1]] or "").upper() == "K":
                return []
            result.append((item, move))
            probe.apply(move)
        except (KeyError, TypeError, ValueError, IndexError):
            return []
    if mate and route[-1].get("illustration") != "king_capture":
        return []
    return result


def enabled():
    try:
        with open(LIVE_CONFIG, encoding="utf-8") as stream:
            return bool(json.load(stream).get("sandtable_enabled", True))
    except (OSError, ValueError, TypeError):
        return True


def kill_confidence(analysis):
    try:
        score = int((analysis or {}).get("score", 0))
        depth = int((analysis or {}).get("depth", 0))
    except (TypeError, ValueError):
        return 0
    if analysis.get("score_type") == "mate":
        return 100 if score > 0 else 0
    if analysis.get("score_type") != "cp" or score <= 0:
        return 0
    confidence = round(50 + max(0, score - 200) / 20)
    if depth < 12:
        confidence -= 12
    return max(0, min(95, confidence))


def build_plan(notation, analysis, pv_line, executed_ucci, board=None):
    """Verify a tactical PV; complete mate demos run before the real move."""
    if board is None:
        return None
    try:
        score = int((analysis or {}).get("score", 0))
    except (TypeError, ValueError):
        score = 0
    if (analysis or {}).get("score_type") == "mate" and score < 0:
        return None  # Defending a mating attack takes priority over an optional excursion.
    from sandbox_narration import demo_budget, result_note
    probe, route, facts, positions = board.copy(), [], [], []
    own = board.turn
    for ucci, _ in list(pv_line or [])[:MAX_MATE_PLIES]:
        try:
            move = Board.from_ucci(ucci)
            if move not in probe.legal_moves() or (probe.sq[move[1]] or "").upper() == "K":
                break
            if not route and ucci != executed_ucci:
                return None
            captured, side, escape = probe.sq[move[1]], probe.turn, probe.in_check(probe.turn)
            text = move_description(probe, move)
            probe.apply(move)
            route.append({"ucci": ucci, "notation": text, "side": side,
                          "capture": captured, "check": probe.in_check(probe.turn), "escape": escape})
            positions.append(probe.copy())
            facts.append({"capture": captured, "own": side == own, "escape": escape,
                          "check": probe.in_check(probe.turn)})
            if not probe.legal_moves():
                if probe.in_check(probe.turn) and side == own and len(route) < MAX_MATE_PLIES:
                    finish = king_capture_finish(probe)
                    if finish:
                        from mate_patterns import identify_pattern
                        pattern = identify_pattern(probe) or "组合将杀"
                        finish["side"] = own
                        plan = {"kind": "mate", "phase": "before_move", "reason": pattern + "绝杀推演",
                                "route": route + [finish], "confidence": 100, "target": text,
                                "target_kind": "将死后演示吃将", "own": own,
                                "pattern": pattern}
                        plan["max_seconds"] = demo_budget(plan)
                        return plan
                break
        except (ValueError, TypeError, IndexError):
            break
    if len(route) < 3:
        return None
    # Normal demos explain exchanges, checks and escapes at ordinary scores too.
    future = facts[1:MAX_TACTIC_PLIES + 1]
    own_target = next((index + 1 for index, fact in enumerate(future)
                       if fact["own"] and (fact["capture"] or fact["check"])), 2)
    big_capture = bool(facts[0]["capture"] and facts[0]["capture"].upper() in "RNC")
    future_big = any(fact["own"] and fact["capture"] and fact["capture"].upper() in "RNC" for fact in future)
    exchange = any(a["capture"] and b["capture"] for a, b in zip(future, future[1:]))
    check = any(fact["own"] and fact["check"] for fact in facts[:4])
    escape = facts[0]["escape"]
    if not (big_capture or future_big or exchange or check or escape):
        return None
    meaningful = [index for index, fact in enumerate(facts[:MAX_TACTIC_PLIES + 1]) if index > 0
                  if fact["own"] and (fact["capture"] and fact["capture"].upper() in "RNC" or fact["check"])]
    end = max(2, meaningful[0] if meaningful else 0)
    # Continue the capture/check/forced-reply chain until its actual outcome;
    # do not truncate a recapture merely to keep a three-ply animation.
    while end + 1 < len(route) and end < MAX_TACTIC_PLIES:
        next_fact = facts[end + 1]
        if not (next_fact["capture"] or next_fact["escape"] or next_fact["check"]):
            break
        end += 1
    if end >= len(route) or facts[end]["check"]:
        return None
    if end + 1 < len(route) and (facts[end + 1]["capture"] or facts[end + 1]["escape"]):
        return None  # The safe size limit must not turn half an exchange into a result.
    if end == len(route) - 1 and facts[end]["capture"]:
        target = Board.from_ucci(route[end]["ucci"])[1]
        if any(move[1] == target for move in positions[end].legal_moves()):
            return None  # PV ended while an immediate recapture is still possible.
    demo_route = route[1:end + 1]
    if not any(item.get("capture") or item.get("check") or item.get("escape") for item in demo_route):
        return None  # A capture followed only by quiet shuffling needs no demo.
    seen = set()
    for position in positions[:end + 1]:
        if position.to_fen() in seen:
            return None
        seen.add(position.to_fen())
    own_target = min(own_target, end)
    reason = ("解将后的衔接" if escape else "关键交换" if exchange else "吃大子后的接法" if big_capture else
              "后续吃大子" if future_big else "将军与应将")
    plan = {"kind": "tactic", "phase": "after_move", "reason": reason, "own": own,
            "confidence": kill_confidence(analysis or {}), "route": demo_route,
            "target": route[own_target]["notation"],
            "target_kind": "吃大子" if future_big else "关键接法"}
    plan["summary"] = result_note(demo_route, own)
    plan["max_seconds"] = demo_budget(plan)
    return plan


def build_deferred_plan(reason, pv_line):
    """Build a fresh current-position PV while it is safely our turn."""
    route = []
    for ucci, text in list(pv_line or [])[:4]:
        if re.fullmatch(r"[a-i][0-9][a-i][0-9]", str(ucci)) and text:
            route.append({"ucci": str(ucci), "notation": str(text)})
    if len(route) < 3:
        return None
    return {"reason": str(reason or "关键路线"), "confidence": 0,
            "route": route, "deferred": True}


def _region_difference(first, second, x0, y0, x1, y1):
    if first is None or second is None or first.shape != second.shape:
        return 0.0
    height, width = first.shape[:2]
    a = first[int(y0 * height):int(y1 * height),
              int(x0 * width):int(x1 * width)]
    b = second[int(y0 * height):int(y1 * height),
               int(x0 * width):int(x1 * width)]
    if not a.size or a.shape != b.shape:
        return 0.0
    return float(cv2.absdiff(a, b).mean())


def _position_matches(board, image, orientation):
    if image is None:
        return False
    observed = recognize_occupancy(image, orientation)
    if trusted_position_matches(board.sq, observed, range(90)):
        return True
    # Piece artwork can be temporarily covered by JJ's last-move glow even
    # though every occupied square and side colour is already correct.  Exact
    # coloured occupancy is sufficient for the sandbox gate: every legal move
    # changes at least a source and destination square, so an opponent move
    # cannot pass this zero-difference check.
    if observed is not None and len(observed) == 90:
        same_coloured_occupancy = all(
            ((wanted is None) == (seen is None) and
             (wanted is None or wanted.isupper() == seen.isupper()))
            for wanted, seen in zip(board.sq, observed))
        if same_coloured_occupancy:
            return True
    # The live board often retains red/green last-move rings exactly when a
    # sandbox trigger fires.  Presence-only recognition is allowed to repair
    # that display variant, but it must describe the unchanged trusted board;
    # a real opponent move changes at least source and destination and is not
    # accepted here.
    presence = recognize_presence_occupancy(image, orientation)
    sequence = resolve_legal_sequence_by_occupancy(
        board, presence, max_plies=0, max_mismatches=1)
    return sequence == []


def _position_probe(board, image, orientation):
    """Explain a rejected sandbox gate without changing any UI state."""
    if image is None:
        return {"strong_differences": 90, "presence_differences": 90,
                "presence_sequence": None}
    strong = recognize_occupancy(image, orientation)
    presence = recognize_presence_occupancy(image, orientation)

    def differences(observed):
        if observed is None or len(observed) != 90:
            return list(range(90))
        return [
            index for index, (wanted, seen) in enumerate(
                zip(board.sq, observed))
            if ((wanted is None) != (seen is None) or
                (wanted is not None and seen is not None and
                 wanted.isupper() != seen.isupper()))]

    sequence = resolve_legal_sequence_by_occupancy(
        board, presence, max_plies=1, max_mismatches=1)
    return {
        "strong_differences": differences(strong),
        "presence_differences": differences(presence),
        "presence_sequence": (None if sequence is None else
                              [Board.ucci(move) for move in sequence]),
    }


def _advance_after_observed_reply(live_board, legal_route, probe):
    """Shift a PV sandbox to our turn when JJ already played its first ply."""
    observed = (probe or {}).get("presence_sequence")
    if (not isinstance(observed, list) or len(observed) != 1 or
            not legal_route or len(legal_route) < 2):
        return None
    item, move = legal_route[0]
    if observed[0] != item.get("ucci") or Board.ucci(move) != observed[0]:
        return None
    advanced = live_board.copy()
    if move not in advanced.legal_moves():
        return None
    advanced.apply(move)
    return advanced, legal_route[1:], observed[0]


def _wait_for_position(device, board, orientation, timeout=2.5,
                       required_hits=2):
    """Require two consecutive live-board frames before opening sandbox."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    required_hits = max(1, int(required_hits))
    stable_hits = 0
    while True:
        image = screenshot(device)
        if _position_matches(board, image, orientation):
            stable_hits += 1
            if stable_hits >= required_hits:
                return image
        else:
            stable_hits = 0
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.18)


def _sandbox_button(image):
    height, width = image.shape[:2]
    # Current JJ portrait layout: the built-in 沙盘 control is the first round
    # button below the board.  Normalized coordinates survive emulator scaling.
    return round(width * 0.085), round(height * 0.837)


def _sandbox_exit_button(image):
    height, width = image.shape[:2]
    return round(width * 0.225), round(height * 0.958)


def _detect_sandbox_exit(image):
    """Return the centre of JJ's red sandbox exit button when visible."""
    if image is None:
        return None
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(
        hsv, np.array((0, 90, 90), dtype=np.uint8),
        np.array((12, 255, 255), dtype=np.uint8))
    red |= cv2.inRange(
        hsv, np.array((170, 90, 90), dtype=np.uint8),
        np.array((179, 255, 255), dtype=np.uint8))
    count, _, stats, centers = cv2.connectedComponentsWithStats(red)
    for index in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[index])
        if (y >= height * 0.85 and w >= width * 0.15 and
                h >= height * 0.035 and area >= width * height * 0.008):
            return round(float(centers[index][0])), round(float(centers[index][1]))
    return None


def _detect_blue_acknowledge(image):
    """Find JJ's large blue health/system notice acknowledgement button."""
    if image is None:
        return None
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv, np.array((85, 80, 100), dtype=np.uint8),
        np.array((115, 255, 255), dtype=np.uint8))
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    for index in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[index])
        if (w >= width * 0.6 and h >= height * 0.25 and
                width * 0.08 <= x <= width * 0.25 and
                height * 0.2 <= y <= height * 0.45 and
                area >= width * height * 0.12):
            return round(float(centers[index][0])), round(y + h * 0.86)
    return None


def _dismiss_interruption(device, image):
    point = _detect_blue_acknowledge(image)
    if point is None:
        return None
    tap(*point, device=device)
    time.sleep(0.65)
    return screenshot(device)


def _endpoint_confirmed(board, move, image, orientation):
    frm, to = move
    moving = board.sq[frm]
    endpoints = recognize_indices(image, orientation, (frm, to))
    if (moving and endpoints.get(frm) is None and
            endpoints.get(to) is not None and
            endpoints[to].isupper() == moving.isupper()):
        return True
    presence = recognize_presence_indices(
        image, orientation, (frm, to))
    return bool(moving and presence.get(frm) is None and
                presence.get(to) is not None and
                presence[to].isupper() == moving.isupper())


def _live_sequence(board, image, orientation, max_plies=1):
    """Recognize live return state with a conservative highlight fallback."""
    if image is None:
        return None
    occupancy = recognize_occupancy(image, orientation)
    sequence = resolve_legal_sequence_by_occupancy(
        board, occupancy, max_plies=max_plies)
    if sequence is not None:
        return sequence
    presence = recognize_presence_occupancy(image, orientation)
    return resolve_legal_sequence_by_occupancy(
        board, presence, max_plies=max_plies, max_mismatches=1)


def _return_to_live(device, live_board, orientation, attempts=2,
                    initial_image=None):
    """Use JJ's red exit control, then prove the live position returned."""
    image = initial_image if initial_image is not None else screenshot(device)
    for attempt in range(attempts):
        dismissed = _dismiss_interruption(device, image)
        if dismissed is not None:
            image = dismissed
        exit_point = _detect_sandbox_exit(image)
        if exit_point is None and image is not None:
            if _live_sequence(live_board, image, orientation) is not None:
                return True
        if exit_point is not None:
            tap(*exit_point, device=device)
        elif image is None:
            adb(["shell", "input", "keyevent", "4"], device=device)
        else:
            tap(*_sandbox_exit_button(image), device=device)
        time.sleep(0.8)
        image = screenshot(device)
        if image is None:
            continue
        sequence = _live_sequence(live_board, image, orientation)
        if sequence is not None:
            return True
        # A changed layout may have hidden the explicit button.  Android BACK
        # is used only as the final sandbox-scoped fallback.
        if attempt + 1 == attempts:
            adb(["shell", "input", "keyevent", "4"], device=device)
            time.sleep(0.6)
            image = screenshot(device)
            if _live_sequence(live_board, image, orientation) is not None:
                return True
    return False


def play_plan(device, live_board, orientation, plan, stop_requested=None,
              on_step=None, on_enter=None, review_seconds=1.2, max_seconds=None,
              on_cue=None,
              gate_timeout=1.5):
    """Enter JJ sandbox, explain a complete verified line, and safely return.

    Returns a dict with ``entered`` and ``safe_exit``.  No route click is sent
    unless the sandbox button visibly changed the control strip while the
    copied board still matches the trusted live position.
    """
    stop_requested = stop_requested or (lambda: False)
    mate = (plan or {}).get("kind") == "mate"
    route = list((plan or {}).get("route") or [])
    legal_route = validated_route(live_board, route, mate=mate)
    if len(legal_route) < 2 or stop_requested():
        return {"entered": False, "safe_exit": True,
                "reason": "绝杀路线未包含已核实的吃将收尾" if mate else "推演路线不足两步或不合法"}

    observed_reply = None
    # Open the sandbox immediately after our confirmed live move.  This is
    # deliberately a short gate: the opponent is free to answer in the real
    # game and must not control the lifetime of our local demonstration.
    before = _wait_for_position(
        device, live_board, orientation, timeout=gate_timeout,
        required_hits=1)
    if before is None:
        failure_image = screenshot(device)
        probe = _position_probe(live_board, failure_image, orientation)
        advanced = (_advance_after_observed_reply(live_board, legal_route, probe)
                    if (plan or {}).get("phase") != "before_move" else None)
        if advanced is not None:
            # JJ answered during our gate check.  The first PV ply is already
            # real, so copy that position and demonstrate the remaining three
            # plies while it is safely our turn.
            live_board, legal_route, observed_reply = advanced
            before = _wait_for_position(
                device, live_board, orientation, timeout=1.2,
                required_hits=1)
        if before is not None:
            probe = None
        else:
            strong_count = len(probe["strong_differences"])
            presence_count = len(probe["presence_differences"])
            diagnostic_path = None
            try:
                from diagnostics import capture_diagnostic
                diagnostic_path = capture_diagnostic(
                    device, "sandbox_live_position_unstable",
                    image=failure_image,
                    extra={"fen": live_board.to_fen(), **probe})
            except Exception:
                pass
            return {"entered": False, "safe_exit": True,
                    "reason": ("实战棋盘尚未稳定"
                               f"（模板差{strong_count}格，占位差{presence_count}格）"),
                    "diagnostic": diagnostic_path,
                    "probe": probe}
    x, y = _sandbox_button(before)
    tap(x, y, device=device)
    # JJ's transition speed varies with emulator load.  A single snapshot at
    # 0.8s caused valid sandbox entries to be rejected while the animation was
    # still running.  Poll briefly and require all three visual proofs on the
    # same frame; route clicks remain impossible until then.
    entered_frame = None
    controls_changed = exit_visible = copied_position = False
    entry_deadline = time.monotonic() + 1.8
    for _ in range(6):
        if time.monotonic() >= entry_deadline or stop_requested():
            break
        time.sleep(0.25)
        candidate = screenshot(device)
        if candidate is None:
            continue
        entered_frame = candidate
        controls_changed = _region_difference(
            before, candidate, 0.0, 0.79, 0.28, 0.90) >= 3.0
        exit_visible = _detect_sandbox_exit(candidate) is not None
        copied_position = _position_matches(
            live_board, candidate, orientation)
        if (controls_changed and exit_visible and not copied_position
                and observed_reply is None and (plan or {}).get("phase") != "before_move"):
            advanced = _advance_after_observed_reply(
                live_board, legal_route, _position_probe(live_board, candidate, orientation))
            if advanced is not None and _position_matches(advanced[0], candidate, orientation):
                live_board, legal_route, observed_reply = advanced
                copied_position = True
        if controls_changed and exit_visible and copied_position:
            break
    if not controls_changed or not exit_visible or not copied_position:
        # If a modal or partial transition did appear, BACK is safer than any
        # board tap.  A completely unchanged screen needs no cleanup.
        safe = True
        if controls_changed:
            safe = _return_to_live(device, live_board, orientation)
        return {"entered": False, "safe_exit": safe,
                "reason": "未确认进入JJ沙盘"}

    if on_enter:
        on_enter()
    sandbox_board = live_board.copy()
    started = time.monotonic()
    if max_seconds is None:
        from sandbox_narration import demo_budget
        max_seconds = demo_budget(plan)
    hard_deadline = started + max(1.0, float(max_seconds))
    completed = 0
    timed_out = False
    last_sandbox_frame = entered_frame
    narration_failed = False
    def cue(name, finish=False):
        if on_cue is None:
            return True
        return on_cue(str(name), finish=finish, deadline=hard_deadline) is not False

    if not cue("intro", finish=True):
        narration_failed = True
    for number, (item, move) in enumerate(legal_route, 1):
        if stop_requested() or narration_failed:
            break
        if time.monotonic() >= hard_deadline:
            timed_out = True
            break
        cue_number = number + int(observed_reply is not None)
        if not cue(cue_number):
            narration_failed = True
            break
        if on_cue is not None:
            last_sandbox_frame = screenshot(device)
        # Recheck mode and copied position before EVERY move, including retries.
        # A slow voice or a platform auto-exit must never expose the live board
        # to the remainder of a simulated variation.
        if (_detect_sandbox_exit(last_sandbox_frame) is None or
                not _position_matches(sandbox_board, last_sandbox_frame, orientation)):
            failed_step = number
            break
        if stop_requested() or time.monotonic() >= hard_deadline:
            timed_out = not stop_requested()
            break
        fx, fy = pt(move[0], orientation)
        tx, ty = pt(move[1], orientation)
        tap(fx, fy, device=device)
        time.sleep(0.18)
        tap(tx, ty, device=device)
        time.sleep(0.42)
        frame = screenshot(device)
        last_sandbox_frame = frame
        if not _endpoint_confirmed(sandbox_board, move, frame, orientation):
            if item.get("illustration"):
                # A king-capture animation can close the mode; do not retry
                # board input on what could now be the live game.
                failed_step = number
                break
            resumed = _dismiss_interruption(device, frame)
            if resumed is not None:
                frame = resumed
                last_sandbox_frame = frame
                # The notice swallowed the sandbox input.  Retrying is safe
                # only here: these are local simulation moves, never live
                # chess inputs, and the trusted sandbox board is unchanged.
                if not _endpoint_confirmed(
                        sandbox_board, move, frame, orientation):
                    if (_detect_sandbox_exit(frame) is None or
                            not _position_matches(sandbox_board, frame, orientation)):
                        failed_step = number
                        break
                    tap(fx, fy, device=device)
                    time.sleep(0.18)
                    tap(tx, ty, device=device)
                    time.sleep(0.42)
                    frame = screenshot(device)
                    last_sandbox_frame = frame
            else:
                # Read a fresh frame before considering a retry.  Only an
                # unchanged sandbox position can authorize the same input.
                time.sleep(0.25)
                frame = screenshot(device)
                last_sandbox_frame = frame
                if _endpoint_confirmed(sandbox_board, move, frame, orientation):
                    sandbox_board.apply(move)
                    completed += 1
                    if on_step:
                        on_step(number, item)
                    if not cue(cue_number, finish=True):
                        narration_failed = True
                    continue
                if (_detect_sandbox_exit(frame) is None or
                        not _position_matches(sandbox_board, frame, orientation)):
                    failed_step = number
                    break
                tap(fx, fy, device=device)
                time.sleep(0.18)
                tap(tx, ty, device=device)
                time.sleep(0.42)
                frame = screenshot(device)
                last_sandbox_frame = frame
            if not _endpoint_confirmed(sandbox_board, move, frame, orientation):
                failed_step = number
                break
        sandbox_board.apply(move)
        completed += 1
        if on_step:
            on_step(number, item)
        if not cue(cue_number, finish=True):
            narration_failed = True
            break
        if time.monotonic() >= hard_deadline:
            timed_out = completed < len(legal_route)
            break

    # Keep the whole excursion bounded.  A fast opponent must not make us burn
    # an unlimited amount of our own clock while the real board is hidden.
    if completed == len(legal_route) and not stop_requested() and not narration_failed:
        narration_failed = not cue("summary", finish=True)
    remaining_review = max(
        0.0, min(float(review_seconds), hard_deadline - time.monotonic()))
    deadline = time.monotonic() + remaining_review
    while time.monotonic() < deadline and not stop_requested():
        time.sleep(min(0.2, deadline - time.monotonic()))
    safe_exit = _return_to_live(
        device, live_board, orientation,
        initial_image=last_sandbox_frame)
    return {"entered": True, "safe_exit": safe_exit,
            "completed": completed,
            "failed_step": locals().get("failed_step"),
            "observed_reply": observed_reply,
            "timed_out": timed_out,
            "narration_failed": narration_failed,
            "king_captured": bool(mate and completed == len(legal_route)),
            # Opponent activity is intentionally not an exit condition.  On
            # return, _return_to_live accepts a legal one-ply real reply and
            # the normal player loop consumes it from the live board.
            "opponent_move_interrupts": False,
            "reason": ("语音未同步，已结束演示" if narration_failed
                       else "吃将收尾演示完成" if mate and completed == len(legal_route)
                       else "绝杀演示未完成吃将收尾" if mate
                       else "演示完成" if completed == len(legal_route)
                       else "达到安全时限，已结束演示" if timed_out
                       else "路线演示提前结束")}
