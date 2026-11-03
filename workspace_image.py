"""A lazily loaded photograph for the workbench when every log is hidden."""
from pathlib import Path
import tkinter as tk
from PIL import Image, ImageOps, ImageTk


DEFAULT_IMAGE = Path(__file__).resolve().parent / "assets" / "grassland-portrait-hd-v1.png"


class WorkspaceImage(tk.Frame):
    def __init__(self, parent, *, background, image_path=DEFAULT_IMAGE):
        super().__init__(parent, bg=background, bd=0, highlightthickness=0)
        self.image_path = Path(image_path)
        self._source = None
        self._photo = None
        self._display_size = None
        self._fit_job = None
        self.image_label = tk.Label(self, bg=background, bd=0, highlightthickness=0)
        self.image_label.place(relx=.5, rely=.5, anchor="center")
        self.bind("<Configure>", self._schedule_fit)
        self.bind("<Map>", self._schedule_fit)
        self.bind("<Unmap>", self._cancel_fit)
        self.bind("<Destroy>", self._cleanup)

    def _schedule_fit(self, event=None):
        self._cancel_fit()
        self._fit_job = self.after(60, self._fit_image)

    def _cancel_fit(self, event=None):
        if self._fit_job is not None:
            self.after_cancel(self._fit_job)
            self._fit_job = None

    def _fit_image(self):
        self._fit_job = None
        if not self.winfo_ismapped():
            return
        width, height = self.winfo_width(), self.winfo_height()
        if width < 2 or height < 2:
            return
        if self._source is None:
            try:
                with Image.open(self.image_path) as source:
                    self._source = ImageOps.exif_transpose(source).convert("RGB")
                    self._source.load()
            except (OSError, ValueError, Image.DecompressionBombError):
                return
        ratio = min(width / self._source.width, height / self._source.height)
        size = (max(1, round(self._source.width * ratio)), max(1, round(self._source.height * ratio)))
        if size == self._display_size:
            return
        self._photo = ImageTk.PhotoImage(self._source.resize(size, Image.Resampling.LANCZOS), master=self)
        self.image_label.configure(image=self._photo)
        self._display_size = size

    def _cleanup(self, event):
        if event.widget is self:
            self._cancel_fit()
            self._photo = None
            self._source = None
            self.image_label = None
