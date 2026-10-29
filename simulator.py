# simulator.py —— 生成"JJ 象棋风格"棋盘截图, 在没有真实客户端时端到端验证识别+AI
import json
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from board import Board, START_FEN, PIECE_CN, ROWS, COLS, RED

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixture")

MARGIN_X, MARGIN_Y = 96, 132
DX, DY = 66, 60
RADIUS = 28
SIZE = (COLS - 1) * DX + MARGIN_X * 2, (ROWS - 1) * DY + MARGIN_Y * 2
WOOD = (206, 170, 118)
LINE = (86, 58, 32)
RED_C = (188, 32, 32)
BLACK_C = (32, 32, 32)


def load_font(size):
    for name in ("simhei.ttf", "msyh.ttc", "simsun.ttc", "Deng.ttf"):
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def wood_background(w, h):
    base = np.zeros((h, w, 3), dtype=np.float32)
    for y in range(h):
        base[y, :] = np.array(WOOD, dtype=np.float32) * (1.0 - 0.12 * (y / h))
    rng = np.random.default_rng(7)
    tex = rng.normal(0, 3.2, (h, w, 1)).repeat(3, axis=2)
    grain = np.sin(np.linspace(0, 40, h))[:, None, None] * 2.0
    return Image.fromarray(np.clip(base + tex + grain, 0, 255).astype(np.uint8))


def grid_points():
    return [(MARGIN_X + c * DX, MARGIN_Y + r * DY) for r in range(ROWS) for c in range(COLS)]


def draw_board(draw):
    x0, y0 = MARGIN_X, MARGIN_Y
    x1 = x0 + (COLS - 1) * DX
    y1 = y0 + (ROWS - 1) * DY
    for pad in (10, 6):
        draw.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline=LINE, width=2)
    for r in range(ROWS):
        y = y0 + r * DY
        draw.line([x0, y, x1, y], fill=LINE, width=2)
    for c in range(COLS):
        x = x0 + c * DX
        if c in (0, COLS - 1):
            draw.line([x, y0, x, y1], fill=LINE, width=2)
        else:
            draw.line([x, y0, x, y0 + 4 * DY], fill=LINE, width=2)
            draw.line([x, y0 + 5 * DY, x, y1], fill=LINE, width=2)
    for top in (0, 7):
        draw.line([x0 + 3 * DX, y0 + top * DY, x0 + 5 * DX, y0 + (top + 2) * DY], fill=LINE, width=2)
        draw.line([x0 + 5 * DX, y0 + top * DY, x0 + 3 * DX, y0 + (top + 2) * DY], fill=LINE, width=2)
    draw.text((x0 + 2 * DX, y0 + 4 * DY + 22), "楚 河", fill=LINE, font=load_font(30))
    draw.text((x0 + 6 * DX - 20, y0 + 4 * DY + 22), "汉 界", fill=LINE, font=load_font(30))
    marks = [(2, 1), (2, 7), (7, 1), (7, 7)]
    marks += [(3, c) for c in (0, 2, 4, 6, 8)] + [(6, c) for c in (0, 2, 4, 6, 8)]
    for r, c in marks:
        x = x0 + c * DX
        y = y0 + r * DY
        for sgn in (-1, 1):
            if 0 < c < 8:
                draw.line([x + sgn * 6, y - 6, x + sgn * 14, y - 6], fill=LINE, width=2)
                draw.line([x + sgn * 6, y - 6, x + sgn * 6, y - 14], fill=LINE, width=2)
                draw.line([x + sgn * 6, y + 6, x + sgn * 14, y + 6], fill=LINE, width=2)
                draw.line([x + sgn * 6, y + 6, x + sgn * 6, y + 14], fill=LINE, width=2)


def draw_piece(draw, cx, cy, ch, is_red, font):
    col = RED_C if is_red else BLACK_C
    draw.ellipse([cx - RADIUS + 2, cy - RADIUS + 3, cx + RADIUS + 2, cy + RADIUS + 3], fill=(120, 88, 52))
    draw.ellipse([cx - RADIUS, cy - RADIUS, cx + RADIUS, cy + RADIUS], fill=(238, 222, 190), outline=col, width=2)
    draw.ellipse([cx - RADIUS + 5, cy - RADIUS + 5, cx + RADIUS - 5, cy + RADIUS - 5], outline=col, width=1)
    bbox = draw.textbbox((0, 0), ch, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), ch, fill=col, font=font)


def render(board: Board, path: str):
    img = wood_background(*SIZE)
    draw = ImageDraw.Draw(img)
    draw_board(draw)
    font = load_font(40)
    for idx, p in enumerate(board.sq):
        if p is None:
            continue
        cx, cy = grid_points()[idx]
        draw_piece(draw, cx, cy, PIECE_CN[p], p.isupper(), font)
    img.save(path)
    return path


def random_playout(steps, seed):
    rng = random.Random(seed)
    b = Board.from_fen(START_FEN)
    for _ in range(steps):
        moves = b.legal_moves()
        if not moves:
            break
        b.apply(rng.choice(moves))
    return b


def main():
    os.makedirs(OUT, exist_ok=True)
    truth = {}
    cases = [("board_start", Board.from_fen(START_FEN)),
             ("board_mid", random_playout(24, 3)),
             ("board_end", random_playout(58, 11))]
    for name, b in cases:
        render(b, os.path.join(OUT, name + ".png"))
        with open(os.path.join(OUT, name + ".fen"), "w", encoding="utf-8") as f:
            f.write(b.to_fen())
        truth[name] = {"fen": b.to_fen(), "squares": b.sq}
    with open(os.path.join(OUT, "truth.json"), "w", encoding="utf-8") as f:
        json.dump(truth, f, ensure_ascii=False, indent=2)
    print(f"[simulator] 生成 {OUT}; size={SIZE} 几何 ox={MARGIN_X},oy={MARGIN_Y},dx={DX},dy={DY}")


if __name__ == "__main__":
    main()
