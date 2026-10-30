# player.py — JJ象棋 AI 代打主控
# 交互: 点选式 (点棋子 -> 点目标格)
# 引擎: Pikafish
from __future__ import annotations
from collections import deque
import sys, os, time, random, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from board import Board
from adb_bridge import screenshot, list_devices
from recognizer import (board_change_scores, recognize_occupancy,
                        recognize_indices, recognize_position,
                        recognize_presence_occupancy)
from mover import move_piece_retry, pt
from state import (position_errors, resolve_fast_reply, resolve_one_legal_move,
                   resolve_legal_sequence_by_occupancy, same_position,
                   temporal_occupancy_consensus, validate_board,
                   verify_trusted_observation)
from events import EventLog
from diagnostics import capture_diagnostic
from notation import move_description
from session_state import (block_position, infer_turn_from_standard,
                           match_live_session, save_session)
from runtime_status import write_runtime_status
from queen_commentary import build_queen_evidence

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "player_config.json")
LIVE_CFG_PATH = os.path.join(HERE, "live_config.json")
SPEECH_PREPARING_PATH = os.path.join(HERE, "work", "speech_preparing.json")
SPEECH_STARTED_PATH = os.path.join(HERE, "work", "speech_started.json")
DEFAULT_CFG = {
    "my_color": "r",
    "orientation": "auto",
    "engine": "pikafish",
    "pikafish_movetime_ms": 1000,
    "move_interval_min": 1.5,
    "move_interval_max": 3.0,
    "opponent_timeout": 60,
    "max_moves": 200,
    "min_pieces": 20,
    "initial_turn": "r",
    "stable_delay": 0.35,
    "dry_run": False,
    "event_log": "work/events.jsonl",
    "auto_continue_after_game": True,
    "opponent_poll_interval": 0.25,
    "full_recognition_interval": 1.0,
}

CN = {"K":"帅","A":"仕","B":"相","N":"马","R":"车","C":"炮","P":"兵",
      "k":"将","a":"士","b":"象","n":"马","r":"车","c":"炮","p":"卒"}


def material_summary(board):
    order = "RNCBAPK"
    red = []
    black = []
    for upper in order:
        red_count = board.sq.count(upper)
        black_count = board.sq.count(upper.lower())
        if red_count:
            red.append(f"{CN[upper]}{red_count}")
        if black_count:
            black.append(f"{CN[upper.lower()]}{black_count}")
    return "红方：" + "、".join(red) + "；黑方：" + "、".join(black)


class EngineMoveError(RuntimeError):
    pass

def load_cfg():
    c = dict(DEFAULT_CFG)
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, encoding="utf-8") as config_file:
                c.update(json.load(config_file))
        except (OSError, json.JSONDecodeError) as error:
            print(f"[!] 配置文件读取失败，使用默认值: {error}")
    return c


def _read_speech_marker(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return {}


def wait_for_speech_start(token, plan_timeout=0.25, audio_timeout=2.8):
    """Wait only for a planned normal line, and stop as soon as audio starts."""
    try:
        with open(LIVE_CFG_PATH, encoding="utf-8") as stream:
            live = json.load(stream)
    except (OSError, ValueError):
        return False
    if not (live.get("commentary_enabled") and live.get("speech_enabled")):
        return False
    deadline = time.monotonic() + max(0.0, plan_timeout)
    plan = {}
    while time.monotonic() < deadline:
        plan = _read_speech_marker(SPEECH_PREPARING_PATH)
        if plan.get("token") == token:
            break
        time.sleep(0.02)
    if (plan.get("token") != token or not plan.get("enabled") or
            plan.get("deferred")):
        return False
    deadline = time.monotonic() + max(0.0, audio_timeout)
    while time.monotonic() < deadline:
        started = _read_speech_marker(SPEECH_STARTED_PATH)
        if started.get("token") == token:
            return True
        time.sleep(0.03)
    return False


def read_squares(device, orientation="auto"):
    img = screenshot(device)
    return (None, None) if img is None else recognize_position(img, orientation)


def read_occupancy(device, orientation):
    image = screenshot(device)
    return None if image is None else recognize_occupancy(image, orientation)


def resolve_move_from_image(board, image, orientation, tolerant_indices=()):
    """Resolve one opponent move from one screenshot without mixing frames."""
    if image is None:
        return None
    occupancy = recognize_occupancy(image, orientation)
    status, sequence, _ = verify_trusted_observation(
        board, occupancy, max_differences=2, max_plies=1)
    return sequence[0] if status == "recovered" and len(sequence) == 1 else None


def assess_image_against_board(board, image, orientation, occupancy_history=None):
    """Return move candidate plus consistency/reverse-validation details."""
    if image is None:
        return None, "screenshot_failed", 90
    occupancy = recognize_occupancy(image, orientation)
    if occupancy_history is not None:
        occupancy_history.append(occupancy)
        occupancy = temporal_occupancy_consensus(list(occupancy_history))
    status, sequence, differences = verify_trusted_observation(
        board, occupancy, max_differences=2, max_plies=1)
    move = (sequence[0] if status == "recovered" and
            sequence is not None and len(sequence) == 1 else None)
    if move is not None:
        frm, to = move
        moving = board.sq[frm]
        endpoints = recognize_indices(image, orientation, (frm, to))
        if (moving is not None and endpoints.get(frm) is None and
                endpoints.get(to) is not None and
                endpoints[to].isupper() == moving.isupper()):
            return move, "verified_recovered", differences
    if move is None and status in ("retry", "illegal"):
        # Last-move rings can hide several glyph templates at once.  Use the
        # presence-only detector only as a secondary proposal: one legal move
        # must explain it uniquely with at most one stray hint intersection.
        # The caller still requires the same proposal in two consecutive
        # frames before advancing the trusted board.
        presence = recognize_presence_occupancy(image, orientation)
        fallback = resolve_legal_sequence_by_occupancy(
            board, presence, max_plies=1, max_mismatches=1)
        if fallback is not None:
            if len(fallback) == 1:
                return fallback[0], "presence_fallback", differences
            if not fallback:
                return None, "unchanged", differences
    return move, status, differences


def resolve_move_from_change(board, reference, current, orientation,
                             minimum_score=8.0):
    """Resolve one unique legal move from its two visually changed endpoints."""
    scores = board_change_scores(reference, current, orientation)
    if not scores:
        return None, 90
    baseline = float(sorted(scores)[len(scores) // 2])
    threshold = max(float(minimum_score), baseline + 6.0)
    changed = sum(score >= threshold for score in scores)
    if changed < 2 or changed > 12:
        return None, changed
    ranked = []
    for move in board.legal_moves():
        frm, to = move
        weaker = min(scores[frm], scores[to])
        if weaker < threshold:
            continue
        ranked.append((scores[frm] + scores[to], weaker, move))
    if not ranked:
        return None, changed
    ranked.sort(reverse=True)
    best = ranked[0]
    if len(ranked) > 1:
        second = ranked[1]
        if best[0] - second[0] < max(4.0, best[0] * 0.08):
            return None, changed
    move = best[2]
    frm, to = move
    endpoints = recognize_indices(current, orientation, (frm, to))
    moving_piece = board.sq[frm]
    # Visual change alone can be a selection highlight.  A real completed move
    # must also leave the source empty and the destination occupied by the
    # moving side before it enters the two-frame stability gate.
    if (moving_piece is None or endpoints.get(frm) is not None or
            endpoints.get(to) is None or
            endpoints[to].isupper() != moving_piece.isupper()):
        return None, changed
    return move, changed


def stable_move_candidate(pending, current):
    """Accept a move only after two consecutive frames identify the same move.

    A sliding piece can temporarily pass over another legal destination during
    JJ's animation.  Treating that single frame as final corrupts the trusted
    board, so the first sighting is only a candidate.
    """
    if current is None:
        return None, None
    if pending == current:
        return current, None
    return None, current


def confirmed_move_candidate(pending, current, status):
    """Accept endpoint-proved frame changes immediately; stabilize fallbacks."""
    if current is not None and status in (
            "fast_change", "verified_recovered"):
        # resolve_move_from_change already proves a unique legal move plus an
        # empty source and a destination occupied by the moving side.  Waiting
        # for an identical second 1080p ADB screenshot adds ~1.5–2 seconds but
        # no new chess invariant.
        return current, None
    return stable_move_candidate(pending, current)


def postgame_image_visible(image):
    """Return true only when an image shows a trustworthy result control."""
    from postgame import detect_large_action_button, detect_modal_close
    return bool(image is not None and
                (detect_large_action_button(image) or detect_modal_close(image)))


def postgame_screen_visible(device, first_image=None, confirm_delay=0.3):
    """Require two consecutive result-screen frames before allowing clicks."""
    first = screenshot(device) if first_image is None else first_image
    if not postgame_image_visible(first):
        return False
    time.sleep(confirm_delay)
    return postgame_image_visible(screenshot(device))


def wait_for_postgame_screen(device, timeout=5.0, interval=0.5):
    """Allow JJ's surrender/result animation time to expose its controls."""
    deadline = time.monotonic() + timeout
    while True:
        if postgame_screen_visible(device):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def read_stable_board(device, turn, min_pieces=20, stable_delay=0.35,
                      orientation="auto"):
    """Only accept two identical, structurally possible recognition frames."""
    first, first_orientation = read_squares(device, orientation)
    if first is None:
        return None, None
    time.sleep(stable_delay)
    second, second_orientation = read_squares(device, orientation)
    if (second is None or first_orientation != second_orientation or
            not same_position(first, second)):
        print("[!] 连续两帧棋盘不一致，可能仍有动画或高亮")
        return None, None
    count = sum(p is not None for p in second)
    errors = position_errors(second)
    if count < min_pieces:
        print(f"[!] 只识别到 {count} 个棋子，低于最低要求 {min_pieces}")
        return None, None
    if errors:
        print("[!] 识别局面结构不可能: " + "; ".join(errors))
        return None, None
    board = Board(second, turn=turn)
    errors = validate_board(board)
    if errors:
        print("[!] 识别局面不合法: " + "; ".join(errors))
        return None, None
    return board, second_orientation

def pick_move(b, cfg, pf):
    if pf is None:
        raise EngineMoveError("Pikafish 未启动")
    try:
        uci = pf.bestmove(b.to_fen(), movetime_ms=cfg["pikafish_movetime_ms"])
        return ((Board.from_ucci(uci), f"Pikafish {uci}") if uci else
                (None, "Pikafish 未返回走法"))
    except Exception as error:
        print(f"[引擎错误] Pikafish 计算失败：{error}")
        raise EngineMoveError(str(error)) from error


def engine_commentary(board, analysis, max_pv_plies=6):
    """Return short factual engine-analysis lines and a spoken PV sequence."""
    if not analysis:
        return "暂无可用分析数据", []
    depth = analysis.get("depth", "?")
    seldepth = analysis.get("seldepth")
    nodes = analysis.get("nodes")
    elapsed = analysis.get("time", analysis.get("elapsed_ms"))
    if analysis.get("score_type") == "mate":
        mate = analysis.get("score", 0)
        score_text = f"预计{abs(mate)}步内{'我方将胜' if mate > 0 else '我方将负'}"
    elif analysis.get("score_type") == "cp":
        value = analysis.get("score", 0) / 100.0
        if value >= 1.5:
            judgment = "我方明显优势"
        elif value >= 0.35:
            judgment = "我方稍优"
        elif value <= -1.5:
            judgment = "我方明显劣势"
        elif value <= -0.35:
            judgment = "我方稍劣"
        else:
            judgment = "局面接近均势"
        score_text = f"{judgment}（{value:+.2f}）"
    else:
        score_text = "评分未知"
    details = [f"深度 {depth}" + (f"/{seldepth}" if seldepth else ""), score_text]
    if nodes is not None:
        details.append(f"节点 {nodes:,}")
    if elapsed is not None:
        details.append(f"用时 {elapsed} 毫秒")

    pv_text = []
    pv_ucci = []
    line_board = board.copy()
    for ucci in analysis.get("pv", [])[:max_pv_plies]:
        try:
            move = Board.from_ucci(ucci)
            if move not in line_board.legal_moves():
                break
            pv_text.append(move_description(line_board, move))
            pv_ucci.append(ucci)
            line_board.apply(move)
        except (ValueError, IndexError):
            break
    return "，".join(details), list(zip(pv_ucci, pv_text))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--engine", choices=["pikafish"], default=None)
    ap.add_argument("--movetime", type=int, default=None)
    ap.add_argument("--max-moves", type=int, default=None)
    ap.add_argument("--initial-turn", choices=["r", "b"], default=None,
                    help="接管残局时当前轮到红方(r)或黑方(b)")
    ap.add_argument("--min-pieces", type=int, default=None,
                    help="允许接管的最低棋子数")
    ap.add_argument("--stop-file", default=None,
                    help="存在此文件时安全停止（供控制面板使用）")
    args = ap.parse_args()

    cfg = load_cfg()
    if args.engine: cfg["engine"] = args.engine
    if args.movetime: cfg["pikafish_movetime_ms"] = args.movetime
    if args.max_moves: cfg["max_moves"] = args.max_moves
    if args.initial_turn: cfg["initial_turn"] = args.initial_turn
    if args.min_pieces is not None: cfg["min_pieces"] = args.min_pieces
    if args.dry: cfg["dry_run"] = True

    event_path = cfg.get("event_log")
    if event_path and not os.path.isabs(event_path):
        event_path = os.path.join(HERE, event_path)
    events = EventLog(event_path)

    def stop_requested():
        return bool(args.stop_file and os.path.exists(args.stop_file))

    write_runtime_status("starting", "正在连接模拟器")
    devs = list_devices()
    if not devs:
        print("[!] 无设备")
        events.emit("error", code="no_device", message="未找到 ADB 设备")
        return 3
    device = devs[0]
    my = cfg["my_color"]
    requested_orientation = cfg.get("orientation", "auto")
    color_label = "自动" if my == "auto" else ("红" if my == "r" else "黑")
    print("\n" + "=" * 54)
    print("             JJ 象棋自动对弈助手")
    print("=" * 54)
    print(f"启动模式：{'只读预览，不会点击棋盘' if cfg['dry_run'] else '自动对弈'}")
    print(f"连接设备：{device}")
    print(f"象棋引擎：{'Pikafish' if cfg['engine'] == 'pikafish' else '内置引擎'}")
    print(f"执棋方：{color_label}")
    events.emit("start", device=device, engine=cfg["engine"], dry_run=cfg["dry_run"])

    write_runtime_status("board_recognition", "正在识别可信棋盘")
    tolerant_indices = set()
    resumed = match_live_session(device)
    if resumed is not None:
        b = resumed["board"]
        orientation = resumed["orientation"]
        my = resumed["my_color"]
        cfg["my_color"] = my
        tolerant_indices = set(resumed.get("tolerant_indices", ()))
        inference_board = Board.from_fen(resumed["fen"])
        for inferred in resumed.get("inferred_moves", ()):
            text = move_description(inference_board, inferred)
            inference_board.apply(inferred)
            tolerant_indices.discard(inferred[0])
            tolerant_indices.add(inferred[1])
            print("[断点续局] 补入停止期间已发生的走法：" + text)
        print("[断点续局] 已恢复上次保存的可信棋局。")
    else:
        initial_turn = cfg.get("initial_turn", "r")
        b, orientation = read_stable_board(
            device, initial_turn, cfg["min_pieces"], cfg["stable_delay"],
            requested_orientation)
        if b is None:
            print("[!] 未识别到对局棋盘, 请先进入人机对局并停在棋盘界面")
            events.emit("error", code="board_not_found", message="未识别到稳定合法棋盘")
            return 2
        if my == "auto":
            my = "r" if orientation == "red_bottom" else "b"
            cfg["my_color"] = my
        inferred_turn = infer_turn_from_standard(b.sq)
        if inferred_turn is not None and inferred_turn != b.turn:
            b.turn = inferred_turn
            print("[轮次修正] 根据标准开局后的实际走法，当前轮到"
                  + ("红方。" if b.turn == "r" else "黑方。"))
    save_session(b, orientation, my, tolerant_indices)
    print("\n[棋盘识别成功]")
    print(f"棋子数量：{b.attacker_count()} 枚")
    print(f"我的阵营：{'红方' if my == 'r' else '黑方'}")
    print(f"棋盘方向：{'红方在下' if orientation == 'red_bottom' else '黑方在下'}")
    print("状态：等待开始走棋\n")
    events.emit("position", fen=b.to_fen(), orientation=orientation, my_color=my,
                pieces=b.attacker_count())

    write_runtime_status("engine_starting", "棋盘已确认，正在启动引擎")
    pf = None
    try:
        from pikafish import get_engine
        pf = get_engine()
        print("[引擎准备完成] Pikafish 已就绪\n")
    except Exception as error:
        print(f"[引擎启动失败] {error}")
        print("为避免使用不同棋力的备用引擎，本次任务已停止。")
        events.emit("error", code="pikafish_unavailable", message=str(error))
        return 4

    n = 0
    result = "stopped"
    # JJ 会给移动后的棋子换字形。这里保存这些可信占位，只放宽字形，
    # 仍严格检查颜色、空格和合法的一步转移。
    try:
        while n < cfg["max_moves"] and not stop_requested():
            if b.turn != my:
                write_runtime_status(
                    "waiting_opponent", "等待对方落子",
                    details={"own_moves": n, "fen": b.to_fen()})
                print("等待对方走棋……", flush=True)
                events.emit("waiting_for_opponent", fen=b.to_fen(),
                            after_move_number=n)
                got, waited, total_waited = False, 0.0, 0.0
                wait_started = time.monotonic()
                timeout_reported_at = 0.0
                pending_opponent_move = None
                anomaly_count = 0
                last_anomaly_report = 0.0
                last_anomaly_capture = 0.0
                last_wait_capture = 0.0
                occupancy_history = deque(maxlen=3)
                reference_image = screenshot(device)
                last_full_recognition = 0.0
                while not stop_requested():
                    write_runtime_status(
                        "waiting_opponent", "等待对方落子",
                        details={"own_moves": n,
                                 "waited_seconds": round(total_waited, 1)})
                    poll_interval = max(
                        0.1, float(cfg.get("opponent_poll_interval", 0.25)))
                    time.sleep(poll_interval)
                    image = screenshot(device)
                    total_waited = time.monotonic() - wait_started
                    waited = total_waited - timeout_reported_at
                    if postgame_screen_visible(device, image):
                        result = "finished"
                        print("[本局结束] 对方可能已投降，检测到 JJ 结算页面。")
                        events.emit("game_over", result=result,
                                    reason="jj_result_screen", fen=b.to_fen())
                        break
                    current_candidate, difference_count = resolve_move_from_change(
                        b, reference_image, image, orientation)
                    if current_candidate is not None:
                        check_status = "fast_change"
                    else:
                        now = time.monotonic()
                        full_interval = max(
                            0.5, float(cfg.get("full_recognition_interval", 1.0)))
                        if now - last_full_recognition < full_interval:
                            pending_opponent_move = None
                            continue
                        last_full_recognition = now
                        current_candidate, check_status, difference_count = (
                            assess_image_against_board(
                                b, image, orientation, occupancy_history))
                        if check_status == "unchanged":
                            reference_image = image
                    if check_status in ("retry", "illegal", "screenshot_failed"):
                        pending_opponent_move = None
                        anomaly_count += 1
                        now = time.monotonic()
                        if (anomaly_count == 3 or
                                now - last_anomaly_report >= 15.0):
                            if anomaly_count == 3:
                                try:
                                    from postgame import dismiss_rank_promotion
                                    promotion_closed = dismiss_rank_promotion(device)
                                except Exception:
                                    promotion_closed = False
                                if promotion_closed:
                                    result = "finished"
                                    print("[本局结束] 已识别并关闭段位提升页，准备进入下一局。",
                                          flush=True)
                                    events.emit(
                                        "game_over", result=result,
                                        reason="rank_promotion_screen",
                                        fen=b.to_fen())
                                    break
                            reason = {
                                "retry": "棋盘偏差超过2格，正在重新截图",
                                "illegal": "画面变化无法由合法走法反推",
                                "screenshot_failed": "本次截图失败",
                            }[check_status]
                            print(f"[识别告警] {reason}；已暂停落子，继续自动复核。",
                                  flush=True)
                            events.emit(
                                "recognition_warning", status=check_status,
                                differences=difference_count,
                                message=reason, fen=b.to_fen())
                            if now - last_anomaly_capture >= 60.0:
                                capture_diagnostic(
                                    device, "trusted_board_recheck", image=image,
                                    extra={"status": check_status,
                                           "differences": difference_count,
                                           "trusted_fen": b.to_fen()})
                                last_anomaly_capture = now
                            last_anomaly_report = now
                        if result == "finished":
                            break
                        continue
                    anomaly_count = 0
                    mv_seen, pending_opponent_move = confirmed_move_candidate(
                        pending_opponent_move, current_candidate, check_status)
                    if mv_seen is not None:
                        opponent_ucci = Board.ucci(mv_seen)
                        opponent_text = move_description(b, mv_seen)
                        print(f"对方回应：{opponent_text}"
                              f"  （{total_waited:.1f} 秒，坐标 {opponent_ucci}）")
                        b.apply(mv_seen)
                        tolerant_indices.discard(mv_seen[0])
                        tolerant_indices.add(mv_seen[1])
                        save_session(b, orientation, my, tolerant_indices)
                        events.emit("opponent_move", ucci=opponent_ucci,
                                    notation=opponent_text,
                                    waited_seconds=round(total_waited, 1),
                                    recognition=check_status, fen=b.to_fen())
                        if not b.legal_moves():
                            result = "lost"
                            print("[!] 我方无合法着法，本局失败")
                            events.emit("game_over", result=result, reason="no_legal_moves",
                                        fen=b.to_fen())
                        got = True
                        break
                    if waited >= cfg["opponent_timeout"]:
                        print("[持续监听] 暂未发现可信走法；程序不退出，继续等待画面恢复。",
                              flush=True)
                        events.emit(
                            "recognition_waiting",
                            waited_seconds=round(total_waited, 1),
                            fen=b.to_fen())
                        now = time.monotonic()
                        if (last_wait_capture == 0.0 or
                                now - last_wait_capture >= 300.0):
                            capture_diagnostic(
                                device, "opponent_waiting_recovery", image=image,
                                extra={"waited_seconds": round(total_waited, 1),
                                       "trusted_fen": b.to_fen()})
                            last_wait_capture = now
                        timeout_reported_at = total_waited
                if stop_requested():
                    break
                if result == "finished":
                    break
                if not got and not stop_requested():
                    if wait_for_postgame_screen(device):
                        result = "finished"
                        print("[本局结束] 等待期间检测到延迟出现的结算页面。")
                        events.emit("game_over", result=result,
                                    reason="delayed_jj_result_screen", fen=b.to_fen())
                    break
                if result == "lost":
                    break
                if b.turn != my: continue

            write_runtime_status(
                "engine_thinking", "Pikafish 正在计算走法",
                details={"own_moves": n, "fen": b.to_fen()})
            try:
                mv, desc = pick_move(b, cfg, pf)
            except EngineMoveError as error:
                result = "recognition_pause"
                print("[识别暂停] 引擎没有接受当前局面；保留可信断点并自动重抓。")
                print("诊断信息：" + str(error))
                events.emit("error", code="engine_search_failed",
                            message=str(error), fen=b.to_fen())
                break
            if stop_requested():
                break
            if not mv:
                if not b.legal_moves():
                    result = "lost"
                    print("[!] 我方确实没有合法着法，本局失败")
                else:
                    result = "recognition_pause"
                    print("[识别暂停] 引擎未返回走法；保留断点并自动重抓。")
                break
            legal = b.legal_moves()
            if mv not in legal:
                result = "recognition_pause"
                print(f"    [!] 引擎返回非法着法 {Board.ucci(mv)}，暂停并自动复核局面")
                break

            u = Board.ucci(mv)
            d = move_description(b, mv)
            analysis = dict(getattr(pf, "last_analysis", {}) or {})
            analysis_summary, pv_line = engine_commentary(b, analysis)
            candidate_analyses = list(
                getattr(pf, "last_candidates", [])[:3])
            candidate_plans = []
            for rank, candidate in enumerate(candidate_analyses, start=1):
                candidate_summary, candidate_line = engine_commentary(
                    b, dict(candidate))
                if not candidate_line:
                    continue
                candidate_plans.append({
                    "rank": rank,
                    "move": candidate_line[0][1],
                    "assessment": candidate_summary,
                    "line": [text for _, text in candidate_line[:3]],
                })
            queen_evidence = build_queen_evidence(
                b, mv, candidate_analyses)
            print("-" * 54)
            print(f"第 {n + 1} 回合｜我方走棋：{d}", flush=True)
            print(f"技术信息：{desc}，坐标 {u}")
            print("[引擎分析] " + analysis_summary)
            if candidate_plans:
                print("[候选思路] " + " ｜ ".join(
                    f"方案{item['rank']}：{item['move']}"
                    for item in candidate_plans))
            if pv_line:
                print("[预测路线] " + " → ".join(text for _, text in pv_line))
            try:
                from game_sandbox import build_plan as build_sandbox_plan
                sandbox_plan = build_sandbox_plan(
                    d, analysis, pv_line, u, board=b)
            except Exception:
                sandbox_plan = None

            from_xy = tuple(round(value) for value in pt(mv[0], orientation))
            to_xy = tuple(round(value) for value in pt(mv[1], orientation))
            print(f"[执行指令] 点击起点 {mv[0]}@{from_xy}，再点击终点 "
                  f"{mv[1]}@{to_xy}；执行 {d}")
            speech_token = f"{os.getpid()}-{n + 1}-{time.time_ns()}"
            events.emit("move_selected", number=n + 1, ucci=u, notation=d,
                        engine=desc, fen_before=b.to_fen(),
                        speech_token=speech_token)
            events.emit("engine_analysis", number=n + 1, ucci=u,
                        notation=d, side=b.turn,
                        fen_before=b.to_fen(),
                        material=material_summary(b),
                        summary=analysis_summary, analysis=analysis,
                        candidates=candidate_plans,
                        queen_evidence=queen_evidence,
                        speech_token=speech_token,
                        pv=[{"ucci": move_ucci, "notation": text}
                            for move_ucci, text in pv_line])
            print("[语音同步] 落子与短解说预生成已并行启动；"
                  "将在点击棋子前开始播放。", flush=True)
            events.emit("move_command", number=n + 1, ucci=u, notation=d,
                        speech_token=speech_token,
                        from_index=mv[0], to_index=mv[1],
                        from_screen=from_xy, to_screen=to_xy,
                        orientation=orientation)
            if wait_for_speech_start(speech_token):
                print("[语音同步] 解说已起声，现在执行棋盘点击。", flush=True)

            confirmation_seconds = 0.0
            if cfg["dry_run"]:
                print("预览完成：只读模式未点击棋盘")
                print(f"点击位置：{pt(mv[0], orientation)} → {pt(mv[1], orientation)}")
                ok = True
            else:
                write_runtime_status(
                    "move_confirming", "已发送落子，正在核对棋盘",
                    details={"own_moves": n + 1, "ucci": u})
                confirmation_started = time.monotonic()
                ok, msg = move_piece_retry(
                    mv[0], mv[1], device, expected_before=b.sq,
                    orientation=orientation,
                    tolerant_indices=tolerant_indices)
                confirmation_seconds = time.monotonic() - confirmation_started
                if ok:
                    mode = "快速确认" if "局部快速校验" in msg else "完整确认"
                    print(f"落子确认：成功（{mode} {confirmation_seconds:.1f} 秒）。",
                          flush=True)
                else:
                    print("落子确认：暂未看到预期局面，正在做快速回应检查。",
                          flush=True)

            fast_reply = None
            if not ok and not cfg["dry_run"]:
                observed, seen_orientation = read_squares(device, orientation)
                if observed is not None and seen_orientation == orientation:
                    fast_reply = resolve_fast_reply(
                        b, mv, observed, tolerant_indices)
                if fast_reply is None:
                    recovery_image = screenshot(device)
                    occupancy = (None if recovery_image is None else
                                 recognize_occupancy(
                                     recovery_image, orientation))
                    after_ours = b.copy()
                    after_ours.apply(mv)
                    sequence = (None if occupancy is None else
                                resolve_legal_sequence_by_occupancy(
                                    after_ours, occupancy, max_plies=1))
                    if sequence is None and recovery_image is not None:
                        presence = recognize_presence_occupancy(
                            recovery_image, orientation)
                        sequence = resolve_legal_sequence_by_occupancy(
                            after_ours, presence, max_plies=1,
                            max_mismatches=1)
                    if sequence:
                        fast_reply = sequence[0]
                if fast_reply is not None:
                    ok = True
                    after_ours = b.copy()
                    after_ours.apply(mv)
                    reply_text = move_description(after_ours, fast_reply)
                    print("落子确认成功；对方回应很快，已同时识别："
                          + reply_text + f"（{Board.ucci(fast_reply)}）", flush=True)
            if not ok:
                events.emit("move_rejected", number=n + 1, ucci=u,
                            notation=d, speech_token=speech_token,
                            reason=msg)
                # 将死后 JJ 可能依次播放棋盘特效、段位动画，再显示结算按钮。
                # 只在落子无法确认的异常路径多等待，不增加正常每步用时。
                if wait_for_postgame_screen(device, timeout=18.0):
                    result = "finished"
                    print("[本局结束] 最后一步后已进入 JJ 结算页面，准备进入下一局。")
                    events.emit("game_over", result=result,
                                reason="jj_result_screen", fen=b.to_fen())
                else:
                    # A precondition failure explicitly means mover did not
                    # send either tap.  Keep the trusted session active so the
                    # watcher can re-read a stable frame and safely resume.
                    # Only lock the position when input may already have been
                    # sent and its result cannot be established.
                    if "未发送点击" in msg or "落子前截图失败" in msg:
                        result = "recognition_pause"
                        print("[识别暂停] 落子前画面暂不稳定，本次没有点击棋盘；"
                              "保留断点并等待自动恢复。")
                    else:
                        # Input may have been sent, but the trusted pre-move
                        # board is still the correct recovery anchor.  Keep it
                        # active: the watcher will reconcile the live red/black
                        # occupancy through legal moves before trying again.
                        result = "recovery_pause"
                        block_position(b, msg)
                        print("[恢复等待] 本次点击结果暂未确认；已保留落子前断点，"
                              "稍后将按合法局面自动恢复。")
                    print("诊断信息：" + msg)
                break
            b.apply(mv)
            tolerant_indices.discard(mv[0])
            tolerant_indices.add(mv[1])
            save_session(b, orientation, my, tolerant_indices)
            n += 1
            events.emit("move_applied" if not cfg["dry_run"] else "move_preview",
                        number=n, ucci=u, notation=d, fen=b.to_fen(),
                        speech_token=speech_token,
                        confirmation_seconds=round(confirmation_seconds, 3))
            if not cfg["dry_run"] and not b.legal_moves():
                result = "won"
                print("\n【本局获胜】对方已经没有任何合法走法。")
                events.emit("game_over", result=result, reason="no_legal_moves",
                            fen=b.to_fen())
                break
            if (not cfg["dry_run"] and sandbox_plan and
                    fast_reply is None and not stop_requested()):
                try:
                    from game_sandbox import enabled as sandbox_enabled
                    from game_sandbox import play_plan as play_sandbox_plan
                    if sandbox_enabled():
                        route = sandbox_plan["route"]
                        print(f"[游戏沙盘] 我方落子已确认；等待对方期间演示"
                              f"{len(route)}步路线。", flush=True)
                        events.emit(
                            "game_sandbox", action="start",
                            reason=sandbox_plan["reason"], route=route,
                            deferred=False, fen=b.to_fen(),
                            speech_token=speech_token)
                        write_runtime_status(
                            "sandbox_demo", "等待对方期间演示关键路线",
                            details={"own_moves": n, "steps": len(route),
                                     "opponent_interrupts": False})

                        def report_sandbox_step(number, item):
                            print(f"[游戏沙盘] {number}. {item['notation']}",
                                  flush=True)
                            events.emit(
                                "game_sandbox", action="step", number=number,
                                notation=item["notation"], ucci=item["ucci"])

                        def report_sandbox_entered():
                            events.emit(
                                "game_sandbox", action="entered",
                                reason=sandbox_plan["reason"], route=route,
                                speech_token=speech_token, fen=b.to_fen())

                        sandbox_result = play_sandbox_plan(
                            device, b, orientation, sandbox_plan,
                            stop_requested=stop_requested,
                            on_step=report_sandbox_step,
                            on_enter=report_sandbox_entered,
                            max_seconds=8.0)
                        events.emit(
                            "game_sandbox", action="end",
                            speech_token=speech_token, **sandbox_result)
                        if sandbox_result.get("entered"):
                            print(f"[游戏沙盘] {sandbox_result.get('reason')}；"
                                  f"已演示{sandbox_result.get('completed', 0)}步。"
                                  " 对方实战落子不会中断沙盘。", flush=True)
                        else:
                            print("[游戏沙盘] 本次未进入：" +
                                  str(sandbox_result.get(
                                      "reason", "界面未确认")), flush=True)
                        if not sandbox_result.get("safe_exit", True):
                            result = "recognition_pause"
                            print("[游戏沙盘] 无法确认已返回实战，为防止误点已暂停。",
                                  flush=True)
                            capture_diagnostic(
                                device, "sandbox_exit_unconfirmed",
                                extra={"fen": b.to_fen(),
                                       "sandbox_result": sandbox_result})
                            break
                except Exception as error:
                    print(f"[游戏沙盘] 演示异常，已跳过：{error}", flush=True)
                    capture_diagnostic(
                        device, "sandbox_demo_error",
                        extra={"error": str(error), "fen": b.to_fen()})
            if fast_reply is not None:
                b.apply(fast_reply)
                tolerant_indices.discard(fast_reply[0])
                tolerant_indices.add(fast_reply[1])
                save_session(b, orientation, my, tolerant_indices)
                reply_ucci = Board.ucci(fast_reply)
                events.emit("opponent_move", ucci=reply_ucci,
                            notation=reply_text,
                            waited_seconds=0.0, fast_reply=True, fen=b.to_fen())
                if not b.legal_moves():
                    result = "lost"
                    print("[!] 对方快速回应后我方无合法着法，本局失败")
                    events.emit("game_over", result=result,
                                reason="no_legal_moves", fen=b.to_fen())
                    break
            # 只读模式从不伪造连续对局；预览一手后立即返回。
            if args.once or cfg["dry_run"]: break
            delay = random.uniform(cfg["move_interval_min"], cfg["move_interval_max"])
            write_runtime_status(
                "inter_move_delay", "本回合已确认",
                details={"own_moves": n, "delay_seconds": round(delay, 2)})
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline and not stop_requested():
                time.sleep(min(0.2, deadline - time.monotonic()))
    except KeyboardInterrupt:
        print("[*] 收到停止信号")
    finally:
        write_runtime_status(
            "stopped", "对弈子进程已结束",
            details={"result": result, "own_moves": n})
        result_text = {
            "won": "获胜", "lost": "失败", "timeout": "等待超时",
            "stopped": "已停止", "finished": "JJ 已结算",
            "engine_error": "引擎异常，已安全停止",
            "unsafe_position": "落子未确认，已锁定当前局面",
            "recognition_pause": "识别暂不稳定，断点已保留",
            "recovery_pause": "落子结果待恢复，断点已保留",
        }.get(result, result)
        print("\n" + "=" * 54)
        print(f"对局结束：{result_text}｜我方共走 {n} 步")
        print("=" * 54)
        events.emit("end", result=result, own_moves=n, fen=b.to_fen() if b else None)
        save_session(b, orientation, my, tolerant_indices,
                     active=result not in ("won", "lost", "finished",
                                           "engine_error", "unsafe_position"))
        continued_actions = []
        if result in ("timeout", "engine_error", "unsafe_position",
                      "recognition_pause", "recovery_pause"):
            diagnostic = capture_diagnostic(
                device, "player_" + result,
                extra={"result": result, "own_moves": n,
                       "fen": b.to_fen() if b else None})
            if diagnostic:
                print("[故障截图] 已保存现场：" + diagnostic)
        if (result in ("won", "lost", "finished") and not cfg["dry_run"] and
                cfg.get("auto_continue_after_game", True)):
            print("[结算处理] 正在等待“继续/再来一局”按钮……")
            try:
                from postgame import handle_postgame
                continued_actions = handle_postgame(device)
            except Exception as error:
                print(f"[结算处理] 自动点击异常：{error}")
                diagnostic = capture_diagnostic(
                    device, "postgame_handler_error",
                    extra={"error": str(error), "result": result})
                if diagnostic:
                    print("[故障截图] 已保存现场：" + diagnostic)
            if continued_actions:
                print("[结算处理] 已点击：" + " → ".join(continued_actions))
                events.emit("postgame_continue", actions=continued_actions)
            else:
                print("[结算处理] 未找到可信的继续按钮，没有执行点击。")
                diagnostic = capture_diagnostic(
                    device, "postgame_continue_not_found",
                    extra={"result": result, "own_moves": n})
                if diagnostic:
                    print("[故障截图] 已保存现场：" + diagnostic)
        if pf:
            pf.quit()
    if result == "engine_error":
        return 12
    if result == "unsafe_position":
        return 13
    if result == "recognition_pause":
        return 14
    if result == "recovery_pause":
        return 14
    return 10 if continued_actions else 0

if __name__ == "__main__":
    raise SystemExit(main())
