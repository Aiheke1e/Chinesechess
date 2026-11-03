"""Local avatar library and a thumbnail picker outside the capture area."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageOps, ImageTk

from soft_ui import ui_scale
from local_images import import_image
from stream_brand import AVATAR_PRESETS, DEFAULT_AVATAR


UPLOAD_DIRECTORY = Path(__file__).resolve().parent / "work" / "avatars"


def avatar_caption(path):
    selected = Path(path) if path else DEFAULT_AVATAR
    for caption, preset in AVATAR_PRESETS:
        if selected.resolve() == preset.resolve():
            return caption
    return "已上传图片"


def import_avatar(path):
    """Validate and preserve the original file locally; no external upload."""
    return import_image(path, UPLOAD_DIRECTORY)


class AvatarPicker(tk.Toplevel):
    def __init__(self, parent, selected, on_select, on_upload, palette):
        super().__init__(parent)
        self.title("互动展示头像")
        self.transient(parent)
        self.configure(bg=palette["panel"])
        self.resizable(False, False)
        self._images = []
        self._buttons = []
        self._palette = palette
        self._on_select = on_select
        grid = tk.Frame(self, bg=palette["panel"], padx=16, pady=16)
        grid.pack(fill="both", expand=True)
        scale = ui_scale(self)
        size = round(104 * scale)
        columns = min(5, max(2, (self.winfo_screenwidth() - round(48 * scale)) // round(140 * scale)))
        for index, (caption, path) in enumerate(AVATAR_PRESETS):
            with Image.open(path) as source:
                thumbnail = ImageOps.contain(ImageOps.exif_transpose(source).convert("RGBA"),
                    (size, size), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(thumbnail, master=self)
            self._images.append(photo)
            button = tk.Button(grid, text=caption, image=photo, compound="top",
                bg=palette["panel"], fg=palette["text"], activebackground=palette["panel2"],
                activeforeground=palette["text"], font=("Microsoft YaHei UI", 10),
                padx=10, pady=8, bd=0, relief="flat", highlightthickness=2, cursor="hand2",
                command=lambda target=path: self._choose(str(target)))
            button.grid(row=index // columns, column=index % columns, padx=5, pady=5, sticky="nsew")
            self._buttons.append((button, path))
        actions = ttk.Frame(self, padding=(20, 0, 20, 16))
        actions.pack(fill="x")
        ttk.Button(actions, text="上传图片…", command=lambda: on_upload(self)).pack(side="left")
        ttk.Button(actions, text="完成", command=self.destroy).pack(side="right")
        self.set_selection(selected)
        self.update_idletasks()
        x = max(0, min(parent.winfo_rootx() + 60, self.winfo_screenwidth() - self.winfo_reqwidth()))
        y = max(0, min(parent.winfo_rooty() + 60, self.winfo_screenheight() - self.winfo_reqheight() - 50))
        self.geometry(f"+{x}+{y}")

    def _choose(self, path):
        if self._on_select(path):
            self.set_selection(path)

    def set_selection(self, selected):
        selected = Path(selected) if selected else DEFAULT_AVATAR
        for button, path in self._buttons:
            color = self._palette["jade"] if selected.resolve() == path.resolve() else self._palette["border"]
            button.configure(highlightbackground=color, highlightcolor=color)
