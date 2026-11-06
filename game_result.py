"""Read only explicit finish causes from an already confirmed JJ result page."""
import os
from pathlib import Path
import re
import subprocess
import tempfile

import cv2

ROOT = Path(__file__).resolve().parent


def classify_result(text):
    text = re.sub(r"\s+", "", str(text or ""))
    if re.search(r"如果|假如|假设|规则|须知|说明|[？?]|超时了吗", text):
        return {}
    # A countdown or a long wait is never evidence of a timeout. Require a
    # complete subject and cause on the result overlay, including our losses.
    options = (
        (r"(?:对方|对手)(?:已经|已|也|因|走棋|落子|用时|思考)*超时", "won", "opponent_timeout"),
        (r"(?:您|你|我方|本方)(?:已经|已|也|因|走棋|落子|用时|思考)*超时", "lost", "own_timeout"),
        (r"(?:对方|对手)(?:已经|已|主动)?(?:认输|投降)", "won", "opponent_resigned"),
        (r"(?:对方|对手)(?:已经|已|主动)?(?:逃跑|逃离|退出)", "won", "opponent_left"),
        (r"(?:您|你|我方|本方)(?:已经|已|主动)?(?:认输|投降|逃跑|逃离)", "lost", "own_resigned"),
    )
    matched = {(result, cause) for pattern, result, cause in options if re.search(pattern, text)}
    if len(matched) != 1:
        return {}
    result, cause = matched.pop()
    if result == "won" and re.search(r"(?:您|你|我方)(?:已经|已)?(?:输了|失败|判负)", text):
        return {}
    return {"result": result, "finish_cause": cause, "finish_evidence": "jj_result_text"}


def read_result(image):
    from postgame import has_actionable_postgame
    if os.name != "nt" or image is None or not has_actionable_postgame(image):
        return {}
    path = None
    try:
        height, width = image.shape[:2]
        # Crop away both avatar/name headers; only the central result area is
        # processed locally. No OCR text, image or user name enters the logs.
        area = image[int(height * .19):int(height * .75), int(width * .1):int(width * .9)]
        with tempfile.NamedTemporaryFile(prefix="jj-result-", suffix=".png", delete=False) as stream:
            path = Path(stream.name)
            stream.write(cv2.imencode(".png", area)[1].tobytes())
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(ROOT / "result_ocr.ps1"),
             "-ImagePath", str(path)], capture_output=True, timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if completed.returncode == 0:
            return classify_result(completed.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError, cv2.error):
        pass
    finally:
        if path:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return {}
