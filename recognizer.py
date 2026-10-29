# recognizer.py — 稳健识别 JJ 象棋棋盘
# 流程: 1) 笔画像素数判有无子  2) 笔画红色占比判红黑  3) 同色子集内字形模板匹配
import sys, os, json
from functools import lru_cache
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "adb_geom.json"), encoding="utf-8") as _geom_file:
    G = json.load(_geom_file)
OX, OY, DX, DY = G["ox"], G["oy"], G["dx"], G["dy"]
SHIFT_X, SHIFT_Y = 0, 0
HALF = 44

CN = {"K":"帅","A":"仕","B":"相","N":"马","R":"车","C":"炮","P":"兵",
      "k":"将","a":"士","b":"象","n":"马","r":"车","c":"炮","p":"卒"}

@lru_cache(maxsize=1)
def load_templates():
    """返回 (红方模板dict, 黑方模板dict), key 为大写字母"""
    tdir = os.path.join(HERE, "templates")
    red, blk = {}, {}
    if not os.path.isdir(tdir):
        return red, blk
    for f in sorted(os.listdir(tdir)):
        if not f.endswith(".png"): continue
        g = cv2.imread(os.path.join(tdir, f), 0)
        if g is None: continue
        parts = f[:-4].split("_")   # red_R_00 或 blk_R_00
        if len(parts) < 3: continue
        kind, letter = parts[0], parts[1]
        if kind == "red":
            red.setdefault(letter, []).append(g)
        elif kind == "blk":
            blk.setdefault(letter, []).append(g)
    return red, blk

def crop(img, cx, cy, half=HALF):
    xi, yi = int(cx), int(cy)
    return img[max(0,yi-half):yi+half, max(0,xi-half):xi+half]

def analyze(tile):
    """返回 (笔画像素数, 红色占比)"""
    g = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
    med = np.median(g)
    diff = np.abs(g.astype(int) - med)
    mask = (diff > 28).astype(np.uint8)
    cnt = int(mask.sum())
    if cnt < 30:
        return cnt, 0.0
    hsv = cv2.cvtColor(tile, cv2.COLOR_BGR2HSV)
    h = hsv[:,:,0].astype(int); s = hsv[:,:,1]; v = hsv[:,:,2]
    red_mask = ((s > 70) & ((h < 18) | (h > 165)) & (v > 60)).astype(np.uint8)
    nr = int((red_mask * mask).sum())
    return cnt, nr / cnt

def match_shape(tile, sub):
    if tile.size == 0 or not sub: return None, -1.0
    g48 = cv2.resize(cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY), (48,48))
    best = (None, -1.0)
    for letter, imgs in sub.items():
        for t in imgs:
            v = float(cv2.matchTemplate(g48, t, cv2.TM_CCOEFF_NORMED)[0,0])
            if v > best[1]: best = (letter, v)
    return best


def _screen_index(index, orientation):
    if orientation == "red_bottom":
        return index
    if orientation == "black_bottom":
        return 89 - index
    raise ValueError(f"unknown board orientation: {orientation}")


def _recognize_tile(img, screen_index, stroke_thr=2000, red_thr=0.36,
                    conf_thr=0.28):
    """Classify one board intersection without scanning the other 89."""
    row, column = divmod(screen_index, 9)
    tile = crop(img, OX + column * DX + SHIFT_X,
                OY + row * DY + SHIFT_Y)
    if tile.size == 0:
        return None
    count, red_ratio = analyze(tile)
    if count < stroke_thr:
        return None
    red_templates, black_templates = load_templates()
    templates = red_templates if red_ratio > red_thr else black_templates
    letter, confidence = match_shape(tile, templates)
    if not letter or confidence <= conf_thr:
        return None
    return letter if red_ratio > red_thr else letter.lower()


def recognize_indices(img, orientation, indices, stroke_thr=2000,
                      red_thr=0.36, conf_thr=0.28):
    """Recognize a small set of canonical squares."""
    if img is None or getattr(img, "ndim", 0) != 3:
        return {}
    result = {}
    for index in dict.fromkeys(int(value) for value in indices
                               if 0 <= int(value) < 90):
        result[index] = _recognize_tile(
            img, _screen_index(index, orientation), stroke_thr, red_thr,
            conf_thr)
    return result


def board_change_scores(reference, current, orientation, half=34):
    """Return a visual-change score for every canonical board square."""
    if (reference is None or current is None or
            getattr(reference, "shape", None) != getattr(current, "shape", None)):
        return None
    scores = [0.0] * 90
    for index in range(90):
        screen_index = _screen_index(index, orientation)
        row, column = divmod(screen_index, 9)
        cx = int(round(OX + column * DX + SHIFT_X))
        cy = int(round(OY + row * DY + SHIFT_Y))
        first = crop(reference, cx, cy, half=half)
        second = crop(current, cx, cy, half=half)
        if first.size == 0 or second.size == 0 or first.shape != second.shape:
            return None
        first_lab = cv2.cvtColor(first, cv2.COLOR_BGR2LAB)
        second_lab = cv2.cvtColor(second, cv2.COLOR_BGR2LAB)
        scores[index] = float(cv2.absdiff(first_lab, second_lab).mean())
    return scores

def recognize(img, verbose=False, stroke_thr=2000, red_thr=0.36, conf_thr=0.28):
    """返回 list[90]: 每点为单字符 code (大写=红 小写=黑) 或 None"""
    red_T, blk_T = load_templates()
    out = [None]*90
    for r in range(10):
        for c in range(9):
            cx = OX + c*DX + SHIFT_X
            cy = OY + r*DY + SHIFT_Y
            tile = crop(img, cx, cy)
            if tile.size == 0: continue
            cnt, rr = analyze(tile)
            i = r*9 + c
            if cnt < stroke_thr:
                continue                      # 空点
            sub = red_T if rr > red_thr else blk_T
            letter, conf = match_shape(tile, sub)
            if letter and conf > conf_thr:
                code = letter if rr > red_thr else letter.lower()
                out[i] = code
                if verbose:
                    kind = "红" if code.isupper() else "黑"
                    print(f"  idx{i:2d} 行{r}列{c}: {code}({kind}{CN.get(code,'?')}) "
                          f"conf={conf:.2f} 笔画={cnt} 红占比={rr:.2f}")
    return out


def infer_orientation(screen_squares):
    """Return red_bottom/black_bottom from the two kings, or None."""
    try:
        red_row = screen_squares.index("K") // 9
        black_row = screen_squares.index("k") // 9
    except ValueError:
        return None
    if red_row > black_row:
        return "red_bottom"
    if black_row > red_row:
        return "black_bottom"
    return None


def canonicalize(screen_squares, orientation):
    """Convert screen indexes to canonical black-top/red-bottom indexes."""
    if orientation == "red_bottom":
        return screen_squares[:]
    if orientation == "black_bottom":
        return list(reversed(screen_squares))
    raise ValueError(f"unknown board orientation: {orientation}")


def recognize_position(img, orientation="auto", **kwargs):
    raw = recognize(img, **kwargs)
    resolved = infer_orientation(raw) if orientation == "auto" else orientation
    if resolved not in ("red_bottom", "black_bottom"):
        return None, None
    return canonicalize(raw, resolved), resolved


def recognize_occupancy(img, orientation, stroke_thr=2000, red_thr=0.36,
                        conf_thr=0.28):
    """Recognize only empty/red/black for safe saved-session verification.

    Piece glyph variants do not matter here.  ``R`` and ``r`` are placeholders
    for side/color, not claims that every occupied square is a rook.
    """
    # Do not treat every high-contrast coloured circle as a piece.  JJ's AI
    # hints draw red/green target dots directly on intersections and the old
    # stroke-only detector counted them as extra pieces.  Requiring any chess
    # glyph template match still tolerates a horse being classified as a rook:
    # occupancy only keeps the recognized side, never the guessed piece type.
    detected = recognize(img, stroke_thr=stroke_thr, red_thr=red_thr,
                         conf_thr=conf_thr)
    raw = [None if piece is None else
           ("R" if piece.isupper() else "r") for piece in detected]
    return canonicalize(raw, orientation)


def recognize_presence_occupancy(img, orientation, stroke_thr=3500,
                                 red_thr=0.36):
    """Return a highlight-tolerant empty/red/black observation.

    This deliberately ignores the glyph-template score.  It is therefore not
    suitable as the primary recognizer because JJ hint dots can look occupied.
    Callers may only use it behind legal-sequence validation with a very small
    mismatch allowance.  Its purpose is to retain a real piece whose glowing
    last-move ring makes the normal glyph template temporarily fail.
    """
    if img is None or getattr(img, "ndim", 0) != 3:
        return None
    raw = []
    for row in range(10):
        for column in range(9):
            tile = crop(img, OX + column * DX + SHIFT_X,
                        OY + row * DY + SHIFT_Y)
            if tile.size == 0:
                raw.append(None)
                continue
            count, red_ratio = analyze(tile)
            if count < stroke_thr:
                raw.append(None)
            else:
                raw.append("R" if red_ratio > red_thr else "r")
    return canonicalize(raw, orientation)


def recognize_presence_indices(img, orientation, indices, stroke_thr=3500,
                               red_thr=0.36):
    """Read only side/occupancy for selected trusted-board intersections."""
    if img is None or getattr(img, "ndim", 0) != 3:
        return {}
    result = {}
    for index in dict.fromkeys(int(value) for value in indices
                               if 0 <= int(value) < 90):
        screen_index = _screen_index(index, orientation)
        row, column = divmod(screen_index, 9)
        tile = crop(img, OX + column * DX + SHIFT_X,
                    OY + row * DY + SHIFT_Y)
        if tile.size == 0:
            result[index] = None
            continue
        count, red_ratio = analyze(tile)
        result[index] = (None if count < stroke_thr else
                         ("R" if red_ratio > red_thr else "r"))
    return result

if __name__ == "__main__":
    from board import Board
    img = cv2.imread(os.path.join(HERE, "jj_live.png"))
    res = recognize(img, verbose=True)
    print("\n识别到棋子数:", sum(1 for x in res if x), "/ 32")
    b = Board(res, turn="r")
    print(b.pretty())
    print("FEN:", b.to_fen())
