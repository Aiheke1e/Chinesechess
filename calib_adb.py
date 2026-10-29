# calib_adb.py — 从真实 JJ 象棋截图标定棋盘几何并生成棋子模板
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

# 从 jj_board.png 目测的棋盘几何 (1080x1920):
# 顶行 y≈437, 底行 y≈1462, 左列 x≈77, 右列 x≈943
GEOM = {"ox": 77, "oy": 437, "dx": 108.25, "dy": 113.9}

# 起始局面 FEN 对应的棋盘 (黑上红下, 行0=黑底=第10横线)
START = [
    ["rN","rB","rE","rA","rK","rA","rE","rB","rN"],
    [None]*9,
    [None,"rC",None,None,None,None,None,"rC",None],
    ["rP",None,"rP",None,"rP",None,"rP",None,"rP"],
    [None]*9,
    [None]*9,
    ["bP",None,"bP",None,"bP",None,"bP",None,"bP"],
    [None,"bC",None,None,None,None,None,"bC",None],
    [None]*9,
    ["bN","bB","bE","bA","bK","bA","bE","bB","bN"],
]
# 注意: JJ界面红方在下方(我方), 红色棋子是"红"。上面 START 中 r=黑(上), b=红(下)?
# 实际: 上方是黑字(楚), 下方是红字(汉)。我方红棋=下方。所以行0-4是黑方, 行5-9是红方。
# 修正: 用颜色标记: 上方黑=black方, 下方红=red方。我们的代码 r=红 b=黑。
# 上面表里 "rN" 放在行0是错的——行0是黑方。交换:
START[0] = ["bN","bB","bE","bA","bK","bA","bE","bB","bN"]
START[2] = [None,"bC",None,None,None,None,None,"bC",None]
START[3] = ["bP",None,"bP",None,"bP",None,"bP",None,"bP"]
START[6] = ["rP",None,"rP",None,"rP",None,"rP",None,"rP"]
START[7] = [None,"rC",None,None,None,None,None,"rC",None]
START[9] = ["rN","rB","rE","rA","rK","rA","rE","rB","rN"]

def pt(r, c):
    return (GEOM["ox"] + c * GEOM["dx"], GEOM["oy"] + r * GEOM["dy"])

def main():
    img = cv2.imread(os.path.join(HERE, "jj_board.png"))
    if img is None:
        print("读不到 jj_board.png"); return
    h, w = img.shape[:2]
    print("img", w, "x", h)
    tdir = os.path.join(HERE, "templates")
    os.makedirs(tdir, exist_ok=True)
    half = 46  # 半径像素, 棋子直径约 100-110
    saved = {}
    # 画标定图: 标出 90 个交叉点
    vis = img.copy()
    for r in range(10):
        for c in range(9):
            x, y = pt(r, c)
            cv2.circle(vis, (int(x), int(y)), 6, (0, 0, 255), -1)
            cv2.circle(vis, (int(x), int(y)), half, (0, 255, 0), 1)
            code = START[r][c]
            if code:
                x0, y0 = int(x - half), int(y - half)
                tile = img[y0:y0 + 2 * half, x0:x0 + 2 * half]
                if tile.size == 0:
                    continue
                # 存模板: 灰度 48x48
                g = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
                g48 = cv2.resize(g, (48, 48))
                fn = f"{code}_{saved.get(code,0):02d}.png"
                cv2.imwrite(os.path.join(tdir, fn), g48)
                saved[code] = saved.get(code, 0) + 1
    cv2.imwrite(os.path.join(HERE, "calib_vis.png"), vis)
    print("模板数:", saved)
    print("标定可视化: calib_vis.png")
    json.dump(GEOM, open(os.path.join(HERE, "adb_geom.json"), "w"))

if __name__ == "__main__":
    main()
