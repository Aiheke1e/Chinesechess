# calibrate.py —— 采集 JJ 象棋真实窗口的棋盘几何 + 生成棋子模板
# 用法:
#   1) 打开 JJ 象棋棋盘界面, 确保窗口可见
#   2) python calibrate.py           # 自动检测几何并写到 config.json
#   3) python calibrate.py --templates  # 在自动几何下截图, 手动确认交叉点后生成模板
import os
import json
import argparse
import numpy as np
import cv2

from vision import find_window, grab_window, detect_geometry, BoardGeometry, extract_square

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")


def save_geom(geom: BoardGeometry, title):
    # 合并进 config.json
    cfg = {}
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["title"] = title
    cfg["geom"] = {"ox": geom.ox, "oy": geom.oy, "dx": geom.dx,
                   "dy": geom.dy, "w": geom.w, "h": geom.h}
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"[calibrate] 几何已写入 {CONFIG}: {cfg['geom']}")


def auto_calibrate(title):
    hwnd, wtitle = find_window(title)
    if not hwnd:
        print(f"[calibrate] 未找到包含 '{title}' 的窗口")
        return None
    grabbed = grab_window(hwnd)
    if grabbed is None:
        return None
    img, wxy = grabbed
    geom = detect_geometry(img)
    if geom is None:
        print("[calibrate] 自动检测失败, 请改用手动模式或调整亮度")
        return None
    geom.title = wtitle
    save_geom(geom, wtitle)
    # 可视化: 画网格交点
    vis = img.copy()
    for idx in range(90):
        px, py = geom.square_center(idx, (0, 0))
        cv2.circle(vis, (px, py), 3, (0, 0, 255), -1)
    cv2.imwrite(os.path.join(HERE, "calib_visual.png"), vis)
    print(f"[calibrate] 已保存网格可视化 calib_visual.png, 请核对交点是否对齐棋子中心")
    return geom


def manual_calibrate(title):
    """鼠标点 4 个角交叉点 (左上/右上/左下/右下) 推算几何。"""
    hwnd, wtitle = find_window(title)
    if not hwnd:
        print("窗口未找到")
        return None
    grabbed = grab_window(hwnd)
    if grabbed is None:
        return None
    img, wxy = grabbed
    pts = []
    print("请在图像窗口中用左键依次点击: 左上、右上、左下、右下 四个棋盘角交叉点")
    cv2.namedWindow("click")
    clone = img.copy()

    def on_click(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN and len(pts) < 4:
            pts.append((x, y))
            cv2.circle(clone, (x, y), 5, (0, 255, 0), -1)
            cv2.imshow("click", clone)

    cv2.setMouseCallback("click", on_click)
    cv2.imshow("click", clone)
    while len(pts) < 4:
        cv2.waitKey(50)
    cv2.destroyAllWindows()
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
    ox = x0
    oy = y0
    dx = (x1 - x0) / 8
    dy = (y2 - y0) / 9
    geom = BoardGeometry(ox, oy, dx, dy, img.shape[1], img.shape[0])
    geom.title = wtitle
    save_geom(geom, wtitle)
    return geom


def make_templates(title, geom, out_dir):
    """抓当前棋盘, 按几何切 90 个交叉点, 让你逐个确认并命名 -> 生成模板。"""
    hwnd, _ = find_window(title)
    grabbed = grab_window(hwnd)
    if grabbed is None:
        return
    img, wxy = grabbed
    os.makedirs(out_dir, exist_ok=True)
    print("逐个交叉点: 按对应棋子键 (K帅 A仕 B相 N马 R车 C炮 P兵 / k将 a士 b象 n马 r车 c炮 p卒), 空格=空, q=结束")
    for idx in range(90):
        tile = extract_square(img, geom, idx, wxy)
        cv2.imshow("tile", tile)
        key = cv2.waitKey(0) & 0xFF
        if key == ord('q'):
            break
        if key == ord(' '):
            continue
        ch = chr(key)
        if ch in "KA NRCBPkanrcbp":
            cv2.imwrite(os.path.join(out_dir, f"{ch}_{idx}.png"), tile)
    cv2.destroyAllWindows()
    print(f"[calibrate] 模板保存到 {out_dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="JJ象棋")
    ap.add_argument("--manual", action="store_true", help="手动点角标定几何")
    ap.add_argument("--templates", action="store_true", help="生成棋子模板")
    args = ap.parse_args()

    if args.templates:
        cfg = {}
        if os.path.exists(CONFIG):
            with open(CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
        g = cfg.get("geom", {"ox": 96, "oy": 132, "dx": 66, "dy": 60})
        geom = BoardGeometry(g["ox"], g["oy"], g["dx"], g["dy"], g["w"], g["h"], args.title)
        make_templates(args.title, geom, os.path.join(HERE, "templates"))
    else:
        if args.manual:
            manual_calibrate(args.title)
        else:
            auto_calibrate(args.title)
