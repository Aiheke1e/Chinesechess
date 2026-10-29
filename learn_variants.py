"""Add trusted moved-glyph samples from a live JJ board to the template set.

Use only with manually verified squares, for example:
  py -3 learn_variants.py --sample blk:N:h9 --sample red:N:h6
"""
from __future__ import annotations

import argparse
import os

import cv2

from adb_bridge import list_devices, screenshot
from board import Board
from recognizer import OX, OY, DX, DY, analyze, crop


HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="append", default=[],
                        help="kind:letter:ucci-square, e.g. red:N:h6")
    parser.add_argument("--fen", help="Trusted FEN matching the current static screen")
    args = parser.parse_args()
    devices = list_devices()
    if not devices:
        raise SystemExit("未找到 ADB 设备")
    image = screenshot(devices[0])
    if image is None:
        raise SystemExit("截图失败")
    folder = os.path.join(HERE, "templates")
    os.makedirs(folder, exist_ok=True)
    entries = []
    for spec in args.sample:
        kind, letter, square = spec.split(":")
        index = Board.from_ucci(square + square)[0]
        entries.append((kind, letter, index))
    if args.fen:
        trusted = Board.from_fen(args.fen)
        for index, piece in enumerate(trusted.sq):
            if piece:
                entries.append(("red" if piece.isupper() else "blk",
                                piece.upper(), index))
    if not entries:
        parser.error("至少提供一个 --sample 或 --fen")

    # A trusted FEN may be learned only when every expected occupied square is
    # visibly occupied with the expected side/color and every empty square is empty.
    if args.fen:
        for index, wanted in enumerate(trusted.sq):
            row, column = divmod(index, 9)
            tile = crop(image, OX + column * DX, OY + row * DY)
            strokes, red_ratio = analyze(tile)
            # Empty intersections contain grid/coordinate strokes (~2500), while
            # a real JJ piece is consistently above ~5000 in this skin.
            occupied = strokes >= 4000
            if occupied != (wanted is not None):
                raise SystemExit(f"画面与 FEN 占位不一致：idx{index}")
            if wanted is not None:
                seen_red = red_ratio > 0.36
                if seen_red != wanted.isupper():
                    raise SystemExit(f"画面与 FEN 红黑不一致：idx{index}")

    for number, (kind, letter, index) in enumerate(entries):
        row, column = divmod(index, 9)
        tile = crop(image, OX + column * DX, OY + row * DY)
        gray = cv2.resize(cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY), (48, 48))
        suffix = "learned" if args.fen else "variant"
        path = os.path.join(folder, f"{kind}_{letter}_{suffix}_{number:02d}.png")
        if not cv2.imwrite(path, gray):
            raise SystemExit("模板写入失败：" + path)
        print("已保存：" + path)


if __name__ == "__main__":
    main()
