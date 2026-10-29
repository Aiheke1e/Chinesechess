# make_templates.py — 生成模板 (文件名显式区分大小写, 避免 Windows 覆盖)
# 命名: blk_<lower>_NN.png = 黑棋 ; red_<UPPER>_NN.png = 红棋
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "adb_geom.json"), encoding="utf-8") as _geom_file:
    G = json.load(_geom_file)
OX, OY, DX, DY = G["ox"], G["oy"], G["dx"], G["dy"]
SHIFT_X, SHIFT_Y = 0, 0
HALF = 44

# 开局布局: 行0-4 黑方(小写), 行5-9 红方(大写)
START = [None]*10
START[0] = ["r","n","b","a","k","a","b","n","r"]
START[2] = [None,"c",None,None,None,None,None,"c",None]
START[3] = ["p",None,"p",None,"p",None,"p",None,"p"]
START[6] = ["P",None,"P",None,"P",None,"P",None,"P"]
START[7] = [None,"C",None,None,None,None,None,"C",None]
START[9] = ["R","N","B","A","K","A","B","N","R"]

def crop(img, r, c):
    cx = OX + c*DX + SHIFT_X
    cy = OY + r*DY + SHIFT_Y
    xi, yi = int(cx), int(cy)
    return img[max(0,yi-HALF):yi+HALF, max(0,xi-HALF):xi+HALF]

def stroke_red_ratio(tile):
    g = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
    med = np.median(g)
    diff = np.abs(g.astype(int) - med)
    mask = (diff > 28).astype(np.uint8)
    cnt = int(mask.sum())
    if cnt < 30: return 0.0, False
    hsv = cv2.cvtColor(tile, cv2.COLOR_BGR2HSV)
    h = hsv[:,:,0].astype(int); s = hsv[:,:,1]; v = hsv[:,:,2]
    red_mask = ((s > 70) & ((h < 18) | (h > 165)) & (v > 60)).astype(np.uint8)
    nr = int((red_mask * mask).sum())
    return nr/cnt, True

def main():
    img = cv2.imread(os.path.join(HERE, "jj_live.png"))
    tdir = os.path.join(HERE, "templates")
    if os.path.isdir(tdir):
        import shutil; shutil.rmtree(tdir)
    os.makedirs(tdir)
    saved = {}
    print("=== 生成模板 (红黑分离命名) ===")
    for r in range(10):
        row = START[r]
        if not row: continue
        for c in range(9):
            code = row[c]
            if not code: continue
            tile = crop(img, r, c)
            if tile.size == 0: continue
            rr, ok = stroke_red_ratio(tile)
            kind = "red" if code.isupper() else "blk"
            prefix = kind + "_" + code
            if code.isupper():
                prefix = "red_" + code          # 红: red_R
            else:
                prefix = "blk_" + code.upper()  # 黑: blk_R (统一大写避免大小写冲突)
            n = saved.get(code, 0)
            g = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
            g48 = cv2.resize(g, (48,48))
            fn = f"{prefix}_{n:02d}.png"
            cv2.imwrite(os.path.join(tdir, fn), g48)
            saved[code] = n + 1
            cn = {"K":"帅","A":"仕","B":"相","N":"马","R":"车","C":"炮","P":"兵",
                  "k":"将","a":"士","b":"象","n":"马","r":"车","c":"炮","p":"卒"}.get(code, code)
            kk = "红" if code.isupper() else "黑"
            print(f"  行{r}列{c}: {fn}  ({kk}{cn}) 红占比={rr:.2f}")
    print("\n统计:", saved, " 共", sum(saved.values()))

if __name__ == "__main__":
    main()
