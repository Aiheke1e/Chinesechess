"""Native Text editing and layout, drawn over a shared wallpaper on a Canvas.

Tk Text has no image background. Keeping its normal text engine underneath a
Canvas preserves wrapping, tags, selection, copying and scrolling without
Windows color-key transparency (which damages antialiased glyph edges).
"""
import tkinter as tk
from tkinter import font as tkfont, ttk


class WallpaperText(tk.Text):
    def __init__(self, parent, *, wallpaper, **kwargs):
        super().__init__(parent, **kwargs)
        self._draw_job = None
        self._fonts = {}
        self._display = tk.Canvas(parent, bd=0, highlightthickness=0, takefocus=False)
        wallpaper.register(self._display)
        self._original_command = self._w + "_native"
        self.tk.call("rename", self._w, self._original_command)
        self.tk.createcommand(self._w, self._proxy)
        for event in ("<Configure>", "<Map>"):
            self.bind(event, self._sync_display, add="+")
        for event in ("<FocusIn>", "<FocusOut>", "<KeyRelease>", "<<Selection>>"):
            self.bind(event, self._schedule_draw, add="+")
        for event in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>", "<MouseWheel>"):
            self._display.bind(event, lambda e, sequence=event: self._pointer(e, sequence))
        self.bind("<Unmap>", lambda event: self._display.place_forget(), add="+")
        self.bind("<Destroy>", self._cleanup, add="+")

    def _proxy(self, *args):
        result = self.tk.call(self._original_command, *args)
        if args and (args[0] in ("insert", "delete", "replace", "see", "xview", "yview", "configure")
                     or args[0] == "mark" and len(args) > 1 and args[1] == "set"
                     or args[0] == "tag" and len(args) > 1 and args[1] in ("add", "remove", "configure", "raise", "lower")):
            self._schedule_draw()
        return result

    def _sync_display(self, event=None):
        if self.winfo_ismapped():
            self._display.place(in_=self, x=0, y=0, relwidth=1, relheight=1, bordermode="outside")
            tk.Misc.lift(self._display)
            self._schedule_draw()

    def _schedule_draw(self, event=None):
        if self._draw_job is None:
            self._draw_job = self.after(16, self._draw)

    def _pointer(self, event, sequence):
        if sequence == "<ButtonPress-1>":
            self.focus_set()
        options = dict(x=event.x, y=event.y, rootx=event.x_root, rooty=event.y_root,
                       state=event.state, time=event.time)
        if sequence == "<MouseWheel>":
            options["delta"] = event.delta
        self.event_generate(sequence, **options)
        self._schedule_draw()
        return "break"

    def _text_style(self, index):
        values = {"font": self.cget("font"), "foreground": self.cget("fg"), "background": ""}
        for tag in self.tag_names(index):
            for name in values:
                value = self.tag_cget(tag, name)
                if value:
                    values[name] = value
        return values

    def _draw(self):
        if self._draw_job is not None:
            self.after_cancel(self._draw_job)
        self._draw_job = None
        if not self.winfo_ismapped():
            return
        canvas = self._display
        canvas.delete("text_content")
        start = self.index("@0,0")
        end = self.index(f"@{self.winfo_width()},{self.winfo_height()} +1 chars")
        for kind, content, index in self.dump(start, end, text=True):
            if kind != "text":
                continue
            # Split at tag transitions as well as native display-line wraps.
            offset = 0
            run = None
            for char in content:
                current = f"{index}+{offset}c"
                box = self.bbox(current)
                if box is None:
                    offset += 1
                    continue
                style = self._text_style(current)
                if char in "\n\t" or run is not None and (box[1] != run[1] or style != run[4]):
                    if run is not None:
                        self._draw_run(*run)
                        run = None
                if char not in "\n\t":
                    if run is None:
                        run = [box[0], box[1], box[3], char, style, box[2]]
                    else:
                        run[3] += char
                        run[5] += box[2]
                offset += 1
            if run is not None:
                self._draw_run(*run)
        if self.focus_get() is self and str(self.cget("state")) == "normal":
            box = self.bbox("insert")
            if box:
                x, y, width, height = box
                canvas.create_line(x, y, x, y + height, fill=self.cget("insertbackground"),
                                   tags="text_content")

    def _draw_run(self, x, y, height, text, style, width):
        canvas = self._display
        if style["background"]:
            canvas.create_rectangle(x, y, x + width, y + height, fill=style["background"],
                                    outline="", tags="text_content")
        font_key = str(style["font"])
        if font_key not in self._fonts:
            self._fonts[font_key] = tkfont.Font(root=self, font=style["font"])
        font = self._fonts[font_key]
        canvas.create_text(x - 1, y + max(0, (height - font.metrics("linespace")) // 2),
                           text=text, anchor="nw", font=font, fill=style["foreground"],
                           tags="text_content")

    def _cleanup(self, event):
        if event.widget is not self:
            return
        if self._draw_job is not None:
            self.after_cancel(self._draw_job)
            self._draw_job = None
        self._fonts.clear()
        self.tk.deletecommand(self._w)
        # Tk deletes the renamed native command when its window is destroyed.


class WallpaperLabel:
    """Keep native label sizing/variables while painting its text on the image."""
    def __init__(self, label, wallpaper):
        self.label = label
        self._job = None
        self.canvas = tk.Canvas(label.master, bd=0, highlightthickness=0, takefocus=False)
        wallpaper.register(self.canvas)
        self._original_command = label._w + "_native"
        label.tk.call("rename", label._w, self._original_command)
        label.tk.createcommand(label._w, self._proxy)
        for event in ("<Map>", "<Configure>"):
            label.bind(event, self._sync, add="+")
        label.bind("<Unmap>", lambda event: self.canvas.place_forget(), add="+")
        label.bind("<Destroy>", self._cleanup, add="+")
        self._variable = str(label.cget("textvariable"))
        self._trace = label.register(self._schedule)
        self._switch = isinstance(label, ttk.Checkbutton)
        self._pressed = False
        self._variables = [self._variable] if self._variable else []
        if self._switch:
            self._variables.append(str(label.cget("variable")))
            self.canvas.bind("<ButtonPress-1>", self._press)
            self.canvas.bind("<ButtonRelease-1>", self._toggle)
            label.bind("<FocusIn>", self._schedule, add="+")
            label.bind("<FocusOut>", self._schedule, add="+")
        for variable in self._variables:
            label.tk.call("trace", "add", "variable", variable, "write", self._trace)

    def _press(self, event):
        self._pressed = True
        self.label.focus_set()
        return "break"

    def _toggle(self, event):
        if self._pressed and 0 <= event.x < self.canvas.winfo_width() and 0 <= event.y < self.canvas.winfo_height():
            self.label.invoke()
        self._pressed = False
        return "break"

    def _proxy(self, *args):
        result = self.label.tk.call(self._original_command, *args)
        if args and (args[0] in ("configure", "state") and len(args) > 1 or args[0] == "invoke"):
            self._schedule()
        return result

    def _sync(self, event=None):
        if self.label.winfo_ismapped():
            self.canvas.place(in_=self.label, x=0, y=0, relwidth=1, relheight=1, bordermode="outside")
            tk.Misc.lift(self.canvas)
            self._schedule()

    def _schedule(self, *_):
        if self._job is None:
            self._job = self.label.after(16, self._draw)

    def _draw(self):
        if self._job is not None:
            self.label.after_cancel(self._job)
        self._job = None
        if not self.label.winfo_ismapped():
            return
        label = self.label
        text = label.getvar(self._variable) if self._variable else label.cget("text")
        width, height = label.winfo_width(), label.winfo_height()
        themed = isinstance(label, ttk.Widget)
        if themed:
            style = ttk.Style(label)
            name = str(label.cget("style")) or label.winfo_class()
            font = style.lookup(name, "font", label.state()) or "TkDefaultFont"
            foreground = style.lookup(name, "foreground", label.state()) or "#172d44"
            anchor = "w"
            padx = pady = 0
            justify, wrap = "left", 0
        else:
            anchor = str(label.cget("anchor"))
            padx = label.winfo_pixels(label.cget("padx"))
            pady = label.winfo_pixels(label.cget("pady"))
            font, foreground = label.cget("font"), label.cget("fg")
            justify, wrap = label.cget("justify"), label.winfo_pixels(label.cget("wraplength"))
        x = padx if anchor in ("w", "nw", "sw") else width - padx if anchor in ("e", "ne", "se") else width / 2
        y = pady if anchor in ("n", "nw", "ne") else height - pady if anchor in ("s", "sw", "se") else height / 2
        self.canvas.delete("text_content")
        if self._switch:
            scale = max(1, label.winfo_fpixels("1i") / 96)
            selected = str(label.getvar(label.cget("variable"))) == str(label.cget("onvalue"))
            color = "#9aa4ad" if label.instate(("disabled",)) else "#269766" if selected else "#c45b66"
            self.canvas.create_line(8 * scale, height / 2, 20 * scale, height / 2,
                width=16 * scale, fill=color, capstyle="round", tags="text_content")
            center, radius = (20 if selected else 8) * scale, 5 * scale
            self.canvas.create_oval(center - radius, height / 2 - radius,
                center + radius, height / 2 + radius, fill="white", outline="", tags="text_content")
            x = 34 * scale
            if label.focus_get() is label:
                self.canvas.create_rectangle(1, 1, width - 1, height - 1,
                    outline=foreground, dash=(2, 2), tags="text_content")
        self.canvas.create_text(x, y, text=text, anchor=anchor, font=font,
                                fill=foreground, justify=justify, width=wrap, tags="text_content")

    def _cleanup(self, event):
        if event.widget is not self.label:
            return
        if self._job is not None:
            self.label.after_cancel(self._job)
            self._job = None
        for variable in self._variables:
            self.label.tk.call("trace", "remove", "variable", variable, "write", self._trace)
        self.label.tk.deletecommand(self.label._w)
