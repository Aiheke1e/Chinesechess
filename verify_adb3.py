# verify_adb3.py — 单几何快速识别验证 (不搜索, 直接看识别结果)
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

# 用更贴合的几何: 从可视化看红点需右下移约5px, 间距略增
GEOM = (82.0, 442.0, 108.0, 113.6)

def load_templates():
    tdir = os.path.join(HERE, "templates")
    T = {}
    for f in sorted(os.listdir(tdir)):
        if f.endswith(".png"):
            code = f.split("_")[0]
            g = cv2.imread(os.path.join(tdir, f), 0)
            if g is not None: T.setdefault(code, []).append(g)
    return T

# 预拼成一个大数组做批量匹配
def build_bank(T):
    codes, mats = [], []
    for code, imgs in T.items():
        for t in imgs:
            codes.append(code); mats.append(t)
    return codes, mats

def classify(g48, codes, mats):
    best_i, best_v = -1, -1.0
    for i, t in enumerate(mats):
        v = float(cv2.matchTemplate(g48, t, cv2.TM_CCOEFF_NORMED)[0,0])
        if v > best_v: best_v, best_i = v, i
    return (codes[best_i] if best_i >= 0 else None), best_v

def main():
    img = cv2.imread(os.path.join(HERE, "jj_board.png"))
    T = load_templates()
    codes, mats = build_bank(T)
    print("模板库:", len(mats), "张,", len(codes), "类")
    ox, oy, dx, dy = GEOM
    half = 46
    ok = tot = 0
    rows = []
    for r in range(10):
        line = []
        for c in range(9):
            xi, yi = int(ox + c*dx), int(oy + r*dy)
            tile = img[max(0,yi-half):yi+half, max(0,xi-half):xi+half]
            if tile.size == 0:
                line.append("  --  "); continue
            g48 = cv2.resize(cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY), (48,48))
            code, v = classify(g48, codes, mats)
            truth = START[r][c] if START[r] else None
            if truth:
                tot += 1
                mark = "√" if code == truth else "×"
                if code == truth: ok += 1
                line.append(f"{mark}{code or '??'}:{v:.2f}")
                if code != truth:
                    rows.append(f"  r{r}c{c} 真值={truth} 识别={code} conf={v:.2f}")
            else:
                # 空点: 高置信度说明误检
                if v > 0.6:
                    rows.append(f"  空点误检 r{r}c{c} -> {code} conf={v:.2f}")
                line.append(f" .{(code or '')}:{v:.1f}" if v > 0.6 else "  ...")
        print(" ".join(f"{x:>10}" for x in line))
    print(f"\n识别正确: {ok}/{tot}")
    for r in rows: print(r)

if __name__ == "__main__":
    main()
