# main.py —— JJ 象棋 AI 外挂主控入口
# 流程: 找窗口 -> 定时抓屏 -> 识别局面 -> AI 选招 -> 自动落子 -> 循环
import os
import time
import json

from vision import find_window, grab_window, BoardGeometry, board_from_templates, detect_geometry
from board import Board, RED, BLACK
from engine import choose_move
from play import click_drag, _ctypes_drag

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "title": "JJ象棋",            # 窗口标题包含的关键字
    "my_color": "r",              # "r"=红先, "b"=黑
    "engine_mode": "pikafish",    # 仅使用 Pikafish
    "engine_path": "engine_bin/Pikafish-Windows-x86-64-universal.exe",
    "think_depth": 6,             # 旧入口传给 Pikafish 的搜索深度
    "move_interval": 1.2,         # 每步间隔秒
    "max_moves": 200,
    "templates_dir": "templates", # 零依赖识别用模板目录
    "use_detector": False,        # True 用 YOLO 检测器
    "detector_weights": "best.pt",
    "geom": {"ox": 96, "oy": 132, "dx": 66, "dy": 60, "w": 720, "h": 804},
    "auto_geom": False            # True 时自动检测几何(需直线清晰)
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        DEFAULT_CONFIG.update(cfg)
    return DEFAULT_CONFIG


def make_geom(cfg):
    g = cfg["geom"]
    return BoardGeometry(g["ox"], g["oy"], g["dx"], g["dy"], g["w"], g["h"], cfg["title"])


def live_read(geom, cfg):
    hwnd, title = find_window(cfg["title"])
    if not hwnd:
        return None, None, None
    grabbed = grab_window(hwnd)
    if grabbed is None:
        return None, None, None
    img, wxy = grabbed
    if cfg.get("auto_geom"):
        g2 = detect_geometry(img)
        if g2:
            geom = g2
    b = Board()
    if cfg.get("use_detector"):
        from vision import YOLODetector
        det = YOLODetector(cfg["detector_weights"])
        b = det.read_board(img, geom, wxy)
    elif os.path.isdir(cfg["templates_dir"]):
        b = board_from_templates(img, geom, wxy, cfg["templates_dir"])
    return b, wxy, img


def run():
    cfg = load_config()
    geom = make_geom(cfg)
    my_color = cfg["my_color"]
    print(f"[main] 标题={cfg['title']} 我的颜色={'红' if my_color==RED else '黑'} "
          f"引擎={cfg['engine_mode']}")
    last_fen = None
    n = 0
    while n < cfg["max_moves"]:
        b, wxy, img = live_read(geom, cfg)
        if b is None:
            print("[main] 未找到窗口, 2s 后重试")
            time.sleep(2)
            continue
        if b.turn != my_color:
            time.sleep(cfg["move_interval"])
            continue
        fen = b.to_fen()
        if fen == last_fen:
            time.sleep(0.4)
            continue
        print(f"[main] 局面: {fen}")
        mv, score = choose_move(b, depth=cfg["think_depth"], mode=cfg["engine_mode"],
                                engine_path=cfg["engine_path"], my_color=my_color)
        if not mv:
            print("[main] 无合法走法, 结束")
            break
        fxy = geom.square_center(mv[0], wxy)
        txy = geom.square_center(mv[1], wxy)
        print(f"[main] 走 {Board.ucci(mv)} 评分 {score} 拖拽 {fxy}->{txy}")
        click_drag(fxy, txy)
        last_fen = b.to_fen()
        n += 1
        time.sleep(cfg["move_interval"])


if __name__ == "__main__":
    run()
