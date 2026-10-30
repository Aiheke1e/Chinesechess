from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET

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
    adb(["shell", "uiautomator", "dump", "/sdcard/jj_window.xml"],
        timeout=15, device=device)
    result = adb(["exec-out", "cat", "/sdcard/jj_window.xml"],
                 timeout=15, device=device)
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


def has_actionable_postgame(image):
    """Return whether a known, safe JJ postgame control is visible.

    Keep this gate in the postgame module so the continuous-game watcher and
    the actual click handler cannot drift apart when JJ introduces another
    result-page layer.
    """
    return bool(detect_modal_close(image) or
                detect_confirmation_button(image) or
                detect_large_action_button(image))


def wait_for_screen_change(device, before, timeout=4.0):
    if before is None:
        time.sleep(1.0)
        return True
    baseline = cv2.resize(cv2.cvtColor(before, cv2.COLOR_BGR2GRAY), (160, 284))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        after = screenshot(device)
        if after is None:
            continue
        current = cv2.resize(cv2.cvtColor(after, cv2.COLOR_BGR2GRAY), (160, 284))
        if float(np.mean(cv2.absdiff(baseline, current))) > 5.0:
            return True
    return False


def handle_postgame(device, timeout=20.0, max_actions=8):
    """Click result-page continuation controls; return labels that were clicked."""
    deadline = time.monotonic() + timeout
    actions = []
    while time.monotonic() < deadline and len(actions) < max_actions:
        before = screenshot(device)
        xml_text = read_ui_xml(device)
        close_nodes = parse_close_nodes(xml_text)
        close_point = (None if close_nodes else detect_modal_close(before))
        if close_nodes or close_point:
            if close_nodes:
                label, x, y = close_nodes[0]
            else:
                label, (x, y) = "关闭奖励弹窗", close_point
            tap(x, y, device=device)
            actions.append(label)
            if not wait_for_screen_change(device, before):
                break
            time.sleep(0.6)
            continue
        confirm_point = detect_confirmation_button(before)
        if confirm_point:
            label, (x, y) = "结算确认", confirm_point
            tap(x, y, device=device)
            actions.append(label)
            if not wait_for_screen_change(device, before):
                break
            time.sleep(0.6)
            continue
        nodes = parse_action_nodes(xml_text)
        if nodes:
            label, x, y = nodes[0]
        else:
            point = detect_large_action_button(before)
            if point is None:
                # The result control has disappeared and a real board is now
                # visible: return immediately so the watcher can take over
                # before the opponent moves.
                if actions and before is not None:
                    from recognizer import recognize_position
                    squares, orientation = recognize_position(before, "auto")
                    if (orientation in ("red_bottom", "black_bottom") and
                            squares is not None and
                            sum(piece is not None for piece in squares) >= 4):
                        break
                time.sleep(0.75)
                continue
            label, (x, y) = "结算页主按钮", point
        tap(x, y, device=device)
        actions.append(label)
        if not wait_for_screen_change(device, before):
            break
        time.sleep(0.6)
    return actions
