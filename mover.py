# mover.py — JJ象棋落子执行器 (点选式: 点棋子 -> 点目标格)
from __future__ import annotations
import sys, os, time, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adb_bridge import adb, screenshot, list_devices
from recognizer import (OX, OY, DX, DY, SHIFT_X, SHIFT_Y,
                        recognize_indices, recognize_occupancy,
                        recognize_presence_indices,
                        recognize_position)
from state import (after_move, matches_after_move, same_position,
                   matches_move_with_limited_vision_loss,
                   trusted_position_matches)


def pt(i: int, orientation="red_bottom") -> tuple[float, float]:
    """交叉点索引 -> 屏幕坐标"""
    if orientation == "black_bottom":
        i = 89 - i
    elif orientation != "red_bottom":
        raise ValueError(f"unknown board orientation: {orientation}")
    r, c = divmod(i, 9)
    return (OX + c * DX + SHIFT_X, OY + r * DY + SHIFT_Y)


def tap(x, y, device, jitter=2):
    """带随机抖动的点击 (拟人)"""
    jx = random.uniform(-jitter, jitter)
    jy = random.uniform(-jitter, jitter)
    adb(["shell", "input", "tap", str(int(x + jx)), str(int(y + jy))], device=device)


def _same_occupancy_side(expected, observed):
    if expected is None or observed is None:
        return expected is observed
    return expected.isupper() == observed.isupper()


def _probe_matches(expected, observed, indices):
    return bool(observed) and all(
        index in observed and
        _same_occupancy_side(expected[index], observed[index])
        for index in indices)


def _probe_indices(expected, frm, to, limit=8):
    """Select move endpoints, both kings and spread-out occupied sentinels."""
    result = [frm, to]
    for king in ("K", "k"):
        try:
            result.append(expected.index(king))
        except ValueError:
            pass
    occupied = [index for index, piece in enumerate(expected)
                if piece is not None and index not in result]
    if occupied:
        slots = max(1, limit - len(result))
        step = max(1, len(occupied) // slots)
        result.extend(occupied[::step])
    return list(dict.fromkeys(result))[:limit]


def move_piece(frm: int, to: int, device, expected_before=None,
               settle_first=0.25, verify=True, verify_timeout=4.0,
               poll_interval=0.25, orientation="red_bottom",
               tolerant_indices=(), precondition_timeout=2.0):
    """点选式走一步，一次输入，多次只读确认。

    返回 (ok: bool, 说明: str)
    """
    fx, fy = pt(frm, orientation)
    tx, ty = pt(to, orientation)

    deadline = time.monotonic() + max(0.0, float(precondition_timeout))
    image = before = None
    detected = None
    while True:
        image = screenshot(device)
        if image is not None:
            trusted = False
            if expected_before is not None:
                probe = _probe_indices(expected_before, frm, to)
                quick = recognize_indices(image, orientation, probe)
                # JJ keeps the last moved piece glowing.  The glyph matcher
                # may reject that piece even though its circle and side colour
                # remain unmistakable.  Repair only missing squares that the
                # trusted board says are occupied; never manufacture a piece
                # on an expected-empty hint intersection.
                if not _probe_matches(expected_before, quick, probe):
                    presence = recognize_presence_indices(
                        image, orientation, probe)
                    repaired = dict(quick)
                    for index in probe:
                        wanted = expected_before[index]
                        seen = presence.get(index)
                        if (wanted is not None and quick.get(index) is None and
                                seen is not None and
                                wanted.isupper() == seen.isupper()):
                            repaired[index] = wanted
                    quick = repaired
                trusted = _probe_matches(expected_before, quick, probe)
                if trusted:
                    before, detected = expected_before, orientation
            if not trusted:
                before, detected = recognize_position(image, orientation)
                trusted = before is not None and detected == orientation
                if trusted and expected_before is not None:
                    trusted = trusted_position_matches(
                        expected_before, before, tolerant_indices)
                    if not trusted:
                        occupancy = recognize_occupancy(image, orientation)
                        trusted = trusted_position_matches(
                            expected_before, occupancy, range(90))
            if trusted:
                break
        if time.monotonic() >= deadline:
            if image is None:
                return False, "FAIL 落子前截图失败"
            try:
                from diagnostics import capture_diagnostic
                capture_diagnostic(
                    device, "move_precondition_failed", image=image,
                    extra={"from": frm, "to": to,
                           "orientation": orientation})
            except Exception:
                pass
            if before is None or detected != orientation:
                return False, "FAIL 无法确认棋盘朝向，未发送点击"
            return False, "FAIL 落子前画面与可信局面不一致，未发送点击"
        time.sleep(0.25)
    piece = expected_before[frm] if expected_before is not None else before[frm]
    if piece is None:
        return False, f"FAIL 起点 idx{frm} 未识别到棋子，未发送点击"
    trusted_before = expected_before if expected_before is not None else before
    expected_after = after_move(trusted_before, (frm, to))

    tap(fx, fy, device)
    time.sleep(settle_first)
    tap(tx, ty, device)

    if not verify:
        return True, f"tap {frm}->{to} (未校验)"

    deadline = time.monotonic() + verify_timeout
    last = before
    last_image = image
    last_occupancy = None
    relaxed_hits = 0
    next_full_check = time.monotonic() + 0.8
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        image = screenshot(device)
        if image is None:
            continue
        last_image = image
        endpoints = recognize_indices(image, orientation, (frm, to))
        if _probe_matches(expected_after, endpoints, (frm, to)):
            return True, f"OK {piece} {frm}->{to} 落子成功（局部快速校验）"
        # The destination glyph can disappear under JJ's bright last-move
        # ring.  Presence recognition only repairs the expected occupied
        # destination; it never invents an empty source, so this preserves the
        # same source-empty/destination-owned safety proof on the first frame.
        presence = recognize_presence_indices(
            image, orientation, (frm, to))
        repaired = dict(endpoints)
        for index in (frm, to):
            wanted = expected_after[index]
            seen = presence.get(index)
            if (wanted is not None and endpoints.get(index) is None and
                    seen is not None and
                    wanted.isupper() == seen.isupper()):
                repaired[index] = wanted
        if _probe_matches(expected_after, repaired, (frm, to)):
            return True, (f"OK {piece} {frm}->{to} 落子成功"
                          "（高亮容错局部校验）")
        # An empty result means the local classifier itself was unavailable;
        # do not postpone the proven full-board fallback in that case.
        if endpoints and time.monotonic() < next_full_check:
            continue
        next_full_check = time.monotonic() + 0.8
        last, detected = recognize_position(image, orientation)
        if last is None or detected != orientation:
            continue
        if (trusted_position_matches(expected_after, last,
                                     set(tolerant_indices) | {to}) or
                matches_after_move(trusted_before, (frm, to), last,
                                   tolerant_indices)):
            return True, f"OK {piece} {frm}->{to} 落子成功"
        occupancy = recognize_occupancy(image, orientation)
        last_occupancy = occupancy
        if trusted_position_matches(expected_after, occupancy, range(90)):
            return True, f"OK {piece} {frm}->{to} 落子成功（占位校验）"
        if matches_move_with_limited_vision_loss(
                trusted_before, (frm, to), occupancy, max_collateral=1):
            relaxed_hits += 1
            if relaxed_hits >= 2:
                return True, (f"OK {piece} {frm}->{to} 落子成功"
                              "（连续两帧容错占位校验）")
        else:
            relaxed_hits = 0
    diagnostic_path = None
    try:
        from diagnostics import capture_diagnostic
        full_differences = [
            {"index": index, "expected": wanted, "observed": seen}
            for index, (wanted, seen) in enumerate(zip(expected_after, last))
            if wanted != seen
        ]
        occupancy_differences = []
        if last_occupancy is not None:
            occupancy_differences = [
                {"index": index, "expected": wanted, "observed": seen}
                for index, (wanted, seen) in enumerate(
                    zip(expected_after, last_occupancy))
                if ((wanted is None) != (seen is None) or
                    (wanted is not None and seen is not None and
                     wanted.isupper() != seen.isupper()))
            ]
        diagnostic_path = capture_diagnostic(
            device, "move_confirmation_failed", image=last_image,
            extra={"from": frm, "to": to, "orientation": orientation,
                   "full_differences": full_differences,
                   "occupancy_differences": occupancy_differences})
    except Exception:
        pass
    message = (f"FAIL 输入已发送但未确认；期望 idx{to}={piece}，"
               f"实际={last[to]}，起点 idx{frm}={last[frm]}。为避免重复走子未重试")
    if diagnostic_path:
        message += "；原始现场=" + diagnostic_path
    return False, message


def move_piece_retry(frm: int, to: int, device, tries=1, expected_before=None, **kwargs):
    """兼容旧入口；有副作用的落子绝不盲目重试。"""
    return move_piece(frm, to, device, expected_before=expected_before, **kwargs)


if __name__ == "__main__":
    devs = list_devices()
    if not devs:
        print("无设备"); sys.exit(1)
    dev = devs[0]
    sq = recognize(screenshot(dev))
    print("当前棋子数:", sum(1 for x in sq if x))
    from board import Board
    print(Board(sq, turn="r").pretty())
    print("\n用法示例: move_piece_retry(64, 67, dev)  # 红炮 b2->e2")
