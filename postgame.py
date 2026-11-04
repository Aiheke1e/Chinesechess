from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from adb_bridge import adb, screenshot, tap


ACTION_TEXTS = ("继续", "再来一局", "再来一盘", "下一局", "确定", "确认")
CLOSE_TEXTS = ("关闭", "取消", "以后再说")
PROMOTION_TEXTS = ("段位提升", "段位晋升", "晋级", "升段")
BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
MATCH_MODE_X = {
    "5分钟": 0.19,
    "10分钟": 0.40,
    "15分钟": 0.60,
    "超快": 0.81,
}


@lru_cache(maxsize=1)
def _rank_templates():
    folder = Path(__file__).resolve().parent / "assets"
    result = []
    for name in ("title", "back"):
        try:
            image = cv2.imdecode(np.frombuffer(
                (folder / f"jj-rank-{name}.png").read_bytes(), dtype=np.uint8),
                cv2.IMREAD_GRAYSCALE)
        except OSError:
            return None
        if image is None:
            return None
        result.append(image)
    return result


def detect_rank_promotion_dismiss(image):
    """Recognize the fixed title AND return arrow, independent of rank art.

    A blank click was ineffective on the observed page.  Its visible bottom
    left return arrow is the only target; unrelated full-screen pages must
    never acquire a generic blank-click/BACK fallback.
    """
    if image is None or image.ndim != 3:
        return None
    height, width = image.shape[:2]
    if height < 800 or width < 450 or not .48 <= width / height <= .65:
        return None
    templates = _rank_templates()
    if templates is None:
        return None
    gray = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (1080, 1920))
    title_area = gray[25:190, 320:760]
    back_area = gray[1750:1900, 30:155]
    matches = []
    for template, area in zip(templates, (title_area, back_area)):
        scores = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(scores)
        if score < .82:
            return None
        matches.append(point)
    x, y = matches[1]
    arrow = templates[1]
    return (round((30 + x + arrow.shape[1] / 2) * width / 1080),
            round((1750 + y + arrow.shape[0] / 2) * height / 1920))


def parse_action_nodes(xml_text):
    """Return prioritized (text, center-x, center-y) actions from UI XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    found = []
    for node in root.iter("node"):
        label = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
        matched = next((wanted for wanted in ACTION_TEXTS if wanted in label), None)
        bounds = BOUNDS_RE.fullmatch(node.attrib.get("bounds", ""))
        if not matched or not bounds:
            continue
        x1, y1, x2, y2 = map(int, bounds.groups())
        if x2 > x1 and y2 > y1:
            found.append((ACTION_TEXTS.index(matched), label,
                          (x1 + x2) // 2, (y1 + y2) // 2))
    found.sort()
    return [(label, x, y) for _, label, x, y in found]


def read_action_nodes(device):
    adb(["shell", "uiautomator", "dump", "/sdcard/jj_window.xml"],
        timeout=15, device=device)
    result = adb(["exec-out", "cat", "/sdcard/jj_window.xml"],
                 timeout=15, device=device)
    if result.returncode != 0:
        return []
    return parse_action_nodes(result.stdout.decode("utf-8", "ignore"))


def parse_close_nodes(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    found = []
    for node in root.iter("node"):
        label = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
        if not any(word in label for word in CLOSE_TEXTS):
            continue
        bounds = BOUNDS_RE.fullmatch(node.attrib.get("bounds", ""))
        if bounds:
            x1, y1, x2, y2 = map(int, bounds.groups())
            found.append((label, (x1 + x2) // 2, (y1 + y2) // 2))
    return found


def read_ui_xml(device):
    # This is a fallback for unknown controls, never the visual fast path.
    # Some Unity screens cannot be dumped; do not block screen polling for 30s.
    try:
        dumped = adb(["shell", "uiautomator", "dump", "/sdcard/jj_window.xml"],
                     timeout=2, device=device)
        if dumped.returncode != 0:
            return ""
        result = adb(["exec-out", "cat", "/sdcard/jj_window.xml"],
                     timeout=1, device=device)
    except subprocess.TimeoutExpired:
        return ""
    return ("" if result.returncode != 0 else
            result.stdout.decode("utf-8", "ignore"))


def dismiss_rank_promotion(device):
    """Leave JJ's full-screen rank animation only when its text is explicit.

    Rank artwork changes colour between tiers, so this deliberately does not
    use colour matching.  Android BACK is sent only after the accessibility
    tree contains a rank-promotion label; unrelated non-board pages are left
    untouched.
    """
    xml_text = read_ui_xml(device)
    if not any(text in xml_text for text in PROMOTION_TEXTS):
        return False
    adb(["shell", "input", "keyevent", "4"], device=device)
    time.sleep(0.6)
    return True


def dismiss_nonboard_interruption(device, image, orientation="auto"):
    """Close only an explicitly identified full-screen rank-promotion page.

    Failed recognition is not evidence that BACK is safe: JJ's resignation
    animation and confirmation modal can temporarily hide the whole board.
    Leave unknown/loading frames alone and let their result controls appear.
    """
    if image is None or has_actionable_postgame(image):
        return False
    try:
        from recognizer import recognize_position
        squares, detected = recognize_position(image, orientation)
    except Exception:
        return False
    pieces = (0 if squares is None else
              sum(piece is not None for piece in squares))
    if detected in ("red_bottom", "black_bottom") and pieces >= 4:
        return False
    return dismiss_rank_promotion(device)


def detect_large_action_button(image):
    """Detect JJ's large green result-page button in the lower screen area."""
    if image is None:
        return None
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # The safe replay/continue control is green.  Do not include orange/gold:
    # reward-video buttons and the in-game countdown glow use those colors.
    mask = cv2.inRange(hsv, (35, 75, 90), (100, 255, 255))
    mask[:int(height * 0.58), :] = 0
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (19, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    candidates = []
    for contour in cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)[0]:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if (w >= width * 0.22 and 45 <= h <= height * 0.14 and
                2.0 <= w / max(h, 1) <= 9.0 and area >= w * h * 0.35):
            candidates.append((area, x + w // 2, y + h // 2))
    if not candidates:
        return None
    # Reward popups may contain a large gold "领取" button above the real
    # bottom-page action.  Prefer the lowest trustworthy horizontal button.
    _, x, y = max(candidates, key=lambda item: (item[2], item[0]))
    return x, y


def detect_confirmation_button(image):
    """Detect the green confirmation button on JJ's central result modal."""
    if image is None:
        return None
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (35, 75, 80), (100, 255, 255))
    # The modal confirmation is around the vertical center.  Exclude the lower
    # replay button so it cannot be clicked through an open modal.
    mask[:int(height * 0.42), :] = 0
    mask[int(height * 0.66):, :] = 0
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    candidates = []
    for contour in cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)[0]:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if (w >= width * 0.18 and 45 <= h <= height * 0.11 and
                1.8 <= w / max(h, 1) <= 6.0 and area >= w * h * 0.35):
            candidates.append((area, x + w // 2, y + h // 2))
    if not candidates:
        return None
    _, x, y = max(candidates)
    return x, y


def detect_match_mode_button(image, mode="15分钟"):
    """Detect the four-button mode layout without relying on theme colours."""
    if image is None or mode not in MATCH_MODE_X:
        return None
    height, width = image.shape[:2]
    if height < 800 or width < 450:
        return None
    if detect_rules_overlay_dismiss(image):
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    vertical_gradient = np.abs(cv2.Sobel(
        gray, cv2.CV_32F, 0, 1, ksize=3))
    row_strength = np.mean(
        vertical_gradient[:, int(width * 0.03):int(width * 0.97)], axis=1)
    upper_edge = float(np.max(row_strength[
        int(height * 0.69):int(height * 0.76)]))
    lower_edge = float(np.max(row_strength[
        int(height * 0.79):int(height * 0.84)]))
    if upper_edge < 75 or lower_edge < 75:
        return None
    # Each of the four expected controls must have its own boundary/text
    # structure.  This prevents a single wide banner or one ordinary button
    # from being treated as the mode row.
    for center in MATCH_MODE_X.values():
        x1, x2 = int(width * (center - 0.10)), int(width * (center + 0.10))
        y1, y2 = int(height * 0.70), int(height * 0.84)
        roi_gray = gray[y1:y2, x1:x2]
        roi_edges = edges[y1:y2, x1:x2]
        if (float(np.std(roi_gray)) < 15 or
                float(np.mean(roi_edges > 0)) < 0.009):
            return None
    if not np.isfinite(upper_edge + lower_edge):
        return None
    return int(width * MATCH_MODE_X[mode]), int(height * 0.77)


def detect_rules_overlay_dismiss(image):
    """Return a safe backdrop point for JJ's no-X promotion-rules overlay.

    This intentionally requires the four underlying match controls plus the
    characteristic full central dimming.  It must never become a generic
    "click blank on every unknown screen" fallback.
    """
    if image is None:
        return None
    height, width = image.shape[:2]
    if height < 800 or width < 450:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    card_area = gray[int(height * 0.27):int(height * 0.68),
                     int(width * 0.08):int(width * 0.92)]
    # The rules sheet dims the whole board behind its card.  The live board
    # has the same long horizontal grid lines, but this central area remains
    # much brighter; requiring the dim layer prevents repeated backdrop taps
    # during an actual timed game.
    if card_area.size == 0 or float(np.mean(card_area)) > 120.0:
        return None
    vertical_gradient = np.abs(cv2.Sobel(
        gray, cv2.CV_32F, 0, 1, ksize=3))
    row_strength = np.mean(
        vertical_gradient[:, int(width * 0.03):int(width * 0.97)], axis=1)
    top_rule = float(np.max(row_strength[
        int(height * 0.23):int(height * 0.27)]))
    bottom_rule = float(np.max(row_strength[
        int(height * 0.68):int(height * 0.72)]))
    # The rules card has two almost full-width horizontal borders at stable
    # relative heights.  Their edge strength is independent of rank colours,
    # season artwork and the background theme.
    if top_rule >= 170 and bottom_rule >= 170:
        return int(width * 0.055), int(height * 0.135)
    return None


def detect_modal_close(image):
    """Detect the bright X in JJ's right-side reward/modal close circle."""
    if image is None:
        return None
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    central = gray[int(height * 0.12):int(height * 0.88),
                   int(width * 0.05):int(width * 0.95)]
    # 真正的奖励弹窗会给整个棋盘加明显暗色遮罩。正常棋盘上的交叉线、
    # 棋子高亮和选中标记即使形成 X，也绝不能被当成关闭按钮。
    if central.size == 0 or float(np.mean(central)) > 100.0:
        return None
    cx, cy = int(width * 0.885), int(height * 0.34)
    radius = max(40, int(width * 0.052))
    roi = image[cy-radius:cy+radius, cx-radius:cx+radius]
    roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    if roi_gray.size == 0 or float(np.mean(roi_gray > 170)) < 0.01:
        return None
    edges = cv2.Canny(roi_gray, 80, 180)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=18,
                            minLineLength=18, maxLineGap=8)
    if lines is None:
        return None
    positive = negative = False
    center = radius
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        midpoint_near_center = (
            abs((x1 + x2) / 2 - center) < radius * 0.55 and
            abs((y1 + y2) / 2 - center) < radius * 0.55)
        if not midpoint_near_center:
            continue
        positive |= 28 <= angle <= 62
        negative |= -62 <= angle <= -28
    return (cx, cy) if positive and negative else None


def postgame_action(image):
    """Return one visual action, with modal controls ahead of replay."""
    for label, detect in (("关闭奖励弹窗", detect_modal_close),
                          ("结算确认", detect_confirmation_button),
                          ("结算页主按钮", detect_large_action_button),
                          ("关闭段位提升", detect_rank_promotion_dismiss)):
        point = detect(image)
        if point:
            return label, *point
    return None


def has_actionable_postgame(image):
    """Return whether a known, safe JJ postgame control is visible.

    Keep this gate in the postgame module so the continuous-game watcher and
    the actual click handler cannot drift apart when JJ introduces another
    result-page layer.
    """
    return postgame_action(image) is not None


def same_action(first, second):
    return bool(first and second and first[0] == second[0] and
                abs(first[1] - second[1]) <= 16 and
                abs(first[2] - second[2]) <= 16)


def screen_difference(first, second):
    if first is None or second is None:
        return float("inf")
    frames = [cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 284))
              for frame in (first, second)]
    return float(np.mean(cv2.absdiff(*frames)))


def wait_for_screen_change(device, before, timeout=4.0, *, continue_allowed=None):
    if before is None:
        time.sleep(0.12)
        return True
    action = postgame_action(before)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if continue_allowed is not None and not continue_allowed():
            return False
        time.sleep(0.12)
        after = screenshot(device)
        if after is None:
            continue
        if action:
            # Confetti/countdowns move while the same button remains visible.
            # Only a control transition acknowledges that our tap took effect.
            if not same_action(action, postgame_action(after)):
                return True
        elif screen_difference(before, after) > 5.0:
            return True
    return False


def handle_postgame(device, timeout=20.0, max_actions=8, *, continue_allowed=None):
    """Click result-page continuation controls; return labels that were clicked."""
    deadline = time.monotonic() + timeout
    actions = []
    last_xml = float("-inf")
    while time.monotonic() < deadline and len(actions) < max_actions:
        if continue_allowed is not None and not continue_allowed():
            break
        before = screenshot(device)
        action = postgame_action(before)
        if action:
            # Confirm the actual control, not just any result-looking frame.
            time.sleep(0.08)
            confirmed = screenshot(device)
            if not same_action(action, postgame_action(confirmed)):
                continue
            before = confirmed
        else:
            if before is None:
                time.sleep(0.12)
                continue
            from recognizer import recognize_position
            squares, orientation = recognize_position(before, "auto")
            board_visible = (orientation in ("red_bottom", "black_bottom") and
                             squares is not None and
                             sum(piece is not None for piece in squares) >= 4)
            if board_visible:
                if actions and actions[-1] != "关闭段位提升":
                    break
                # Closing the rank page can expose the just-finished board
                # before its replay control appears; it is not a new game.
                time.sleep(0.12)
                continue
            if detect_match_mode_button(before) or detect_rules_overlay_dismiss(before):
                break  # Let the watcher choose the configured match mode now.
            if time.monotonic() - last_xml < 3.0:
                time.sleep(0.12)
                continue
            xml_text = read_ui_xml(device)
            last_xml = time.monotonic()
            nodes = parse_close_nodes(xml_text) or parse_action_nodes(xml_text)
            if not nodes:
                time.sleep(0.12)
                continue
            # A slow dump must not apply stale coordinates to a newly started
            # game.  Re-enter visual polling whenever the scene has changed.
            current = screenshot(device)
            if screen_difference(before, current) > 3.0:
                continue
            action = nodes[0]
            before = current
        label, x, y = action
        # Recheck after screenshots/XML, immediately before submitting input.
        # The desktop can turn off auto-continue while any of them is running.
        if continue_allowed is not None and not continue_allowed():
            break
        tap(x, y, device=device)
        actions.append(label)
        if not wait_for_screen_change(device, before, continue_allowed=continue_allowed):
            break
        if label == "结算页主按钮" or any(word in label for word in ACTION_TEXTS[:4]):
            # Matching/loading belongs to the fast board watcher.  Do not hold
            # it behind the remaining 20s postgame budget or another XML dump.
            break
    return actions
