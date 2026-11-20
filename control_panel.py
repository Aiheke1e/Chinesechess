from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from adb_bridge import (adb, current_package, list_devices, resolution,
                        screenshot, tap, cancellable_adb, TaskCancelled)
from board import Board, START_FEN
from broadcast_view import BroadcastView
from borderless_window import BorderlessControls
from avatar_picker import AvatarPicker, avatar_caption, import_avatar
from broadcast_layout import chess_window_bounds, popup_position
from monitoring import MonitorIssues, player_view, speech_view, live_view
from diagnostics import DIAGNOSTIC_DIR, capture_diagnostic
from recognizer import recognize_position
from state import position_errors, validate_board
from session_state import (blocked_position_matches, clear_blocked_position,
                           infer_turn_from_standard, match_live_session)
from cache_cleanup import cleanup_generated_cache, scan_generated_cache
from tts_backend import available_voices
from local_tts_client import ensure_service as ensure_local_tts
from douyin_auth import clear_cookie
from live_host import CHESS_SPEECH_KINDS, LiveController, load_settings
from health_check import collect_readiness, readiness_level
from runtime_status import (heartbeat_age, read_runtime_status, rotate_log,
                            write_runtime_status)
from console_format import styled_player_line
from room_feed import room_message_entry
from soft_ui import SoftCard, install_button_styles, ui_scale, blend
from desktop_display import enable_high_dpi
from board import Board
from stream_brand import APP_TITLE, AREA_A_TITLE
from game_control import CONTROL_PATH as GAME_CONTROL_PATH, write_auto_continue
from stream_shutdown import StreamShutdown
from companion_shutdown import CompanionShutdown

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
    "清晰蓝白": {
        "bg": "#e4ebf3", "panel": "#ffffff", "panel2": "#edf2f7",
        "border": "#b9c9d9", "text": "#172d44", "muted": "#50667b",
        "jade": "#16764f", "jade2": "#dcefe5", "gold": "#986015",
        "red": "#bb3f50", "blue": "#205f9f", "purple": "#71539c",
        "console": "#ffffff", "console_text": "#20364c",
        "editor": "#f0f4f8", "select": "#c6e2f5",
        "button": "#dfe8f2", "button_active": "#cbdced",
        "ok_bg": "#dcefe5", "ok_fg": "#145e40",
        "wait_bg": "#f6e8cb", "wait_fg": "#7e5210",
        "error_bg": "#fae0e3", "error_fg": "#9f283c",
    },
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
THEME = dict(THEMES["清晰蓝白"])


def load_ui_settings():
    defaults = {"theme": "清晰蓝白", "opacity": 100, "preview_visible": True,
                "broadcast_width": 600, "broadcast_height": 160, "broadcast_sized": False,
                "area_a_avatar_path": "", "auto_continue": True}
    try:
        with open(UI_CFG, encoding="utf-8") as stream:
            saved = json.load(stream)
        if saved.get("appearance_version", 0) < 2:
            saved.update(opacity=100)
        name = {"青瓷清风": "云白青黛", "深海极光": "午夜蓝",
                "墨玉流金": "暖砂茶", "雾蓝柔光": "清晰蓝白"}.get(saved.get("theme"), saved.get("theme"))
        if name in THEMES:
            defaults["theme"] = name
        defaults["opacity"] = max(72, min(100, int(saved.get("opacity", 100))))
        defaults["preview_visible"] = bool(saved.get("preview_visible", True))
        defaults["auto_continue"] = saved.get("auto_continue", True) is True
        defaults["broadcast_width"] = max(260, min(1600, int(saved.get("broadcast_width", 600))))
        defaults["broadcast_height"] = max(110, min(600, int(saved.get("broadcast_height", 160))))
        defaults["broadcast_sized"] = "broadcast_width" in saved and "broadcast_height" in saved
        if isinstance(saved.get("area_a_avatar_path"), str):
            defaults["area_a_avatar_path"] = saved["area_a_avatar_path"]
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
        self.preview_visible = ui.get("preview_visible", True)
        self.preview_enabled = tk.BooleanVar(value=self.preview_visible)
        self.broadcast_visible = tk.BooleanVar(value=False)
        self.broadcast_width = tk.StringVar(value=str(ui.get("broadcast_width", 600)))
        self.broadcast_height = tk.StringVar(value=str(ui.get("broadcast_height", 160)))
        self.broadcast_sized = ui.get("broadcast_sized", False)
        self.area_a_avatar_path = ui.get("area_a_avatar_path", "")
        self.monitor_issues = MonitorIssues()
        self._monitor_job = None
        self._player_runtime = None
        self.connection_state = "disconnected"
        self.room_metrics = {}
        self.process = None
        self.output = queue.Queue()
        self.room_pending = deque(maxlen=3000)
        self.room_pending_lock = threading.Lock()
        self.room_feed_epoch = None
        self.status = tk.StringVar(value="正在检查设备…")
        self.dry = tk.BooleanVar(value=True)
        self.continuous = tk.BooleanVar(value=ui.get("auto_continue", True))
        self.movetime = tk.StringVar(value="800")
        self.max_moves = tk.StringVar(value="150")
        self.turn_choice = tk.StringVar(value="自动判断")
        self.auto_retry = autostart
        self.waiting_for_board = False
        self.waiter_automatic = False
        self.chess_session_requested = False
        self._prepare_job = None
        self._prepare_generation = 0
        self.task_cancel = threading.Event()
        self.closing = False
        self.stream_shutdown = StreamShutdown()
        self.shutdown_cancel = threading.Event()
        self.shutdown_time = tk.StringVar(value="23:30")
        self.shutdown_scheduled = tk.BooleanVar(value=False)
        self.shutdown_status = tk.StringVar(value="")
        self.live_controller = None
        live = load_settings()
        self.live_room = tk.StringVar(value=live.room)
        self.live_model = tk.StringVar(value=live.model)
        self.live_voice = tk.StringVar(value=live.voice)
        self.live_density = tk.StringVar(value=live.commentary_density)
        self.live_welcome = tk.BooleanVar(value=live.welcome_enabled)
        self.live_gifts = tk.BooleanVar(value=live.gift_enabled)
        self.live_speech = tk.BooleanVar(value=live.speech_enabled)
        self.live_commentary = tk.BooleanVar(value=live.commentary_enabled)
        self.live_sandtable = tk.BooleanVar(value=live.sandtable_enabled)
        self.live_queen_commentary = tk.BooleanVar(
            value=live.queen_commentary_enabled)
        self.live_status = tk.StringVar(value="直播间未连接")
        self.broadcaster_open = False
        self.speech_status = tk.StringVar(
            value="语音已开启" if live.speech_enabled else "仅文本模式")
        self.device_state = tk.StringVar(value="检测中")
        self.board_state = tk.StringVar(value="等待棋盘")
        self.voice_state = tk.StringVar(
            value="已开启" if live.speech_enabled else "仅文本")
        self.stream_state = tk.StringVar(value="未连接")
        self.audience_text = tk.StringVar(value="在线人数 · 待获取")
        self.chat_sampling_text = tk.StringVar(value="自动抽选 · 无需弹幕前缀")
        self.interaction_state = tk.StringVar(value="互动未开启")
        self.interaction_user = tk.StringVar(value="")
        self.interaction_message = tk.StringVar(value="")
        self.broadcast_window = None
        self.interaction_id = None
        self.runtime_stats = tk.StringVar(value="识别告警 0 · 成功落子 0 · 完成对局 0")
        self.session_summary = tk.StringVar(value="本次 0 手 · 0 局")
        self.attention_text = tk.StringVar(value="")
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
        root.title(APP_TITLE)
        scale = ui_scale(root)
        width = min(round(1240 * scale), root.winfo_screenwidth() - round(60 * scale))
        height = min(round(800 * scale), root.winfo_screenheight() - round(90 * scale))
        root.geometry(f"{width}x{height}")
        root.minsize(min(round(1000 * scale), width), min(round(680 * scale), height))
        root.configure(bg=THEME["bg"])
        try:
            root.attributes("-alpha", ui["opacity"] / 100)
        except tk.TclError:
            pass
        self._build()
        self.status.trace_add("write", self._sync_status_badges)
        self.speech_status.trace_add("write", self._sync_live_badges)
        self.live_status.trace_add("write", self._sync_live_badges)
        self.live_speech.trace_add("write", self._queue_monitor)
        try:
            self.live_controller = LiveController(self._receive_live_event)
            self.append_live("系统", "就绪")
            self._sync_interaction_button()
        except Exception as error:
            self._note_issue("后台", "直播互动模块初始化失败：" + str(error))
            self.append_live("错误", "直播互动模块初始化失败：" + str(error))
        self.refresh_device()
        root.after(80, self._poll)
        root.after(1500, self._watchdog_tick)
        self._launch_local_tts()
        root.after(250, self._shutdown_tick)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if autostart:
            self._schedule_prepare(delay=400, automatic=False)

    def _launch_local_tts(self):
        previous = getattr(self, "_local_tts_startup", None)
        if previous is not None and previous.is_alive():
            return
        self._local_tts_startup = threading.Thread(
            target=self._start_local_tts, name="local-tts-startup", daemon=True)
        self._local_tts_startup.start()

    def _request_start(self):
        self._launch_local_tts()
        self.prepare_and_wait()

    def _start_local_tts(self):
        try:
            ensure_local_tts()
        except Exception as error:
            self.output.put(("local_tts_startup_error", str(error)))

    def _build(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self._configure_theme_styles()
        self.chess_follow = tk.BooleanVar(value=True)
        self.live_follow = tk.BooleanVar(value=True)
        self.room_follow = tk.BooleanVar(value=True)
        self.latest_move = tk.StringVar(value="棋盘准备就绪，开始后显示最新走法与局势。")
        self.latest_speech = tk.StringVar(value="讲解准备就绪，开局后将在这里显示最新内容。")

        self.broadcast_view = BroadcastView(
            self.root, self.interaction_user, self.interaction_message, THEME,
            on_popout=self.open_broadcast_window, avatar_path=self.area_a_avatar_path)
        self.broadcast_view.configure(width=round(int(self.broadcast_width.get()) * ui_scale(self.root)))
        if self.preview_visible:
            self.broadcast_view.pack(anchor="center", padx=16, pady=(12, 10))
        self.theme_name.trace_add("write", self._change_theme)

        controls = self.controls = self._ui_card(self.root)
        controls.pack(fill="x", padx=16, pady=(0 if self.preview_visible else 12, 6))
        control_row = ttk.Frame(controls.content, style="Card.TFrame")
        control_row.pack(fill="x")
        actions = self.actionbar = ttk.Frame(control_row, style="Card.TFrame", padding=(4, 6))
        actions.pack(side="left")
        self.start_button = ttk.Button(actions, text="▶ 开始下棋", width=11,
            style="Action.Primary.TButton", command=self._request_start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="暂停下棋", width=11,
            style="Action.Danger.TButton", command=self.stop)
        self.stop_button.pack(side="left", padx=(4, 12))
        indicators = tk.Frame(control_row, bg=THEME["panel"])
        indicators.pack(side="left", fill="both", expand=True, padx=(12, 4), pady=2)
        self.monitor_labels = {}
        for i, (key, caption) in enumerate((("board", "对弈"), ("voice", "语音"), ("stream", "直播"))):
            indicators.columnconfigure(i, weight=1, uniform="monitor")
            cell = tk.Frame(indicators, bg=THEME["panel2"], padx=8, pady=6)
            cell.grid(row=0, column=i, sticky="nsew", padx=(0, 6 if i < 2 else 0))
            cell.columnconfigure(2, weight=1)
            dot = tk.Canvas(cell, width=10, height=10, bg=THEME["panel2"], highlightthickness=0)
            dot.grid(row=0, column=0, padx=(0, 5))
            oval = dot.create_oval(2, 2, 8, 8, fill=THEME["muted"], outline=THEME["muted"])
            tk.Label(cell, text=caption, bg=THEME["panel2"], fg=THEME["muted"],
                     font=("Microsoft YaHei UI", 9)).grid(row=0, column=1, sticky="w", padx=(0, 10))
            title = tk.Label(cell, text="待机", bg=THEME["panel2"], fg=THEME["text"],
                             font=("Microsoft YaHei UI", 10, "bold"), anchor="w")
            title.grid(row=0, column=2, sticky="ew")
            detail = tk.Label(cell, text="", bg=THEME["panel2"], fg=THEME["muted"],
                              font=("Microsoft YaHei UI", 9), anchor="w")
            detail.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(3, 0))
            detail.bind("<Configure>", lambda event, target=detail:
                        target.configure(wraplength=max(120, event.width)))
            self.monitor_labels[key] = (title, detail)
            self.indicators[key] = (dot, oval)
        self.header_status = self.monitor_labels["board"][0]
        self.attention_bar = tk.Frame(controls.content, bg=THEME["error_bg"])
        self.attention_label = tk.Label(self.attention_bar, textvariable=self.attention_text,
            bg=THEME["error_bg"], fg=THEME["error_fg"], font=("Microsoft YaHei UI", 9),
            anchor="w", justify="left", padx=12, pady=7)
        self.attention_label.pack(side="left", fill="x", expand=True)
        self.attention_label.bind("<Configure>", lambda event:
            self.attention_label.configure(wraplength=max(200, event.width - 24)))

        footer = tk.Frame(self.root, bg=THEME["bg"])
        footer.pack(side="bottom", fill="x", padx=20, pady=(8, 12))
        tk.Label(footer, textvariable=self.session_summary, bg=THEME["bg"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 9)).pack(side="right", padx=(16, 0))
        self.footer_status = tk.Label(footer, textvariable=self.status, anchor="w", justify="left",
            bg=THEME["bg"], fg=THEME["muted"], font=("Microsoft YaHei UI", 9))
        self.footer_status.pack(side="left", fill="x", expand=True)
        self.footer_status.bind("<Configure>", lambda event: self.footer_status.configure(wraplength=max(200, event.width)))

        self.pages = ttk.Notebook(self.root, style="Workspace.TNotebook")
        self.pages.pack(fill="both", expand=True, padx=16)
        workspace = ttk.Frame(self.pages, style="App.TFrame", padding=(0, 8, 0, 0))
        self.pages.add(workspace, text="  工作台  ")
        self.live_settings_page = self._scroll_page("  设置  ")
        self._build_workspace(workspace)
        self._build_live_settings(self.live_settings_page)
        self._layout_preview()
        self.root.bind("<F8>", lambda event: self.stop())
        self.root.bind("<MouseWheel>", self._scroll_settings, add="+")
        self.broadcaster_open = True
        self._queue_monitor()

    @staticmethod
    def _ui_card(parent):
        return SoftCard(parent, THEME, inset=7)

    def open_broadcast_window(self):
        """Open a clean, opaque capture target that follows the same comment."""
        if hasattr(self, "broadcast_visible"):
            self.broadcast_visible.set(True)
        existing = getattr(self, "broadcast_window", None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return
        window = self.broadcast_window = tk.Toplevel(self.root)
        window.withdraw()
        window.overrideredirect(True)
        window.title(AREA_A_TITLE)
        scale = ui_scale(self.root)
        if not hasattr(self, "broadcast_width"):
            self.broadcast_width = tk.StringVar(master=self.root, value="600")
            self.broadcast_height = tk.StringVar(master=self.root, value="160")
        game = chess_window_bounds()
        if game and not getattr(self, "broadcast_sized", False):
            self.broadcast_width.set(str(round((game["viewport"][2] - game["viewport"][0]) / scale)))
        width, height = self._broadcast_dimensions()
        width, height = round(width * scale), round(height * scale)
        x, y = popup_position((width, height),
            (0, round(38 * scale), self.root.winfo_screenwidth(), self.root.winfo_screenheight() - round(48 * scale)),
            game["outer"] if game else None, margin=round(12 * scale))
        window.geometry(f"{width}x{height}{x:+d}{y:+d}")
        window.minsize(round(260 * scale), round(110 * scale))
        window.maxsize(min(round(1600 * scale), self.root.winfo_screenwidth() - round(24 * scale)),
            min(round(600 * scale), self.root.winfo_screenheight() - round(110 * scale)))
        window.resizable(True, True)
        window.configure(bg="#101d34")
        window.attributes("-alpha", 1.0)
        view = BroadcastView(window, self.interaction_user, self.interaction_message, THEME,
            avatar_path=getattr(self, "area_a_avatar_path", ""))
        view.pack(fill="both", expand=True)
        window.capture_controls = BorderlessControls(window, view)
        window.bind("<Configure>", self._remember_broadcast_size)
        window.protocol("WM_DELETE_WINDOW", self.hide_broadcast_window)
        window.deiconify()

    def _set_area_a_avatar(self, path):
        if not self.broadcast_view.set_avatar(path, strict=True):
            return False
        self.area_a_avatar_path = path
        if hasattr(self, "area_a_avatar_name"):
            self.area_a_avatar_name.set(avatar_caption(path))
        window = getattr(self, "broadcast_window", None)
        if window is not None and window.winfo_exists():
            for view in window.winfo_children():
                if isinstance(view, BroadcastView):
                    view.set_avatar(path)
        picker = getattr(self, "avatar_picker_window", None)
        if picker is not None and picker.winfo_exists():
            picker.set_selection(path)
        self._schedule_ui_save()
        return True

    def open_avatar_picker(self):
        picker = getattr(self, "avatar_picker_window", None)
        if picker is not None and picker.winfo_exists():
            picker.deiconify()
            picker.lift()
            return
        self.avatar_picker_window = AvatarPicker(self.root,
            getattr(self, "area_a_avatar_path", ""), self._set_area_a_avatar,
            self.choose_area_a_avatar, dict(THEME))

    def choose_area_a_avatar(self, parent=None):
        parent = parent or self.root
        path = filedialog.askopenfilename(parent=parent, title="上传互动展示头像",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            imported = import_avatar(path)
            if not self._set_area_a_avatar(imported):
                os.unlink(imported)
                raise ValueError("图片无法读取")
        except (OSError, ValueError):
            messagebox.showerror("头像未更换", "图片无法读取或保存，请选择 PNG、JPG 或 WebP 图片。", parent=parent)

    def reset_area_a_avatar(self):
        self._set_area_a_avatar("")

    def _broadcast_dimensions(self):
        try:
            width, height = int(self.broadcast_width.get()), int(self.broadcast_height.get())
        except (ValueError, tk.TclError):
            width, height = 600, 160
        scale = ui_scale(self.root)
        width = max(260, min(width, 1600, round(self.root.winfo_screenwidth() / scale) - 24))
        height = max(110, min(height, 600, round(self.root.winfo_screenheight() / scale) - 110))
        return width, height

    def apply_broadcast_size(self, event=None):
        width, height = self._broadcast_dimensions()
        self.broadcast_width.set(str(width))
        self.broadcast_height.set(str(height))
        self.broadcast_sized = True
        scale = ui_scale(self.root)
        view = getattr(self, "broadcast_view", None)
        if view is not None and view.winfo_exists():
            view.configure(width=round(width * scale))
        window = getattr(self, "broadcast_window", None)
        if window is not None and window.winfo_exists():
            pixels_w, pixels_h = round(width * scale), round(height * scale)
            x = max(0, min(window.winfo_x(), self.root.winfo_screenwidth() - pixels_w))
            y = max(0, min(window.winfo_y(), self.root.winfo_screenheight() - pixels_h))
            window.geometry(f"{pixels_w}x{pixels_h}{x:+d}{y:+d}")
        self._schedule_ui_save()

    def align_broadcast_width(self):
        game = chess_window_bounds()
        if game:
            self.broadcast_width.set(str(round((game["viewport"][2] - game["viewport"][0]) / ui_scale(self.root))))
            self.apply_broadcast_size()
        elif hasattr(self, "status"):
            self.status.set("请先展开象棋窗口，再对齐展示宽度。")

    def _focus_broadcast_size(self):
        self._show_settings()
        self.broadcast_width_entry.focus_set()
        self.broadcast_width_entry.selection_range(0, "end")

    def _remember_broadcast_size(self, event):
        if event.widget is not self.broadcast_window or event.width < 100 or event.height < 50:
            return
        scale = ui_scale(self.root)
        self.broadcast_width.set(str(round(event.width / scale)))
        self.broadcast_height.set(str(round(event.height / scale)))
        self.broadcast_sized = True
        preview = getattr(self, "broadcast_view", None)
        if preview is not None and preview.winfo_exists():
            preview.configure(width=event.width)
        if hasattr(self, "theme_name"):
            self._schedule_ui_save()

    def close_broadcast_window(self):
        window, self.broadcast_window = getattr(self, "broadcast_window", None), None
        if window is not None and window.winfo_exists():
            window.destroy()
        if hasattr(self, "broadcast_visible"):
            self.broadcast_visible.set(False)

    def hide_broadcast_window(self):
        window = getattr(self, "broadcast_window", None)
        if window is not None and window.winfo_exists():
            window.withdraw()
        if hasattr(self, "broadcast_visible"):
            self.broadcast_visible.set(False)

    def toggle_broadcast_window(self):
        if self.broadcast_visible.get():
            self.open_broadcast_window()
        else:
            self.hide_broadcast_window()

    def toggle_preview(self):
        self.preview_visible = not self.preview_visible
        self._layout_preview()
        if hasattr(self, "preview_enabled"):
            self.preview_enabled.set(self.preview_visible)
        self._schedule_ui_save()

    def _layout_preview(self):
        visible = self.preview_visible
        if visible:
            self.broadcast_view.pack(anchor="center", padx=16, pady=(12, 10), before=self.controls)
        else:
            self.broadcast_view.pack_forget()
        self.controls.pack_configure(pady=(0 if visible else 12, 6))

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
        self.log_visible = {"chess": True, "live": True, "room": False}
        self.log_vars = {key: tk.BooleanVar(value=visible) for key, visible in self.log_visible.items()}
        self.log_buttons = {}
        self.body = tk.PanedWindow(parent, orient="horizontal", bg=THEME["bg"],
                                   sashwidth=8, sashrelief="flat", bd=0)
        self.body.pack(fill="both", expand=True)
        self.body.bind("<Configure>", lambda event: self._queue_log_balance())
        self.chess_panel = self._ui_card(self.body)
        self.live_panel = self._ui_card(self.body)
        self.room_panel = self._ui_card(self.body)
        self.log_panels = {"chess": self.chess_panel, "live": self.live_panel, "room": self.room_panel}
        self.logs_empty = ttk.Frame(parent, style="App.TFrame")
        self._log_layout_job = None
        self._layout_logs()
        for key, panel, title, follow in (
                ("chess", self.chess_panel, "棋局记录", self.chess_follow),
                ("live", self.live_panel, "解说记录", self.live_follow),
                ("room", self.room_panel, "直播间消息", self.room_follow)):
            surface = panel.content
            surface.columnconfigure(0, weight=1)
            surface.rowconfigure(5, weight=1)
            heading = ttk.Frame(surface, style=f"{key}.Log.TFrame", padding=(12, 9))
            heading.grid(row=0, column=0, sticky="ew")
            ttk.Label(heading, text=title, style=f"{key}.Log.TLabel").pack(side="left")
            menu = tk.Menu(self.root, tearoff=False, bg=THEME["panel"], fg=THEME["text"])
            menu.add_command(label="跳过旧播报" if key == "live" else "回到最新",
                command=self.jump_live_speech if key == "live" else lambda k=key: self._log_widget(k).see("end"))
            menu.add_checkbutton(label="跟随最新", variable=follow, command=lambda k=key: self._follow_latest(k))
            menu.add_separator()
            menu.add_command(label="清空显示", command=lambda k=key: self._clear_log_view(k))
            ttk.Menubutton(heading, text="···", width=2, menu=menu, style=f"{key}.Log.TMenubutton").pack(side="right")
            log_frame = ttk.Frame(surface, style="Card.TFrame", padding=(2, 0, 2, 2))
            log_frame.grid(row=5, column=0, sticky="nsew")
            text = tk.Text(log_frame, bg=THEME["console"], fg=THEME["console_text"],
                insertbackground=THEME["text"], selectbackground=THEME["select"],
                font=("Microsoft YaHei UI", 11), wrap="word", state="disabled", relief="flat",
                width=1, height=1, padx=12, pady=12, highlightthickness=0)
            scroll = ttk.Scrollbar(log_frame, orient="vertical", command=text.yview, style="Slim.Vertical.TScrollbar")
            scroll.pack(side="right", fill="y")
            text.pack(side="left", fill="both", expand=True)
            text.configure(yscrollcommand=lambda first, last, bar=scroll: self._update_scrollbar(bar, first, last))
            if key == "chess":
                self.log = text
            elif key == "live":
                self.live_log = text
                self.live_log_container = log_frame
            else:
                self.room_log = text
        self._configure_chess_log_tags()
        self._configure_speech_log_tags()
        self._configure_room_log_tags()
        self._build_sandtable(self.live_panel.content)

    def _log_widget(self, key):
        return {"chess": self.log, "live": self.live_log, "room": self.room_log}[key]

    def toggle_log(self, key):
        self.log_visible[key] = not self.log_visible[key]
        self._layout_logs()

    def _layout_logs(self):
        for pane in self.body.panes():
            self.body.forget(pane)
        visible = [key for key in self.log_panels if self.log_visible[key]]
        for key, variable in self.log_vars.items():
            variable.set(self.log_visible[key])
        if visible:
            self.logs_empty.pack_forget()
            self.body.pack(fill="both", expand=True)
            for key in visible:
                self.body.add(self.log_panels[key], stretch="always", minsize=260)
            self._queue_log_balance()
        else:
            self.body.pack_forget()
            self.logs_empty.pack(fill="both", expand=True)

    def _queue_log_balance(self):
        if self._log_layout_job:
            self.root.after_cancel(self._log_layout_job)
        self._log_layout_job = self.root.after_idle(self._balance_logs)

    def _balance_logs(self):
        self._log_layout_job = None
        count = len(self.body.panes())
        width = self.body.winfo_width()
        for index in range(count - 1):
            self.body.sash_place(index, round((index + 1) * width / count) - 4, 0)

    def _reveal_room_log(self, epoch):
        # Reveal once per connection. A user's later hide remains respected.
        if not hasattr(self, "log_visible") or self.room_feed_epoch == epoch:
            return
        self.room_feed_epoch = epoch
        self._clear_log_view("room")
        self.log_visible["room"] = True
        self._layout_logs()

    def _show_settings(self, section="live"):
        self.pages.select(1)
        canvas = self._scroll_pages[0][1]
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
        parent.columnconfigure(0, weight=1)
        surface = self._ui_card(parent)
        surface.grid(row=0, column=0, sticky="nsew")
        form = self.settings_form = ttk.Frame(surface.content, style="Card.TFrame", padding=(10, 8))
        form.pack(fill="both", expand=True)
        form.columnconfigure(0, minsize=82)
        form.columnconfigure((1, 2, 3, 4, 5), weight=1, uniform="settings")
        self.toggle_group = form

        def label(text, row):
            ttk.Label(form, text=text, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=8)

        label("直播间", 0)
        connection_row = ttk.Frame(form, style="Card.TFrame")
        connection_row.grid(row=0, column=1, columnspan=5, sticky="ew")
        self.room_entry = ttk.Entry(connection_row, textvariable=self.live_room, width=1)
        self.room_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        actions = self.settings_actions = ttk.Frame(connection_row, style="Card.TFrame")
        actions.pack(side="right")
        self.live_connect_button = ttk.Button(actions, text="连接直播间", width=10,
            style="Primary.TButton", command=self.toggle_live_connection)
        self.live_connect_button.pack(side="left")
        self.interaction_button = ttk.Button(actions, text="开启弹幕互动", width=12, command=self.toggle_interaction)
        self.interaction_button.pack(side="left", padx=(6, 0))
        status_row = ttk.Frame(form, style="Card.TFrame")
        status_row.grid(row=1, column=1, columnspan=5, sticky="ew", pady=(4, 10))
        ttk.Label(status_row, textvariable=self.live_status, style="Muted.TLabel").pack(side="left")
        ttk.Label(status_row, textvariable=self.audience_text, style="Muted.TLabel").pack(side="right")

        save = lambda: self.save_live_settings(quiet=True)
        rows = (
            (2, "对弈", (("自动连局", self.continuous, self.toggle_auto_continue),
                ("解说", self.live_commentary, self.toggle_live_commentary),
                ("语音", self.live_speech, self.toggle_live_speech),
                ("沙盘演示", self.live_sandtable, save),
                ("延伸讲解", self.live_queen_commentary, save))),
            (4, "互动展示", (("顶部预览", self.preview_enabled, self.toggle_preview),
                ("独立窗口", self.broadcast_visible, self.toggle_broadcast_window))),
            (6, "日志", tuple((title, self.log_vars[key], lambda k=key: self.toggle_log(k))
                for key, title in (("chess", "棋局记录"), ("live", "解说记录"), ("room", "直播间消息")))),
        )
        self.toggle_widgets = {}
        for number, caption, options in rows:
            label(caption, number)
            for column, (title, variable, command) in enumerate(options, start=1):
                switch = ttk.Checkbutton(form, text=title, variable=variable,
                    command=command, style="Settings.TCheckbutton")
                switch.grid(row=number, column=column, sticky="w", padx=(0, 8), pady=8)
                self.toggle_widgets[title] = switch
        self.preview_toggle = self.toggle_widgets["顶部预览"]
        self.log_buttons = {key: (self.toggle_widgets[title], title)
            for key, title in (("chess", "棋局记录"), ("live", "解说记录"), ("room", "直播间消息"))}

        size_row = ttk.Frame(form, style="Card.TFrame")
        size_row.grid(row=4, column=3, columnspan=3, sticky="w")
        ttk.Label(size_row, text="宽", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        self.broadcast_width_entry = ttk.Spinbox(size_row, from_=260, to=1600, increment=10,
            textvariable=self.broadcast_width, width=5, command=self.apply_broadcast_size)
        self.broadcast_width_entry.pack(side="left")
        ttk.Label(size_row, text="高", style="Muted.TLabel").pack(side="left", padx=(10, 4))
        height_entry = ttk.Spinbox(size_row, from_=110, to=600, increment=10,
            textvariable=self.broadcast_height, width=5, command=self.apply_broadcast_size)
        height_entry.pack(side="left")
        ttk.Button(size_row, text="对齐棋盘", width=8, command=self.align_broadcast_width).pack(side="left", padx=(10, 0))
        for entry in (self.broadcast_width_entry, height_entry):
            entry.bind("<Return>", self.apply_broadcast_size)
            entry.bind("<FocusOut>", self.apply_broadcast_size)

        label("展示头像", 5)
        avatar_row = ttk.Frame(form, style="Card.TFrame")
        avatar_row.grid(row=5, column=1, columnspan=3, sticky="w", pady=5)
        self.area_a_avatar_name = tk.StringVar(master=self.root, value=avatar_caption(self.area_a_avatar_path))
        ttk.Label(avatar_row, textvariable=self.area_a_avatar_name, style="Muted.TLabel").pack(side="left", padx=(0, 10))
        self.avatar_presets_button = ttk.Button(avatar_row, text="默认头像", command=self.open_avatar_picker)
        self.avatar_presets_button.pack(side="left", padx=(0, 6))
        self.avatar_upload_button = ttk.Button(avatar_row, text="上传图片", command=self.choose_area_a_avatar)
        self.avatar_upload_button.pack(side="left")
        ttk.Label(form, text="主题", style="Card.TLabel").grid(row=5, column=4, sticky="e", padx=(0, 10))
        theme_combo = ttk.Combobox(form, textvariable=self.theme_name, values=tuple(THEMES),
            state="readonly", width=10)
        theme_combo.grid(row=5, column=5, sticky="ew", pady=5)
        theme_combo.bind("<<ComboboxSelected>>", lambda event: self.save_live_settings(quiet=True))

        label("音色", 7)
        voice_combo = ttk.Combobox(form, textvariable=self.live_voice, values=available_voices(),
            state="readonly", width=16)
        voice_combo.grid(row=7, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(form, text="解说频率", style="Card.TLabel").grid(row=7, column=2, sticky="e", padx=(0, 10))
        density_combo = ttk.Combobox(form, textvariable=self.live_density,
            values=("安静", "自然", "热闹"), state="readonly", width=10)
        density_combo.grid(row=7, column=3, sticky="ew", pady=(8, 0))
        for combo in (voice_combo, density_combo):
            combo.bind("<<ComboboxSelected>>", lambda event: self.save_live_settings(quiet=True))
        ttk.Label(form, text="思考时间", style="Card.TLabel").grid(row=7, column=4, sticky="e", padx=(0, 10))
        time_row = ttk.Frame(form, style="Card.TFrame")
        time_row.grid(row=7, column=5, sticky="ew", pady=(8, 0))
        ttk.Entry(time_row, textvariable=self.movetime, width=7).pack(side="left", fill="x", expand=True)
        ttk.Label(time_row, text="毫秒", style="Muted.TLabel").pack(side="left", padx=(5, 0))
        label("说话风格", 8)
        self.persona_text = tk.Text(form, height=3, width=1, wrap="word", bg=THEME["editor"],
            fg=THEME["text"], insertbackground=THEME["text"], selectbackground=THEME["select"],
            relief="flat", highlightthickness=1, highlightbackground=THEME["border"],
            highlightcolor=THEME["jade"], padx=10, pady=8, font=("Microsoft YaHei UI", 10), undo=True)
        self.persona_text.grid(row=8, column=1, columnspan=5, sticky="ew", pady=(12, 0))
        self.persona_text.insert("1.0", load_settings().persona)
        self.persona_text.bind("<FocusOut>", lambda event: self.save_live_settings(quiet=True))

        label("下播", 3)
        shutdown_area = ttk.Frame(form, style="Card.TFrame")
        shutdown_area.grid(row=3, column=1, columnspan=5, sticky="ew", pady=(6, 0))
        shutdown_row = ttk.Frame(shutdown_area, style="Card.TFrame")
        shutdown_row.pack(fill="x")
        self.shutdown_button = ttk.Button(shutdown_row, text="本局后下播", width=12,
            style="Primary.TButton", command=self.toggle_stream_shutdown)
        self.shutdown_button.pack(side="left")
        ttk.Checkbutton(shutdown_row, text="定时", variable=self.shutdown_scheduled,
            style="Settings.TCheckbutton", command=self.toggle_shutdown_schedule).pack(side="left", padx=(18, 6))
        self.shutdown_time_entry = ttk.Entry(shutdown_row, textvariable=self.shutdown_time, width=7)
        self.shutdown_time_entry.pack(side="left")
        self.shutdown_time_entry.bind("<Return>", lambda event: self._update_shutdown_schedule())
        self.shutdown_time_entry.bind("<FocusOut>", lambda event: self._update_shutdown_schedule())
        ttk.Label(shutdown_row, text="到点后下完当局", style="Muted.TLabel").pack(side="left", padx=(8, 0))
        ttk.Label(shutdown_area, textvariable=self.shutdown_status, style="Muted.TLabel").pack(
            anchor="w", pady=(4, 0))
        self._layout_logs()

        self.tools_menu = tk.Menu(self.root, tearoff=False, bg=THEME["panel"], fg=THEME["text"],
                                 activebackground=THEME["jade2"], activeforeground=THEME["text"])
        self.tools_menu.add_command(label="保存设置", command=self.save_live_settings)
        self.tools_menu.add_command(label="当前异常", command=self.show_monitor_issues)
        self.tools_menu.add_command(label="登录抖音（接收礼物）", command=self.login_douyin)
        self.tools_menu.add_command(label="清除抖音登录", command=self.clear_douyin_login)
        self.tools_menu.add_separator()
        self.tools_menu.add_command(label="预览一手", command=self.preview_once)
        self.tools_menu.add_separator()
        for label, command in (("刷新设备", self.refresh_device), ("标定棋盘", self.calibrate),
                               ("开播体检", self.run_health_check), ("故障截图", self.open_diagnostics),
                               ("清理缓存", self.clean_cache)):
            self.tools_menu.add_command(label=label, command=command)
        self.tools_menu.add_separator()
        self.tools_menu.add_command(label="恢复窗口不透明", command=lambda: (
            self.opacity.set(100), self._change_opacity(100)))
        ttk.Menubutton(actions, text="工具", width=5, menu=self.tools_menu).pack(side="left", padx=4)

    def preview_once(self):
        self.dry.set(True)
        self.start(True)

    def _follow_latest(self, key):
        widget = self._log_widget(key)
        follow = {"chess": self.chess_follow, "live": self.live_follow, "room": self.room_follow}[key]
        if follow.get():
            widget.see("end")

    def _clear_log_view(self, key):
        widget = self._log_widget(key)
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.configure(state="disabled")

    def _configure_theme_styles(self):
        style = ttk.Style(self.root)
        font = ("Microsoft YaHei UI", 11)
        style.configure(".", font=font, background=THEME["bg"],
                        foreground=THEME["text"])
        style.configure("App.TFrame", background=THEME["bg"])
        style.configure("Card.TFrame", background=THEME["panel"])
        style.configure("SwitchGroup.TFrame", background=THEME["panel2"])
        for key, accent in (("chess", "blue"), ("live", "jade"), ("room", "gold")):
            color = "#%02x%02x%02x" % blend(THEME[accent], "#000000", .25)
            style.configure(f"{key}.Log.TFrame", background=color)
            style.configure(f"{key}.Log.TLabel", background=color, foreground="#ffffff",
                            font=("Microsoft YaHei UI", 11, "bold"))
            style.configure(f"{key}.Log.TMenubutton", background=color, foreground="#ffffff",
                            padding=(6, 0), font=("Microsoft YaHei UI", 12, "bold"))
            style.layout(f"{key}.Log.TMenubutton", [("Menubutton.padding", {"sticky": "nsew", "children": [
                ("Menubutton.label", {"sticky": "nsew"})]})])
            style.map(f"{key}.Log.TMenubutton", background=[("active", color)], foreground=[("active", "#ffffff")])
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
                  foreground=[("selected", "#ffffff"), ("active", THEME["text"])])
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
        self._configure_switch_style(style)
        style.configure("TButton", background=THEME["button"],
                        foreground=THEME["text"], borderwidth=0,
                        padding=(12, 7), font=font)
        style.configure("TMenubutton", background=THEME["button"],
                        foreground=THEME["text"], borderwidth=0, padding=(12, 7))
        for widget_style in ("TButton", "TMenubutton"):
            style.map(widget_style,
                      background=[("active", THEME["button_active"]),
                                  ("pressed", THEME["panel2"])])
        style.configure("LogToggle.TButton", background=THEME["jade2"], foreground=THEME["jade"],
                        padding=(12, 7))
        style.map("LogToggle.TButton", background=[("active", THEME["select"])])
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
        self._soft_button_images = install_button_styles(self.root, THEME)
        style.layout("LogMenu.TMenubutton", [("Menubutton.padding", {"sticky": "nsew", "children": [
            ("Menubutton.label", {"sticky": "nsew"})]})])
        style.configure("LogMenu.TMenubutton", background=THEME["panel"], foreground=THEME["muted"],
                        padding=(6, 2), font=("Microsoft YaHei UI", 12, "bold"))

    def _walk_widgets(self, widget):
        yield widget
        for child in widget.winfo_children():
            yield from self._walk_widgets(child)

    def _configure_switch_style(self, style):
        if not hasattr(self, "_switch_images"):
            scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
            width, height = round(28 * scale), round(16 * scale)

            def switch_image(selected, color):
                image = tk.PhotoImage(master=self.root, width=width + round(6 * scale), height=height)
                radius = height / 2
                center_x = width - radius if selected else radius
                knob_radius = radius - max(2, round(2 * scale))
                for y in range(height):
                    for x in range(width):
                        nearest_x = min(width - radius, max(radius, x + 0.5))
                        if (x + 0.5 - nearest_x) ** 2 + (y + 0.5 - radius) ** 2 <= radius ** 2:
                            pixel = "#ffffff" if ((x + 0.5 - center_x) ** 2 +
                                (y + 0.5 - radius) ** 2 <= knob_radius ** 2) else color
                            image.put(pixel, (x, y))
                return image

            off = switch_image(False, "#c45b66")
            on = switch_image(True, "#269766")
            disabled_off = switch_image(False, "#9aa4ad")
            disabled_on = switch_image(True, "#9aa4ad")
            self._switch_images = (off, on, disabled_off, disabled_on)
            style.element_create("Switch.indicator", "image", off,
                ("disabled", "selected", disabled_on), ("disabled", disabled_off),
                ("selected", on), sticky="w")
        style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Switch.indicator", {"side": "left", "sticky": "w"}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})])
        style.configure("TCheckbutton", background=THEME["panel2"], foreground=THEME["text"])
        style.map("TCheckbutton", foreground=[("disabled", THEME["muted"]), ("active", THEME["text"])],
                  background=[("active", THEME["panel2"])])
        style.configure("Settings.TCheckbutton", background=THEME["panel"])
        style.map("Settings.TCheckbutton", background=[("active", THEME["panel"])])

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
        for widget in self._walk_widgets(self.root):
            if isinstance(widget, SoftCard):
                widget.set_palette(THEME)
        self._configure_speech_log_tags()
        self._configure_room_log_tags()
        if hasattr(self, "log"):
            self._configure_chess_log_tags()
        for name, background in (("log", "console"), ("live_log", "console"), ("room_log", "console"),
                                 ("persona_text", "editor")):
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
                           "appearance_version": 2,
                           "opacity": int(self.opacity.get()),
                           "preview_visible": getattr(self, "preview_visible", True),
                           "auto_continue": bool(self.continuous.get()),
                           "broadcast_width": self._broadcast_dimensions()[0],
                           "broadcast_height": self._broadcast_dimensions()[1],
                           "area_a_avatar_path": getattr(self, "area_a_avatar_path", "")},
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
        if any(word in text for word in ("失败", "未找到", "异常", "锁定", "未启动", "未确认", "拒绝")):
            self._note_issue("设备" if any(x in text for x in ("设备", "ADB", "模拟器")) else "对弈", text)
        if hasattr(self, "start_button"):
            running = bool(not self.task_cancel.is_set() and
                           self.process and self.process.poll() is None)
            busy = self.waiting_for_board or running
            self.start_button.configure(
                text="等待棋盘…" if self.waiting_for_board else "下棋中" if running else "▶ 开始下棋")
            self.start_button.state(["disabled"] if busy else ["!disabled"])
            if hasattr(self, "tools_menu"):
                self.tools_menu.entryconfigure("预览一手", state="disabled" if busy else "normal")
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
        self._queue_monitor()

    def _sync_live_badges(self, *_):
        speech = self.speech_status.get()
        self.voice_state.set("已开启" if self.live_speech.get() else "仅文本")
        self._set_indicator(
            "voice", THEME["jade"] if self.live_speech.get() else THEME["purple"])
        live = self.live_status.get()
        if any(word in live for word in ("已连接", "连接成功")) and "未" not in live:
            self.connection_state = "connected"
            self._clear_issue("直播")
            self.stream_state.set("已连接")
            self._set_indicator("stream", THEME["jade"])
        elif any(word in live for word in ("正在连接", "连接中", "正在解析")):
            self.connection_state = "connecting"
            self.stream_state.set("连接中")
            self._set_indicator("stream", THEME["gold"])
        else:
            self.stream_state.set("未连接")
            self._set_indicator("stream", THEME["muted"])
            if any(word in live for word in ("失败", "异常", "缺少", "拒绝")):
                self.connection_state = "error"
                self._note_issue("直播", live)
            elif "结束" in live:
                self.connection_state = "ended"
            elif "断开" in live or "未连接" in live:
                self.connection_state = "disconnected"
        if "异常" in speech or "失败" in speech:
            self._note_issue("播报", speech)
        self._sync_connection_button()
        self._queue_monitor()

    def _note_issue(self, source, text, level="error"):
        if hasattr(self, "monitor_issues"):
            message = str(text).strip()
            if source == "播报":
                message = message.removeprefix("播报异常：").strip()
            self.monitor_issues.set(source, message, level)
            self._queue_monitor()

    def _clear_issue(self, source):
        if hasattr(self, "monitor_issues"):
            self.monitor_issues.clear(source)
            self._queue_monitor()

    def _queue_monitor(self, *_):
        if hasattr(self, "monitor_labels") and not getattr(self, "_monitor_job", None):
            self._monitor_job = self.root.after_idle(self._refresh_monitor)

    def _refresh_monitor(self):
        self._monitor_job = None
        if not hasattr(self, "monitor_labels") or self.closing:
            return
        running = bool(self.process and self.process.poll() is None)
        board = player_view(self._player_runtime, pid=self.process.pid if running else None,
            running=running, stopped=self.task_cancel.is_set(), waiting=self.waiting_for_board,
            warning_after=getattr(self, "_heartbeat_warning_seconds", 25))
        if board[0] == "待机":
            board = (board[0], "设备" + self.device_state.get() + " · 等待开始", board[2])
        if running and board[2] == "ok":
            self.monitor_issues.clear("对弈")
        if board[0] == "心跳停滞":
            self.monitor_issues.set("心跳", board[1])
        else:
            self.monitor_issues.clear("心跳")
        if self.device_state.get() == "已连接":
            self.monitor_issues.clear("设备")
        if not self.live_speech.get():
            self.monitor_issues.clear("播报")
        if self.task_cancel.is_set():
            self.monitor_issues.clear("对弈")
            self.monitor_issues.clear("识别")
        elif "识别" in self.monitor_issues.active:
            board = ("自动复核", "正在核对棋盘，暂缓落子", "warn")
        elif not running and "对弈" in self.monitor_issues.active:
            board = ("已暂停", self.monitor_issues.active["对弈"][0][:42], "error")
        activity = self.live_controller.pipeline.activity_snapshot() if self.live_controller else {}
        if not isinstance(activity, dict):
            activity = {}
        voice = speech_view(self.live_speech.get(), activity, self.speech_status.get())
        if "播报" in self.monitor_issues.active and voice[0] == "待播":
            voice = ("播报异常", self.monitor_issues.active["播报"][0][:48], "error")
        live = live_view(self.connection_state, self.room_metrics.get("online"), self.room_metrics.get("chat_rate", 0))
        colors = {"ok": THEME["jade"], "warn": THEME["gold"], "error": THEME["red"], "muted": THEME["muted"]}
        for key, (title, detail, level) in (("board", board), ("voice", voice), ("stream", live)):
            labels = self.monitor_labels[key]
            labels[0].configure(text=title, fg=colors[level])
            labels[1].configure(text=detail)
            self._set_indicator(key, colors[level])
        issues = self.monitor_issues.rows()
        if issues:
            source, message, level = issues[0]
            self.attention_text.set(f"需关注 {len(issues)} 项 · {source}：{message}")
            bg, fg = (THEME["error_bg"], THEME["error_fg"]) if level == "error" else (THEME["wait_bg"], THEME["wait_fg"])
            self.attention_bar.configure(bg=bg)
            self.attention_label.configure(bg=bg, fg=fg)
            if not self.attention_bar.winfo_manager():
                self.attention_bar.pack(fill="x")
        else:
            self.attention_bar.pack_forget()
        if (getattr(self, "_issue_window", None) is not None and self._issue_window.winfo_exists()
                and issues != getattr(self, "_issue_details_snapshot", None)):
            self._issue_details_snapshot = issues
            self._issue_text.configure(state="normal")
            self._issue_text.delete("1.0", "end")
            self._issue_text.insert("end", "\n\n".join(f"{source}\n{message}" for source, message, _ in issues)
                                    or "当前没有需要处理的异常。")
            self._issue_text.configure(state="disabled")

    def show_monitor_issues(self):
        rows = self.monitor_issues.rows()
        if not rows:
            return
        if getattr(self, "_issue_window", None) is not None and self._issue_window.winfo_exists():
            self._issue_window.deiconify()
            self._issue_window.lift()
            return
        window = tk.Toplevel(self.root)
        self._issue_window = window
        window.title("当前异常")
        scale = ui_scale(self.root)
        window.geometry(f"{round(620 * scale)}x{round(300 * scale)}")
        window.configure(bg=THEME["panel"])
        text = tk.Text(window, wrap="word", bg=THEME["panel"], fg=THEME["text"],
            relief="flat", padx=18, pady=18, font=("Microsoft YaHei UI", 10))
        text.pack(fill="both", expand=True)
        self._issue_text = text
        self._issue_details_snapshot = rows
        text.insert("end", "\n\n".join(f"{source}\n{message}" for source, message, _ in rows))
        text.configure(state="disabled")

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
            font=("Microsoft YaHei UI", 10), lmargin1=10, lmargin2=10, rmargin=10,
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
        if data.get("测试输入"):
            return None
        kind = data.get("kind", "")
        label = {
            "弹幕回复": "回复观众", "礼物答谢": "礼物答谢",
            "欢迎观众": "欢迎观众", "自我介绍": "开场介绍",
            "沙盘推演解说": "沙盘推演", "开局定式解说": "开局讲解",
            "获胜收官": "本局结束", "结算收官": "本局结束",
            "绝杀长解说": "绝杀解说",
            "对手状态解说": "对手状态",
            "直播报时": "北京时间",
            "新局开场": "新局开始", "局间互动": "局间互动",
            "关键走棋解说": "本手走法", "我方走棋播报": "本手走法", "我方走棋解说": "本手走法",
            "等待期长讲解": "后续看点", "QUEEN式等待期解说": "后续看点",
            "应手预判": "可能应手", "对手走棋播报": "对手应手",
            "对手走棋解说": "对手应手", "关键应手解说": "对手应手",
        }.get(kind, "棋局解说" if kind in CHESS_SPEECH_KINDS else "主播口播")
        if data.get("上一手预判复盘"):
            label = "对手应手"
        if kind == "沙盘推演解说" and data.get("沙盘步骤"):
            cue = str(data["沙盘步骤"])
            label += " · " + {"question": "互动提问", "intro": "看点", "summary": "结果"}.get(cue, f"第{cue}步")
        if kind in ("弹幕回复", "礼物答谢", "欢迎观众") and data.get("观众"):
            label += " · " + str(data["观众"])
        # Provenance belongs to this speech job, never to the newer live board.
        meta = ControlPanel._commentary_log_label(label, data)
        timestamp = str(record.get("time") or "").strip()
        header = f"{timestamp}  ·  {meta}" if timestamp else meta
        if kind == "弹幕回复" and data.get("弹幕"):
            header += "\n弹幕：" + str(data["弹幕"])
        return header, text

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

    def _configure_room_log_tags(self):
        if not hasattr(self, "room_log"):
            return
        for tag, color in (("room_meta", "muted"), ("room_user", "jade"),
                           ("room_gift", "gold"), ("room_body", "console_text")):
            self.room_log.tag_configure(tag, foreground=THEME[color],
                font=("Microsoft YaHei UI", 10 if tag == "room_meta" else 11,
                      "bold" if tag in ("room_user", "room_gift") else "normal"),
                spacing1=4, spacing3=5)

    def _receive_live_event(self, record):
        # Drain incoming room traffic in batches so a busy room cannot starve
        # Stop, speech acknowledgements, or board updates on the Tk thread.
        if record.get("kind") == "room_message":
            with self.room_pending_lock:
                self.room_pending.append(record)
        else:
            self.output.put(("live_event", record))

    def _flush_room_messages(self):
        if not hasattr(self, "room_pending"):
            return
        with self.room_pending_lock:
            records = [self.room_pending.popleft() for _ in range(min(200, len(self.room_pending)))]
        if not records:
            return
        self.room_log.configure(state="normal")
        for record in records:
            epoch = (record.get("data") or {}).get("connector_epoch")
            if epoch is not None and epoch != self.live_controller.connector_epoch:
                continue
            entry = room_message_entry(record)
            if entry is None:
                continue
            timestamp, label, user, text = entry
            self.room_log.insert("end", f"{timestamp} · {label}  ", "room_meta")
            self.room_log.insert("end", user + ("：" if label == "弹幕" else "  "), "room_user")
            self.room_log.insert("end", text + "\n", "room_gift" if label == "礼物" else "room_body")
        self._trim_text_widget(self.room_log, 2000)
        if self.room_follow.get():
            self.room_log.see("end")
        self.room_log.configure(state="disabled")

    @staticmethod
    def _commentary_log_label(label, data):
        """Attach move provenance to UI logs only; never alter spoken text."""
        data = data or {}
        number = data.get("解说对应手数")
        move = str(data.get("解说对应走法") or
                   data.get("我方计划走法") or "").strip()
        details = []
        if number not in (None, ""):
            details.append(f"第{number}手后" if label in ("后续看点", "可能应手") else f"第{number}手")
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
        return {
            "room": self.live_room.get().strip(),
            "model": self.live_model.get().strip() or "glm-4-flash",
            "voice": self.live_voice.get(),
            "commentary_density": self.live_density.get(),
            "prefix": "",
            "persona": self.persona_text.get("1.0", "end").strip(),
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
            self.interaction_state.set("自动抽选中")
            self._sync_interaction_button()

    def toggle_interaction(self):
        if self.live_controller and self.live_controller.enabled:
            self.pause_live()
        else:
            self.enable_live()

    def _sync_interaction_button(self):
        if hasattr(self, "interaction_button"):
            active = self.live_controller and self.live_controller.enabled
            self.interaction_button.configure(text="暂停弹幕互动" if active else "开启弹幕互动",
                style="LogToggle.TButton" if active else "TButton")

    def pause_live(self):
        if self.live_controller:
            self.live_controller.pause()
        self.interaction_state.set("互动已暂停")
        self._reset_interaction_display()
        self._sync_interaction_button()

    def _reset_interaction_display(self):
        self._cancel_interaction_clear()
        self._interaction_display_generation = getattr(self, "_interaction_display_generation", 0) + 1
        self.interaction_id = None
        if hasattr(self, "interaction_user"):
            self.interaction_user.set("")
            self.interaction_message.set("")

    def login_douyin(self):
        if getattr(self, "_douyin_login_request", None) is not None:
            self.live_status.set("请先完成已打开的抖音登录窗口")
            return
        runtime = os.path.join(r"D:\environment\shuzhiren\runtime\douyin-env\Scripts", "python.exe")
        if not os.path.isfile(runtime):
            self.live_status.set("抖音连接器运行环境不存在，无法打开登录窗口")
            return
        request = self._douyin_login_request = object()
        self.live_status.set("正在打开抖音官方登录页面…")
        def worker():
            final = False
            process = None
            try:
                environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
                process = subprocess.Popen([runtime, "-u", os.path.join(HERE, "douyin_login.py")],
                    cwd=HERE, env=environment, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
                for line in process.stdout:
                    if line.startswith("CHESS_DOUYIN_LOGIN ") and len(line) < 2000:
                        data = json.loads(line[len("CHESS_DOUYIN_LOGIN "):])
                        final |= data.get("state") in ("saved", "cancelled", "error")
                        self.output.put(("douyin_login", request, data))
                process.wait()
            except Exception:
                pass
            finally:
                if process is not None and process.stdout:
                    process.stdout.close()
                if not final:
                    self.output.put(("douyin_login", request, {"state": "error", "text": "抖音登录未完成，请重试"}))
        threading.Thread(target=worker, name="douyin-login", daemon=True).start()

    def clear_douyin_login(self):
        if getattr(self, "_douyin_login_request", None) is not None:
            self.live_status.set("请先关闭抖音登录窗口，再清除登录信息")
            return
        self.disconnect_live()
        clear_cookie()
        self.live_status.set("抖音登录信息已清除")

    def _handle_douyin_login(self, request, data):
        if request is not getattr(self, "_douyin_login_request", None):
            return
        self.live_status.set(str(data.get("text") or ""))
        if data.get("state") == "waiting":
            return
        self._douyin_login_request = None
        if data.get("state") == "saved":
            if self.live_room.get().strip():
                self.disconnect_live()
                self.connect_live()
            else:
                self.live_status.set("已保存抖音登录信息，粘贴直播链接后连接即可")

    def _cancel_interaction_clear(self):
        job = getattr(self, "_interaction_display_job", None)
        self._interaction_display_job = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass

    def _show_current_interaction(self, data):
        self._cancel_interaction_clear()
        self._interaction_display_generation = getattr(self, "_interaction_display_generation", 0) + 1
        self.interaction_id = data.get("互动标识")
        self._interaction_display_started = time.monotonic()
        self._interaction_display_audio = data.get("_speech_mode") == "audio"
        self.interaction_state.set("正在互动")
        self.interaction_user.set(str(data.get("观众") or "观众"))
        self.interaction_message.set(str(data.get("弹幕") or ""))

    def _finish_current_interaction(self, data):
        if self.interaction_id is None or data.get("互动标识") != self.interaction_id:
            return
        self.interaction_state.set("互动已暂停" if not self.live_controller.enabled else (
            "已回复 · 自动抽选中" if data.get("completed") else "互动已中断"))
        if not data.get("completed") or getattr(self, "_interaction_display_audio", False):
            self._reset_interaction_display()
            return
        self._cancel_interaction_clear()
        identity = self.interaction_id
        generation = getattr(self, "_interaction_display_generation", 0)
        # Audio clears at completion; text-only tests need a short read window.
        elapsed = time.monotonic() - getattr(self, "_interaction_display_started", time.monotonic())
        delay = max(.1, 6 - elapsed)
        def clear_finished():
            if identity == self.interaction_id and generation == getattr(self, "_interaction_display_generation", 0):
                self._reset_interaction_display()
        self._interaction_display_job = self.root.after(round(delay * 1000), clear_finished)

    def toggle_live_speech(self):
        if not self.save_live_settings(quiet=True):
            return
        if self.live_speech.get():
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
            self.live_controller.pipeline.invalidate_chess()
            self.append_live("系统", "直播解说已关闭，只自动下棋，不调用棋局语言模型和 TTS。")

    def toggle_live_connection(self):
        if (getattr(self, "live_connect_request", None) is not None
                or getattr(self, "connection_state", "disconnected") in ("connected", "connecting")):
            self.disconnect_live()
        else:
            self.connect_live()

    def _sync_connection_button(self):
        if not hasattr(self, "live_connect_button"):
            return
        state = getattr(self, "connection_state", "disconnected")
        text = ("断开直播间" if state == "connected" else "取消连接" if (
            state == "connecting" or getattr(self, "live_connect_request", None) is not None)
            else "连接直播间")
        self.live_connect_button.configure(state="normal", text=text)

    def connect_live(self):
        if self._shutdown_active():
            self._cancel_stream_shutdown()
        if getattr(self, "live_connect_request", None) is not None:
            return
        if not self.live_controller or not self.save_live_settings(quiet=True):
            return
        room = self.live_room.get()
        request = self.live_connect_request = object()
        self._reset_interaction_display()
        self.connection_state = "connecting"
        self._sync_connection_button()
        self.live_status.set("正在解析直播间分享链接…")
        controller = self.live_controller

        def worker():
            error = None
            try:
                controller.connect(room, cancelled=lambda: (
                    request is not self.live_connect_request or self.closing))
            except Exception as failure:
                error = str(failure)
            self.output.put(("live_connect_result", request, error))

        threading.Thread(target=worker, name="douyin-connect", daemon=True).start()

    def disconnect_live(self):
        if self._shutdown_active():
            self._cancel_stream_shutdown()
        self.live_connect_request = None
        self.connection_state = "disconnected"
        self._sync_connection_button()
        if self.live_controller:
            self.live_controller.disconnect()
        self._clear_issue("直播")
        self.live_status.set("直播间已断开")
        self._reset_interaction_display()

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
            self._note_issue("识别", text.replace("[识别告警]", "").strip(), "warn")
        if "对方回应：" in text or "棋盘识别成功" in text:
            self._clear_issue("识别")
        if "落子确认：成功" in text or "落子确认成功；" in text:
            self.move_success_count += 1
            self.board_state.set("识别稳定")
            self._set_indicator("board", THEME["jade"])
            self._clear_issue("识别")
        if "对局结束：" in text:
            self.game_count += 1
        self.runtime_stats.set(
            f"识别告警 {self.warning_count} · 成功落子 {self.move_success_count} · 完成对局 {self.game_count}")
        if hasattr(self, "session_summary"):
            self.session_summary.set(f"本次 {self.move_success_count} 手 · {self.game_count} 局")
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
            self._player_runtime = status if status and status.get("pid") == self.process.pid else None
            if running and status and status.get("pid") == self.process.pid:
                age = heartbeat_age(status)
                stage = status.get("stage", "")
                label = RUNTIME_STAGE_LABELS.get(stage, stage or "运行中")
                if age is not None and age < 5 and stage != "stopped":
                    self.board_state.set(label)
                    if self.status.get().startswith("运行心跳停滞"):
                        self.status.set("运行心跳已恢复")
                cfg = self._assistant_cfg()
                warning_after = float(cfg.get("watchdog_warning_seconds", 25))
                self._heartbeat_warning_seconds = warning_after
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
                self._queue_monitor()
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

    def _shutdown_active(self):
        flow = getattr(self, "stream_shutdown", None)
        return bool(flow and flow.active)

    def _sync_shutdown(self):
        flow = self.stream_shutdown
        self.shutdown_status.set(flow.message)
        self.shutdown_button.configure(text="正在下播" if flow.state == "closing" else
                                       "取消下播" if flow.active else "本局后下播")
        self.shutdown_button.state(["disabled"] if flow.state == "closing" else ["!disabled"])
        self.shutdown_time_entry.state(["disabled"] if flow.active else ["!disabled"])
        if self.live_controller:
            self.live_controller.set_shutdown(
                "final" if flow.finalizing else "waiting_game" if flow.active else None, flow.epoch)

    def _cancel_stream_shutdown(self):
        self.shutdown_cancel.set()
        self.stream_shutdown.cancel()
        self.shutdown_scheduled.set(False)
        self._sync_shutdown()

    def toggle_stream_shutdown(self):
        if self.stream_shutdown.active:
            self._cancel_stream_shutdown()
        else:
            self._arm_stream_shutdown()

    def toggle_shutdown_schedule(self):
        if self.stream_shutdown.active:
            self.shutdown_scheduled.set(False)
            return
        if self.shutdown_scheduled.get():
            self._update_shutdown_schedule()
        else:
            self.stream_shutdown.cancel()
            self.stream_shutdown.message = "已取消定时下播"
            self._sync_shutdown()

    def _update_shutdown_schedule(self):
        if not self.shutdown_scheduled.get() or self.stream_shutdown.active:
            return
        try:
            self.stream_shutdown.schedule(self.shutdown_time.get().strip())
        except ValueError as error:
            self.shutdown_scheduled.set(False)
            self.stream_shutdown.fail(str(error))
        self._sync_shutdown()

    def _arm_stream_shutdown(self):
        controller = self.live_controller
        self.shutdown_scheduled.set(False)
        if controller is None or not controller.settings.speech_enabled:
            self.stream_shutdown.fail("请先开启语音，保证告别说完再结束直播")
            self._sync_shutdown()
            return
        if not self._publish_auto_continue(False):
            self.stream_shutdown.fail("自动连局关闭失败，尚未启用下播")
            self._sync_shutdown()
            return
        self.continuous.set(False)
        self.auto_retry = False
        self._cancel_scheduled_prepare()
        running = bool(self.process and self.process.poll() is None)
        active_game = (controller.game_active or (running and self.chess_session_requested
                       and not controller.game_terminal and not self.dry.get()))
        was_waiting = self.waiting_for_board
        if was_waiting and not running:
            self.task_cancel.set()
            self.waiting_for_board = False
        self.shutdown_cancel.set()
        self.shutdown_cancel = threading.Event()
        self.stream_shutdown.arm(active_game)
        self._save_ui_settings()
        self._sync_shutdown()
        self.append("[下播] 已关闭下一局匹配；" + ("当前这盘照常下完。\n" if active_game else "当前没有进行中的棋局，准备告别。\n"))
        if active_game and was_waiting and not running:
            self.task_cancel = threading.Event()
            self._schedule_prepare(automatic=False, resume_only=True)

    def _shutdown_tick(self):
        if self.closing:
            return
        flow = self.stream_shutdown
        if flow.due():
            self._arm_stream_shutdown()
        controller = self.live_controller
        if flow.active and controller:
            idle, safe = controller.shutdown_activity()
            action = flow.tick(idle=idle, prelude_safe=safe)
            self._sync_shutdown()
            if action and action[0] == "speak":
                _, kind, line, payload = action
                if not controller.shutdown_speech(kind, line, payload):
                    flow.speech_done({**payload, "kind": kind, "completed": False})
                    self._sync_shutdown()
            elif action and action[0] == "close":
                epoch, cancel = action[1], self.shutdown_cancel
                def finish():
                    try:
                        success, message = CompanionShutdown().end_live(cancel)
                    except Exception as error:
                        success, message = False, "未完成下播：" + str(error)[:160]
                    self.output.put(("shutdown_result", epoch, success, message))
                threading.Thread(target=finish, name="companion-end-live", daemon=True).start()
        self.root.after(250, self._shutdown_tick)

    def _publish_auto_continue(self, enabled):
        try:
            write_auto_continue(enabled, path=GAME_CONTROL_PATH)
            return True
        except OSError as error:
            self.append(f"[自动连局] 设置同步失败：{error}\n")
            return False

    def toggle_auto_continue(self):
        enabled = bool(self.continuous.get())
        if enabled and self._shutdown_active():
            self.continuous.set(False)
            self.status.set("已安排本局后下播；取消下播后可重新开启自动连局")
            return
        self.auto_retry = enabled
        synced = self._publish_auto_continue(enabled)
        self._save_ui_settings()
        if not enabled:
            self._cancel_scheduled_prepare()
            if self.waiting_for_board and getattr(self, "waiter_automatic", False):
                self.task_cancel.set()
                self.waiting_for_board = False
                if self.live_controller:
                    self.live_controller.stop_chess_tasks()
                self.status.set("自动连局已关闭，已取消下一局等待")
            if synced:
                self.append("[自动连局] 已关闭：本局照常下完，结束后不再点击继续或开启下一局。\n")
        elif synced:
            running = bool(self.process and self.process.poll() is None)
            if (getattr(self, "chess_session_requested", False) and not running
                    and not self.waiting_for_board and not self.closing):
                # This session was explicitly started before it was paused by
                # the switch.  Enabling it again authorizes a fresh waiter,
                # not revival of a cancelled callback or old worker token.
                self.task_cancel.set()
                self.task_cancel = threading.Event()
                self._schedule_prepare()
                self.status.set("自动连局已开启，正在继续下一局…")
                self.append("[自动连局] 已开启：正在恢复下一局等待。\n")
            elif running or self.waiting_for_board:
                self.append("[自动连局] 已开启：本局结束后继续下一局。\n")
            else:
                self.append("[自动连局] 已开启：点击开始下棋后生效。\n")

    def prepare_and_wait(self, *, automatic=False, resume_only=False):
        if self._shutdown_active():
            if self.stream_shutdown.state != "waiting_game":
                self.status.set("正在准备下播，请先取消下播再开始下棋")
                return
            resume_only = True
        if automatic and not (self.auto_retry and bool(self.continuous.get())):
            return
        self.append("[控制指令] 一键准备并等待新棋\n")
        if self.waiting_for_board:
            self.status.set("正在等待新棋或残局，无需重复点击")
            return
        if self.process and self.process.poll() is None:
            messagebox.showinfo("正在运行", "当前已经有对弈任务")
            return
        self._cancel_scheduled_prepare()
        self.chess_session_requested = True
        self.waiting_for_board = True
        self.waiter_automatic = automatic
        self.task_cancel.set()
        self.task_cancel = threading.Event()
        if self.live_controller:
            self.live_controller.resume_chess_tasks()
        self.auto_retry = bool(self.continuous.get())
        self.dry.set(False)
        self.status.set("一键助手启动中…")
        self.append("\n[助手] 正在准备模拟器与 JJ 象棋。标准新棋和合法残局都可以接管。\n")
        if self.auto_retry:
            self.append("[持续下棋] 已开启：胜负、投降或超时后都会继续监听下一局。\n")
        requested_turn = TURN_LABEL_TO_CODE.get(self.turn_choice.get(), "auto")
        threading.Thread(target=self._prepare_worker,
                         args=(requested_turn, self.task_cancel, resume_only), daemon=True).start()

    def _prepare_worker(self, requested_turn="auto", cancel=None, resume_only=False):
        cancel = cancel or self.task_cancel
        try:
            with cancellable_adb(cancel):
                self._prepare_worker_body(requested_turn, cancel, resume_only=resume_only)
        except TaskCancelled:
            pass

    def _prepare_worker_body(self, requested_turn, cancel, resume_only=False):
        import time

        def publish(item):
            if not cancel.is_set():
                self.output.put(("assistant_task", cancel, item))

        cfg = self._assistant_cfg()
        try:
            devices = list_devices()
            if not devices:
                if resume_only:
                    raise RuntimeError("等待原局恢复时未找到模拟器，未启动新局")
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
                if resume_only:
                    raise RuntimeError("JJ象棋不在前台，未启动新局；请回到原局后开始下棋")
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
                image = screenshot(device)
                if cancel.is_set():
                    return
                if image is not None:
                    from postgame import (detect_match_mode_button,
                                          detect_rules_overlay_dismiss,
                                          dismiss_rest_reminder,
                                          handle_postgame,
                                          has_actionable_postgame)
                    if not resume_only and dismiss_rest_reminder(device, image):
                        publish(("status", "已关闭 JJ 在线时长提醒，继续识别棋盘"))
                        waiting_started = time.monotonic()
                        stable = 0
                        last_signature = None
                        image = screenshot(device)
                        if image is None:
                            continue
                    if has_actionable_postgame(image):
                        cancel.wait(0.08)
                        confirm = screenshot(device)
                        if (confirm is not None and
                                has_actionable_postgame(confirm)):
                            if resume_only:
                                publish(("shutdown_game_over", {
                                    "event": "game_over", "result": "finished", "reason": "jj_result_screen"}))
                                return
                            publish(("status", "已连续确认结算页面，正在自动进入下一局…"))
                            actions = handle_postgame(device, continue_allowed=lambda:
                                not cancel.is_set() and (not getattr(self, "waiter_automatic", False)
                                                        or self.auto_retry))
                            if actions:
                                publish(("status", "结算已处理，正在识别下一局棋盘"))
                                waiting_started = time.monotonic()
                            else:
                                self._save_diagnostic(
                                    device, "watcher_postgame_no_action",
                                    extra={"stage": "handle_postgame"}, image=confirm)
                            stable = 0
                            last_signature = None
                        # A modal cannot be used as evidence for session
                        # recovery, even if pieces are visible behind it.
                        continue
                resumed = match_live_session(device, first_image=image)
                if cancel.is_set():
                    return
                if resumed is not None:
                    clear_blocked_position()
                    publish(("autostart", {
                        "turn": resumed["board"].turn,
                        "pieces": resumed["board"].attacker_count(),
                        "standard": False,
                        "resume": True,
                    }))
                    return
                if resume_only:
                    cancel.wait(.15)
                    continue  # Never match, dismiss overlays, or adopt a new board.
                squares, orientation = ((None, None) if image is None else
                                        recognize_position(image, "auto"))
                visible_pieces = sum(
                    piece is not None for piece in (squares or []))
                live_board_visible = (
                    orientation in ("red_bottom", "black_bottom") and
                    visible_pieces >= int(cfg.get("endgame_min_pieces", 4)))
                if image is not None:
                    # Result controls are checked first.  A result page still
                    # contains a full board and its horizontal lines can look
                    # like the promotion-rules card; clicking the backdrop in
                    # that case leaves "再来一局" untouched and stalls the
                    # continuous-game loop.
                    rules_point = (None if live_board_visible else
                                   detect_rules_overlay_dismiss(image))
                    if (rules_point and not rules_dismiss_attempted and
                            time.monotonic() - last_rules_dismiss >= 8.0):
                        cancel.wait(0.08)
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
                            cancel.wait(0.15)
                            continue
                    mode = cfg.get("match_mode", "15分钟")
                    mode_point = (None if live_board_visible else
                                  detect_match_mode_button(image, mode))
                    if (mode_point and
                            time.monotonic() - last_mode_click >= 8.0):
                        cancel.wait(0.08)
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
                            cancel.wait(0.15)
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
                    cancel.wait(0.2)
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
                            "fen": Board(squares, turn=turns[0]).to_fen(),
                            "orientation": orientation,
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
                cancel.wait(0.15)
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
            self.live_controller.resume_chess_tasks()
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

    def start(self, once, initial_turn=None, min_pieces=None, resume_shutdown=False,
              startup_position=None):
        if self._shutdown_active() and not (resume_shutdown and self.stream_shutdown.state == "waiting_game"):
            self.status.set("已安排下播；只恢复当前棋局，不开启新局")
            return
        self.append("[控制指令] " + ("运行一手" if once else "开始自动对弈") + "\n")
        try:
            movetime = int(self.movetime.get())
            max_moves = int(self.max_moves.get())
            if not 50 <= movetime <= 30000 or not 1 <= max_moves <= 500:
                raise ValueError
        except ValueError:
            messagebox.showerror("参数错误", "思考时间应为 50–30000 毫秒，步数应为 1–500")
            return
        self.auto_retry = bool(self.continuous.get()) and not once
        if not self._publish_auto_continue(bool(self.continuous.get()) and not once):
            self.status.set("自动连局设置未同步，本次未启动")
            return
        self._cancel_scheduled_prepare()
        self.chess_session_requested = not once
        args = ["player.py", "--engine", "pikafish", "--movetime", str(movetime),
                "--max-moves", str(max_moves), "--stop-file", STOP_FILE,
                "--control-file", str(GAME_CONTROL_PATH)]
        chosen_turn = initial_turn or TURN_LABEL_TO_CODE.get(
            self.turn_choice.get(), "auto")
        if chosen_turn == "auto":
            chosen_turn = self._assistant_cfg().get("ambiguous_turn_default", "r")
        args += ["--initial-turn", chosen_turn,
                 "--min-pieces", str(min_pieces if min_pieces is not None else
                                     self._assistant_cfg().get("endgame_min_pieces", 4))]
        if startup_position and startup_position.get("fen") and startup_position.get("orientation"):
            args += ["--startup-fen", startup_position["fen"],
                     "--startup-orientation", startup_position["orientation"]]
        if once:
            args.append("--once")
        if self.dry.get():
            args.append("--dry")
        self._launch(args, "只读检查中…" if self.dry.get() else "自动对弈运行中…")

    def stop(self):
        if self._shutdown_active():
            self._cancel_stream_shutdown()
        self._cancel_scheduled_prepare()
        self._publish_auto_continue(False)
        self.chess_session_requested = False
        self.task_cancel.set()
        self.waiting_for_board = False
        self.auto_retry = False
        self._cancel_sandtable_jobs()
        self._close_sandtable_ui()
        if self.live_controller:
            self.live_controller.stop_chess_tasks()
        os.makedirs(os.path.dirname(STOP_FILE), exist_ok=True)
        with open(STOP_FILE, "w", encoding="utf-8") as stream:
            stream.write("stop\n")
        process = self.process
        if process and process.poll() is None:
            # Kill the whole task tree, including a running ADB command and
            # Pikafish.  Do not wait for another recognition/postgame cycle.
            self._terminate_task(process)
        self.board_state.set("已停止")
        self.status.set("下棋已暂停：识别、落子和棋局解说已取消")
        self.append("[控制指令] 已暂停下棋；弹幕互动保持原状态\n")

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

    def _cancel_scheduled_prepare(self):
        self._prepare_generation = getattr(self, "_prepare_generation", 0) + 1
        job = getattr(self, "_prepare_job", None)
        self._prepare_job = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass

    def _schedule_prepare(self, delay=120, *, automatic=True, resume_only=False):
        self._cancel_scheduled_prepare()
        cancel = self.task_cancel
        generation = self._prepare_generation

        def restart():
            if (cancel.is_set() or self.closing or cancel is not self.task_cancel
                    or generation != self._prepare_generation):
                return
            self._prepare_job = None
            if automatic and not (self.auto_retry and bool(self.continuous.get())):
                return
            if resume_only:
                self.prepare_and_wait(automatic=automatic, resume_only=True)
            else:
                self.prepare_and_wait(automatic=automatic)

        self._prepare_job = self.root.after(delay, restart)

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
                        self.status.set("结算已处理，正在等待下一局…" if keep_running else "本局已结束，自动连局已关闭")
                        self.append("[助手] 已完成结算页面操作，继续等待下一局。\n" if keep_running
                                    else "[任务结束] 本局已结束；自动连局已关闭，不再匹配下一局。\n")
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
                        self.chess_session_requested = False
                        self.waiting_for_board = False
                        if self.live_controller:
                            self.live_controller.stop_chess_tasks()
                        self.status.set("本手语音未启动，已暂停；未发送落子")
                        self.append("[语音同步] 已取消待播台词。检查语音后重新开始，程序会核对原局面。\n")
                    elif item[1] == 12:
                        self.auto_retry = False
                        self.chess_session_requested = False
                        self.waiting_for_board = False
                        self.status.set("引擎拒绝当前识别局面，已安全停止")
                        self.append("[助手] 当前画面中的棋子类型存在矛盾；没有判负，也没有点击结算页。请等待高亮消失后重新开始。\n")
                    elif item[1] == 13:
                        self.auto_retry = False
                        self.chess_session_requested = False
                        self.waiting_for_board = False
                        self.status.set("落子未确认，当前局面已锁定")
                        self.append("[助手] 为防止重复点击，持续下棋已暂停；当前局面变化或进入新一局后再手动开始。\n")
                    elif item[1] == 14:
                        recovering_for_shutdown = (self._shutdown_active() and self.stream_shutdown.state == "waiting_game")
                        self.status.set("识别短暂不稳定，正在从断点重新接管…" if keep_running
                                        else "当前落子未确认，已暂停并保留断点")
                        self.append("[助手] 本次未点击棋盘，已保留可信断点；等待画面稳定后自动恢复。\n" if keep_running
                                    else "[助手] 本次未点击棋盘，已保留可信断点；画面稳定后可手动开始。\n")
                        if recovering_for_shutdown:
                            self._schedule_prepare(automatic=False, resume_only=True)
                            self.status.set("本局后下播 · 仅恢复当前棋局")
                        elif keep_running:
                            self._schedule_prepare()
                    else:
                        self.status.set("本次接管未成功，继续等待稳定棋盘…")
                        self.append("[助手] 本次画面识别未通过，未执行点击；继续等待。\n")
                        if keep_running:
                            self._schedule_prepare()
                elif isinstance(item, tuple) and item[0] == "shutdown_game_over":
                    self.waiting_for_board = False
                    if self.live_controller:
                        self.live_controller.submit_chess(item[1])
                elif isinstance(item, tuple) and item[0] == "shutdown_result":
                    if self.stream_shutdown.closed(item[1], success=item[2], message=item[3]):
                        if item[2] and self.live_controller:
                            self.chess_session_requested = False
                            self.live_controller.pause()
                            self.live_controller.disconnect()
                            self._sync_interaction_button()
                        self._sync_shutdown()
                        self.append("[下播] " + item[3] + "\n")
                elif isinstance(item, tuple) and item[0] == "status":
                    self.status.set(item[1])
                    self.append("[助手] " + item[1] + "\n")
                elif isinstance(item, tuple) and item[0] == "autostart":
                    if self.task_cancel.is_set() or not self.waiting_for_board:
                        continue
                    if getattr(self, "waiter_automatic", False) and not (
                            self.auto_retry and bool(self.continuous.get())):
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
                                   "endgame_min_pieces", 4),
                               resume_shutdown=bool(details.get("resume") and self._shutdown_active()),
                               startup_position=(details if not details.get("resume") else None))
                elif isinstance(item, tuple) and item[0] == "assistant_error":
                    self.waiting_for_board = False
                    self.status.set("一键助手失败：" + item[1])
                    self.append("[助手] 失败：" + item[1] + "\n")
                elif isinstance(item, tuple) and item[0] == "local_tts_startup_error":
                    self._note_issue("本地语音", item[1])
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
                elif isinstance(item, tuple) and item[0] == "live_connect_result":
                    if item[1] is not getattr(self, "live_connect_request", None):
                        continue
                    self.live_connect_request = None
                    if item[2]:
                        self.connection_state = "error"
                        self.live_status.set("连接失败：" + item[2])
                        self.append_live("连接失败", item[2])
                    self._sync_connection_button()
                elif isinstance(item, tuple) and item[0] == "douyin_login":
                    self._handle_douyin_login(item[1], item[2])
                elif isinstance(item, tuple) and item[0] == "live_event":
                    record = item[1]
                    kind = record.get("kind", "status")
                    data = record.get("data") or {}
                    if data.get("测试输入"):
                        continue
                    if kind in ("game_lifecycle", "shutdown_speech_done"):
                        flow = getattr(self, "stream_shutdown", None)
                        if flow:
                            if kind == "game_lifecycle":
                                flow.observe_game(data)
                            else:
                                flow.speech_done(data)
                            self._sync_shutdown()
                        continue
                    if self.task_cancel.is_set() and (
                            kind in ("chess", "sandtable") or (
                                kind in ("reply", "speech_status", "queued", "error") and (
                                    data.get("kind") in CHESS_SPEECH_KINDS or data.get("speech_token")))):
                        continue
                    text = record.get("text", "")
                    if kind == "sandtable":
                        self._handle_sandtable(record.get("data", {}))
                        continue
                    if kind == "connection":
                        epoch = record.get("data", {}).get("connector_epoch")
                        if epoch is not None and epoch != self.live_controller.connector_epoch:
                            continue
                        self.connection_state = record.get("data", {}).get("state") or getattr(self, "connection_state", "disconnected")
                        if record.get("data", {}).get("state") == "connected":
                            self._reveal_room_log(epoch)
                        if self.connection_state == "error":
                            self._note_issue("直播", text)
                        elif record.get("data", {}).get("state") == "disconnected":
                            self._note_issue("直播", "直播连接已断开，请重新连接", "warn")
                        elif self.connection_state == "ended":
                            self._clear_issue("直播")
                        self.live_status.set(text)
                        self._sync_connection_button()
                        if record.get("data", {}).get("state") in ("disconnected", "ended", "error"):
                            self.room_metrics = {}
                            self.audience_text.set("在线人数 · 待获取")
                            self._reset_interaction_display()
                        self._queue_monitor()
                    elif kind == "room_stats":
                        stats = record.get("data") or {}
                        epoch = stats.get("connector_epoch")
                        if epoch is not None and epoch != self.live_controller.connector_epoch:
                            continue
                        self.room_metrics = stats
                        count = stats.get("online")
                        self.audience_text.set(f"在线 {count:,} 人" if isinstance(count, int)
                                               else "在线人数 · 待获取")
                        if stats.get("mode") == "逐条互动":
                            mode_text = "低流量直播间 · 按到达顺序逐条互动"
                        else:
                            mode_text = (f"自动抽选 · 约每 {stats.get('interval', 15)} 秒最多回复一条")
                        self.chat_sampling_text.set(f"{mode_text} · 弹幕 {stats.get('chat_rate', 0):g} 条/秒")
                        self._queue_monitor()
                    elif kind == "interaction_done":
                        self._finish_current_interaction(record.get("data") or {})
                    elif kind == "speech_status":
                        self.speech_status.set(text)
                    elif kind == "error":
                        self.speech_status.set("播报异常：" + str(text)[:80])
                    elif kind == "reply":
                        self._clear_issue("播报")
                        data = record.get("data") or {}
                        current_interaction = ("_chat_epoch" not in data or
                            data["_chat_epoch"] == self.live_controller.pipeline.chat_epoch)
                        if data.get("kind") == "弹幕回复" and current_interaction:
                            self._show_current_interaction(data)
                        elif data.get("_speech_mode") == "audio" and current_interaction:
                            self._reset_interaction_display()
                        self.append_speech(record)

                else:
                    if isinstance(item, str):
                        self.append(item, display_text=styled_player_line(item))
                    else:
                        self.append(str(item))
        except queue.Empty:
            pass
        self._flush_room_messages()
        self.root.after(80, self._poll)

    def close(self):
        self.closing = True
        if hasattr(self, "shutdown_cancel"):
            self.shutdown_cancel.set()
        self._cancel_interaction_clear()
        self._cancel_scheduled_prepare()
        self._publish_auto_continue(False)
        self.task_cancel.set()
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
    enable_high_dpi()
    parser = argparse.ArgumentParser()
    parser.add_argument("--autostart", action="store_true",
                        help="启动模拟器/JJ，并等待标准棋盘后自动对弈")
    args = parser.parse_args()
    root = tk.Tk()
    ControlPanel(root, autostart=args.autostart)
    root.mainloop()


if __name__ == "__main__":
    main()
