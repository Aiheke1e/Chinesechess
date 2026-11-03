"""Small, evidence-based views for the operator monitor; no control decisions."""
import time


PLAYER_STAGES = {
    "starting": "启动中", "board_recognition": "识别棋盘",
    "engine_starting": "启动引擎", "engine_thinking": "引擎思考",
    "waiting_opponent": "等待对手", "move_confirming": "核对落子",
    "inter_move_delay": "准备下一手", "sandbox_demo": "沙盘演示",
    "stopped": "已停止", "speech_waiting": "等待播报",
}


def player_view(snapshot, *, pid=None, running=False, stopped=False, waiting=False, now=None, warning_after=25):
    if stopped:
        return "已停止", "识别与落子已取消", "muted"
    if not running:
        return ("等待棋盘", "识别稳定后开始", "warn") if waiting else ("待机", "尚未开始对弈", "muted")
    if not snapshot or snapshot.get("pid") != pid:
        return "启动中", "等待当前进程心跳", "warn"
    if snapshot.get("stage") == "stopped":
        return "已停止", "等待任务退出", "muted"
    age = max(0, (time.time() if now is None else now) - snapshot["updated_at"])
    if age >= warning_after:
        return "心跳停滞", f"{age:.0f} 秒未更新，请查看运行记录", "error"
    label = PLAYER_STAGES.get(snapshot.get("stage"), "运行中")
    return label, f"心跳 {age:.0f} 秒前", "ok" if age < 5 else "warn"


def speech_view(enabled, activity, status=""):
    if not enabled:
        return "仅文本", "语音已关闭", "muted"
    if activity.get("suspended"):
        return "已暂停", "语音任务已取消", "muted"
    if activity.get("playing"):
        return "正在播报", f"排队 {activity.get('queued', 0)} 条", "ok"
    if "异常" in status or "失败" in status:
        return "播报异常", status.removeprefix("播报异常：")[:48], "error"
    if activity.get("preparing") or activity.get("working") or activity.get("queued"):
        return "准备播报", f"排队 {activity.get('queued', 0)} 条", "warn"
    return "待播", "等待新内容", "muted"


def live_view(state, online=None, rate=0):
    detail = f"在线 {online:,} 人" if isinstance(online, int) and online >= 0 else "在线人数待获取"
    if state == "connected":
        return "已连接", f"{detail} · 弹幕 {rate:g}/秒", "ok"
    if state == "connecting":
        return "连接中", "正在建立弹幕连接", "warn"
    if state == "error":
        return "连接异常", "请查看异常详情或重新连接", "error"
    if state == "ended":
        return "直播已结束", "等待重新开播", "muted"
    return "未连接", "到设置连接房间", "muted"


class MonitorIssues:
    def __init__(self):
        self.active = {}
        self.sequence = 0

    def set(self, source, text, level="error"):
        if not text:
            self.active.pop(source, None)
            return
        if self.active.get(source, ())[:2] == (text, level):
            return
        self.sequence += 1
        self.active[source] = (text, level, self.sequence)

    def clear(self, source):
        self.active.pop(source, None)

    def rows(self):
        ordered = sorted(self.active.items(), key=lambda item: (
            item[1][1] == "error", item[1][2]), reverse=True)
        return [(source, text, level) for source, (text, level, _) in ordered]
