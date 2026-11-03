"""One cached wallpaper shared by the main window and page backgrounds."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageOps, ImageTk


DEFAULT_BACKGROUND = Path(__file__).resolve().parent / "assets" / "grassland-portrait-hd-v2.png"
BACKGROUND_DIRECTORY = Path(__file__).resolve().parent / "work" / "backgrounds"


def compose_background(source, size, base_color, transparency):
    base = Image.new("RGB", size, base_color)
    layer = base.copy()
    photo = ImageOps.fit(source, size, Image.Resampling.LANCZOS)
    layer.paste(photo, (0, 0))
    return Image.blend(layer, base, max(0, min(100, transparency)) / 100)


class AppWallpaper:
    def __init__(self, root, *, image_path="", transparency=30, base_color):
        self.root = root
        self.image_path = Path(image_path) if image_path else DEFAULT_BACKGROUND
        self.transparency = max(0, min(100, transparency))
        self.base_color = base_color
        self._source = None
        self._image = None
        self._render_key = None
        self._job = None
        self._revision = 0
        self._surfaces = {}
        self._labels = []
        self.register(root)
        root.bind("<Destroy>", self._cleanup, add="+")

    def register(self, widget):
        if widget in self._surfaces:
            return
        if isinstance(widget, tk.Canvas):
            display = widget.create_image(0, 0, anchor="nw")
            widget.tag_lower(display)
        else:
            display = tk.Label(widget, bg=self.base_color, bd=0, highlightthickness=0,
                               takefocus=False)
            display.place(x=0, y=0, relwidth=1, relheight=1, bordermode="outside")
            display.lower()
        if widget not in self._surfaces:
            self._surfaces[widget] = {"display": display, "photo": None, "key": None}
        widget.bind("<Configure>", self._schedule, add="+")
        widget.bind("<Map>", self._schedule, add="+")
        self._schedule()

    def cover_widgets(self, widget, *, exclude=()):
        """Share root-aligned image slices; keep capture windows and controls intact."""
        if widget in exclude or isinstance(widget, tk.Toplevel):
            return
        children = tuple(widget.winfo_children())
        displays = {entry["display"] for entry in self._surfaces.values()
                    if isinstance(entry["display"], tk.Widget)}
        if widget in displays:
            return
        if hasattr(widget, "use_wallpaper"):
            widget.use_wallpaper()
        if isinstance(widget, ttk.Frame) and ".Log." in str(widget.cget("style")):
            pass
        elif isinstance(widget, (tk.Label, ttk.Label, ttk.Checkbutton)):
            if not isinstance(widget, ttk.Label) or ".Log." not in str(widget.cget("style")):
                from wallpaper_text import WallpaperLabel
                self._labels.append(WallpaperLabel(widget, self))
        elif isinstance(widget, (tk.Frame, ttk.Frame, tk.PanedWindow, tk.Canvas)):
            self.register(widget)
        for child in children:
            if hasattr(widget, "canvas") and getattr(widget, "_wallpaper_mode", False) and child is widget.canvas:
                continue
            self.cover_widgets(child, exclude=exclude)
        self._schedule()

    def canvas_scrolled(self, canvas):
        surface = self._surfaces.get(canvas)
        if surface:
            canvas.coords(surface["display"], canvas.canvasx(0), canvas.canvasy(0))

    def _schedule(self, event=None):
        if self.root is None:
            return
        if self._job is not None:
            self.root.after_cancel(self._job)
        self._job = self.root.after(80, self._render)

    def set_image(self, path=""):
        requested = Path(path) if path else DEFAULT_BACKGROUND
        try:
            with Image.open(requested) as source:
                loaded = ImageOps.exif_transpose(source).convert("RGB")
                loaded.load()
        except (OSError, ValueError, Image.DecompressionBombError):
            return False
        self.image_path, self._source = requested, loaded
        self._revision += 1
        self._schedule()
        return True

    def set_transparency(self, value):
        self.transparency = max(0, min(100, float(value)))
        self._schedule()

    def set_base_color(self, color):
        self.base_color = color
        for label in self._labels:
            label._schedule()
        self._schedule()

    def _render(self):
        if self._job is not None:
            self.root.after_cancel(self._job)
        self._job = None
        if self.root is None or not self.root.winfo_ismapped():
            return
        size = self.root.winfo_width(), self.root.winfo_height()
        if min(size) < 2:
            return
        if self._source is None:
            if not self.set_image(str(self.image_path)) and not self.set_image():
                return
            if self._job is not None:
                self.root.after_cancel(self._job)
                self._job = None
        key = (size, self.transparency, self.base_color, self._revision)
        if key != self._render_key:
            self._image = compose_background(self._source, size, self.base_color, self.transparency)
            self._render_key = key
        origin_x, origin_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        for widget, surface in tuple(self._surfaces.items()):
            if not widget.winfo_exists() or not widget.winfo_ismapped():
                continue
            width, height = widget.winfo_width(), widget.winfo_height()
            x, y = widget.winfo_rootx() - origin_x, widget.winfo_rooty() - origin_y
            patch_key = key, x, y, width, height
            if patch_key == surface["key"] or min(width, height) < 2:
                continue
            photo = ImageTk.PhotoImage(self._image.crop((x, y, x + width, y + height)), master=widget)
            surface["photo"], surface["key"] = photo, patch_key
            if isinstance(widget, tk.Canvas):
                widget.itemconfigure(surface["display"], image=photo)
                widget.tag_lower(surface["display"])
                self.canvas_scrolled(widget)
            else:
                surface["display"].configure(image=photo)
                surface["display"].lower()

    def _cleanup(self, event):
        if event.widget is self.root:
            if self._job is not None:
                self.root.after_cancel(self._job)
            self._job = None
            self._source = self._image = None
            self._surfaces.clear()
            self._labels.clear()
            self.root = None
