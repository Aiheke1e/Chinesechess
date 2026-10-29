"""Compact human-facing console formatting.

Raw player output remains in work/control_panel.log for diagnostics.  This
module only decides what the main UI should show.
"""
from __future__ import annotations

import re


_HIDDEN_PREFIXES = (
    "技术信息：", "[候选思路]", "[预测路线]", "[语音同步]",
)


def compact_player_line(text):
    stripped = text.strip()
    if not stripped or set(stripped) <= {"=", "-"}:
        return None
    if stripped == "JJ 象棋自动对弈助手":
        return None
    if stripped.startswith(_HIDDEN_PREFIXES):
        return None
    if stripped.startswith("[执行指令]"):
        action = stripped.rsplit("；执行 ", 1)
        return f"执行 · {action[-1]}\n" if len(action) == 2 else None
    if stripped.startswith("[引擎分析]"):
        body = stripped[len("[引擎分析]"):].strip()
        parts = [item.strip() for item in body.split("，")]
        useful = [item for item in parts
                  if ("优势" in item or "劣势" in item or "均势" in item or
                      "将胜" in item or "将负" in item)]
        return "局势 · " + "，".join(useful or parts[:2]) + "\n"
    match = re.match(r"第\s*(\d+)\s*回合｜我方走棋：(.*)", stripped)
    if match:
        return f"\n第{match.group(1)}手 · {match.group(2)}\n"
    if stripped.startswith("对方回应："):
        body = stripped[len("对方回应："):]
        elapsed = re.search(r"（([0-9.]+) 秒，坐标", body)
        body = re.sub(r"\s*（[^）]*坐标[^）]*）\s*$", "", body)
        suffix = f" · {elapsed.group(1)}秒" if elapsed else ""
        return f"对方 · {body.strip()}{suffix}\n"
    if stripped == "等待对方走棋……":
        return "等待对方…\n"
    if stripped.startswith("落子确认：成功"):
        detail = re.search(r"（([^）]+)）", stripped)
        suffix = f" · {detail.group(1)}" if detail else ""
        return f"✓ 落子已确认{suffix}\n"
    if stripped.startswith("落子确认成功；"):
        return "✓ 落子及快速应手已确认\n"
    if stripped.startswith("落子确认：暂未"):
        return "… 正在复核落子\n"
    if stripped == "[棋盘识别成功]":
        return "✓ 棋盘识别成功\n"
    if stripped.startswith("[识别告警]"):
        return "⚠ " + stripped[len("[识别告警]"):].strip() + "\n"
    return stripped + "\n"


def styled_player_line(text):
    """Return styled text fragments for the on-screen chess transcript."""
    stripped = text.strip()
    move = re.match(r"第\s*(\d+)\s*回合｜我方走棋：(.*)", stripped)
    if move:
        notation = move.group(2).strip()
        side_tag = "move_red" if "红方" in notation else "move_black"
        return [
            ("\n" + "┈" * 44 + "\n", "separator"),
            (f"第 {move.group(1)} 手  ", "move_number"),
            (notation + "\n", side_tag),
        ]
    shown = compact_player_line(text)
    if not shown:
        return None
    if shown.startswith("对方 ·"):
        tag = "reply_red" if "红方" in shown else "reply_black"
    elif shown.startswith("局势 ·"):
        tag = "analysis"
    elif shown.startswith("执行 ·"):
        tag = "action"
    elif shown.startswith("✓"):
        tag = "success"
    elif shown.startswith("等待"):
        tag = "waiting"
    elif shown.startswith("⚠") or "失败" in shown or "异常" in shown:
        tag = "warning"
    elif "[游戏沙盘]" in shown:
        tag = "sandbox"
    else:
        tag = "normal"
    return [(shown, tag)]
