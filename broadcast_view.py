"""Viewer-facing comment display, shared by the workbench and capture window."""
import tkinter as tk
from tkinter import font as tkfont
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageOps, ImageTk
from soft_ui import SoftCard, ui_scale
from stream_brand import CHANNEL_NAME, DEFAULT_AVATAR


BROADCAST_PALETTE = {
    "bg": "#101d34", "panel": "#172b4b", "border": "#bd944c",
    "text": "#fff7e6", "jade": "#f2cf83",
}


@lru_cache(maxsize=4)
def _avatar_source(path, modified):
    with Image.open(path) as source:
        result = ImageOps.exif_transpose(source).convert("RGBA")
        result.load()
    return result


class BroadcastView(SoftCard):
    def __init__(self, parent, user, message, palette=None, *, on_popout=None,
                 avatar_path=None, on_avatar_change=None):
        palette = dict(BROADCAST_PALETTE)
        super().__init__(parent, palette, height=round(132 * ui_scale(parent)), inset=4)
        self.pack_propagate(False)
        self._layout_job = None
        self._variables = (user, message)
        self._traces = [variable.trace_add("write", self._schedule_layout)
                        for variable in self._variables]
        self.display_user = tk.StringVar(master=self, value=user.get())
        self.display_message = tk.StringVar(master=self, value=message.get())
        self.avatar_column = tk.Frame(self.content, bg=palette["panel"])
        self.avatar_column.pack(side="left", fill="y", padx=(0, 12))
        self.avatar_label = tk.Label(self.avatar_column, text="豆", bg=palette["panel"],
            fg=palette["jade"], bd=0, font=("Microsoft YaHei UI", 22, "bold"))
        self.avatar_label.pack(expand=True)
        self.brand_label = tk.Label(self.avatar_column, text=CHANNEL_NAME, bg=palette["panel"],
            fg=palette["jade"], font=("Microsoft YaHei UI", 9, "bold"), bd=0)
        self.brand_label.pack(pady=(2, 2))
        if on_avatar_change:
            self.avatar_label.configure(cursor="hand2")
            self.avatar_label.bind("<Button-1>", lambda event: on_avatar_change())
        self._avatar_source_image = None
        self._avatar_image = None
        self._avatar_size = None
        content = self.inner = tk.Frame(self.content, bg=palette["panel"])
        content.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=5)
        self.header = tk.Frame(content, bg=palette["panel"])
        self.header.pack(fill="x")
        if on_popout:
            self.popout_button = tk.Button(self.header, text="弹出A区 ↗", command=on_popout,
                bg=palette["panel"], fg=palette["jade"], activebackground="#243c60",
                activeforeground=palette["text"], relief="flat", bd=0, cursor="hand2",
                font=("Microsoft YaHei UI", 10), padx=8, pady=2)
            self.popout_button.pack(side="right", padx=(8, 0))
            content.bind("<Double-Button-1>", lambda event: on_popout())
        self.user_font = tkfont.Font(root=self, family="Microsoft YaHei UI", size=13, weight="bold")
        self.user_label = tk.Label(self.header, textvariable=self.display_user, bg=palette["panel"],
            fg=palette["jade"], font=("Microsoft YaHei UI", 13, "bold"),
            width=1, anchor="w")
        self.user_label.configure(font=self.user_font)
        self.user_label.pack(side="left", fill="x", expand=True)
        self.rule = tk.Frame(content, height=1, bg=palette["border"])
        self.rule.pack(fill="x", pady=(6, 0))
        self.message_font = tkfont.Font(root=self, family="Microsoft YaHei UI", size=22, weight="bold")
        self.message_label = tk.Label(content, textvariable=self.display_message, bg=palette["panel"],
            fg=palette["text"], font=self.message_font, width=1, anchor="nw", justify="left")
        self.message_label.pack(fill="both", expand=True, pady=(8, 0))
        self.message_label.bind("<Configure>", self._schedule_layout)
        self.user_label.bind("<Configure>", self._schedule_layout)
        self.content.bind("<Configure>", self._schedule_layout)
        self.bind("<Destroy>", self._cleanup, add="+")
        self.set_avatar(avatar_path)

    def set_avatar(self, path=None, *, strict=False):
        requested = Path(path) if path else DEFAULT_AVATAR
        try:
            source = _avatar_source(str(requested), requested.stat().st_mtime_ns)
        except (OSError, ValueError, Image.DecompressionBombError):
            if strict:
                return False
            try:
                source = _avatar_source(str(DEFAULT_AVATAR), DEFAULT_AVATAR.stat().st_mtime_ns)
            except (OSError, ValueError, Image.DecompressionBombError):
                source = None
        self._avatar_source_image = source
        self._avatar_size = None
        self._schedule_layout()
        return source is not None

    def _fit_avatar(self):
        scale = ui_scale(self)
        compact = self.winfo_width() < round(380 * scale)
        if compact:
            self.brand_label.pack_forget()
        elif not self.brand_label.winfo_manager():
            self.brand_label.pack(pady=(2, 2))
        available = self.content.winfo_height() - (0 if compact else self.brand_label.winfo_reqheight() + 8)
        size = max(round(28 * scale), min(round((42 if compact else 160) * scale), available))
        if self._avatar_source_image is not None and size != self._avatar_size:
            self._avatar_size = size
            self._avatar_image = ImageTk.PhotoImage(ImageOps.contain(self._avatar_source_image,
                (size, size), Image.Resampling.LANCZOS), master=self)
            self.avatar_label.configure(image=self._avatar_image, text="")

    def _schedule_layout(self, *_):
        # Keep updates synchronous; fitting runs once after the geometry settles.
        self.display_user.set(self._variables[0].get())
        self.display_message.set(self._variables[1].get())
        if self._layout_job is None:
            self._layout_job = self.after_idle(self._fit_message)

    def _fit_message(self):
        self._layout_job = None
        self._fit_avatar()
        width = max(40, self.message_label.winfo_width() - 8)
        height = max(1, self.message_label.winfo_height() - 4)
        name = self._variables[0].get()
        name_width = max(40, self.user_label.winfo_width() - 4)
        for size in (13, 12, 11, 10):
            self.user_font.configure(size=size)
            if self.user_font.measure(name) <= name_width:
                break
        shown = name
        while shown and self.user_font.measure(shown) > name_width:
            shown = shown[:-2].rstrip("…") + "…" if len(shown) > 2 else ""
        self.display_user.set(shown)
        text = self._variables[1].get()
        for size in (22, 20, 18, 16, 14, 12, 10):
            self.message_font.configure(size=size)
            lines, used = 1, 0
            widths = {}
            for char in text:
                if char == "\n":
                    lines, used = lines + 1, 0
                    continue
                if char not in widths:
                    widths[char] = self.message_font.measure(char)
                measured = widths[char]
                if used and used + measured > width:
                    lines, used = lines + 1, 0
                used += measured
            if lines * self.message_font.metrics("linespace") <= height:
                break
        if lines * self.message_font.metrics("linespace") > height:
            def fits(candidate):
                count, used_width = 1, 0
                for char in candidate:
                    if char == "\n":
                        count, used_width = count + 1, 0
                        continue
                    if char not in widths:
                        widths[char] = self.message_font.measure(char)
                    measured = widths[char]
                    if used_width and used_width + measured > width:
                        count, used_width = count + 1, 0
                    used_width += measured
                return count * self.message_font.metrics("linespace") <= height
            shown = text
            while shown and not fits(shown):
                shown = shown[:-2].rstrip("…") + "…" if len(shown) > 2 else ""
            self.display_message.set(shown)
        else:
            self.display_message.set(text)
        self.message_label.configure(wraplength=width)

    def set_palette(self, palette):
        # The viewer-facing blue/gold theme is independent of operator themes.
        super().set_palette(BROADCAST_PALETTE)
        for widget in (self.inner, self.header, self.user_label, self.message_label,
                       self.avatar_column, self.avatar_label, self.brand_label):
            widget.configure(bg=BROADCAST_PALETTE["panel"])
        self.user_label.configure(fg=BROADCAST_PALETTE["jade"])
        self.message_label.configure(fg=BROADCAST_PALETTE["text"])
        self.rule.configure(bg=BROADCAST_PALETTE["border"])

    def _cleanup(self, event):
        if event.widget is not self:
            return
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
        for variable, trace in zip(self._variables, self._traces):
            variable.trace_remove("write", trace)
