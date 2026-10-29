"""Calibrate the live JJ standard board and rebuild recognition templates."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import cv2

from adb_bridge import list_devices, screenshot
from geometry import detect_standard_geometry

HERE = os.path.dirname(os.path.abspath(__file__))
NO_CONSOLE = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", help="Use a saved standard-start screenshot")
    args = parser.parse_args()
    if args.image:
        image = cv2.imread(args.image)
        source = args.image
    else:
        devices = list_devices()
        if not devices:
            raise SystemExit("未找到 ADB 设备")
        image = screenshot(devices[0])
        source = devices[0]
    geometry = detect_standard_geometry(image)
    if geometry is None:
        raise SystemExit("无法从画面确定标准开局棋盘；请确认是完整 32 子初始阵型")
    output = {key: geometry[key] for key in ("ox", "oy", "dx", "dy")}
    path = os.path.join(HERE, "adb_geom.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"标定来源: {source}")
    print("棋盘几何:", output, "残差:", geometry["x_error"], geometry["y_error"])
    result = subprocess.run([sys.executable, os.path.join(HERE, "make_templates.py")],
                            cwd=HERE, creationflags=NO_CONSOLE)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
