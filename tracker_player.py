# tracker_player.py — JJ象棋自动对弈主控（状态追踪版，不认字）
# 架构: 初始局面已知(象棋开局固定) -> 每回合像素diff追踪变化 -> AI算招 -> 拟人拖拽落子
from __future__ import annotations
import sys, os, time, random, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np
from board import Board
from engine import choose_move
from adb_bridge import screenshot, swipe, list_devices

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "adb_geom.json"), encoding="utf-8") as _geom_file:
    GEOM = json.load(_geom_file)
OX, OY, DX, DY = GEOM["ox"], GEOM["oy"], GEOM["dx"], GEOM["dy"]
HALF = 28  # 指纹采样半径

# 初始局面 (黑上红下, 行0=黑方底线, 行9=红方底线)
# 编码规则同 board.py: 大写=红方, 小写=黑方; K将 A士 B象 N马 R车 C炮 P兵
START = [None]*10
START[0] = ["r","n","b","a","k","a","b","n","r"]   # 黑方底线
START[2] = [None,"c",None,None,None,None,None,"c",None]
START[3] = ["p",None,"p",None,"p",None,"p",None,"p"]
START[6] = ["P",None,"P",None,"P",None,"P",None,"P"]
START[7] = [None,"C",None,None,None,None,None,"C",None]
START[9] = ["R","N","B","A","K","A","B","N","R"]   # 红方底线

def idx(r, c): return r*9 + c

def cell_fp(img, r, c):
    """交叉点中心区域平均色, 用于 diff 检测变化"""
    xi, yi = int(OX + c*DX), int(OY + r*DY)
    reg = img[max(0,yi-HALF):yi+HALF, max(0,xi-HALF):xi+HALF]
    if reg.size == 0: return None
    return reg.reshape(-1,3).mean(0)

def all_fps(img):
    return [cell_fp(img, r, c) for r in range(10) for c in range(9)]

def fp_dist(a, b):
    if a is None or b is None: return 0.0
    return float(np.abs(a-b).mean())

def build_start():
    b = Board()
    for r in range(10):
        row = START[r]
        if not row:
            continue
        for c in range(9):
            code = row[c]
            if code: b.sq[idx(r,c)] = code
    b.turn = "r"  # 红先
    return b

def human_drag(fx, fy, tx, ty, device):
    jx = random.uniform(-7, 7); jy = random.uniform(-7, 7)
    dur = random.randint(160, 340)
    swipe(fx+jx, fy+jy, tx+jx*0.4, ty+jy*0.4, duration_ms=dur, device=device)

def board_pt(i):
    r, c = divmod(i, 9)
    return (OX + c*DX, OY + r*DY)

def find_opponent_move(fp_prev, fp_now, b_now):
    """对比指纹, 返回 (frm, to) 或 None。
    两个变化点: 内部棋盘上"当前有子"的那个是起点(子离开), "当前空"的是终点(子落入)。"""
    changed = []
    for i in range(90):
        d = fp_dist(fp_prev[i], fp_now[i])
        if d > 10:
            changed.append(i)
    if len(changed) < 2:
        return None
    # 在变化点里找: 一个有子(起点), 一个空(终点)
    occ = [i for i in changed if b_now.sq[i] is not None]
    emp = [i for i in changed if b_now.sq[i] is None]
    if len(occ) >= 1 and len(emp) >= 1:
        return (occ[0], emp[0])
    # 兜底: 取变化最大的两个
    changed.sort(key=lambda i: -fp_dist(fp_prev[i], fp_now[i]))
    return (changed[0], changed[1])

def main():
    dev = list_devices()
    if not dev:
        print("无设备"); return
    device = dev[0]
    print("设备:", device)

    b = build_start()
    img = screenshot(device)
    fp = all_fps(img)
    print("基准指纹建立 (90 交叉点)")

    my_color = "r"
    think_depth = 4
    move_count = 0

    while move_count < 120:
        if b.turn == my_color:
            mv, score = choose_move(b, depth=think_depth, mode="pikafish", my_color=my_color)
            if not mv:
                print("无合法着法, 结束"); break
            u = Board.ucci(mv)
            fpt, tpt = board_pt(mv[0]), board_pt(mv[1])
            print(f"[我] {u} score={score}")
            human_drag(fpt[0], fpt[1], tpt[0], tpt[1], device)
            b.apply(mv)  # 自动翻转 turn
            move_count += 1
            time.sleep(random.uniform(2.5, 4.5))
            img = screenshot(device); fp = all_fps(img)
        else:
            waited = 0.0; moved = None
            while waited < 45:
                time.sleep(1.2); waited += 1.2
                img2 = screenshot(device)
                fp2 = all_fps(img2)
                mv = find_opponent_move(fp, fp2, b)
                if mv:
                    moved = mv; fp = fp2; break
            if not moved:
                print("45秒未检测到对方走子, 结束"); break
            frm, to = moved
            u = Board.ucci((frm, to))
            print(f"[对] {u}")
            b.apply((frm, to))
            time.sleep(random.uniform(0.8, 1.8))
    print("对局结束或达到步数上限")

if __name__ == "__main__":
    main()
