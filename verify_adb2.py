# verify_adb2.py — 两级快速几何微调
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

START = [None]*10
START[0] = ["bN","bB","bE","bA","bK","bA","bE","bB","bN"]
START[2] = [None,"bC",None,None,None,None,None,"bC",None]
START[3] = ["bP",None,"bP",None,"bP",None,"bP",None,"bP"]
START[6] = ["rP",None,"rP",None,"rP",None,"rP",None,"rP"]
START[7] = [None,"rC",None,None,None,None,None,"rC",None]
START[9] = ["rN","rB","rE","rA","rK","rA","rE","rB","rN"]

def load_templates():
    tdir = os.path.join(HERE, "templates")
    T = {}
    for f in os.listdir(tdir):
        if f.endswith(".png"):
            code = f.split("_")[0]
            T.setdefault(code, []).append(cv2.imread(os.path.join(tdir, f), 0))
    return T

def classify(g48, T):
    best = (None, -1)
    for code, imgs in T.items():
        for t in imgs:
            v = float(cv2.matchTemplate(g48, t, cv2.TM_CCOEFF_NORMED).max())
            if v > best[1]: best = (code, v)
    return best

def score_geom(img, geom, T, half=46):
    ox, oy, dx, dy = geom
    ok = 0
    for r in range(10):
        for c in range(9):
            truth = START[r][c] if START[r] else None
            if not truth: continue
            xi, yi = int(ox + c*dx), int(oy + r*dy)
            tile = img[max(0,yi-half):yi+half, max(0,xi-half):xi+half]
            g = cv2.resize(cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY), (48,48))
            code, _ = classify(g, T)
            if code == truth: ok += 1
    return ok

def main():
    img = cv2.imread(os.path.join(HERE, "jj_board.png"))
    T = load_templates()
    base = [77.0, 437.0, 108.25, 113.9]
    best = (score_geom(img, tuple(base), T), base)
    print("基准分:", best[0])
    # 第一级: 平移粗搜
    for ox in np.arange(base[0]-14, base[0]+15, 3.5):
        for oy in np.arange(base[1]-14, base[1]+15, 3.5):
            s = score_geom(img, (ox, oy, base[2], base[3]), T)
            if s > best[0]: best = (s, [ox, oy, base[2], base[3]]); print("平移提升:", best)
    # 第二级: 间距细化
    g0 = best[1]
    for dx in np.arange(g0[2]-4, g0[2]+4.5, 1.0):
        for dy in np.arange(g0[3]-4, g0[3]+4.5, 1.0):
            for ox in np.arange(g0[0]-3, g0[0]+3.5, 1.5):
                for oy in np.arange(g0[1]-3, g0[1]+3.5, 1.5):
                    s = score_geom(img, (ox, oy, dx, dy), T)
                    if s > best[0]: best = (s, [ox, oy, dx, dy]); print("细化提升:", best)
    print(f"\n最终: ox={best[1][0]:.1f} oy={best[1][1]:.1f} dx={best[1][2]:.2f} dy={best[1][3]:.2f}  正确 {best[0]}/32")
    json.dump({"ox":best[1][0],"oy":best[1][1],"dx":best[1][2],"dy":best[1][3]},
              open(os.path.join(HERE,"adb_geom.json"),"w"))
    print("已写入 adb_geom.json")

if __name__ == "__main__":
    main()
