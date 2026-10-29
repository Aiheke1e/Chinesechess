# verify_adb.py — 用模板识别 jj_board.png, 与起始局面比对, 并网格搜索微调几何
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
START[9] = ["rN","rB","bE" if False else "rE","rA","rK","rA","rE","rB","rN"]

def load_templates():
    tdir = os.path.join(HERE, "templates")
    T = {}
    for f in os.listdir(tdir):
        if f.endswith(".png"):
            code = f.split("_")[0]
            T.setdefault(code, []).append(cv2.imread(os.path.join(tdir, f), 0))
    return T

def classify(tile, T):
    if tile.size == 0: return None, -1
    g = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (48, 48))
    best = (None, -1)
    for code, imgs in T.items():
        for t in imgs:
            v = float(cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED).max())
            if v > best[1]: best = (code, v)
    return best

def read_board(img, geom, T, half=46):
    ox, oy, dx, dy = geom
    sq = []
    for r in range(10):
        for c in range(9):
            x, y = ox + c*dx, oy + r*dy
            xi, yi = int(x), int(y)
            tile = img[max(0,yi-half):yi+half, max(0,xi-half):xi+half]
            code, conf = classify(tile, T)
            sq.append((code, conf))
    return sq

def main():
    img = cv2.imread(os.path.join(HERE, "jj_board.png"))
    T = load_templates()
    base = [77, 437, 108.25, 113.9]
    best = None
    # 网格搜索微调: ox±12 oy±12 dx±4 dy±4
    for ox in range(65, 92, 4):
        for oy in range(425, 452, 4):
            for dx in [104,106,108,110,112]:
                for dy in [110,112,113.9,115,117]:
                    geom = (ox, oy, dx, dy)
                    sq = read_board(img, geom, T)
                    ok = 0
                    for i,(code,_) in enumerate(sq):
                        r, c = divmod(i, 9)
                        truth = START[r][c] if START[r] else None
                        if code == truth: ok += 1
                    score = ok
                    if best is None or score > best[0]:
                        best = (score, geom)
    score, geom = best
    print(f"最佳几何: ox={geom[0]} oy={geom[1]} dx={geom[2]} dy={geom[3]}  正确 {score}/32")
    json.dump({"ox":geom[0],"oy":geom[1],"dx":geom[2],"dy":geom[3]},
              open(os.path.join(HERE,"adb_geom.json"),"w"))
    # 详细输出识别结果
    sq = read_board(img, geom, T)
    for i,(code,conf) in enumerate(sq):
        r, c = divmod(i, 9)
        truth = START[r][c] if START[r] else None
        mark = "OK " if code==truth else "BAD"
        if code != truth or truth:
            print(f"{mark} r{r}c{c} 真值={truth} 识别={code} conf={conf:.2f}")
    print("已写入 adb_geom.json")

if __name__ == "__main__":
    main()
