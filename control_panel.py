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
                        screenshot, tap, cancellable_adb, TaskCancelled)
from board import Board, START_FEN
from diagnostics import DIAGNOSTIC_DIR, capture_diagnostic
from recognizer import recognize_position
from state import position_errors, validate_board
from session_state import (blocked_position_matches, clear_blocked_position,
                           infer_turn_from_standard, match_live_session)
from cache_cleanup import cleanup_generated_cache, scan_generated_cache
from bailian_tts import available_voices
from live_host import CHESS_SPEECH_KINDS, LiveController, load_settings
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
    "speech_waiting": "等待播报",
}

TURN_LABEL_TO_CODE = {"自动判断": "auto", "红方": "r", "黑方": "b"}

THEMES = {
    "云白青黛": {
        "bg": "#f2f5f7", "panel": "#ffffff", "panel2": "#edf2f5",
        "border": "#dce4ea", "text": "#203348", "muted": "#718096",
        "jade": "#167d8d", "jade2": "#e1f1f3", "gold": "#a77d42",
        "red": "#c94f60", "blue": "#437cb5", "purple": "#7c70a6",
        "console": "#ffffff", "console_text": "#2b4054",
        "editor": "#f6f8fa", "select": "#cce7eb",
        "button": "#eaf0f4", "button_active": "#dae5ec",
        "ok_bg": "#e1f1ed", "ok_fg": "#267761",
        "wait_bg": "#faf0dc", "wait_fg": "#936d2f",
        "error_bg": "#fbe9ed", "error_fg": "#b44356",
    },
    "午夜蓝": {
        "bg": "#101722", "panel": "#192330", "panel2": "#202e3e",
        "border": "#304054", "text": "#e4edf7", "muted": "#96a9bd",
        "jade": "#328b9b", "jade2": "#203e48", "gold": "#d5af75",
        "red": "#c9586c", "blue": "#77afe7", "purple": "#b3a4dd",
        "console": "#16202c", "console_text": "#d5e2ef",
        "editor": "#121d29", "select": "#34566b",
        "button": "#2b3c4f", "button_active": "#3a5069",
        "ok_bg": "#203c38", "ok_fg": "#95d3ba",
        "wait_bg": "#403724", "wait_fg": "#e3c48f",
        "error_bg": "#472c36", "error_fg": "#efa2af",
    },
    "暖砂茶": {
        "bg": "#f4f1eb", "panel": "#fffdf9", "panel2": "#eee9df",
        "border": "#e1d9cb", "text": "#3e3b32", "muted": "#847c6e",
        "jade": "#667956", "jade2": "#e9efdf", "gold": "#a57a40",
        "red": "#bd5858", "blue": "#567b9e", "purple": "#8b7097",
        "console": "#fffdf9", "console_text": "#454237",
        "editor": "#f8f5ee", "select": "#e0e8cf",
        "button": "#eae5db", "button_active": "#dfd7c9",
        "ok_bg": "#e7eedd", "ok_fg": "#5c7147",
        "wait_bg": "#f6ecd8", "wait_fg": "#89672f",
        "error_bg": "#f9e7e3", "error_fg": "#a94a4a",
    },
}
THEME = dict(THEMES["云白青黛"])


def load_ui_settings():
    defaults = {"theme": "云白青黛", "opacity": 100}
    try:
        with open(UI_CFG, encoding="utf-8") as stream:
            saved = json.load(stream)
        name = {"青瓷清风": "云白青黛", "深海极光": "午夜蓝",
                "墨玉流金": "暖砂茶"}.get(saved.get("theme"), saved.get("theme"))
        if name in THEMES:
            defaults["theme"] = name
        defaults["opacity"] = max(72, min(100, int(saved.get("opacity", 100))))
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
        self.task_cancel = threading.Event()
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
        root.geometry("1240x800")
        root.minsize(1000, 680)
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
            self._schedule_prepare(delay=400)

    def _build(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self._configure_theme_styles()
        self.chess_follow = tk.BooleanVar(value=True)
        self.live_follow = tk.BooleanVar(value=True)
        self.latest_move = tk.StringVar(value="棋盘准备就绪，开始后显示最新走法与局势。")
        self.latest_speech = tk.StringVar(value="讲解准备就绪，开局后将在这里显示最新内容。")

        header = tk.Frame(self.root, bg=THEME["bg"])
        header.pack(fill="x", padx=20, pady=(12, 10))
        self.emblem = tk.Canvas(header, width=44, height=44, bg=THEME["bg"],
                                 highlightthickness=0)
        self.emblem.pack(side="left", padx=(0, 12))
        self.emblem.create_oval(2, 2, 42, 42, fill=THEME["jade2"], outline=THEME["jade"], width=1)
        self.emblem.create_text(22, 22, text="棋", fill=THEME["jade"],
                                font=("Microsoft YaHei UI", 17, "bold"))
        titles = tk.Frame(header, bg=THEME["bg"])
        titles.pack(side="left")
        tk.Label(titles, text="象棋工作台", bg=THEME["bg"], fg=THEME["text"],
                 font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w")
        tk.Label(titles, text="JJ 象棋助手  /  对弈与直播", bg=THEME["bg"],
                 fg=THEME["muted"], font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        ttk.Combobox(header, textvariable=self.theme_name, values=tuple(THEMES),
                     state="readonly", width=10).pack(side="right")
        ttk.Label(header, text="外观", style="TLabel").pack(side="right", padx=8)
        self.theme_name.trace_add("write", self._change_theme)
        self.header_status = tk.Label(header, textvariable=self.board_state,
            bg=THEME["panel2"], fg=THEME["muted"], padx=14, pady=7,
            font=("Microsoft YaHei UI", 9, "bold"))
        self.header_status.pack(side="right", padx=18)

        controls = self._ui_card(self.root)
        controls.pack(fill="x", padx=20, pady=(0, 12))
        main_actions = ttk.Frame(controls, style="Card.TFrame", padding=(14, 8))
        main_actions.pack(fill="x")
        action_group = ttk.Frame(main_actions, style="Card.TFrame")
        action_group.pack(side="left", fill="x", expand=True)
        ttk.Label(action_group, text="对弈控制", style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
        buttons = ttk.Frame(action_group, style="Card.TFrame")
        buttons.pack(fill="x")
        for i in range(3):
            buttons.columnconfigure(i, weight=1, uniform="actions")
        self.start_button = ttk.Button(buttons, text="▶  开始对弈", style="Action.Primary.TButton",
                                       command=self.prepare_and_wait)
        self.stop_button = ttk.Button(buttons, text="■  立即停止  F8", style="Action.Danger.TButton",
                                      command=self.stop)
        self.preview_button = ttk.Button(buttons, text="只读检查一手", style="Action.TButton", command=self.preview_once)
        for i, button in enumerate((self.start_button, self.stop_button, self.preview_button)):
            button.grid(row=0, column=i, sticky="ew", padx=(0, 8 if i < 2 else 0))
        switches = ttk.Frame(main_actions, style="Card.TFrame", padding=(24, 0, 0, 0))
        switches.pack(side="right")
        ttk.Label(switches, text="播报与连局", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(switches, style="Card.TFrame")
        row.pack(fill="x")
        for text, variable, command in (("自动下一局", self.continuous, None),
                ("棋局解说", self.live_commentary, self.toggle_live_commentary),
                ("语音播报", self.live_speech, self.toggle_live_speech)):
            ttk.Checkbutton(row, text=text, variable=variable, command=command).pack(side="left", padx=(0, 10))

        indicators = tk.Frame(self.root, bg=THEME["bg"])
        indicators.pack(fill="x", padx=20, pady=(0, 10))
        specs = (("device", "模拟器", self.device_state, THEME["blue"]),
                 ("board", "当前棋局", self.board_state, THEME["jade"]),
                 ("voice", "语音播报", self.voice_state, THEME["purple"]),
                 ("stream", "直播间", self.stream_state, THEME["muted"]))
        for i, (key, label, variable, color) in enumerate(specs):
            indicators.columnconfigure(i, weight=1, uniform="status")
            box = self._ui_card(indicators)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            dot = tk.Canvas(box, width=14, height=14, bg=THEME["panel"], highlightthickness=0)
            dot.grid(row=0, column=0, rowspan=2, padx=(12, 8), pady=10)
            oval = dot.create_oval(3, 3, 11, 11, fill=color, outline=color)
            tk.Label(box, text=label, bg=THEME["panel"], fg=THEME["muted"],
                     font=("Microsoft YaHei UI", 8)).grid(row=0, column=1, sticky="w", pady=(6, 0))
            tk.Label(box, textvariable=variable, bg=THEME["panel"], fg=THEME["text"],
                     font=("Microsoft YaHei UI", 10, "bold")).grid(row=1, column=1, sticky="w", pady=(0, 6))
            self.indicators[key] = (dot, oval)

        footer = tk.Frame(self.root, bg=THEME["bg"])
        footer.pack(side="bottom", fill="x", padx=20, pady=(8, 12))
        tk.Label(footer, textvariable=self.runtime_stats, bg=THEME["bg"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side="right", padx=(12, 0))
        self.footer_status = tk.Label(footer, textvariable=self.status, anchor="w", justify="left",
            bg=THEME["bg"], fg=THEME["muted"], font=("Microsoft YaHei UI", 9))
        self.footer_status.pack(side="left", fill="x", expand=True)
        self.footer_status.bind("<Configure>", lambda event: self.footer_status.configure(wraplength=max(200, event.width)))

        self.pages = ttk.Notebook(self.root, style="Workspace.TNotebook")
        self.pages.pack(fill="both", expand=True, padx=20)
        workspace = ttk.Frame(self.pages, style="App.TFrame", padding=(0, 12, 0, 0))
        self.pages.add(workspace, text="  对弈工作台  ")
        self.live_settings_page = self._scroll_page("  直播与设置  ")
        self.game_settings_page = self.live_settings_page
        self._build_workspace(workspace)
        self._build_live_settings(self.live_settings_page)
        self._build_game_settings(self.game_settings_page, row_offset=4)
        self.root.bind("<F8>", lambda event: self.stop())
        self.root.bind("<MouseWheel>", self._scroll_settings, add="+")
        self.broadcaster_open = True

    @staticmethod
    def _ui_card(parent):
        return tk.Frame(parent, bg=THEME["panel"], highlightthickness=1,
                        highlightbackground=THEME["border"], bd=0)

    def _section(self, parent, title, subtitle=""):
        card = self._ui_card(parent)
        inner = ttk.Frame(card, style="Card.TFrame", padding=16)
        inner.pack(fill="both", expand=True)
        ttk.Label(inner, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        if subtitle:
            label = ttk.Label(inner, text=subtitle, style="Muted.TLabel", wraplength=400)
            label.pack(anchor="w", pady=(0, 12))
            label.bind("<Configure>", lambda event: label.configure(wraplength=max(180, event.width)))
        return card, inner

    def _scroll_page(self, title):
        page = ttk.Frame(self.pages, style="App.TFrame")
        self.pages.add(page, text=title)
        canvas = tk.Canvas(page, bg=THEME["bg"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview, style="Page.Slim.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=lambda first, last: self._update_scrollbar(scrollbar, first, last))
        inner = ttk.Frame(canvas, style="App.TFrame", padding=(0, 12, 10, 8))
        item = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))
        if not hasattr(self, "_scroll_pages"):
            self._scroll_pages = []
        self._scroll_pages.append((page, canvas))
        return inner

    def _scroll_settings(self, event):
        if isinstance(event.widget, (tk.Text, ttk.Combobox)):
            return
        for page, canvas in self._scroll_pages:
            if str(page) == self.pages.select() and canvas.yview() != (0.0, 1.0):
                canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"

    @staticmethod
    def _update_scrollbar(scrollbar, first, last):
        scrollbar.set(first, last)
        full = float(first) <= 0.0 and float(last) >= 1.0
        scrollbar.state(["disabled"] if full else ["!disabled"])

    def _build_workspace(self, parent):
        self.body = tk.PanedWindow(parent, orient="horizontal", bg=THEME["bg"],
                                   sashwidth=10, sashrelief="flat", bd=0)
        self.body.pack(fill="both", expand=True)
        self.chess_panel = self._ui_card(self.body)
        self.live_panel = self._ui_card(self.body)
        self.body.add(self.chess_panel, stretch="always", minsize=350)
        self.body.add(self.live_panel, stretch="always", minsize=350)
        self.body.after_idle(lambda: self.body.sash_place(0, max(360, (self.body.winfo_width() - 10) // 2), 0))
        self._summary_cards = []
        for key, panel, title, subtitle, summary, follow, status in (
                ("chess", self.chess_panel, "实时棋局", "走法、局势与落子确认", self.latest_move, self.chess_follow, self.board_state),
                ("live", self.live_panel, "主播口播", "只显示播报台词 · 上方标明对应走法", self.latest_speech, self.live_follow, self.speech_status)):
            panel.columnconfigure(0, weight=1)
            panel.rowconfigure(5, weight=1)
            heading = ttk.Frame(panel, style="Card.TFrame", padding=(14, 8))
            heading.grid(row=0, column=0, sticky="ew")
            ttk.Label(heading, text=title, style="Section.TLabel").pack(side="left")
            ttk.Button(heading, text="对弈设置  →" if key == "chess" else "直播设置  →",
                       width=12, command=lambda k=key: self._show_settings(k)).pack(side="right")
            ttk.Label(panel, text=subtitle, style="Muted.TLabel").grid(row=1, column=0, sticky="w", padx=14, pady=(0, 8))
            latest = tk.Frame(panel, bg=THEME["jade2"], padx=12, pady=8)
            latest.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 10))
            self._summary_cards.append(latest)
            tk.Label(latest, text="最新棋况" if key == "chess" else "最新讲解", bg=THEME["jade2"], fg=THEME["jade"],
                     font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
            label = tk.Label(latest, textvariable=summary, bg=THEME["jade2"], fg=THEME["text"], height=2,
                             font=("Microsoft YaHei UI", 11), justify="left", anchor="nw", wraplength=400)
            label.pack(fill="x", pady=(5, 0))
            label.bind("<Configure>", lambda event, target=label: self._wrap_summary(target, event.width))
            ttk.Label(panel, textvariable=status, style="Muted.TLabel").grid(row=3, column=0, sticky="w", padx=14, pady=(0, 8))
            log_frame = ttk.Frame(panel, style="Card.TFrame", padding=(10, 0, 10, 8))
            log_frame.grid(row=5, column=0, sticky="nsew")
            text = tk.Text(log_frame, bg=THEME["console"], fg=THEME["console_text"],
                insertbackground=THEME["text"], selectbackground=THEME["select"],
                font=("Microsoft YaHei UI", 10), wrap="word", state="disabled", relief="flat",
                width=1, height=1, padx=8, pady=7, highlightthickness=0)
            scroll = ttk.Scrollbar(log_frame, orient="vertical", command=text.yview, style="Slim.Vertical.TScrollbar")
            scroll.pack(side="right", fill="y")
            text.pack(side="left", fill="both", expand=True)
            text.configure(yscrollcommand=lambda first, last, bar=scroll: self._update_scrollbar(bar, first, last))
            toolbar = ttk.Frame(panel, style="Card.TFrame", padding=(10, 0, 10, 8))
            toolbar.grid(row=6, column=0, sticky="ew")
            ttk.Checkbutton(toolbar, text="跟随最新", variable=follow,
                            command=lambda k=key: self._follow_latest(k)).pack(side="left")
            ttk.Button(toolbar, text="清空显示", width=9, command=lambda k=key: self._clear_log_view(k)).pack(side="right")
            ttk.Button(toolbar, text="回到最新" if key == "chess" else "跳过旧播报", width=11,
                       command=(lambda: self.log.see("end")) if key == "chess" else self.jump_live_speech).pack(side="right", padx=5)
            if key == "chess":
                self.log = text
            else:
                self.live_log = text
                self.live_log_container = log_frame
        self.latest_move.trace_add("write", self._queue_summary_alignment)
        self.latest_speech.trace_add("write", self._queue_summary_alignment)
        self._queue_summary_alignment()
        self._configure_chess_log_tags()
        self._configure_speech_log_tags()
        self._build_sandtable(self.live_panel)

    def _wrap_summary(self, label, width):
        wrap = max(200, width)
        if int(label.cget("wraplength")) != wrap:
            label.configure(wraplength=wrap)
            self._queue_summary_alignment()

    def _queue_summary_alignment(self, *_):
        if not getattr(self, "_summary_align_job", None):
            self._summary_align_job = self.root.after_idle(self._align_summaries)

    def _align_summaries(self):
        self._summary_align_job = None
        height = max(card.winfo_reqheight() for card in self._summary_cards) + 10
        for panel in (self.chess_panel, self.live_panel):
            panel.rowconfigure(2, minsize=height)

    def _show_settings(self, section="live"):
        self.pages.select(1)
        canvas = self._scroll_pages[0][1]
        self.root.update_idletasks()
        if section == "chess":
            total = max(1, self.game_settings_anchor.winfo_y() + self.game_settings_anchor.winfo_height())
            bounds = canvas.bbox("all")
            canvas.yview_moveto(self.game_settings_anchor.winfo_y() / max(total, bounds[3] if bounds else total))
        else:
            canvas.yview_moveto(0)

    def _build_sandtable(self, parent):
        self.sandtable_frame = tk.Frame(parent, bg=THEME["panel2"], padx=10, pady=8)
        tk.Label(self.sandtable_frame, textvariable=self.sandtable_title, bg=THEME["panel2"],
                 fg=THEME["gold"], font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        tk.Label(self.sandtable_frame, textvariable=self.sandtable_status, bg=THEME["panel2"],
                 fg=THEME["muted"]).pack(anchor="w")
        self.sandtable_canvas = tk.Canvas(self.sandtable_frame, width=230, height=166,
                                          bg=THEME["console"], highlightthickness=0)
        self.sandtable_canvas.pack(side="left")
        self.sandtable_route = tk.Label(self.sandtable_frame, text="", bg=THEME["panel2"],
            fg=THEME["console_text"], justify="left", anchor="nw", wraplength=230)
        self.sandtable_route.pack(side="left", fill="both", expand=True, padx=8)

    def _build_live_settings(self, parent):
        parent.columnconfigure((0, 1), weight=1, uniform="settings")
        room_card, room = self._section(parent, "直播间连接", "粘贴抖音直播链接、分享文案或直播间号。")
        room_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        ttk.Entry(room, textvariable=self.live_room).pack(fill="x", pady=(0, 10))
        row = ttk.Frame(room, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="连接直播间", style="Primary.TButton", command=self.connect_live).pack(side="left")
        ttk.Button(row, text="断开", command=self.disconnect_live).pack(side="left", padx=6)
        ttk.Label(room, textvariable=self.live_status, style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
        speech_card, speech = self._section(parent, "声音与讲解", "声音、解说频率修改后立即生效。")
        speech_card.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        grid = ttk.Frame(speech, style="Card.TFrame")
        grid.pack(fill="x")
        for i, (label, variable, values) in enumerate((
                ("播报音色", self.live_voice, available_voices()),
                ("解说频率", self.live_density, ("安静", "自然", "热闹")))):
            ttk.Label(grid, text=label, style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=5)
            combo = ttk.Combobox(grid, textvariable=variable, values=values, state="readonly", width=17)
            combo.grid(row=i, column=1, sticky="ew", padx=(14, 0), pady=5)
            combo.bind("<<ComboboxSelected>>", lambda event: self.save_live_settings(quiet=True))
        grid.columnconfigure(1, weight=1)
        row = ttk.Frame(speech, style="Card.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(row, text="游戏沙盘演示", variable=self.live_sandtable,
                        command=lambda: self.save_live_settings(quiet=True)).pack(side="left")
        ttk.Checkbutton(row, text="局面延伸讲解", variable=self.live_queen_commentary,
                        command=lambda: self.save_live_settings(quiet=True)).pack(side="left", padx=12)

        persona_card, persona = self._section(parent, "主播风格", "设置说话方式；留出明确、自然的讲解空间。")
        persona_card.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        intro_card, intro = self._section(parent, "开场介绍", "点击试听可检查当前音色和语音链路。")
        intro_card.grid(row=1, column=1, sticky="nsew", pady=(0, 10))
        settings = load_settings()
        for attr, container, content in (("persona_text", persona, settings.persona),
                                          ("intro_text", intro, settings.introduction)):
            editor = tk.Text(container, height=4, width=1, wrap="word", bg=THEME["editor"],
                fg=THEME["text"], insertbackground=THEME["text"], selectbackground=THEME["select"],
                relief="flat", padx=10, pady=8, font=("Microsoft YaHei UI", 10), undo=True)
            editor.pack(fill="x")
            editor.insert("1.0", content)
            setattr(self, attr, editor)
        ttk.Button(intro, text="试听开场介绍", command=self.speak_intro).pack(anchor="w", pady=(10, 0))

        interaction_card, interaction = self._section(parent, "观众互动", "仅回复指定前缀的弹幕；前缀留空则回复全部。")
        interaction_card.grid(row=2, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        row = ttk.Frame(interaction, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="弹幕前缀", style="Card.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.live_prefix, width=12).pack(side="left", padx=10)
        row = ttk.Frame(interaction, style="Card.TFrame")
        row.pack(fill="x", pady=10)
        ttk.Checkbutton(row, text="欢迎进场", variable=self.live_welcome).pack(side="left")
        ttk.Checkbutton(row, text="礼物答谢", variable=self.live_gifts).pack(side="left", padx=12)
        row = ttk.Frame(interaction, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="开启互动", command=self.enable_live).pack(side="left")
        ttk.Button(row, text="暂停互动", command=self.pause_live).pack(side="left", padx=6)
        test_card, test = self._section(parent, "试播测试", "使用测试内容检查回复和语音，无需真实观众。")
        test_card.grid(row=2, column=1, sticky="nsew", pady=(0, 10))
        row = ttk.Frame(test, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.live_user, width=10).pack(side="left")
        ttk.Entry(row, textvariable=self.live_test_text, width=1).pack(side="left", fill="x", expand=True, padx=(8, 0))
        row = ttk.Frame(test, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="测试弹幕", command=self.test_live_chat).pack(side="left")
        ttk.Button(row, text="测试礼物", command=self.test_live_gift).pack(side="left", padx=6)
        page, canvas = self._scroll_pages[0]
        save = ttk.Frame(page, style="App.TFrame", padding=(0, 10, 10, 0))
        save.pack(side="bottom", fill="x", before=canvas)
        ttk.Button(save, text="保存播报与互动设置", style="Primary.TButton", command=self.save_live_settings).pack(side="left")
        ttk.Button(save, text="开播体检", command=self.run_health_check).pack(side="left", padx=8)
        ttk.Label(save, text="对弈参数在下次开始时生效；播报与互动选项请保存。", style="TLabel").pack(side="left", padx=8)

    def _build_game_settings(self, parent, row_offset=4):
        self.game_settings_anchor = ttk.Label(parent, text="对弈与设备", style="Section.TLabel")
        self.game_settings_anchor.grid(row=row_offset - 1, column=0, columnspan=2, sticky="ew", pady=(14, 12))
        parent.columnconfigure((0, 1), weight=1, uniform="settings")
        card, settings = self._section(parent, "对弈参数", "用于下一次开始；残局接管时可指定当前轮到谁走。")
        card.grid(row=row_offset, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        grid = ttk.Frame(settings, style="Card.TFrame")
        grid.pack(fill="x")
        for i, (label, variable) in enumerate((("思考时间 / 毫秒", self.movetime), ("单次最多走子 / 手", self.max_moves))):
            ttk.Label(grid, text=label, style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=7)
            ttk.Entry(grid, textvariable=variable, width=12).grid(row=i, column=1, sticky="ew", padx=(15, 0), pady=7)
        ttk.Label(grid, text="当前行棋方", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=7)
        ttk.Combobox(grid, textvariable=self.turn_choice, state="readonly",
                     values=tuple(TURN_LABEL_TO_CODE), width=12).grid(row=2, column=1, sticky="ew", padx=(15, 0))
        grid.columnconfigure(1, weight=1)
        ttk.Checkbutton(settings, text="只读模式（预览走法，不点击）", variable=self.dry).pack(anchor="w", pady=10)
        row = ttk.Frame(settings, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="运行一手", command=lambda: self.start(True)).pack(side="left")
        ttk.Button(row, text="直接接管当前局", command=lambda: self.start(False)).pack(side="left", padx=8)
        card, tools = self._section(parent, "设备与诊断", "换分辨率或棋盘皮肤后，请从标准开局重新标定。")
        card.grid(row=row_offset, column=1, sticky="nsew", pady=(0, 10))
        for label, command in (("刷新模拟器状态", self.refresh_device), ("重新标定棋盘", self.calibrate),
                               ("开播体检", self.run_health_check), ("打开故障截图", self.open_diagnostics),
                               ("清理生成缓存", self.clean_cache)):
            ttk.Button(tools, text=label, command=command).pack(fill="x", pady=(0, 7))
        card, appearance = self._section(parent, "窗口外观", "顶部可随时切换主题；透明度会自动记住。")
        card.grid(row=row_offset + 1, column=0, sticky="nsew", padx=(0, 10))
        row = ttk.Frame(appearance, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="窗口透明度", style="Card.TLabel").pack(side="left")
        ttk.Label(row, textvariable=self.opacity, style="Muted.TLabel").pack(side="right")
        ttk.Scale(appearance, from_=72, to=100, variable=self.opacity,
                  command=self._change_opacity).pack(fill="x", pady=12)
        card, advanced = self._section(parent, "语音高级选项", "一般保留默认设置即可。")
        card.grid(row=row_offset + 1, column=1, sticky="nsew")
        row = ttk.Frame(advanced, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="播报间隔 / 秒", style="Card.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.live_cooldown, width=8).pack(side="right")
        ttk.Button(advanced, text="保存语音设置", command=self.save_live_settings).pack(anchor="w", pady=(12, 0))

    def preview_once(self):
        self.dry.set(True)
        self.start(True)

    def _follow_latest(self, key):
        widget, follow = (self.log, self.chess_follow) if key == "chess" else (self.live_log, self.live_follow)
        if follow.get():
            widget.see("end")

    def _clear_log_view(self, key):
        widget = self.log if key == "chess" else self.live_log
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.configure(state="disabled")

    def _configure_theme_styles(self):
        style = ttk.Style(self.root)
        font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=font, background=THEME["bg"],
                        foreground=THEME["text"])
        style.configure("App.TFrame", background=THEME["bg"])
        style.configure("Card.TFrame", background=THEME["panel"])
        style.configure("Workspace.TNotebook", background=THEME["bg"], borderwidth=0,
                        tabmargins=(0, 0, 0, 0), bordercolor=THEME["bg"],
                        lightcolor=THEME["bg"], darkcolor=THEME["bg"])
        style.configure("Workspace.TNotebook.Tab", background=THEME["bg"],
                        foreground=THEME["muted"], padding=(18, 10), borderwidth=0,
                        bordercolor=THEME["bg"], lightcolor=THEME["bg"], darkcolor=THEME["bg"])
        style.map("Workspace.TNotebook.Tab",
                  expand=[("selected", (0, 0, 0, 0))],
                  padding=[("selected", (18, 10)), ("!selected", (18, 10))],
                  background=[("selected", THEME["panel"]), ("active", THEME["panel2"])],
                  foreground=[("selected", THEME["jade"]), ("active", THEME["text"])])
        style.configure("TScrollbar", background=THEME["border"], troughcolor=THEME["console"],
                        borderwidth=0, arrowsize=12, arrowcolor=THEME["muted"])
        for name, trough in (("Slim.Vertical.TScrollbar", THEME["console"]),
                             ("Page.Slim.Vertical.TScrollbar", THEME["bg"])):
            style.layout(name, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
            style.configure(name, width=6, arrowsize=0, borderwidth=0, relief="flat",
                            background=THEME["panel2"], troughcolor=trough,
                            bordercolor=trough, lightcolor=trough, darkcolor=trough)
            style.map(name, background=[("disabled", trough), ("pressed", THEME["muted"]),
                                       ("active", THEME["border"])])
        style.configure("TScale", background=THEME["panel"], troughcolor=THEME["panel2"])
        style.configure("TLabel", background=THEME["bg"], foreground=THEME["text"])
        style.configure("Card.TLabel", background=THEME["panel"], foreground=THEME["text"])
        style.configure("Muted.TLabel", background=THEME["panel"], foreground=THEME["muted"])
        style.configure("Section.TLabel", background=THEME["panel"],
                        foreground=THEME["text"],
                        font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TEntry", fieldbackground=THEME["panel2"],
                        foreground=THEME["text"], insertcolor=THEME["text"],
                        bordercolor=THEME["border"], lightcolor=THEME["border"],
                        darkcolor=THEME["border"], padding=6)
        style.configure("TCombobox", fieldbackground=THEME["panel2"],
                        foreground=THEME["text"], arrowcolor=THEME["muted"], padding=5,
                        bordercolor=THEME["border"], lightcolor=THEME["border"],
                        darkcolor=THEME["border"], background=THEME["panel2"])
        style.map("TCombobox", fieldbackground=[("readonly", THEME["panel2"])],
                  foreground=[("readonly", THEME["text"])],
                  background=[("readonly", THEME["panel2"])],
                  bordercolor=[("focus", THEME["jade"])])
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
                ("Primary", THEME["jade"], THEME["jade"], "#ffffff"),
                ("Gold", THEME["gold"], THEME["gold"], "#ffffff"),
                ("Danger", THEME["red"], THEME["red"], "#ffffff"),
                ("Blue", THEME["blue"], THEME["blue"], "#ffffff")):
            style.configure(f"{name}.TButton", background=color, foreground=fg,
                            borderwidth=0, padding=(13, 7),
                            font=("Microsoft YaHei UI", 10, "bold"))
            style.map(f"{name}.TButton", background=[("active", active),
                                                     ("pressed", color)])
        for name in ("Action.TButton", "Action.Primary.TButton", "Action.Danger.TButton"):
            style.configure(name, padding=(16, 10), font=("Microsoft YaHei UI", 11, "bold"))
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
        color_map[old["panel"].lower()] = THEME["panel"]
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
        self._configure_speech_log_tags()
        if hasattr(self, "log"):
            self._configure_chess_log_tags()
        for name, background in (("log", "console"), ("live_log", "console"),
                                 ("persona_text", "editor"), ("intro_text", "editor")):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.configure(bg=THEME[background], fg=THEME["console_text"] if background == "console" else THEME["text"])
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
        if hasattr(self, "start_button"):
            running = bool(not self.task_cancel.is_set() and
                           self.process and self.process.poll() is None)
            busy = self.waiting_for_board or running
            self.start_button.configure(
                text="等待棋盘…" if self.waiting_for_board else "对弈进行中" if running else "▶  开始对弈")
            self.start_button.state(["disabled"] if busy else ["!disabled"])
            self.preview_button.state(["disabled"] if busy else ["!disabled"])
        if self.task_cancel.is_set():
            self.board_state.set("已停止")
            self._set_indicator("board", THEME["muted"])
        elif self.waiting_for_board:
            self.board_state.set("等待画面")
            self._set_indicator("board", THEME["gold"])
        elif self.process and self.process.poll() is None:
            self.board_state.set("对弈运行中")
            self._set_indicator("board", THEME["jade"])
        elif any(word in text for word in ("失败", "异常", "锁定")):
            self.board_state.set("需要复核")
            self._set_indicator("board", THEME["red"])
        else:
            self.board_state.set("准备就绪")
            self._set_indicator("board", THEME["muted"])

    def _sync_live_badges(self, *_):
        speech = self.speech_status.get()
        self.voice_state.set("已开启" if self.live_speech.get() else "仅文本")
        self._set_indicator(
            "voice", THEME["jade"] if self.live_speech.get() else THEME["purple"])
        live = self.live_status.get()
        if any(word in live for word in ("已连接", "连接成功")) and "未" not in live:
            self.stream_state.set("已连接")
            self._set_indicator("stream", THEME["jade"])
        elif any(word in live for word in ("正在连接", "连接中")):
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
        """Keep compatibility with service actions that reveal the transcript."""
        self.pages.select(0)
        self.broadcaster_open = True

    def append_live(self, label, text):
        """Keep operational feedback out of the spoken transcript."""
        if label == "错误":
            self.speech_status.set("播报异常：" + str(text)[:80])
        elif label in ("系统", "语音"):
            self.status.set(str(text))

    def _configure_speech_log_tags(self):
        if not hasattr(self, "live_log"):
            return
        self.live_log.tag_configure("speech_meta", foreground=THEME["muted"],
            font=("Microsoft YaHei UI", 9), lmargin1=10, lmargin2=10, rmargin=10,
            spacing1=9, spacing3=4)
        self.live_log.tag_configure("speech_body", foreground=THEME["console_text"],
            font=("Microsoft YaHei UI", 12, "bold"), lmargin1=10, lmargin2=10,
            rmargin=10, spacing1=2, spacing2=5, spacing3=16)

    @staticmethod
    def _speech_log_entry(record):
        if record.get("kind") != "reply":
            return None
        text = str(record.get("text") or "").strip()
        if not text:
            return None
        data = record.get("data") or {}
        kind = data.get("kind", "")
        label = {
            "弹幕回复": "回复观众", "礼物答谢": "礼物答谢",
            "欢迎观众": "欢迎观众", "自我介绍": "开场介绍",
            "沙盘推演解说": "沙盘推演", "开局定式解说": "开局讲解",
            "获胜收官": "本局结束", "结算收官": "本局结束",
            "绝杀长解说": "绝杀解说",
        }.get(kind, "棋局解说" if kind in CHESS_SPEECH_KINDS else "主播口播")
        # Provenance belongs to this speech job, never to the newer live board.
        meta = ControlPanel._commentary_log_label(label, data)
        timestamp = str(record.get("time") or "").strip()
        return (f"{timestamp}  ·  {meta}" if timestamp else meta), text

    def append_speech(self, record):
        entry = self._speech_log_entry(record)
        if entry is None:
            return
        metadata, text = entry
        self.live_log.configure(state="normal")
        self.live_log.insert("end", metadata + "\n", "speech_meta")
        self.live_log.insert("end", text + "\n", "speech_body")
        self._trim_text_widget(self.live_log, 1500)
        if not hasattr(self, "live_follow") or self.live_follow.get():
            self.live_log.see("end")
        self.live_log.configure(state="disabled")
        self.latest_speech.set(text)

    @staticmethod
    def _commentary_log_label(label, data):
        """Attach move provenance to UI logs only; never alter spoken text."""
        data = data or {}
        number = data.get("解说对应手数")
        move = str(data.get("解说对应走法") or
                   data.get("我方计划走法") or "").strip()
        details = []
        if number not in (None, ""):
            details.append(f"第{number}手")
        if move:
            details.append(move)
        return f"{label} · {' · '.join(details)}" if details else label

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
            self.sandtable_frame.grid_remove()

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
            self.sandtable_frame.grid(row=4, column=0, sticky="ew", pady=(0, 6))

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
        if hasattr(self, "latest_move"):
            if "我方走棋：" in text:
                self.latest_move.set(text.strip())
            elif "对方回应：" in text:
                self.latest_move.set(text.split("（", 1)[0].strip())
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
            if not hasattr(self, "chess_follow") or self.chess_follow.get():
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
        if self.task_cancel.is_set():
            return
        try:
            with cancellable_adb(self.task_cancel):
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
        self.task_cancel.set()
        self.task_cancel = threading.Event()
        if self.live_controller:
            self.live_controller.resume_tasks()
        self.auto_retry = bool(self.continuous.get())
        self.dry.set(False)
        self.status.set("一键助手启动中…")
        self.append("\n[助手] 正在准备模拟器与 JJ 象棋。标准新棋和合法残局都可以接管。\n")
        if self.auto_retry:
            self.append("[持续下棋] 已开启：胜负、投降或超时后都会继续监听下一局。\n")
        requested_turn = TURN_LABEL_TO_CODE.get(self.turn_choice.get(), "auto")
        threading.Thread(target=self._prepare_worker,
                         args=(requested_turn, self.task_cancel), daemon=True).start()

    def _prepare_worker(self, requested_turn="auto", cancel=None):
        cancel = cancel or self.task_cancel
        try:
            with cancellable_adb(cancel):
                self._prepare_worker_body(requested_turn, cancel)
        except TaskCancelled:
            pass

    def _prepare_worker_body(self, requested_turn, cancel):
        import time

        def publish(item):
            if not cancel.is_set():
                self.output.put(("assistant_task", cancel, item))

        cfg = self._assistant_cfg()
        try:
            devices = list_devices()
            if not devices:
                emulator = cfg["emulator_path"]
                if not os.path.isfile(emulator):
                    raise FileNotFoundError("未找到模拟器：" + emulator)
                publish(("status", "正在启动雷电模拟器…"))
                subprocess.Popen([emulator], cwd=os.path.dirname(emulator))
                deadline = time.monotonic() + float(cfg["device_timeout_seconds"])
                while time.monotonic() < deadline and not self.closing and not cancel.is_set():
                    devices = list_devices()
                    if devices:
                        break
                    time.sleep(2)
            if not devices:
                raise RuntimeError("等待模拟器连接超时")
            device = devices[0]
            package, _ = current_package(device)
            if package != cfg["jj_package"]:
                publish(("status", "正在打开 JJ 象棋…"))
                adb(["shell", "monkey", "-p", cfg["jj_package"],
                     "-c", "android.intent.category.LAUNCHER", "1"],
                    timeout=30, device=device)

            publish(("status", "请进入标准新棋或合法残局，稳定识别后会自动开始"))
            expected = Board.from_fen(START_FEN).sq
            stable = 0
            last_signature = None
            blocked_reported = False
            waiting_started = time.monotonic()
            last_diagnostic = 0.0
            last_mode_click = 0.0
            last_rules_dismiss = 0.0
            rules_dismiss_attempted = False
            while not self.closing and self.waiting_for_board and not cancel.is_set():
                resumed = match_live_session(device)
                if resumed is not None:
                    clear_blocked_position()
                    publish(("autostart", {
                        "turn": resumed["board"].turn,
                        "pieces": resumed["board"].attacker_count(),
                        "standard": False,
                        "resume": True,
                    }))
                    return
                image = screenshot(device)
                if cancel.is_set():
                    return
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
                            publish(("status", "已连续确认结算页面，正在自动进入下一局…"))
                            actions = handle_postgame(device)
                            if actions:
                                publish(("status", "结算已处理，正在识别下一局棋盘"))
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
                            publish((
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
                            publish((
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
                        publish((
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
                        publish(("autostart", {
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
        except TaskCancelled:
            return
        except Exception as error:
            if cancel.is_set():
                return
            if "device" in locals():
                self._save_diagnostic(
                    device, "watcher_exception", extra={"error": str(error)})
            publish(("assistant_error", str(error)))

    def _launch(self, arguments, label):
        if self.process and self.process.poll() is None:
            messagebox.showinfo("正在运行", "请先停止当前任务")
            return
        self.task_cancel.set()
        self.task_cancel = threading.Event()
        if self.live_controller:
            self.live_controller.resume_tasks()
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

        process = self.process
        cancel = self.task_cancel

        def reader():
            for line in process.stdout:
                self.output.put(("player_output", cancel, line))
            code = process.wait()
            self.output.put(("done", code, process))

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
        self.task_cancel.set()
        self.waiting_for_board = False
        self.auto_retry = False
        self._cancel_sandtable_jobs()
        self._close_sandtable_ui()
        if self.live_controller:
            self.live_controller.stop_tasks()
        os.makedirs(os.path.dirname(STOP_FILE), exist_ok=True)
        with open(STOP_FILE, "w", encoding="utf-8") as stream:
            stream.write("stop\n")
        process = self.process
        if process and process.poll() is None:
            # Kill the whole task tree, including a running ADB command and
            # Pikafish.  Do not wait for another recognition/postgame cycle.
            self._terminate_task(process)
        self.board_state.set("已停止")
        self.status.set("已停止：识别、对弈和播报已取消")
        self.append("[控制指令] 已立即停止识别、对弈和播报\n")

    @staticmethod
    def _terminate_task(process):
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            process.kill()

    def _schedule_prepare(self, delay=1200):
        cancel = self.task_cancel

        def restart():
            if not cancel.is_set() and not self.closing:
                self.prepare_and_wait()

        self.root.after(delay, restart)

    def _poll(self):
        try:
            while True:
                item = self.output.get_nowait()
                if isinstance(item, tuple) and item[0] in ("assistant_task", "player_output"):
                    if item[1].is_set() or item[1] is not self.task_cancel:
                        continue
                    item = item[2]
                if isinstance(item, tuple) and item[0] == "done":
                    if len(item) > 2 and item[2] is not self.process:
                        continue
                    if self.task_cancel.is_set():
                        continue
                    keep_running = (self.auto_retry and bool(self.continuous.get())
                                    and not self.closing)
                    if item[1] == 10:
                        self.status.set("结算已处理，正在等待下一局…")
                        self.append("[助手] 已完成结算页面操作，继续等待下一局。\n")
                        if keep_running:
                            self._schedule_prepare()
                    elif item[1] == 0:
                        if keep_running:
                            self.status.set("本次对弈已停止，正在自动衔接下一局…")
                            self.append("[助手] 本次对弈结束或安全停止，继续处理结算并等待下一局。\n")
                            self._schedule_prepare()
                        else:
                            self.status.set("任务已正常结束")
                            self.append("[任务结束] 程序已正常停止。\n")
                    elif item[1] == 15:
                        self.auto_retry = False
                        self.waiting_for_board = False
                        if self.live_controller:
                            self.live_controller.stop_tasks()
                        self.status.set("本手语音未启动，已暂停；未发送落子")
                        self.append("[语音同步] 已取消待播台词。检查语音后重新开始，程序会核对原局面。\n")
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
                            self._schedule_prepare()
                    else:
                        self.status.set("本次接管未成功，继续等待稳定棋盘…")
                        self.append("[助手] 本次画面识别未通过，未执行点击；继续等待。\n")
                        if keep_running:
                            self._schedule_prepare()
                elif isinstance(item, tuple) and item[0] == "status":
                    self.status.set(item[1])
                    self.append("[助手] " + item[1] + "\n")
                elif isinstance(item, tuple) and item[0] == "autostart":
                    if self.task_cancel.is_set() or not self.waiting_for_board:
                        continue
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
                    self.append("[开播体检] 检查结果：\n")
                    for check in items:
                        self.append(
                            f"{icons.get(check['level'], '·')} "
                            f"{check['label']}：{check['detail']}\n")
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
                    if self.task_cancel.is_set() and kind in (
                            "reply", "speech_status", "queued", "chess", "sandtable"):
                        continue
                    text = record.get("text", "")
                    if kind == "sandtable":
                        self._handle_sandtable(record.get("data", {}))
                        continue
                    if kind == "connection":
                        self.live_status.set(text)
                    elif kind == "speech_status":
                        self.speech_status.set(text)
                    elif kind == "error":
                        self.speech_status.set("播报异常：" + str(text)[:80])
                    elif kind == "reply":
                        self.append_speech(record)

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
