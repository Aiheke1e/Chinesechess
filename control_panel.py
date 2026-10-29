from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from adb_bridge import (adb, current_package, list_devices, resolution,
                        screenshot, tap)
from board import Board, START_FEN
from diagnostics import DIAGNOSTIC_DIR, capture_diagnostic
from recognizer import recognize_position
from state import position_errors, validate_board
from session_state import (blocked_position_matches, clear_blocked_position,
                           infer_turn_from_standard, match_live_session)
from cache_cleanup import cleanup_generated_cache, scan_generated_cache
from bailian_tts import available_voices
from live_host import LiveController, load_settings
from health_check import collect_readiness, readiness_level
from runtime_status import (heartbeat_age, read_runtime_status, rotate_log,
                            write_runtime_status)
from console_format import styled_player_line

HERE = os.path.dirname(os.path.abspath(__file__))
STOP_FILE = os.path.join(HERE, "work", "stop.request")
PANEL_LOG = os.path.join(HERE, "work", "control_panel.log")
ASSISTANT_CFG = os.path.join(HERE, "assistant_config.json")
UI_CFG = os.path.join(HERE, "work", "ui_settings.json")
DEFAULT_ASSISTANT_CFG = {
    "emulator_path": r"D:\software\leidian\LDPlayer9\dnplayer.exe",
    "jj_package": "cn.jj.chess",
    "device_timeout_seconds": 120,
    "allow_endgame": True,
    "endgame_min_pieces": 4,
    "ambiguous_turn_default": "r",
    "diagnostic_wait_seconds": 15,
    "diagnostic_interval_seconds": 300,
    "watchdog_warning_seconds": 25,
    "watchdog_recovery_seconds": 75,
    "match_mode": "15分钟",
}

WATCHDOG_SAFE_STAGES = {
    "starting", "board_recognition", "waiting_opponent", "inter_move_delay",
}
RUNTIME_STAGE_LABELS = {
    "starting": "启动中", "board_recognition": "识别棋盘",
    "engine_starting": "启动引擎", "engine_thinking": "引擎思考",
    "waiting_opponent": "等待落子", "move_confirming": "确认落子",
    "inter_move_delay": "回合间隔", "sandbox_demo": "游戏沙盘演示",
    "stopped": "已停止",
}

TURN_LABEL_TO_CODE = {"自动判断": "auto", "红方": "r", "黑方": "b"}

THEMES = {
    "青瓷清风": {
        "bg": "#eaf3f1", "panel": "#ffffff", "panel2": "#e5f0ed",
        "border": "#c7dcd6", "text": "#173832", "muted": "#68817c",
        "jade": "#15977a", "jade2": "#d8eee8", "gold": "#c8892f",
        "red": "#d85461", "blue": "#347fba", "purple": "#806bc0",
        "console": "#f6faf9", "console_text": "#203c37",
        "editor": "#f2f8f6", "select": "#bfe2d9",
        "button": "#dbeae6", "button_active": "#c9dfd9",
        "ok_bg": "#d9f1e9", "ok_fg": "#13765e",
        "wait_bg": "#f8edcf", "wait_fg": "#90631d",
        "error_bg": "#f7dfe2", "error_fg": "#a33d49",
    },
    "深海极光": {
        "bg": "#07111f", "panel": "#0d1b2a", "panel2": "#12243a",
        "border": "#20364f", "text": "#e8f0f7", "muted": "#8fa6bc",
        "jade": "#20c997", "jade2": "#123d36", "gold": "#f5b942",
        "red": "#ef6a6a", "blue": "#5ca9ff", "purple": "#a78bfa",
        "console": "#081523", "console_text": "#dce8f3",
        "editor": "#0a1726", "select": "#23594c",
        "button": "#23364d", "button_active": "#304a68",
        "ok_bg": "#16362f", "ok_fg": "#8ff0cd",
        "wait_bg": "#4a3a17", "wait_fg": "#ffd678",
        "error_bg": "#4a222a", "error_fg": "#ff9c9c",
    },
    "墨玉流金": {
        "bg": "#101713", "panel": "#17221c", "panel2": "#203027",
        "border": "#34483b", "text": "#f0eee4", "muted": "#a5ad9f",
        "jade": "#55b78a", "jade2": "#244534", "gold": "#ddb35d",
        "red": "#db6f72", "blue": "#6fa8c9", "purple": "#a892c7",
        "console": "#0f1813", "console_text": "#e3e6dc",
        "editor": "#142019", "select": "#365b45",
        "button": "#2a3d31", "button_active": "#395342",
        "ok_bg": "#244534", "ok_fg": "#85d6aa",
        "wait_bg": "#493d22", "wait_fg": "#efd080",
        "error_bg": "#4b292c", "error_fg": "#f2a0a2",
    },
}
THEME = dict(THEMES["青瓷清风"])


def load_ui_settings():
    defaults = {"theme": "青瓷清风", "opacity": 96}
    try:
        with open(UI_CFG, encoding="utf-8") as stream:
            saved = json.load(stream)
        if saved.get("theme") in THEMES:
            defaults["theme"] = saved["theme"]
        defaults["opacity"] = max(72, min(100, int(saved.get("opacity", 96))))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return defaults


def valid_turns(squares):
    """Return turns that make a recognized position structurally legal."""
    if squares is None or position_errors(squares):
        return []
    result = []
    for turn in ("r", "b"):
        board = Board(squares, turn=turn)
        if not validate_board(board):
            result.append(turn)
    return result


class ControlPanel:
    def __init__(self, root, autostart=False):
        self.root = root
        ui = load_ui_settings()
        THEME.clear()
        THEME.update(THEMES[ui["theme"]])
        self.theme_name = tk.StringVar(value=ui["theme"])
        self.opacity = tk.IntVar(value=ui["opacity"])
        self.current_palette = dict(THEME)
        self.process = None
        self.output = queue.Queue()
        self.status = tk.StringVar(value="正在检查设备…")
        self.dry = tk.BooleanVar(value=True)
        self.continuous = tk.BooleanVar(value=True)
        self.movetime = tk.StringVar(value="800")
        self.max_moves = tk.StringVar(value="150")
        self.turn_choice = tk.StringVar(value="自动判断")
        self.auto_retry = autostart
        self.waiting_for_board = False
        self.closing = False
        self.live_controller = None
        live = load_settings()
        self.live_room = tk.StringVar(value=live.room)
        self.live_model = tk.StringVar(value=live.model)
        self.live_voice = tk.StringVar(value=live.voice)
        self.live_density = tk.StringVar(value=live.commentary_density)
        self.live_prefix = tk.StringVar(value=live.prefix)
        self.live_cooldown = tk.StringVar(value=str(live.cooldown))
        self.live_user = tk.StringVar(value="测试观众")
        self.live_test_text = tk.StringVar(value="问主播 这步棋为什么这样走？")
        self.live_welcome = tk.BooleanVar(value=live.welcome_enabled)
        self.live_gifts = tk.BooleanVar(value=live.gift_enabled)
        self.live_speech = tk.BooleanVar(value=live.speech_enabled)
        self.live_commentary = tk.BooleanVar(value=live.commentary_enabled)
        self.live_sandtable = tk.BooleanVar(value=live.sandtable_enabled)
        self.live_queen_commentary = tk.BooleanVar(
            value=live.queen_commentary_enabled)
        self.live_status = tk.StringVar(value="互动未开启")
        self.broadcaster_open = False
        self.speech_status = tk.StringVar(
            value="语音已开启" if live.speech_enabled else "仅文本模式")
        self.device_state = tk.StringVar(value="检测中")
        self.board_state = tk.StringVar(value="等待棋盘")
        self.voice_state = tk.StringVar(
            value="已开启" if live.speech_enabled else "仅文本")
        self.stream_state = tk.StringVar(value="未连接")
        self.runtime_stats = tk.StringVar(value="识别告警 0 · 成功落子 0 · 完成对局 0")
        self.sandtable_title = tk.StringVar(value="战术沙盘")
        self.sandtable_status = tk.StringVar(value="")
        self.sandtable_jobs = []
        self.sandtable_close_job = None
        self.warning_count = 0
        self.move_success_count = 0
        self.game_count = 0
        self.watchdog_reported_at = 0.0
        self.watchdog_recovery_started = False
        self.indicators = {}
        root.title("JJ 象棋直播助手")
        root.geometry("1180x820")
        root.minsize(1080, 700)
        root.configure(bg=THEME["bg"])
        try:
            root.attributes("-alpha", ui["opacity"] / 100)
        except tk.TclError:
            pass
        self._build()
        self.status.trace_add("write", self._sync_status_badges)
        self.speech_status.trace_add("write", self._sync_live_badges)
        self.live_status.trace_add("write", self._sync_live_badges)
        try:
            self.live_controller = LiveController(
                lambda record: self.output.put(("live_event", record)))
            self.append_live("系统", "棋局台词会自动显示；开启互动后可接收弹幕。")
            if self.live_speech.get():
                self.live_controller.enable()
                self.live_status.set("直播讲解运行中")
        except Exception as error:
            self.append_live("错误", "直播互动模块初始化失败：" + str(error))
        self.refresh_device()
        root.after(80, self._poll)
        root.after(1500, self._watchdog_tick)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if autostart:
            root.after(400, self.prepare_and_wait)

    def _build(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=font, background=THEME["bg"],
                        foreground=THEME["text"])
        style.configure("App.TFrame", background=THEME["bg"])
        style.configure("Card.TFrame", background=THEME["panel"])
        style.configure("TLabel", background=THEME["bg"], foreground=THEME["text"])
        style.configure("Card.TLabel", background=THEME["panel"], foreground=THEME["text"])
        style.configure("Muted.TLabel", background=THEME["panel"], foreground=THEME["muted"])
        style.configure("Section.TLabel", background=THEME["panel"],
                        foreground=THEME["text"], font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TEntry", fieldbackground=THEME["panel2"], foreground=THEME["text"],
                        insertcolor=THEME["text"], bordercolor=THEME["border"], padding=6)
        style.configure("TCombobox", fieldbackground=THEME["panel2"],
                        foreground=THEME["text"], arrowcolor=THEME["text"], padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", THEME["panel2"])],
                  foreground=[("readonly", THEME["text"])])
        style.configure("TCheckbutton", background=THEME["panel"],
                        foreground=THEME["text"], indicatorcolor=THEME["panel2"])
        style.map("TCheckbutton", indicatorcolor=[("selected", THEME["jade"])],
                  foreground=[("active", THEME["text"])],
                  background=[("active", THEME["panel"])])
        style.configure("TButton", background=THEME["button"], foreground=THEME["text"],
                        borderwidth=0, padding=(12, 7), font=font)
        style.map("TButton", background=[("active", THEME["button_active"]),
                                          ("pressed", THEME["panel2"])])
        for name, color, active, fg in (
                ("Primary", THEME["jade"], "#38d9a9", "#041711"),
                ("Gold", THEME["gold"], "#ffd166", "#211600"),
                ("Danger", "#a83d4a", "#c94b59", "#ffffff"),
                ("Blue", "#2868a8", "#347fc7", "#ffffff")):
            style.configure(f"{name}.TButton", background=color, foreground=fg,
                            borderwidth=0, padding=(13, 7),
                            font=("Microsoft YaHei UI", 10, "bold"))
            style.map(f"{name}.TButton", background=[("active", active),
                                                     ("pressed", color)])
        style.configure("Panel.TLabelframe", background=THEME["panel"],
                        foreground=THEME["muted"], bordercolor=THEME["border"],
                        relief="solid", borderwidth=1)
        style.configure("Panel.TLabelframe.Label", background=THEME["panel"],
                        foreground=THEME["gold"], font=("Microsoft YaHei UI", 10, "bold"))
        self._configure_theme_styles()

        def card(parent, **pack):
            outer = tk.Frame(parent, bg=THEME["panel"], highlightthickness=1,
                             highlightbackground=THEME["border"], bd=0)
            outer.pack(**pack)
            return outer

        header = tk.Frame(self.root, bg=THEME["panel"], height=70,
                          highlightthickness=1, highlightbackground=THEME["border"])
        header.pack(fill="x", padx=14, pady=(14, 8))
        header.pack_propagate(False)
        emblem = tk.Canvas(header, width=46, height=46, bg=THEME["panel"],
                           highlightthickness=0)
        emblem.pack(side="left", padx=(15, 9), pady=11)
        emblem.create_oval(3, 3, 43, 43, fill=THEME["jade2"],
                           outline=THEME["jade"], width=2)
        emblem.create_text(23, 23, text="帅", fill=THEME["gold"],
                           font=("Microsoft YaHei UI", 15, "bold"))
        self.emblem = emblem
        title_box = tk.Frame(header, bg=THEME["panel"])
        title_box.pack(side="left", fill="y", pady=12)
        tk.Label(title_box, text="JJ 象棋直播助手", bg=THEME["panel"],
                 fg=THEME["text"], font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        tk.Label(title_box, text="LIVE CONTROL", bg=THEME["jade2"],
                 fg=THEME["jade"], padx=7, pady=1,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(1, 0))
        right_header = tk.Frame(header, bg=THEME["panel"])
        right_header.pack(side="right", padx=13, pady=11)
        ttk.Combobox(right_header, textvariable=self.theme_name, state="readonly",
                     values=tuple(THEMES), width=9).pack(side="right", padx=(8, 0))
        self.theme_name.trace_add("write", self._change_theme)
        tk.Scale(right_header, from_=72, to=100, orient="horizontal",
                 variable=self.opacity, command=self._change_opacity,
                 showvalue=False, length=92, resolution=1, bd=0,
                 highlightthickness=0, bg=THEME["panel"],
                 troughcolor=THEME["panel2"], activebackground=THEME["jade"]).pack(
                     side="right", padx=(9, 0))
        tk.Label(right_header, text="透明", bg=THEME["panel"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side="right")
        self.header_status = tk.Label(
            right_header, textvariable=self.status, bg=THEME["ok_bg"], fg=THEME["ok_fg"],
            padx=11, pady=7, font=("Microsoft YaHei UI", 9, "bold"),
            wraplength=280, justify="left")
        self.header_status.pack(side="right", padx=(0, 8))

        indicators = tk.Frame(self.root, bg=THEME["bg"])
        indicators.pack(fill="x", padx=14, pady=(0, 8))
        specs = (("device", "模拟器", self.device_state, THEME["blue"]),
                 ("board", "棋局状态", self.board_state, THEME["gold"]),
                 ("voice", "语音通道", self.voice_state, THEME["purple"]),
                 ("stream", "直播互动", self.stream_state, THEME["muted"]))
        for index, (key, label, variable, color) in enumerate(specs):
            box = tk.Frame(indicators, bg=THEME["panel"], highlightthickness=1,
                           highlightbackground=THEME["border"], height=43)
            box.pack(side="left", fill="x", expand=True,
                     padx=(0 if index == 0 else 5, 0))
            box.pack_propagate(False)
            dot = tk.Canvas(box, width=18, height=18, bg=THEME["panel"],
                            highlightthickness=0)
            dot.pack(side="left", padx=(12, 7))
            oval = dot.create_oval(4, 4, 14, 14, fill=color, outline=color)
            labels = tk.Frame(box, bg=THEME["panel"])
            labels.pack(side="left", fill="y", pady=10)
            tk.Label(labels, text=label + " ·", bg=THEME["panel"], fg=THEME["muted"],
                     font=("Microsoft YaHei UI", 9)).pack(side="left")
            tk.Label(labels, textvariable=variable, bg=THEME["panel"],
                     fg=THEME["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(
                         side="left", padx=(4, 0))
            self.indicators[key] = (dot, oval)

        config = card(self.root, fill="x", padx=14, pady=(0, 8))
        config_inner = ttk.Frame(config, style="Card.TFrame", padding=(12, 7))
        config_inner.pack(fill="x")
        ttk.Label(config_inner, text="思考", style="Muted.TLabel").grid(row=0, column=0)
        ttk.Entry(config_inner, textvariable=self.movetime, width=7).grid(row=0, column=1, padx=(5, 11))
        ttk.Label(config_inner, text="步数", style="Muted.TLabel").grid(row=0, column=2)
        ttk.Entry(config_inner, textvariable=self.max_moves, width=7).grid(row=0, column=3, padx=(5, 11))
        ttk.Label(config_inner, text="轮次", style="Muted.TLabel").grid(row=0, column=4)
        ttk.Combobox(config_inner, textvariable=self.turn_choice, state="readonly", width=9,
                     values=("自动判断", "红方", "黑方")).grid(row=0, column=5, padx=(5, 12))
        ttk.Checkbutton(config_inner, text="只读", variable=self.dry).grid(row=0, column=6, padx=5)
        ttk.Checkbutton(config_inner, text="连续对弈", variable=self.continuous).grid(row=0, column=7, padx=5)
        config_inner.columnconfigure(8, weight=1)
        ttk.Label(config_inner, textvariable=self.runtime_stats, style="Muted.TLabel").grid(
            row=0, column=8, sticky="e", padx=(12, 0))

        actions = card(self.root, fill="x", padx=14, pady=(0, 8))
        action_inner = ttk.Frame(actions, style="Card.TFrame", padding=(10, 7))
        action_inner.pack(fill="x")
        ttk.Button(action_inner, text="▶  一键接管", style="Primary.TButton",
                   command=self.prepare_and_wait).pack(side="left", padx=3)
        ttk.Button(action_inner, text="■  停止", style="Danger.TButton",
                   command=self.stop).pack(side="left", padx=(3, 8))
        tools_button = ttk.Menubutton(action_inner, text="更多工具  ···")
        tools_menu = tk.Menu(tools_button, tearoff=False, bg=THEME["panel"],
                             fg=THEME["text"], activebackground=THEME["jade2"],
                             activeforeground=THEME["text"], bd=0)
        self.tools_menu = tools_menu
        tools_menu.add_command(label="直接开始当前局", command=lambda: self.start(False))
        tools_menu.add_command(label="只运行一手", command=lambda: self.start(True))
        tools_menu.add_separator()
        tools_menu.add_command(label="刷新模拟器", command=self.refresh_device)
        tools_menu.add_command(label="重新标定棋盘", command=self.calibrate)
        tools_menu.add_command(label="开播体检", command=self.run_health_check)
        tools_menu.add_separator()
        tools_menu.add_command(label="打开故障截图", command=self.open_diagnostics)
        tools_menu.add_command(label="清理缓存", command=self.clean_cache)
        tools_button.configure(menu=tools_menu)
        tools_button.pack(side="left", padx=3)
        self.broadcaster_button = ttk.Button(
            action_inner, text="主播台  ▶", style="Gold.TButton",
            command=self.toggle_broadcaster_mode)
        self.broadcaster_button.pack(side="right", padx=3)

        self.body = tk.PanedWindow(self.root, orient="horizontal", bg=THEME["bg"],
                                   sashwidth=6, sashrelief="flat", bd=0)
        self.body.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.chess_panel = ttk.LabelFrame(
            self.body, text="  实时棋局与引擎分析  ", padding=7,
            style="Panel.TLabelframe")
        self.live_panel = ttk.LabelFrame(
            self.body, text="  主播互动与语音工作台  ", padding=8,
            style="Panel.TLabelframe")
        chess, live = self.chess_panel, self.live_panel
        self.body.add(chess, stretch="always", minsize=520)

        self.log = tk.Text(chess, bg=THEME["console"], fg=THEME["console_text"],
                           insertbackground=THEME["text"],
                           selectbackground=THEME["select"], font=("Cascadia Mono", 10),
                           wrap="word", state="disabled", relief="flat", padx=12, pady=11,
                           highlightthickness=1, highlightbackground="#172d43")
        self.log.pack(fill="both", expand=True)
        self._configure_chess_log_tags()

        status_row = tk.Frame(live, bg=THEME["panel2"], padx=10, pady=7)
        status_row.pack(fill="x", pady=(0, 7))
        tk.Label(status_row, textvariable=self.live_status, bg=THEME["panel2"],
                 fg=THEME["jade"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        tk.Label(status_row, textvariable=self.speech_status, bg=THEME["panel2"],
                 fg=THEME["purple"]).pack(side="right")

        room_row = ttk.Frame(live, style="Card.TFrame")
        room_row.pack(fill="x", pady=3)
        ttk.Label(room_row, text="抖音直播间", style="Card.TLabel").pack(side="left")
        ttk.Entry(room_row, textvariable=self.live_room).pack(side="left", fill="x", expand=True, padx=7)
        ttk.Button(room_row, text="连接", style="Primary.TButton", command=self.connect_live).pack(side="left", padx=2)
        ttk.Button(room_row, text="断开", command=self.disconnect_live).pack(side="left", padx=2)

        switch_row = ttk.Frame(live, style="Card.TFrame")
        switch_row.pack(fill="x", pady=5)
        ttk.Button(switch_row, text="开启互动", style="Primary.TButton",
                   command=self.enable_live).pack(side="left", padx=(0, 4))
        ttk.Button(switch_row, text="暂停", command=self.pause_live).pack(side="left", padx=3)
        ttk.Button(switch_row, text="自我介绍", command=self.speak_intro).pack(side="left", padx=3)
        ttk.Button(switch_row, text="跳到最新", command=self.jump_live_speech).pack(side="left", padx=3)
        ttk.Checkbutton(switch_row, text="直播解说", variable=self.live_commentary,
                        command=self.toggle_live_commentary).pack(side="left", padx=7)
        ttk.Checkbutton(switch_row, text="开播语音", variable=self.live_speech,
                        command=self.toggle_live_speech).pack(side="left", padx=7)

        options = ttk.Frame(live, style="Card.TFrame")
        options.pack(fill="x", pady=3)
        ttk.Label(options, text="音色", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Combobox(options, textvariable=self.live_voice, state="readonly",
                     values=available_voices(), width=10).grid(row=0, column=1, padx=(5, 10))
        ttk.Label(options, text="解说密度", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Combobox(options, textvariable=self.live_density, state="readonly",
                     values=("安静", "自然", "热闹"), width=6).grid(
                         row=0, column=3, padx=(5, 10))
        ttk.Label(options, text="弹幕前缀", style="Muted.TLabel").grid(row=0, column=4, sticky="w")
        ttk.Entry(options, textvariable=self.live_prefix, width=10).grid(row=0, column=5, padx=(5, 10))
        ttk.Label(options, text="间隔", style="Muted.TLabel").grid(row=0, column=6, sticky="w")
        ttk.Entry(options, textvariable=self.live_cooldown, width=4).grid(row=0, column=7, padx=(5, 8))
        ttk.Checkbutton(options, text="欢迎进场", variable=self.live_welcome).grid(row=0, column=8, padx=5)
        ttk.Checkbutton(options, text="礼物答谢", variable=self.live_gifts).grid(row=0, column=9, padx=5)
        ttk.Checkbutton(options, text="游戏内沙盘", variable=self.live_sandtable,
                        command=lambda: self.save_live_settings(quiet=True)).grid(
                            row=0, column=10, padx=5)
        ttk.Checkbutton(options, text="QUEEN式讲解",
                        variable=self.live_queen_commentary,
                        command=lambda: self.save_live_settings(quiet=True)).grid(
                            row=0, column=11, padx=5)

        text_style = dict(bg=THEME["editor"], fg=THEME["text"],
                          insertbackground=THEME["text"],
                          selectbackground=THEME["select"], relief="flat", padx=8, pady=5,
                          highlightthickness=1, highlightbackground=THEME["border"],
                          font=("Microsoft YaHei UI", 9), wrap="word")
        edit_grid = ttk.Frame(live, style="Card.TFrame")
        edit_grid.pack(fill="x", pady=(5, 3))
        edit_grid.columnconfigure(0, weight=1)
        edit_grid.columnconfigure(1, weight=1)
        ttk.Label(edit_grid, text="主播设定", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(edit_grid, text="开场介绍", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(7, 0))
        self.persona_text = tk.Text(edit_grid, height=3, **text_style)
        self.persona_text.grid(row=1, column=0, sticky="ew")
        self.persona_text.insert("1.0", load_settings().persona)
        self.intro_text = tk.Text(edit_grid, height=3, **text_style)
        self.intro_text.grid(row=1, column=1, sticky="ew", padx=(7, 0))
        self.intro_text.insert("1.0", load_settings().introduction)

        save_row = ttk.Frame(live, style="Card.TFrame")
        save_row.pack(fill="x", pady=4)
        ttk.Button(save_row, text="保存设置", style="Blue.TButton",
                   command=self.save_live_settings).pack(side="right")
        ttk.Button(save_row, text="重新体检", style="Gold.TButton",
                   command=self.run_health_check).pack(side="right", padx=5)

        test_row = ttk.Frame(live, style="Card.TFrame")
        test_row.pack(fill="x", pady=(2, 6))
        ttk.Entry(test_row, textvariable=self.live_user, width=10).pack(side="left")
        ttk.Entry(test_row, textvariable=self.live_test_text).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(test_row, text="测试弹幕", command=self.test_live_chat).pack(side="left", padx=2)
        ttk.Button(test_row, text="测试礼物", command=self.test_live_gift).pack(side="left", padx=2)

        self.sandtable_frame = tk.Frame(
            live, bg=THEME["panel2"], highlightthickness=1,
            highlightbackground=THEME["gold"], padx=9, pady=7)
        sand_header = tk.Frame(self.sandtable_frame, bg=THEME["panel2"])
        sand_header.pack(fill="x", pady=(0, 5))
        tk.Label(sand_header, textvariable=self.sandtable_title,
                 bg=THEME["panel2"], fg=THEME["gold"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        tk.Label(sand_header, textvariable=self.sandtable_status,
                 bg=THEME["panel2"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side="right")
        sand_body = tk.Frame(self.sandtable_frame, bg=THEME["panel2"])
        sand_body.pack(fill="x")
        self.sandtable_canvas = tk.Canvas(
            sand_body, width=230, height=166, bg=THEME["console"],
            highlightthickness=0)
        self.sandtable_canvas.pack(side="left")
        self.sandtable_route = tk.Label(
            sand_body, text="", bg=THEME["panel2"], fg=THEME["console_text"],
            justify="left", anchor="nw", wraplength=340,
            font=("Microsoft YaHei UI", 9), padx=10)
        self.sandtable_route.pack(side="left", fill="both", expand=True)

        self.live_log = tk.Text(live, bg=THEME["console"], fg=THEME["console_text"],
                                insertbackground=THEME["text"],
                                selectbackground=THEME["select"], font=("Microsoft YaHei UI", 9),
                                wrap="word", state="disabled", relief="flat", padx=10, pady=9,
                                highlightthickness=1, highlightbackground="#172d43")
        self.live_log.pack(fill="both", expand=True)
        for tag, color in (("礼物", THEME["gold"]), ("弹幕", THEME["blue"]),
                           ("讲解", THEME["jade"]), ("回复", THEME["purple"]),
                           ("错误", THEME["red"]), ("系统", THEME["muted"]),
                           ("语音", THEME["purple"])):
            self.live_log.tag_configure(tag, foreground=color,
                                        font=("Microsoft YaHei UI", 9, "bold"))

    def _configure_theme_styles(self):
        style = ttk.Style(self.root)
        font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=font, background=THEME["bg"],
                        foreground=THEME["text"])
        style.configure("App.TFrame", background=THEME["bg"])
        style.configure("Card.TFrame", background=THEME["panel"])
        style.configure("TLabel", background=THEME["bg"], foreground=THEME["text"])
        style.configure("Card.TLabel", background=THEME["panel"], foreground=THEME["text"])
        style.configure("Muted.TLabel", background=THEME["panel"], foreground=THEME["muted"])
        style.configure("Section.TLabel", background=THEME["panel"],
                        foreground=THEME["text"],
                        font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TEntry", fieldbackground=THEME["panel2"],
                        foreground=THEME["text"], insertcolor=THEME["text"],
                        bordercolor=THEME["border"], padding=6)
        style.configure("TCombobox", fieldbackground=THEME["panel2"],
                        foreground=THEME["text"], arrowcolor=THEME["text"], padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", THEME["panel2"])],
                  foreground=[("readonly", THEME["text"])])
        style.configure("TCheckbutton", background=THEME["panel"],
                        foreground=THEME["text"], indicatorcolor=THEME["panel2"])
        style.map("TCheckbutton", indicatorcolor=[("selected", THEME["jade"])],
                  foreground=[("active", THEME["text"])],
                  background=[("active", THEME["panel"])])
        style.configure("TButton", background=THEME["button"],
                        foreground=THEME["text"], borderwidth=0,
                        padding=(12, 7), font=font)
        style.configure("TMenubutton", background=THEME["button"],
                        foreground=THEME["text"], borderwidth=0, padding=(12, 7))
        for widget_style in ("TButton", "TMenubutton"):
            style.map(widget_style,
                      background=[("active", THEME["button_active"]),
                                  ("pressed", THEME["panel2"])])
        for name, color, active, fg in (
                ("Primary", THEME["jade"], THEME["jade2"], "#ffffff"),
                ("Gold", THEME["gold"], THEME["wait_bg"], "#ffffff"),
                ("Danger", THEME["red"], THEME["error_bg"], "#ffffff"),
                ("Blue", THEME["blue"], THEME["panel2"], "#ffffff")):
            style.configure(f"{name}.TButton", background=color, foreground=fg,
                            borderwidth=0, padding=(13, 7),
                            font=("Microsoft YaHei UI", 10, "bold"))
            style.map(f"{name}.TButton", background=[("active", active),
                                                     ("pressed", color)])
        style.configure("Panel.TLabelframe", background=THEME["panel"],
                        foreground=THEME["muted"], bordercolor=THEME["border"],
                        relief="solid", borderwidth=1)
        style.configure("Panel.TLabelframe.Label", background=THEME["panel"],
                        foreground=THEME["gold"],
                        font=("Microsoft YaHei UI", 10, "bold"))

    def _walk_widgets(self, widget):
        yield widget
        for child in widget.winfo_children():
            yield from self._walk_widgets(child)

    def _change_theme(self, *_):
        name = self.theme_name.get()
        if name not in THEMES:
            return
        old = dict(self.current_palette)
        THEME.clear()
        THEME.update(THEMES[name])
        color_map = {old[key].lower(): THEME[key]
                     for key in old if key in THEME}
        self.root.configure(bg=THEME["bg"])
        self._configure_theme_styles()
        for widget in self._walk_widgets(self.root):
            for option in ("background", "foreground", "highlightbackground",
                           "highlightcolor", "insertbackground",
                           "selectbackground", "troughcolor",
                           "activebackground"):
                try:
                    current = str(widget.cget(option)).lower()
                    if current in color_map:
                        widget.configure(**{option: color_map[current]})
                except (tk.TclError, AttributeError):
                    pass
            if isinstance(widget, tk.Canvas):
                for item in widget.find_all():
                    for option in ("fill", "outline"):
                        try:
                            current = str(widget.itemcget(item, option)).lower()
                            if current in color_map:
                                widget.itemconfigure(item, **{option: color_map[current]})
                        except tk.TclError:
                            pass
        if hasattr(self, "live_log"):
            for tag, color in (("礼物", THEME["gold"]),
                               ("弹幕", THEME["blue"]),
                               ("讲解", THEME["jade"]),
                               ("回复", THEME["purple"]),
                               ("错误", THEME["red"]),
                               ("系统", THEME["muted"]),
                               ("语音", THEME["purple"])):
                self.live_log.tag_configure(tag, foreground=color)
        if hasattr(self, "log"):
            self._configure_chess_log_tags()
        if hasattr(self, "tools_menu"):
            self.tools_menu.configure(
                bg=THEME["panel"], fg=THEME["text"],
                activebackground=THEME["jade2"],
                activeforeground=THEME["text"])
        self.current_palette = dict(THEME)
        self._sync_status_badges()
        self._sync_live_badges()
        self._schedule_ui_save()

    def _change_opacity(self, value=None):
        try:
            opacity = max(72, min(100, int(float(
                self.opacity.get() if value is None else value))))
            self.root.attributes("-alpha", opacity / 100)
            self._schedule_ui_save()
        except (tk.TclError, ValueError):
            pass

    def _schedule_ui_save(self):
        previous = getattr(self, "_ui_save_job", None)
        if previous:
            try:
                self.root.after_cancel(previous)
            except tk.TclError:
                pass
        self._ui_save_job = self.root.after(250, self._save_ui_settings)

    def _save_ui_settings(self):
        self._ui_save_job = None
        try:
            os.makedirs(os.path.dirname(UI_CFG), exist_ok=True)
            with open(UI_CFG, "w", encoding="utf-8") as stream:
                json.dump({"theme": self.theme_name.get(),
                           "opacity": int(self.opacity.get())},
                          stream, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _set_indicator(self, key, color):
        item = self.indicators.get(key)
        if item:
            canvas, oval = item
            canvas.itemconfigure(oval, fill=color, outline=color)

    def _sync_status_badges(self, *_):
        text = self.status.get()
        lowered = text.lower()
        if any(word in text for word in ("失败", "未找到", "异常")):
            color, bg = THEME["error_fg"], THEME["error_bg"]
        elif any(word in text for word in ("等待", "准备", "检查", "连接")):
            color, bg = THEME["wait_fg"], THEME["wait_bg"]
        elif any(word in text for word in ("运行", "接管", "检测到", "正常")):
            color, bg = THEME["ok_fg"], THEME["ok_bg"]
        else:
            color, bg = THEME["muted"], THEME["panel2"]
        self.header_status.configure(fg=color, bg=bg)
        if "等待" in text:
            self.board_state.set("等待画面")
            self._set_indicator("board", THEME["gold"])
        elif any(word in text for word in ("运行", "接管", "检测到")):
            self.board_state.set("对弈运行中")
            self._set_indicator("board", THEME["jade"])
        elif any(word in text for word in ("失败", "异常", "锁定")):
            self.board_state.set("需要复核")
            self._set_indicator("board", THEME["red"])

    def _sync_live_badges(self, *_):
        speech = self.speech_status.get()
        self.voice_state.set("已开启" if self.live_speech.get() else "仅文本")
        self._set_indicator(
            "voice", THEME["jade"] if self.live_speech.get() else THEME["purple"])
        live = self.live_status.get()
        if any(word in live for word in ("运行", "已连接", "互动")) and "未" not in live:
            self.stream_state.set("互动运行中")
            self._set_indicator("stream", THEME["jade"])
        elif "连接" in live:
            self.stream_state.set("连接中")
            self._set_indicator("stream", THEME["gold"])
        else:
            self.stream_state.set("未连接")
            self._set_indicator("stream", THEME["muted"])

    def run_health_check(self):
        """Run a non-network livestream readiness check in the background."""
        try:
            settings = self._live_values()
        except Exception as error:
            messagebox.showerror("设置错误", str(error))
            return
        self.status.set("正在执行开播体检…")
        if not self.broadcaster_open:
            self.toggle_broadcaster_mode()

        def worker():
            try:
                items = collect_readiness(settings)
                self.output.put(("health_result", items))
            except Exception as error:
                self.output.put(("assistant_error", "开播体检失败：" + str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def toggle_broadcaster_mode(self):
        """Expand/collapse the broadcaster panel without stopping its services."""
        if self.broadcaster_open:
            self.body.forget(self.live_panel)
            self.broadcaster_open = False
            self.broadcaster_button.configure(text="主播台  ▶")
            self.root.geometry(f"1180x{max(700, self.root.winfo_height())}")
        else:
            self.body.add(self.live_panel, stretch="always", minsize=650)
            self.broadcaster_open = True
            self.broadcaster_button.configure(text="收起主播台  ◀")
            self.root.geometry(f"1540x{max(780, self.root.winfo_height())}")

    def append_live(self, label, text):
        if not hasattr(self, "live_log"):
            return
        self.live_log.configure(state="normal")
        self.live_log.insert("end", f"[{datetime.now():%H:%M:%S}] {label}｜", label)
        self.live_log.insert("end", str(text) + "\n")
        self._trim_text_widget(self.live_log, 1500)
        self.live_log.see("end")
        self.live_log.configure(state="disabled")

    def _cancel_sandtable_jobs(self, include_close=True):
        for job in self.sandtable_jobs:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self.sandtable_jobs = []
        if include_close and self.sandtable_close_job:
            try:
                self.root.after_cancel(self.sandtable_close_job)
            except tk.TclError:
                pass
            self.sandtable_close_job = None

    def _draw_sandtable_base(self):
        canvas = self.sandtable_canvas
        canvas.delete("all")
        margin_x, margin_y, step_x, step_y = 15, 15, 25, 15
        for file_index in range(9):
            x = margin_x + file_index * step_x
            canvas.create_line(x, margin_y, x, margin_y + 9 * step_y,
                               fill=THEME["border"])
        for rank_index in range(10):
            y = margin_y + rank_index * step_y
            canvas.create_line(margin_x, y, margin_x + 8 * step_x, y,
                               fill=THEME["border"])
        canvas.create_rectangle(
            margin_x, margin_y + 4 * step_y,
            margin_x + 8 * step_x, margin_y + 5 * step_y,
            fill=THEME["panel2"], outline=THEME["border"])
        canvas.create_text(115, 82, text="楚河     汉界",
                           fill=THEME["muted"],
                           font=("Microsoft YaHei UI", 8))

    @staticmethod
    def _sandtable_point(square):
        file_index = ord(square[0]) - ord("a")
        rank = int(square[1])
        return 15 + file_index * 25, 15 + (9 - rank) * 15

    def _animate_sandtable_step(self, item, number):
        ucci = str(item.get("ucci") or "")
        if len(ucci) != 4:
            return
        x1, y1 = self._sandtable_point(ucci[:2])
        x2, y2 = self._sandtable_point(ucci[2:])
        colors = (THEME["jade"], THEME["gold"], THEME["blue"],
                  THEME["purple"], THEME["red"])
        color = colors[(number - 1) % len(colors)]
        line = self.sandtable_canvas.create_line(
            x1, y1, x1, y1, fill=color, width=3,
            arrow=tk.LAST, arrowshape=(8, 10, 4))

        def frame(step):
            ratio = step / 8
            current_x = x1 + (x2 - x1) * ratio
            current_y = y1 + (y2 - y1) * ratio
            self.sandtable_canvas.coords(line, x1, y1, current_x, current_y)
            if step == 8:
                self.sandtable_canvas.create_oval(
                    x2 - 8, y2 - 8, x2 + 8, y2 + 8,
                    fill=color, outline=THEME["console"], width=1)
                self.sandtable_canvas.create_text(
                    x2, y2, text=str(number), fill="#ffffff",
                    font=("Segoe UI", 7, "bold"))

        for step in range(1, 9):
            self.sandtable_jobs.append(
                self.root.after(step * 28, lambda value=step: frame(value)))

    def _close_sandtable_ui(self):
        self._cancel_sandtable_jobs()
        if self.sandtable_frame.winfo_manager():
            self.sandtable_frame.pack_forget()

    def _handle_sandtable(self, data):
        action = str(data.get("action") or "")
        if action == "close":
            self._close_sandtable_ui()
            return
        if action == "pause":
            # Do not clear the viewer on an opponent move.  Freeze the route
            # and leave it available until the next own move boundary.
            self._cancel_sandtable_jobs(include_close=True)
            notation = str(data.get("notation") or "")
            self.sandtable_status.set("实战已落子 · 暂停刷新")
            if notation:
                self.sandtable_route.configure(
                    text=self.sandtable_route.cget("text") +
                    "\n\n实战进度：" + notation)
            return
        if action != "show":
            return
        route = list(data.get("route") or [])[:5]
        if len(route) < 3:
            return
        self._cancel_sandtable_jobs()
        self._draw_sandtable_base()
        self.sandtable_title.set(data.get("title") or "关键战术沙盘")
        reason = str(data.get("reason") or "")
        self.sandtable_status.set(reason + " · 推演中")
        self.sandtable_route.configure(text="\n".join(
            f"{index}. {item.get('notation', '')}"
            for index, item in enumerate(route, 1)))
        if not self.sandtable_frame.winfo_manager():
            self.sandtable_frame.pack(
                fill="x", pady=(0, 6), before=self.live_log)

        def finish():
            self.sandtable_status.set(reason + " · 回看 6 秒")

        for index, item in enumerate(route, 1):
            job = self.root.after(
                (index - 1) * 520,
                lambda move=item, number=index:
                    self._animate_sandtable_step(move, number))
            self.sandtable_jobs.append(job)
        self.sandtable_jobs.append(
            self.root.after(len(route) * 520 + 120, finish))

    @staticmethod
    def _trim_text_widget(widget, max_lines):
        """Bound Tk text memory while keeping the newest operational context."""
        try:
            lines = int(widget.index("end-1c").split(".", 1)[0])
            excess = lines - int(max_lines)
            if excess > 0:
                widget.delete("1.0", f"{excess + 1}.0")
        except (tk.TclError, ValueError):
            pass

    def _live_values(self):
        try:
            cooldown = float(self.live_cooldown.get())
        except ValueError as error:
            raise ValueError("语音间隔必须是数字") from error
        return {
            "room": self.live_room.get().strip(),
            "model": self.live_model.get().strip() or "glm-4-flash",
            "voice": self.live_voice.get(),
            "commentary_density": self.live_density.get(),
            "prefix": self.live_prefix.get().strip(),
            "cooldown": cooldown,
            "persona": self.persona_text.get("1.0", "end").strip(),
            "introduction": self.intro_text.get("1.0", "end").strip(),
            "welcome_enabled": bool(self.live_welcome.get()),
            "gift_enabled": bool(self.live_gifts.get()),
            "speech_enabled": bool(self.live_speech.get()),
            "commentary_enabled": bool(self.live_commentary.get()),
            "sandtable_enabled": bool(self.live_sandtable.get()),
            "queen_commentary_enabled": bool(
                self.live_queen_commentary.get()),
        }

    def save_live_settings(self, quiet=False):
        if not self.live_controller:
            return False
        try:
            self.live_controller.configure(**self._live_values())
            if not quiet:
                self.append_live("系统", "直播设置已保存。")
            return True
        except Exception as error:
            messagebox.showerror("设置错误", str(error))
            return False

    def enable_live(self):
        if self.save_live_settings(quiet=True):
            self.live_controller.enable()
            self.live_status.set("互动运行中")

    def pause_live(self):
        if self.live_controller:
            self.live_controller.pause()
        self.live_status.set("互动已暂停")

    def toggle_live_speech(self):
        if not self.save_live_settings(quiet=True):
            return
        if self.live_speech.get():
            self.live_controller.enable()
            self.live_status.set("直播讲解运行中")
            self.speech_status.set("语音已开启")
            self.append_live("语音", "开播语音已开启，后续内容会调用百炼 TTS。")
        else:
            self.speech_status.set("仅文本模式")
            self.append_live("语音", "语音已关闭，本地测试只生成文本。")

    def toggle_live_commentary(self):
        if not self.save_live_settings(quiet=True):
            return
        if self.live_commentary.get():
            self.append_live("系统", "直播解说已开启，只讲关键棋和等棋空档。")
        else:
            self.live_controller.pipeline.clear(stop_audio=False)
            self.append_live("系统", "直播解说已关闭，只自动下棋，不调用棋局语言模型和 TTS。")

    def connect_live(self):
        if not self.live_controller or not self.save_live_settings(quiet=True):
            return
        try:
            self.live_controller.connect(self.live_room.get())
            self.live_status.set("正在连接直播间…")
        except Exception as error:
            messagebox.showerror("连接失败", str(error))

    def disconnect_live(self):
        if self.live_controller:
            self.live_controller.disconnect()
        self.live_status.set("直播间已断开")

    def test_live_chat(self):
        if self.live_controller and self.save_live_settings(quiet=True):
            self.live_controller.test_chat(self.live_user.get(), self.live_test_text.get())

    def test_live_gift(self):
        if self.live_controller and self.save_live_settings(quiet=True):
            self.live_controller.test_gift(self.live_user.get())

    def speak_intro(self):
        if self.live_controller and self.save_live_settings(quiet=True):
            self.live_controller.speak_intro()

    def jump_live_speech(self):
        if self.live_controller:
            self.live_controller.jump_to_latest()
            self.speech_status.set("已跳到最新讲解")
            self.append_live("语音", "旧播报已清除，从最新棋局内容继续。")

    def _configure_chess_log_tags(self):
        """Apply a readable visual hierarchy to one complete chess turn."""
        if not hasattr(self, "log"):
            return
        base = ("Microsoft YaHei UI", 10)
        self.log.tag_configure(
            "separator", foreground=THEME["border"],
            font=("Cascadia Mono", 9), spacing1=5, spacing3=2)
        self.log.tag_configure(
            "move_number", foreground=THEME["muted"],
            font=("Microsoft YaHei UI", 10, "bold"), spacing1=3, spacing3=5)
        self.log.tag_configure(
            "move_red", foreground=THEME["red"],
            font=("Microsoft YaHei UI", 13, "bold"), spacing1=3, spacing3=5)
        self.log.tag_configure(
            "move_black", foreground=THEME["blue"],
            font=("Microsoft YaHei UI", 13, "bold"), spacing1=3, spacing3=5)
        self.log.tag_configure(
            "reply_red", foreground=THEME["red"],
            font=("Microsoft YaHei UI", 11, "bold"), lmargin1=16, lmargin2=16)
        self.log.tag_configure(
            "reply_black", foreground=THEME["blue"],
            font=("Microsoft YaHei UI", 11, "bold"), lmargin1=16, lmargin2=16)
        self.log.tag_configure(
            "analysis", foreground=THEME["gold"],
            font=("Microsoft YaHei UI", 10, "bold"), lmargin1=16, lmargin2=16)
        self.log.tag_configure(
            "action", foreground=THEME["jade"], font=base,
            lmargin1=16, lmargin2=16)
        self.log.tag_configure(
            "success", foreground=THEME["jade"],
            font=("Microsoft YaHei UI", 10, "bold"),
            lmargin1=16, lmargin2=16)
        self.log.tag_configure(
            "waiting", foreground=THEME["muted"],
            font=("Microsoft YaHei UI", 9, "italic"),
            lmargin1=16, lmargin2=16)
        self.log.tag_configure(
            "warning", foreground=THEME["gold"],
            font=("Microsoft YaHei UI", 10, "bold"),
            lmargin1=16, lmargin2=16)
        self.log.tag_configure(
            "sandbox", foreground=THEME["purple"],
            font=("Microsoft YaHei UI", 10, "bold"),
            lmargin1=16, lmargin2=16)
        self.log.tag_configure("normal", foreground=THEME["console_text"],
                               font=base)

    def append(self, text, display_text=Ellipsis):
        if "[识别告警]" in text:
            self.warning_count += 1
            self.board_state.set("自动复核中")
            self._set_indicator("board", THEME["gold"])
        if "落子确认：成功" in text or "落子确认成功；" in text:
            self.move_success_count += 1
            self.board_state.set("识别稳定")
            self._set_indicator("board", THEME["jade"])
        if "对局结束：" in text:
            self.game_count += 1
        self.runtime_stats.set(
            f"识别告警 {self.warning_count} · 成功落子 {self.move_success_count} · 完成对局 {self.game_count}")
        shown = text if display_text is Ellipsis else display_text
        if shown:
            self.log.configure(state="normal")
            if isinstance(shown, (list, tuple)) and shown and isinstance(
                    shown[0], (list, tuple)):
                for chunk, tag in shown:
                    self.log.insert("end", chunk, tag)
            else:
                self.log.insert("end", shown)
            self._trim_text_widget(self.log, 2500)
            self.log.see("end")
            self.log.configure(state="disabled")
        try:
            os.makedirs(os.path.dirname(PANEL_LOG), exist_ok=True)
            rotate_log(PANEL_LOG)
            with open(PANEL_LOG, "a", encoding="utf-8") as stream:
                stamp = datetime.now().astimezone().isoformat(timespec="seconds")
                stream.write(f"[{stamp}] {text}")
                if text and not text.endswith("\n"):
                    stream.write("\n")
        except OSError:
            pass

    def _watchdog_tick(self):
        """Show a real player heartbeat and recover only from click-safe stalls."""
        import time

        try:
            running = bool(self.process and self.process.poll() is None)
            status = read_runtime_status() if running else None
            if running and status and status.get("pid") == self.process.pid:
                age = heartbeat_age(status)
                stage = status.get("stage", "")
                label = RUNTIME_STAGE_LABELS.get(stage, stage or "运行中")
                if age is not None and age < 5 and stage != "stopped":
                    self.board_state.set(label)
                cfg = self._assistant_cfg()
                warning_after = float(cfg.get("watchdog_warning_seconds", 25))
                recovery_after = float(cfg.get("watchdog_recovery_seconds", 75))
                now = time.monotonic()
                if age is not None and age >= warning_after:
                    self._set_indicator("board", THEME["gold"])
                    self.status.set(f"运行心跳停滞 {age:.0f} 秒，正在保护性处理")
                    if now - self.watchdog_reported_at >= 60:
                        self.append(
                            f"[运行看门狗] {label}阶段 {age:.0f} 秒无心跳；"
                            "已记录现场，不会盲目重复点击。\n")
                        self.watchdog_reported_at = now
                        threading.Thread(
                            target=self._watchdog_capture,
                            args=(stage, age), daemon=True).start()
                    if (age >= recovery_after and
                            stage in WATCHDOG_SAFE_STAGES and
                            self.auto_retry and bool(self.continuous.get()) and
                            not self.watchdog_recovery_started):
                        self.watchdog_recovery_started = True
                        self.append(
                            f"[自动恢复] {label}阶段确认卡死，"
                            "该阶段没有未确认点击；正在重建对弈进程。\n")
                        self.process.terminate()
            elif not running:
                self.watchdog_recovery_started = False
        except Exception as error:
            # The watchdog must never take down the UI it protects.
            if not self.closing:
                self.status.set("看门狗检查失败：" + str(error))
        finally:
            if not self.closing:
                self.root.after(1500, self._watchdog_tick)

    def _watchdog_capture(self, stage, age):
        try:
            devices = list_devices()
            if devices:
                self._save_diagnostic(
                    devices[0], "runtime_heartbeat_stalled",
                    extra={"stage": stage, "heartbeat_age": round(age, 1)})
        except Exception:
            pass

    def refresh_device(self):
        if hasattr(self, "log"):
            self.append("[控制指令] 刷新设备状态\n")
        try:
            devices = list_devices()
            if not devices:
                self.status.set("未找到 ADB 模拟器")
                self.device_state.set("未连接")
                self._set_indicator("device", THEME["red"])
                return
            device = devices[0]
            package, _ = current_package(device)
            self.status.set(f"设备 {device} · {resolution(device)} · 当前应用 {package or '未知'}")
            self.device_state.set("已连接")
            self._set_indicator("device", THEME["jade"])
        except Exception as error:
            self.status.set("设备检查失败：" + str(error))
            self.device_state.set("检查失败")
            self._set_indicator("device", THEME["red"])

    def open_diagnostics(self):
        self.append("[控制指令] 打开故障截图目录\n")
        os.makedirs(DIAGNOSTIC_DIR, exist_ok=True)
        try:
            os.startfile(DIAGNOSTIC_DIR)
        except OSError as error:
            messagebox.showerror("打开失败", str(error))

    def clean_cache(self):
        summary = scan_generated_cache()
        if not summary["files"]:
            messagebox.showinfo("清理缓存", "目前没有可清理的截图、诊断文件或临时语音。")
            return
        size_mb = summary["bytes"] / (1024 * 1024)
        confirmed = messagebox.askyesno(
            "清理缓存",
            f"将删除 {len(summary['files'])} 个可再生成文件，约 {size_mb:.1f} MB。\n\n"
            "包括：故障截图、诊断 JSON、调试 PNG、历史临时语音。\n"
            "不会删除棋局断点、配置、API 密钥和程序日志。\n\n继续清理吗？")
        if not confirmed:
            return
        result = cleanup_generated_cache(summary["files"])
        freed_mb = result["bytes"] / (1024 * 1024)
        self.append(f"[缓存清理] 已删除 {result['deleted']} 个文件，释放 "
                    f"{freed_mb:.1f} MB；跳过 {len(result['failed'])} 个。\n")
        if result["failed"]:
            messagebox.showwarning(
                "清理完成",
                f"已清理 {result['deleted']} 个文件；有 {len(result['failed'])} 个文件正在使用，稍后可再试。")
        else:
            messagebox.showinfo(
                "清理完成", f"已删除 {result['deleted']} 个缓存文件，释放 {freed_mb:.1f} MB。")

    def _save_diagnostic(self, device, reason, *, extra=None, image=None):
        path = capture_diagnostic(
            device, reason, extra=extra, image=image)
        if path:
            self.output.put(("diagnostic", path, reason))
        return path

    def _assistant_cfg(self):
        cfg = dict(DEFAULT_ASSISTANT_CFG)
        try:
            with open(ASSISTANT_CFG, encoding="utf-8") as stream:
                cfg.update(json.load(stream))
        except FileNotFoundError:
            pass
        return cfg

    def prepare_and_wait(self):
        self.append("[控制指令] 一键准备并等待新棋\n")
        if self.waiting_for_board:
            self.status.set("正在等待新棋或残局，无需重复点击")
            return
        if self.process and self.process.poll() is None:
            messagebox.showinfo("正在运行", "当前已经有对弈任务")
            return
        self.waiting_for_board = True
        self.auto_retry = bool(self.continuous.get())
        self.dry.set(False)
        self.status.set("一键助手启动中…")
        self.append("\n[助手] 正在准备模拟器与 JJ 象棋。标准新棋和合法残局都可以接管。\n")
        if self.auto_retry:
            self.append("[持续下棋] 已开启：胜负、投降或超时后都会继续监听下一局。\n")
        requested_turn = TURN_LABEL_TO_CODE.get(self.turn_choice.get(), "auto")
        threading.Thread(target=self._prepare_worker,
                         args=(requested_turn,), daemon=True).start()

    def _prepare_worker(self, requested_turn="auto"):
        import time

        cfg = self._assistant_cfg()
        try:
            devices = list_devices()
            if not devices:
                emulator = cfg["emulator_path"]
                if not os.path.isfile(emulator):
                    raise FileNotFoundError("未找到模拟器：" + emulator)
                self.output.put(("status", "正在启动雷电模拟器…"))
                subprocess.Popen([emulator], cwd=os.path.dirname(emulator))
                deadline = time.monotonic() + float(cfg["device_timeout_seconds"])
                while time.monotonic() < deadline and not self.closing:
                    devices = list_devices()
                    if devices:
                        break
                    time.sleep(2)
            if not devices:
                raise RuntimeError("等待模拟器连接超时")
            device = devices[0]
            package, _ = current_package(device)
            if package != cfg["jj_package"]:
                self.output.put(("status", "正在打开 JJ 象棋…"))
                adb(["shell", "monkey", "-p", cfg["jj_package"],
                     "-c", "android.intent.category.LAUNCHER", "1"],
                    timeout=30, device=device)

            self.output.put(("status", "请进入标准新棋或合法残局，稳定识别后会自动开始"))
            expected = Board.from_fen(START_FEN).sq
            stable = 0
            last_signature = None
            blocked_reported = False
            waiting_started = time.monotonic()
            last_diagnostic = 0.0
            last_mode_click = 0.0
            last_rules_dismiss = 0.0
            rules_dismiss_attempted = False
            while not self.closing and self.waiting_for_board:
                resumed = match_live_session(device)
                if resumed is not None:
                    clear_blocked_position()
                    self.output.put(("autostart", {
                        "turn": resumed["board"].turn,
                        "pieces": resumed["board"].attacker_count(),
                        "standard": False,
                        "resume": True,
                    }))
                    return
                image = screenshot(device)
                squares, orientation = ((None, None) if image is None else
                                        recognize_position(image, "auto"))
                visible_pieces = sum(
                    piece is not None for piece in (squares or []))
                live_board_visible = (
                    orientation in ("red_bottom", "black_bottom") and
                    visible_pieces >= int(cfg.get("endgame_min_pieces", 4)))
                if image is not None:
                    from postgame import (detect_match_mode_button,
                                          detect_rules_overlay_dismiss,
                                          handle_postgame,
                                          has_actionable_postgame)
                    if has_actionable_postgame(image):
                        time.sleep(0.35)
                        confirm = screenshot(device)
                        if (confirm is not None and
                                has_actionable_postgame(confirm)):
                            self.output.put(("status", "已连续确认结算页面，正在自动进入下一局…"))
                            actions = handle_postgame(device)
                            if actions:
                                self.output.put(("status", "结算已处理，正在识别下一局棋盘"))
                                waiting_started = time.monotonic()
                            else:
                                self._save_diagnostic(
                                    device, "watcher_postgame_no_action",
                                    extra={"stage": "handle_postgame"}, image=confirm)
                            stable = 0
                            last_signature = None
                            time.sleep(0.5)
                            continue
                    # Result controls are checked first.  A result page still
                    # contains a full board and its horizontal lines can look
                    # like the promotion-rules card; clicking the backdrop in
                    # that case leaves "再来一局" untouched and stalls the
                    # continuous-game loop.
                    rules_point = (None if live_board_visible else
                                   detect_rules_overlay_dismiss(image))
                    if (rules_point and not rules_dismiss_attempted and
                            time.monotonic() - last_rules_dismiss >= 8.0):
                        time.sleep(0.35)
                        confirm_rules = screenshot(device)
                        confirm_point = detect_rules_overlay_dismiss(confirm_rules)
                        if confirm_point:
                            tap(*confirm_point, device=device)
                            rules_dismiss_attempted = True
                            last_rules_dismiss = time.monotonic()
                            waiting_started = last_rules_dismiss
                            self.output.put((
                                "status",
                                "检测到晋级赛规则说明，已点击安全空白处关闭"))
                            stable = 0
                            last_signature = None
                            time.sleep(0.8)
                            continue
                    mode = cfg.get("match_mode", "15分钟")
                    mode_point = (None if live_board_visible else
                                  detect_match_mode_button(image, mode))
                    if (mode_point and
                            time.monotonic() - last_mode_click >= 8.0):
                        time.sleep(0.35)
                        confirm_mode = screenshot(device)
                        confirm_point = detect_match_mode_button(
                            confirm_mode, mode)
                        if confirm_point:
                            tap(*confirm_point, device=device)
                            # A newly selected mode may show the promotion
                            # rules sheet once.  Permit one safe dismissal for
                            # this matching flow, never a periodic retry loop.
                            rules_dismiss_attempted = False
                            last_mode_click = time.monotonic()
                            waiting_started = last_mode_click
                            self.output.put((
                                "status", f"检测到模式选择页，已选择{mode}，等待匹配…"))
                            stable = 0
                            last_signature = None
                            time.sleep(1.0)
                            continue
                    elif mode_point is None:
                        last_mode_click = 0.0
                turns = valid_turns(squares)
                pieces = sum(piece is not None for piece in (squares or []))
                opening_turn = (None if squares is None else
                                infer_turn_from_standard(squares))
                if requested_turn in ("r", "b"):
                    turns = [requested_turn] if requested_turn in turns else []
                if requested_turn == "auto":
                    if opening_turn in turns:
                        turns = [opening_turn]
                    elif len(turns) > 1:
                        preferred = cfg.get("ambiguous_turn_default", "r")
                        turns = [preferred if preferred in turns else turns[0]]
                signature = (tuple(squares or []), turns[0] if turns else None,
                             orientation)
                blocked = bool(squares is not None and
                               blocked_position_matches(squares))
                if blocked:
                    if not blocked_reported:
                        self.output.put((
                            "status",
                            "当前局面刚发生过落子确认失败，已锁定，等待棋盘变化或新一局"))
                        blocked_reported = True
                        self._save_diagnostic(
                            device, "watcher_blocked_position",
                            extra={"pieces": pieces}, image=image)
                    stable = 0
                    last_signature = None
                    time.sleep(1)
                    continue
                blocked_reported = False
                acceptable = (
                    orientation in ("red_bottom", "black_bottom") and
                    pieces >= int(cfg.get("endgame_min_pieces", 4)) and
                    bool(turns) and
                    (squares == expected or cfg.get("allow_endgame", True))
                )
                if acceptable and signature == last_signature:
                    stable += 1
                    if stable >= 2:
                        self.output.put(("autostart", {
                            "turn": turns[0],
                            "pieces": pieces,
                            "standard": squares == expected,
                            "resume": False,
                        }))
                        return
                else:
                    stable = 1 if acceptable else 0
                    last_signature = signature if acceptable else None
                now = time.monotonic()
                wait_threshold = float(cfg.get("diagnostic_wait_seconds", 15))
                repeat_interval = float(cfg.get("diagnostic_interval_seconds", 30))
                if (now - waiting_started >= wait_threshold and
                        now - last_diagnostic >= repeat_interval):
                    self._save_diagnostic(
                        device, "watcher_waiting_next_game",
                        extra={"waited_seconds": round(now - waiting_started, 1),
                               "pieces": pieces, "orientation": orientation,
                               "valid_turns": turns}, image=image)
                    last_diagnostic = now
                time.sleep(1)
        except Exception as error:
            if "device" in locals():
                self._save_diagnostic(
                    device, "watcher_exception", extra={"error": str(error)})
            self.output.put(("assistant_error", str(error)))

    def _launch(self, arguments, label):
        if self.process and self.process.poll() is None:
            messagebox.showinfo("正在运行", "请先停止当前任务")
            return
        os.makedirs(os.path.dirname(STOP_FILE), exist_ok=True)
        try:
            os.remove(STOP_FILE)
        except FileNotFoundError:
            pass
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        write_runtime_status("starting", label)
        self.watchdog_recovery_started = False
        self.watchdog_reported_at = 0.0
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        if os.name == "nt" and arguments and arguments[0] == "player.py":
            # Keep recognition/engine/ADB ahead of UI animation, LLM and TTS
            # when the machine is busy.  ABOVE_NORMAL is deliberate: it gives
            # the clock-sensitive child preference without starving Windows.
            flags |= getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0)
        self.process = subprocess.Popen(
            [sys.executable, "-u", *arguments], cwd=HERE, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=flags)
        self.status.set(label)
        self.append("\n" + "=" * 44 + "\n")
        self.append("[任务开始] " + label + "\n")

        def reader():
            for line in self.process.stdout:
                self.output.put(line)
            code = self.process.wait()
            self.output.put(("done", code))

        threading.Thread(target=reader, daemon=True).start()

    def calibrate(self):
        self.append("[控制指令] 开始自动标定\n")
        self._launch(["calibrate_live.py"], "正在从标准开局自动标定…")

    def start(self, once, initial_turn=None, min_pieces=None):
        self.append("[控制指令] " + ("运行一手" if once else "开始自动对弈") + "\n")
        try:
            movetime = int(self.movetime.get())
            max_moves = int(self.max_moves.get())
            if not 50 <= movetime <= 30000 or not 1 <= max_moves <= 500:
                raise ValueError
        except ValueError:
            messagebox.showerror("参数错误", "思考时间应为 50–30000 毫秒，步数应为 1–500")
            return
        if not once:
            self.auto_retry = bool(self.continuous.get())
        args = ["player.py", "--engine", "pikafish", "--movetime", str(movetime),
                "--max-moves", str(max_moves), "--stop-file", STOP_FILE]
        chosen_turn = initial_turn or TURN_LABEL_TO_CODE.get(
            self.turn_choice.get(), "auto")
        if chosen_turn == "auto":
            chosen_turn = self._assistant_cfg().get("ambiguous_turn_default", "r")
        args += ["--initial-turn", chosen_turn,
                 "--min-pieces", str(min_pieces if min_pieces is not None else
                                     self._assistant_cfg().get("endgame_min_pieces", 4))]
        if once:
            args.append("--once")
        if self.dry.get():
            args.append("--dry")
        self._launch(args, "只读检查中…" if self.dry.get() else "自动对弈运行中…")

    def stop(self):
        self.append("[控制指令] 请求安全停止\n")
        if not self.process or self.process.poll() is not None:
            self.waiting_for_board = False
            self.auto_retry = False
            self.status.set("已停止等待和自动接管")
            return
        os.makedirs(os.path.dirname(STOP_FILE), exist_ok=True)
        with open(STOP_FILE, "w", encoding="utf-8") as stream:
            stream.write("stop\n")
        self.auto_retry = False
        self.status.set("正在安全停止…")

    def _poll(self):
        try:
            while True:
                item = self.output.get_nowait()
                if isinstance(item, tuple) and item[0] == "done":
                    keep_running = (self.auto_retry and bool(self.continuous.get())
                                    and not self.closing)
                    if item[1] == 10:
                        self.status.set("结算已处理，正在等待下一局…")
                        self.append("[助手] 已完成结算页面操作，继续等待下一局。\n")
                        if keep_running:
                            self.root.after(1200, self.prepare_and_wait)
                    elif item[1] == 0:
                        if keep_running:
                            self.status.set("本次对弈已停止，正在自动衔接下一局…")
                            self.append("[助手] 本次对弈结束或安全停止，继续处理结算并等待下一局。\n")
                            self.root.after(1200, self.prepare_and_wait)
                        else:
                            self.status.set("任务已正常结束")
                            self.append("[任务结束] 程序已正常停止。\n")
                    elif item[1] == 12:
                        self.auto_retry = False
                        self.waiting_for_board = False
                        self.status.set("引擎拒绝当前识别局面，已安全停止")
                        self.append("[助手] 当前画面中的棋子类型存在矛盾；没有判负，也没有点击结算页。请等待高亮消失后重新开始。\n")
                    elif item[1] == 13:
                        self.auto_retry = False
                        self.waiting_for_board = False
                        self.status.set("落子未确认，当前局面已锁定")
                        self.append("[助手] 为防止重复点击，持续下棋已暂停；当前局面变化或进入新一局后再手动开始。\n")
                    elif item[1] == 14:
                        self.status.set("识别短暂不稳定，正在从断点重新接管…")
                        self.append("[助手] 本次未点击棋盘，已保留可信断点；等待画面稳定后自动恢复。\n")
                        if keep_running:
                            self.root.after(1200, self.prepare_and_wait)
                    else:
                        self.status.set("本次接管未成功，继续等待稳定棋盘…")
                        self.append("[助手] 本次画面识别未通过，未执行点击；继续等待。\n")
                        if keep_running:
                            self.root.after(1200, self.prepare_and_wait)
                elif isinstance(item, tuple) and item[0] == "status":
                    self.status.set(item[1])
                    self.append("[助手] " + item[1] + "\n")
                elif isinstance(item, tuple) and item[0] == "autostart":
                    self.waiting_for_board = False
                    details = item[1]
                    kind = ("断点棋局" if details.get("resume") else
                            ("标准新棋" if details["standard"] else "残局"))
                    side = "红方" if details["turn"] == "r" else "黑方"
                    self.status.set(f"已检测到{kind}，正在接管…")
                    self.append(f"[助手] 已连续确认{kind}（{details['pieces']} 枚棋子，"
                                f"当前轮到{side}），启动自动对弈。\n")
                    self.start(False, initial_turn=details["turn"],
                               min_pieces=self._assistant_cfg().get(
                                   "endgame_min_pieces", 4))
                elif isinstance(item, tuple) and item[0] == "assistant_error":
                    self.waiting_for_board = False
                    self.status.set("一键助手失败：" + item[1])
                    self.append("[助手] 失败：" + item[1] + "\n")
                elif isinstance(item, tuple) and item[0] == "diagnostic":
                    self.append("[故障截图] 已保存现场：" + item[1] +
                                "（原因：" + item[2] + "）\n")
                elif isinstance(item, tuple) and item[0] == "health_result":
                    items = item[1]
                    level = readiness_level(items)
                    icons = {"ok": "✓", "warn": "!", "error": "×"}
                    colors = {"ok": THEME["jade"], "warn": THEME["gold"],
                              "error": THEME["red"]}
                    self.append_live("系统", "—— 开播体检结果 ——")
                    for check in items:
                        self.append_live(
                            "系统", f"{icons.get(check['level'], '·')} "
                            f"{check['label']}：{check['detail']}")
                        if check["key"] == "device":
                            self.device_state.set(check["detail"][:12])
                            self._set_indicator("device", colors[check["level"]])
                        elif check["key"] in ("tts", "llm") and check["level"] == "error":
                            self._set_indicator("voice", colors[check["level"]])
                        elif check["key"] == "room":
                            self._set_indicator("stream", colors[check["level"]])
                    if level == "ok":
                        self.status.set("开播体检通过，可以开始直播")
                        self.append_live("系统", "全部关键模块正常，可以开始直播。")
                    elif level == "warn":
                        self.status.set("开播体检完成，有可选项目尚未开启")
                        self.append_live("系统", "核心程序可用；黄色项目请按直播需要开启。")
                    else:
                        self.status.set("开播体检发现关键故障")
                        self.append_live("错误", "红色项目必须修复后再开始自动直播。")
                elif isinstance(item, tuple) and item[0] == "live_event":
                    record = item[1]
                    kind = record.get("kind", "status")
                    text = record.get("text", "")
                    if kind == "sandtable":
                        self._handle_sandtable(record.get("data", {}))
                        continue
                    labels = {
                        "chat": "弹幕", "gift": "礼物", "member": "进场",
                        "social": "互动", "chess": "讲解", "reply": "回复",
                        "error": "错误", "connection": "连接", "status": "系统",
                        "speech_status": "语音", "queued": "队列",
                    }
                    if kind == "connection":
                        self.live_status.set(text)
                    elif kind == "speech_status":
                        self.speech_status.set(text)
                    # The right pane is a broadcast transcript, not a TTS
                    # debug console. Keep technical queue/playback states in
                    # the small status label only.
                    if kind in ("speech_status", "queued", "chess"):
                        continue
                    label = labels.get(kind, "系统")
                    if kind == "reply":
                        spoken_kind = record.get("data", {}).get("kind", "")
                        label = {
                            "我方走棋解说": "我方讲解",
                            "我方走棋播报": "我方走法",
                            "对手走棋解说": "对手讲解",
                            "对手走棋播报": "对手走法",
                            "对面落子短播报": "落子播报",
                            "关键走棋解说": "关键棋解说",
                            "关键应手解说": "关键应手",
                            "沙盘推演解说": "沙盘解说",
                            "等待期长讲解": "局面讲解",
                            "弹幕回复": "弹幕回复",
                            "礼物答谢": "礼物答谢",
                            "欢迎观众": "欢迎",
                            "自我介绍": "自我介绍",
                        }.get(spoken_kind, "主播台词")
                    self.append_live(label, text)
                else:
                    if isinstance(item, str):
                        self.append(item, display_text=styled_player_line(item))
                    else:
                        self.append(str(item))
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def close(self):
        self.closing = True
        self._save_ui_settings()
        self._cancel_sandtable_jobs()
        self.waiting_for_board = False
        if self.live_controller:
            self.live_controller.close()
            self.live_controller = None
        if self.process and self.process.poll() is None:
            self.stop()
            self.root.after(1200, self.root.destroy)
        else:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--autostart", action="store_true",
                        help="启动模拟器/JJ，并等待标准棋盘后自动对弈")
    args = parser.parse_args()
    root = tk.Tk()
    ControlPanel(root, autostart=args.autostart)
    root.mainloop()


if __name__ == "__main__":
    main()
