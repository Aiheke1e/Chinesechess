"""Bounded local OCR worker for Companion's Chinese control labels.

Invoked in a subprocess, with package-bundled CPU models. No network, no logs.
"""
import json
import sys


def recognize_lines(path):
    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    result, _ = engine(path, use_cls=False)
    lines = []
    for polygon, text, confidence, *_ in result or []:
        if confidence < .92:
            continue
        left = min(point[0] for point in polygon)
        top = min(point[1] for point in polygon)
        right = max(point[0] for point in polygon)
        bottom = max(point[1] for point in polygon)
        lines.append({"text": text, "words": [{"text": text, "x": left,
            "y": top, "width": right-left, "height": bottom-top}]})
    return lines


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(recognize_lines(sys.argv[1]), ensure_ascii=False))
