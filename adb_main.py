# adb_main.py —— 安卓 JJ 象棋 AI 代打主控 (ADB 通道)
# 流程: adb 截图 -> 识别棋盘 -> AI 选招 -> adb swipe 落子 -> 循环
# 依赖: board.py / engine.py / adb_bridge.py / vision.py
from __future__ import annotations

import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2
import numpy as np

from board import Board, RED, BLACK, CN_TO_CODE, color_of
from engine import choose_move
from adb_bridge import adb, screenshot, tap, swipe, list_devices, resolution

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "adb_config.json"), encoding="utf-8") as _config_file:
    CFG = json.load(_config_file)


def find_board_region(img):
    """从整屏截图定位棋盘区域。
    安卓竖屏棋盘通常占据屏幕中下部; 通过检测木质背景色区域估计。"""
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # 木色范围 (hue 10-30, sat 60-200, val 100-255)
    lower = np.array([8, 40, 90])
    upper = np.array([30, 220, 255])
    mask = cv2.inRange(hsv, lower, upper)
    # 找最大连通区域
    kernel = np.ones((15, 15), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < w * h * 0.05:
        return None
    x, y, bw, bh = cv2.boundingRect(c)
    return x, y, bw, bh


def estimate_grid(x, y, bw, bh):
    """棋盘区域 -> 90 交叉点像素坐标。
    象棋棋盘 9 列 10 行, 交叉点等距; 边缘留 pad 避开边框。"""
    pad_x = bw * 0.02
    pad_y = bh * 0.02
    dx = (bw - 2 * pad_x) / 8
    dy = (bh - 2 * pad_y) / 9
    pts = []
    for r in range(10):
        for c in range(9):
            px = x + pad_x + c * dx
            py = y + pad_y + r * dy
            pts.append((px, py))
    return pts, dx, dy


def classify_tile(tile, templates, threshold=0.55):
    """模板匹配单个交叉点 -> (code, conf)。templates: {code: [img,...]}"""
    best = (None, -1)
    if tile.size == 0:
        return best
    g = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (48, 48))
    for code, timgs in templates.items():
        for t in timgs:
            res = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
            v = float(np.max(res))
            if v > best[1]:
                best = (code, v)
    if best[1] < threshold:
        return (None, best[1])
    return best


def load_templates(tdir):
    templates = {}
    for f in os.listdir(tdir):
        if not f.lower().endswith((".png", ".jpg")):
            continue
        code = f.split("_")[0]
        img = cv2.imread(os.path.join(tdir, f), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            templates.setdefault(code, []).append(img)
    return templates


def read_board_from_screen(img, templates):
    """整屏 -> (Board, grid_pts, region)。空点返回 None。"""
    region = find_board_region(img)
    if region is None:
        return None, None, None
    x, y, bw, bh = region
    pts, dx, dy = estimate_grid(x, y, bw, bh)
    b = Board()
    half = int(min(dx, dy) * 0.42)
    for idx, (px, py) in enumerate(pts):
        tile = img[int(py) - half:int(py) + half, int(px) - half:int(px) + half]
        code, conf = classify_tile(tile, templates)
        if code:
            b.sq[idx] = code
    return b, pts, region


def calibrate_capture(device, out_dir, n=1):
    """采集 n 张棋盘截图供人工确认几何/模板"""
    os.makedirs(out_dir, exist_ok=True)
    for i in range(n):
        img = screenshot(device)
        if img is None:
            print("截图失败")
            return
        p = os.path.join(out_dir, f"calib_{i:03d}.png")
        cv2.imwrite(p, img)
        print(f"[calib] 已保存 {p}")
        if i < n - 1:
            time.sleep(2)


def main_loop():
    dev = list_devices()
    if not dev:
        print("[main] 未检测到安卓设备/模拟器")
        return
    device = dev[0]
    print(f"[main] 设备: {device} 分辨率: {resolution(device)}")

    tdir = os.path.join(HERE, "templates")
    templates = load_templates(tdir) if os.path.isdir(tdir) else {}
    if not templates:
        print("[main] 警告: templates/ 为空, 识别会失败。先跑 calibrate.py --templates")

    my_color = CFG.get("my_color", "r")
    interval = CFG.get("move_interval", 1.5)
    last_fen = None
    n = 0
    max_moves = CFG.get("max_moves", 200)

    while n < max_moves:
        img = screenshot(device)
        if img is None:
            print("[main] 截图失败, 重试")
            time.sleep(2)
            continue
        b, pts, region = read_board_from_screen(img, templates)
        if b is None:
            print("[main] 未检测到棋盘, 等待...")
            time.sleep(2)
            continue
        # 统计已识别棋子数量, 过少则认为识别失败
        cnt = sum(1 for p in b.sq if p)
        if cnt < 10:
            print(f"[main] 识别棋子过少({cnt}/32), 可能不在对局界面")
            time.sleep(3)
            continue
        if b.turn != my_color:
            time.sleep(interval)
            continue
        fen = b.to_fen()
        if fen == last_fen:
            time.sleep(0.6)
            continue
        print(f"[main] 局面({cnt}子): {fen}")
        mv, score = choose_move(b, depth=CFG.get("think_depth", 4),
                                mode=CFG.get("engine_mode", "pikafish"),
                                engine_path=CFG.get("engine_path", "pikafish.exe"),
                                my_color=my_color)
        if not mv:
            print("[main] 无合法走法, 结束")
            break
        fxy = pts[mv[0]]
        txy = pts[mv[1]]
        print(f"[main] 走 {Board.ucci(mv)} score={score}  {fxy}->{txy}")
        swipe(fxy[0], fxy[1], txy[0], txy[1], duration_ms=CFG.get("swipe_ms", 150), device=device)
        last_fen = b.to_fen()
        n += 1
        time.sleep(interval)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", action="store_true", help="采集标定截图")
    ap.add_argument("--n", type=int, default=1)
    args = ap.parse_args()
    if args.calib:
        d = list_devices()
        if d:
            calibrate_capture(d[0], os.path.join(HERE, "calib"), args.n)
    else:
        main_loop()
