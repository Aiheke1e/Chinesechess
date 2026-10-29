# verify.py —— 端到端验证: 用 simulator 生成的 fixture 图, 跑识别+AI 链路
# 不依赖真实客户端, 在无 JJ 象棋安装时也能证明整套逻辑可用。
import json
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from board import Board, RED, BLACK
from vision import BoardGeometry, board_from_templates, find_window, grab_window
from engine import choose_move

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixture")
GEOM = BoardGeometry(96, 132, 66, 60, 720, 804, "sim")  # 与 simulator 真值一致


def make_templates_from_fixture():
    """用 fixture 生成 14 类模板 (零依赖识别用)"""
    import cv2
    from PIL import ImageDraw
    b = Board.from_fen(open(os.path.join(FIX, "board_start.fen"), encoding="utf-8").read().strip())
    tdir = os.path.join(HERE, "templates")
    os.makedirs(tdir, exist_ok=True)
    img = cv2.imread(os.path.join(FIX, "board_start.png"))
    pts = [(GEOM.ox + c * GEOM.dx, GEOM.oy + r * GEOM.dy) for r in range(10) for c in range(9)]
    for idx, p in enumerate(b.sq):
        if not p:
            continue
        cx, cy = pts[idx]
        tile = img[cy - GEOM.dy // 2 + 4: cy + GEOM.dy // 2 - 4,
                   cx - GEOM.dx // 2 + 4: cx + GEOM.dx // 2 - 4]
        cv2.imwrite(os.path.join(tdir, f"{p}_{idx}.png"), tile)
    print(f"[verify] 生成模板 -> {tdir}")


def verify_recognition():
    truth = json.load(open(os.path.join(FIX, "truth.json"), encoding="utf-8"))
    tdir = os.path.join(HERE, "templates")
    report = []
    for name, info in truth.items():
        img = cv2.imread(os.path.join(FIX, name + ".png"))
        b = board_from_templates(img, GEOM, (0, 0), tdir)
        # 对比真值
        gt = info["squares"]
        ok = sum(1 for i in range(90) if b.sq[i] == gt[i])
        wrong = [(i, gt[i], b.sq[i]) for i in range(90) if b.sq[i] != gt[i]]
        fen_ok = b.to_fen().split()[0] == info["fen"].split()[0]
        report.append((name, ok, 90, fen_ok, wrong[:5]))
        print(f"[verify] {name}: 识别 {ok}/90 正确, FEN={'OK' if fen_ok else 'DIFF'}, "
              f"错误样例 {wrong[:3]}")
    return all(r[3] for r in report)


def verify_ai():
    """Pikafish 选招: 起始局面走一步并确认合法。"""
    b = Board.from_fen(open(os.path.join(FIX, "board_start.fen"), encoding="utf-8").read().strip())
    mv, score = choose_move(b, depth=4, mode="pikafish")
    legal = mv in b.legal_moves()
    print(f"[verify] AI 选招 {Board.ucci(mv)} 合法={legal} 评分={score}")
    return legal


def main():
    make_templates_from_fixture()
    rec_ok = verify_recognition()
    ai_ok = verify_ai()
    print("\n=== 验证结果 ===")
    print(f"识别链路: {'通过' if rec_ok else '有误差(见上)'}")
    print(f"AI 选招:  {'通过' if ai_ok else '失败'}")
    if rec_ok and ai_ok:
        print("结论: 在无客户端环境下, 识别+AI 闭环可运行。接真实 JJ 象棋只需用 calibrate.py 复测几何。")


if __name__ == "__main__":
    main()
