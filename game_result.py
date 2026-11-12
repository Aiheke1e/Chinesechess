"""Verify JJ terminal animation evidence and explicit result-page causes."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
from functools import lru_cache

import cv2

ROOT = Path(__file__).resolve().parent


def mating_move_after(squares, move):
    """Only a legal move leaving the opponent in actual checkmate qualifies."""
    from board import Board
    try:
        piece = squares[move[0]]
        if not piece or (squares[move[1]] or "").upper() == "K":
            return None
        board = Board(list(squares), "r" if piece.isupper() else "b")
        if board.king_pos("r") < 0 or board.king_pos("b") < 0 or move not in board.legal_moves():
            return None
        board.apply(move)
        return board if board.in_check(board.turn) and not board.legal_moves() else None
    except (ValueError, IndexError, TypeError):
        return None


@lru_cache(maxsize=1)
def _mate_banner_template():
    return cv2.imread(str(ROOT / "assets/jj-mate-banner-mask.png"), cv2.IMREAD_GRAYSCALE)


def mating_animation_confirms_move(image, squares, move, orientation):
    """Read JJ's live mate animation AND verify the moved piece and both kings.

    A planned mate or a banner alone proves nothing. The image must show the
    empty source, actual moved piece at the destination and both unchanged
    kings, plus two board anchors, despite the dark celebration overlay.
    """
    if (image is None or getattr(image, "ndim", 0) != 3 or image.shape[0] < 800 or
            not .5 <= image.shape[1] / image.shape[0] <= .6):
        return False
    board = mating_move_after(squares, move)
    if board is None:
        return False
    template = _mate_banner_template()
    if template is None:
        return False
    resized = cv2.resize(image, (1080, 1920))
    mask = cv2.inRange(cv2.cvtColor(resized, cv2.COLOR_BGR2HSV), (12, 90, 100), (40, 255, 255))
    mask = cv2.resize(mask[760:1270, 180:950], (256, 170), interpolation=cv2.INTER_AREA)
    if float(cv2.matchTemplate(mask, template, cv2.TM_CCORR_NORMED)[0, 0]) < .92:
        return False
    from recognizer import recognize_highlight_indices
    frm, to = move
    kings = [board.king_pos("r"), board.king_pos("b")]
    indices = list(dict.fromkeys([frm, to, *kings]))
    anchors = [i for i, piece in enumerate(board.sq) if piece and i not in indices and
               (i // 9 <= 2 or i // 9 >= 8)]
    indices += anchors
    # Hue is contaminated by JJ's red/gold flashes. The glyph recognizer
    # requires decisive independent shape evidence before overriding hue.
    observed = recognize_highlight_indices(cv2.convertScaleAbs(resized, alpha=2), orientation, indices)
    if (observed.get(frm) is not None or observed.get(to) != squares[frm] or
            any(observed.get(i) != board.sq[i] for i in kings)):
        return False
    conflicts = [i for i in anchors if observed.get(i) is not None and
                 observed[i].isupper() != board.sq[i].isupper()]
    matches = sum(observed.get(i) is not None and observed[i].isupper() == board.sq[i].isupper()
                  for i in anchors)
    return not conflicts and matches >= 2


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
