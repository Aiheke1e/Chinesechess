"""Viewer-facing comment display, shared by the workbench and capture window."""
import tkinter as tk
from tkinter import font as tkfont
from soft_ui import SoftCard, ui_scale


BROADCAST_PALETTE = {
    "bg": "#101d34", "panel": "#172b4b", "border": "#bd944c",
    "text": "#fff7e6", "jade": "#f2cf83",
}


class BroadcastView(SoftCard):
    def __init__(self, parent, user, message, palette=None, *, on_popout=None):
        palette = dict(BROADCAST_PALETTE)
        super().__init__(parent, palette, height=round(132 * ui_scale(parent)), inset=4)
        self.pack_propagate(False)
        self._layout_job = None
        self._variables = (user, message)
        self._traces = [variable.trace_add("write", self._schedule_layout)
                        for variable in self._variables]
        self.display_user = tk.StringVar(master=self, value=user.get())
        self.display_message = tk.StringVar(master=self, value=message.get())
        content = self.inner = tk.Frame(self.content, bg=palette["panel"])
        content.pack(fill="both", expand=True, padx=12, pady=5)
        self.header = tk.Frame(content, bg=palette["panel"])
        self.header.pack(fill="x")
        if on_popout:
            self.popout_button = tk.Button(self.header, text="弹出 ↗", command=on_popout,
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
        self.bind("<Destroy>", self._cleanup, add="+")

    def _schedule_layout(self, *_):
        # Keep updates synchronous; fitting runs once after the geometry settles.
        self.display_user.set(self._variables[0].get())
        self.display_message.set(self._variables[1].get())
        if self._layout_job is None:
            self._layout_job = self.after_idle(self._fit_message)

    def _fit_message(self):
        self._layout_job = None
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
        for widget in (self.inner, self.header, self.user_label, self.message_label):
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
