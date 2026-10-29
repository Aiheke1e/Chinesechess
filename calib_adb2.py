# calib_adb2.py — 按颜色分组生成棋子模板 (红/黑)
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
img = cv2.imread(os.path.join(HERE, "jj_board.png"))

# 几何 (微调后)
GEOM = (82.0, 442.0, 108.0, 113.6)
ox, oy, dx, dy = GEOM
half = 46

START = [None]*10
START[0] = ["bN","bB","bE","bA","bK","bA","bE","bB","bN"]
START[2] = [None,"bC",None,None,None,None,None,"bC",None]
START[3] = ["bP",None,"bP",None,"bP",None,"bP",None,"bP"]
START[6] = ["rP",None,"rP",None,"rP",None,"rP",None,"rP"]
START[7] = [None,"rC",None,None,None,None,None,"rC",None]
START[9] = ["rN","rB","rE","rA","rK","rA","rE","rB","rN"]

def piece_color(tile):
    """返回 'r' / 'b' / None(空)。基于棋子中心区域颜色。"""
    reg = tile[15:2*half-15, 15:2*half-15]
    if reg.size == 0: return None
    m = reg.reshape(-1,3).mean(0)
    R,G,B = m
    # 红棋: 蓝/青底(B高且B>R), 黑棋: 深蓝/黑(B相对低)
    # 实测红棋 B>155, 黑棋 B 多在 90-150 (部分190但仍 R-B>-80)
    if B > 165 and B > R + 25:
        return "r"
    if B < 150:
        return "b"
    # 中间带: 用 R-B 强负判红
    if (R - B) < -85:
        return "r"
    return "b"

tdir = os.path.join(HERE, "templates")
if os.path.isdir(tdir):
    import shutil; shutil.rmtree(tdir)
os.makedirs(tdir, exist_ok=True)
saved = {}
for r in range(10):
    for c in range(9):
        code = START[r][c] if START[r] else None
        if not code: continue
        xi, yi = int(ox + c*dx), int(oy + r*dy)
        tile = img[max(0,yi-half):yi+half, max(0,xi-half):xi+half]
        col = piece_color(tile)
        g = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
        g48 = cv2.resize(g, (48,48))
        fn = f"{code}_{saved.get(code,0):02d}.png"
        cv2.imwrite(os.path.join(tdir, fn), g48)
        saved[code] = saved.get(code,0)+1
        print(f"r{r}c{c} {code} 颜色={col}")
print("\n模板:", saved)
json.dump({"ox":ox,"oy":oy,"dx":dx,"dy":dy}, open(os.path.join(HERE,"adb_geom.json"),"w"))
print("几何已写 adb_geom.json")
