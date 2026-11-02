"""Viewer-facing comment display, shared by the workbench and capture window."""
import tkinter as tk
from tkinter import font as tkfont


class BroadcastView(tk.Frame):
    def __init__(self, parent, user, message, palette):
        super().__init__(parent, bg=palette["panel"], height=152,
                         highlightthickness=1, highlightbackground=palette["border"])
        self.pack_propagate(False)
        self._layout_job = None
        self._variables = (user, message)
        self._traces = [variable.trace_add("write", self._schedule_layout)
                        for variable in self._variables]
        accent = tk.Frame(self, bg=palette["jade"], width=4)
        accent.pack(side="left", fill="y", padx=(0, 20))
        content = tk.Frame(self, bg=palette["panel"])
        content.pack(fill="both", expand=True, padx=(0, 24), pady=14)
        self.user_label = tk.Label(content, textvariable=user, bg=palette["panel"],
            fg=palette["jade"], font=("Microsoft YaHei UI", 11, "bold"),
            width=1, anchor="w")
        self.user_label.pack(fill="x")
        self.message_font = tkfont.Font(root=self, family="Microsoft YaHei UI", size=22, weight="bold")
        self.message_label = tk.Label(content, textvariable=message, bg=palette["panel"],
            fg=palette["text"], font=self.message_font, width=1, anchor="nw", justify="left")
        self.message_label.pack(fill="both", expand=True, pady=(8, 0))
        self.message_label.bind("<Configure>", self._schedule_layout)
        self.bind("<Destroy>", self._cleanup)

    def _schedule_layout(self, *_):
        if self._layout_job is None:
            self._layout_job = self.after_idle(self._fit_message)

    def _fit_message(self):
        self._layout_job = None
        width = max(40, self.message_label.winfo_width() - 8)
        height = max(1, self.message_label.winfo_height() - 4)
        self.user_label.configure(wraplength=width)
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
        self.message_label.configure(wraplength=width)

    def _cleanup(self, event):
        if event.widget is not self:
            return
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
        for variable, trace in zip(self._variables, self._traces):
            variable.trace_remove("write", trace)
