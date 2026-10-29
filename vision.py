# vision.py —— 屏幕捕获 + 棋盘定位 + 棋子识别
# 零依赖方案: 模板匹配 (需要 templates/ 下的棋子截图)
# 深度学习方案: 提供 build_detector() 接入 YOLO/CRNN
import os
import time
import ctypes
import numpy as np
import cv2
from PIL import Image

try:
    import pyautogui
    HAVE_PA = True
except Exception:
    HAVE_PA = False

# --- Win32 截图 (无需 pyautogui, 直接用 ctypes 抓窗口 DC) ---
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


def find_window(title_substr):
    """返回 (hwnd, title) 或 (0, '')"""
    result = {"hwnd": 0, "title": ""}

    def enum_cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if title_substr.lower() in title.lower():
            result["hwnd"] = hwnd
            result["title"] = title
            return False
        return True

    EnumWindows = user32.EnumWindows
    EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int), ctypes.c_int]
    EnumWindows.restype = ctypes.c_bool
    EnumWindows(enum_cb, 0)
    return result["hwnd"], result["title"]


def grab_window(hwnd):
    """抓取整窗口位图, 返回 BGR numpy 数组; 失败返回 None"""
    if not hwnd:
        return None
    left, top, right, bottom = ctypes.c_long(), ctypes.c_long(), ctypes.c_long(), ctypes.c_long()
    user32.GetWindowRect(hwnd, ctypes.byref(left))
    user32.GetWindowRect(hwnd, ctypes.byref(top))
    user32.GetWindowRect(hwnd, ctypes.byref(right))
    user32.GetWindowRect(hwnd, ctypes.byref(bottom))
    x, y = left.value, top.value
    w = right.value - left.value
    h = bottom.value - top.value
    if w <= 0 or h <= 0:
        return None
    hwndc = user32.GetWindowDC(hwnd)
    if not hwndc:
        return None
    hdcMem = gdi32.CreateCompatibleDC(hwndc)
    hbmp = gdi32.CreateCompatibleBitmap(hwndc, w, h)
    gdi32.SelectObject(hdcMem, hbmp)
    gdi32.BitBlt(hdcMem, 0, 0, w, h, hwndc, 0, 0, 0x00CC0020)
    bmp = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetBitmapBits(hbmp, w * h * 4, bmp)
    arr = np.frombuffer(bmp, dtype=np.uint8).reshape(h, w, 4)
    img = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdcMem)
    user32.ReleaseDC(hwnd, hwndc)
    return img, (x, y, w, h)


# ---------------------------------------------------------------- 棋盘几何配置
# 这些坐标需按真实 JJ 象棋客户端标定 (用 calibrate.py 采集)
class BoardGeometry:
    def __init__(self, origin_x=96, origin_y=132, dx=66, dy=60, w=720, h=804,
                 title="JJ象棋"):
        self.ox = origin_x   # 左上交叉点相对窗口的像素偏移
        self.oy = origin_y
        self.dx = dx         # 列间距
        self.dy = dy         # 行间距
        self.w = w
        self.h = h
        self.title = title

    def square_center(self, idx, window_xy):
        r, c = divmod(idx, 9)
        wx, wy = window_xy
        px = wx + self.ox + c * self.dx
        py = wy + self.oy + r * self.dy
        return int(px), int(py)

    def all_centers(self, window_xy):
        return [self.square_center(i, window_xy) for i in range(90)]


def detect_geometry(img):
    """从棋盘线自动估计几何: 在 img 中找木色背景上的横线交点网格。
    返回 BoardGeometry 或 None。fixture/真实图通用启发式。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 用霍夫线检测横线, 取最密区间
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 80, minLineLength=img.shape[1] * 0.5,
                            maxLineGap=10)
    if lines is None:
        return None
    ys = sorted({int(np.mean(l[0][1::2])) for l in lines if abs(l[0][1] - l[0][3]) < 5})
    if len(ys) < 10:
        return None
    xs = sorted({int(np.mean(l[0][0::2])) for l in lines if abs(l[0][0] - l[0][2]) < 5})
    if len(xs) < 9:
        return None
    ox = xs[0]
    oy = ys[0]
    dx = (xs[-1] - xs[0]) / 8
    dy = (ys[-1] - ys[0]) / 9
    return BoardGeometry(ox, oy, dx, dy, img.shape[1], img.shape[0])


# ---------------------------------------------------------------- 棋子识别
def extract_square(img, geom, idx, window_xy, pad=4):
    px, py = geom.square_center(idx, window_xy)
    x0, y0 = px - geom.dx // 2 + pad, py - geom.dy // 2 + pad
    x1, y1 = px + geom.dx // 2 - pad, py + geom.dy // 2 - pad
    return img[y0:y1, x0:x1]


def classify_square_templates(tile, templates_dir):
    """对单个交叉点小图, 与模板比对, 返回 (code_or_None, confidence)。"""
    best = (None, -1.0)
    gray = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
    for fname in os.listdir(templates_dir):
        if not fname.lower().endswith((".png", ".jpg")):
            continue
        code = fname.split("_")[0]
        tmpl = cv2.imread(os.path.join(templates_dir, fname), cv2.IMREAD_GRAYSCALE)
        if tmpl is None or tmpl.size == 0:
            continue
        g = cv2.resize(gray, (tmpl.shape[1], tmpl.shape[0]))
        res = cv2.matchTemplate(g, tmpl, cv2.TM_CCOEFF_NORMED)
        v = float(np.max(res))
        if v > best[1]:
            best = (code, v)
    if best[1] < 0.6:
        return (None, best[1])
    return best


def board_from_templates(img, geom, window_xy, templates_dir):
    from board import Board
    b = Board()
    for idx in range(90):
        tile = extract_square(img, geom, idx, window_xy)
        code, conf = classify_square_templates(tile, templates_dir)
        if code:
            b.sq[idx] = code
    return b


# ---------------------------------------------------------------- 深度学习检测器 (可选)
class YOLODetector:
    """占位: 接入 ultralytics YOLOv8 检测棋子 + 裁剪后 OCR 认字。
    训练数据: 标注 JJ 棋盘的棋子 bbox + 类别(14 类)。

    用法:
        det = YOLODetector("best.pt")
        b = det.read_board(img, geom, window_xy)
    """

    def __init__(self, weights):
        from ultralytics import YOLO
        self.model = YOLO(weights)

    def read_board(self, img, geom, window_xy):
        from board import Board
        results = self.model(img, verbose=False)
        b = Board()
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                cls = int(box.cls[0])
                # cls -> 棋子编码映射需在训练时定义
                code = self.class_map.get(cls)
                if code:
                    cx = (x1 + x2) / 2 - window_xy[0]
                    cy = (y1 + y2) / 2 - window_xy[1]
                    c = round((cx - geom.ox) / geom.dx)
                    r_ = round((cy - geom.oy) / geom.dy)
                    if 0 <= r_ < 10 and 0 <= c < 9:
                        b.sq[r_ * 9 + c] = code
        return b

    class_map = {}


def make_template_from_fixture(out_dir="templates", seed_fen=None):
    """从 fixture 图片生成 14 类棋子模板 (用于零依赖方案)。"""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fixture import render, Board, PIECE_CN
    from board import START_FEN
    os.makedirs(out_dir, exist_ok=True)
    b = Board.from_fen(seed_fen or START_FEN)
    from PIL import ImageDraw
    tmp = render(b, os.path.join(out_dir, "_full.png"))
    img = cv2.imread(tmp)
    # 用 fixture 固定几何切棋子
    geom = BoardGeometry(96, 132, 66, 60, 720, 804)
    for idx, p in enumerate(b.sq):
        if not p:
            continue
        tile = extract_square(img, geom, idx, (0, 0))
        cv2.imwrite(os.path.join(out_dir, f"{p}_{PIECE_CN[p]}.png"), tile)
    print(f"[template] 生成 {sum(1 for _ in b.sq if _)} 张模板到 {out_dir}")
