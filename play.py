# play.py —— 自动落子 (鼠标拖拽) + 主控循环
import time
import ctypes
import numpy as np
import cv2

try:
    import pyautogui
    pyautogui.PAUSE = 0.0
    HAVE_PA = True
except Exception:
    HAVE_PA = False

from vision import find_window, grab_window, BoardGeometry
from board import Board, RED, BLACK
from engine import choose_move


def click_drag(from_xy, to_xy, hold=0.08):
    """人类化拖拽: 先移动到起点, 按下, 微抖, 移动, 松开。"""
    fx, fy = from_xy
    tx, ty = to_xy
    if HAVE_PA:
        pyautogui.moveTo(fx, fy, duration=0.06)
        pyautogui.mouseDown()
        time.sleep(hold)
        # 分几步移动, 避免直线被检测
        for i in range(1, 6):
            k = i / 5
            pyautogui.moveTo(fx + (tx - fx) * k + np.random.randint(-2, 3),
                             fy + (ty - fy) * k + np.random.randint(-2, 3),
                             duration=0.02)
        time.sleep(hold)
        pyautogui.mouseUp()
    else:
        # 直接用 ctypes 发送输入
        _ctypes_drag(fx, fy, tx, ty)


def _ctypes_drag(fx, fy, tx, ty):
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(fx), int(fy))
    _down = ctypes.wintypes.DWORD(0x0002)
    _up = ctypes.wintypes.DWORD(0x0004)
    _input = ctypes.wintypes.INPUT * 3
    # 简化: 按下/移动到/松开
    # (为简洁直接用 pyautogui 路径; 无 pyautogui 时也可用 SendInput, 略)


def detect_turn_change(prev_fen, cur_board: Board, my_color):
    """判断当前是否轮到我走。基于: 当前局面 turn 字段 + 我方颜色。"""
    return cur_board.turn == my_color


def read_board_live(geom: BoardGeometry, templates_dir, title):
    """抓窗口 -> 识别 -> 返回 (Board, window_xy, img)"""
    hwnd, _ = find_window(title)
    if not hwnd:
        return None, None, None
    grabbed = grab_window(hwnd)
    if grabbed is None:
        return None, None, None
    img, window_xy = grabbed
    b = Board()  # 占位; 用模板或检测器填充
    if templates_dir and os.path.isdir(templates_dir):
        from vision import board_from_templates
        b = board_from_templates(img, geom, window_xy, templates_dir)
    return b, window_xy, img


def main_loop(cfg):
    """cfg: dict with keys:
        title, geom, templates_dir, my_color, engine_mode, engine_path,
        think_depth, move_interval, max_moves, use_detector
    """
    geom: BoardGeometry = cfg["geom"]
    my_color = cfg.get("my_color", RED)
    n = 0
    last_fen = None
    while n < cfg.get("max_moves", 200):
        b, wxy, img = read_board_live(geom, cfg.get("templates_dir"), cfg["title"])
        if b is None:
            print("[loop] 未找到窗口, 重试...")
            time.sleep(1.0)
            continue
        if not detect_turn_change(last_fen, b, my_color):
            print("[loop] 还没轮到我, 等待...")
            time.sleep(cfg.get("move_interval", 1.5))
            continue
        fen = b.to_fen()
        if fen == last_fen:
            time.sleep(0.5)
            continue
        print(f"[loop] 识别局面 FEN: {fen}")
        mv, score = choose_move(b, depth=cfg.get("think_depth", 4),
                                mode=cfg.get("engine_mode", "pikafish"),
                                engine_path=cfg.get("engine_path", "pikafish.exe"),
                                my_color=my_color)
        if not mv:
            print("[loop] 无合法走法, 认输/和棋")
            break
        frm, to = mv
        fxy = geom.square_center(frm, wxy)
        txy = geom.square_center(to, wxy)
        print(f"[loop] 走子 {Board.ucci(mv)}  评分 {score}  -> 拖拽 {fxy}->{txy}")
        click_drag(fxy, txy)
        last_fen = b.to_fen()
        n += 1
        time.sleep(cfg.get("move_interval", 1.5))


if __name__ == "__main__":
    # 演示: 用 fixture 几何跑一个回合 (不真正抓屏)
    print("run 'python main.py' for full loop; use calibrate.py to capture geometry")
