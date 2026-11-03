"""Minimal viewer-facing avatar and the exact comment being answered."""
import tkinter as tk
from tkinter import font as tkfont
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageOps, ImageTk
from soft_ui import SoftCard, ui_scale
from stream_brand import HOST_NAME, DEFAULT_AVATAR


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
        self.avatar_label = tk.Label(self.content, text=HOST_NAME[:1], bg=palette["panel"],
            fg=palette["jade"], bd=0, font=("Microsoft YaHei UI", 22, "bold"))
        self.avatar_label.pack(side="left", padx=(0, 14), anchor="center")
        if on_avatar_change:
            self.avatar_label.configure(cursor="hand2")
            self.avatar_label.bind("<Button-1>", lambda event: on_avatar_change())
        self._avatar_source_image = None
        self._avatar_image = None
        self._avatar_size = None
        self.message_font = tkfont.Font(root=self, family="Microsoft YaHei UI", size=22, weight="bold")
        self.comment_text = tk.Text(self.content, font=self.message_font, bg=palette["panel"],
            fg=palette["text"], relief="flat", bd=0, highlightthickness=0, wrap="char",
            width=1, height=1, padx=0, pady=0, state="disabled", cursor="arrow",
            takefocus=False, exportselection=False)
        self.comment_text.tag_configure("viewer", foreground=palette["jade"])
        self.comment_text.tag_configure("message", foreground=palette["text"])
        self.comment_text.pack(side="left", fill="both", expand=True, pady=6)
        self.comment_text.bind("<Button-1>", lambda event: "break")
        if on_popout:
            self.comment_text.bind("<Double-Button-1>", lambda event: on_popout())
        self.comment_text.bind("<Configure>", self._schedule_layout)
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
        available = self.content.winfo_height() - 4
        size = max(round(28 * scale), min(round((48 if compact else 140) * scale), available))
        if self._avatar_source_image is not None and size != self._avatar_size:
            self._avatar_size = size
            self._avatar_image = ImageTk.PhotoImage(ImageOps.contain(self._avatar_source_image,
                (size, size), Image.Resampling.LANCZOS), master=self)
            self.avatar_label.configure(image=self._avatar_image, text="")

    def _comment(self):
        name, message = (variable.get() for variable in self._variables)
        return name, ("：" if name and message else "") + message

    def _render_comment(self, shown=None):
        name, body = self._comment()
        if shown is not None:
            name, body = shown[:len(name)], shown[len(name):]
        self.comment_text.configure(state="normal")
        self.comment_text.delete("1.0", "end")
        self.comment_text.insert("end", name, "viewer")
        self.comment_text.insert("end", body, "message")
        self.comment_text.configure(state="disabled")
        self.comment_text.yview_moveto(0)

    def _schedule_layout(self, *_):
        self._render_comment()
        if self._layout_job is None:
            self._layout_job = self.after_idle(self._fit_message)

    def _fit_message(self):
        self._layout_job = None
        self._fit_avatar()
        width = max(40, self.comment_text.winfo_width() - 4)
        height = max(1, self.comment_text.winfo_height() - 4)
        text = "".join(self._comment())

        def text_height(candidate):
            lines, used = 1, 0
            for char in candidate:
                if char == "\n":
                    lines, used = lines + 1, 0
                    continue
                if char not in widths:
                    widths[char] = self.message_font.measure(char)
                measured = widths[char]
                if used and used + measured > width:
                    lines, used = lines + 1, 0
                used += measured
            return lines * self.message_font.metrics("linespace")

        def fits(candidate):
            return text_height(candidate) <= height

        for size in (22, 20, 18, 16, 14, 12, 10):
            self.message_font.configure(size=size)
            widths = {}
            if fits(text):
                break
        shown = text
        if not fits(shown):
            # Keep fitting bounded; the source variables/log retain full text.
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if fits(text[:middle] + "…"):
                    low = middle
                else:
                    high = middle - 1
            shown = text[:low] + "…" if low else ""
        self.comment_text.configure(pady=max(0, (height - text_height(shown)) // 2))
        self._render_comment(shown)

    def set_palette(self, palette):
        super().set_palette(BROADCAST_PALETTE)
        self.avatar_label.configure(bg=BROADCAST_PALETTE["panel"])
        self.comment_text.configure(bg=BROADCAST_PALETTE["panel"], fg=BROADCAST_PALETTE["text"])

    def _cleanup(self, event):
        if event.widget is not self:
            return
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
        for variable, trace in zip(self._variables, self._traces):
            variable.trace_remove("write", trace)
