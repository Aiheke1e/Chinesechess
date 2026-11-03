"""Rounded native Tk surfaces; small cached assets, no drawing in the game loop."""
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageTk


def blend(first, second, amount):
    a = ImageColor.getrgb(first) if isinstance(first, str) else first
    b = ImageColor.getrgb(second) if isinstance(second, str) else second
    return tuple(round(x * (1 - amount) + y * amount) for x, y in zip(a, b))


def surface_image(size, background, fill, *, inset=8, radius=18, pressed=False):
    width, height = size
    result = Image.new("RGB", size, background)
    shadow = Image.new("RGBA", size)
    draw = ImageDraw.Draw(shadow)
    offset = 1 if pressed else 3
    bounds = (inset, inset, width - inset - 1, height - inset - 1)
    draw.rounded_rectangle(tuple(v + offset for v in bounds), radius,
                           fill=(*blend(background, "#50607a", .28), 75))
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(3, inset / 1.4)))
    result.paste(shadow, mask=shadow.getchannel("A"))
    draw = ImageDraw.Draw(result)
    draw.rounded_rectangle(bounds, radius, fill=fill,
                           outline=blend(fill, "#ffffff", .35), width=1)
    return result


class SoftCard(tk.Frame):
    """A rounded outer surface with a normal Frame for native child widgets."""
    def __init__(self, parent, palette, *, inset=10, **kwargs):
        super().__init__(parent, bg=palette["bg"], bd=0, **kwargs)
        self.palette = dict(palette)
        self.inset = inset
        self._paint_job = None
        self._paint_key = None
        self._image = None
        self.canvas = tk.Canvas(self, bg=palette["bg"], bd=0, highlightthickness=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.content = tk.Frame(self, bg=palette["panel"], bd=0)
        self.content.pack(fill="both", expand=True, padx=inset + 8, pady=inset + 8)
        self.bind("<Configure>", self._schedule_paint)
        self.bind("<Destroy>", self._surface_cleanup)

    def _schedule_paint(self, *_):
        if self._paint_job is None:
            self._paint_job = self.after(20, self._paint)

    def _paint(self):
        self._paint_job = None
        width, height = self.winfo_width(), self.winfo_height()
        if width < 2 * self.inset + 4 or height < 2 * self.inset + 4:
            return
        key = (width, height, self.palette["bg"], self.palette["panel"])
        if key == self._paint_key:
            return
        self._paint_key = key
        self._image = ImageTk.PhotoImage(surface_image((width, height), *key[2:],
            inset=self.inset, radius=22), master=self)
        self.canvas.itemconfigure(self._image_item, image=self._image)

    def set_palette(self, palette):
        self.palette = dict(palette)
        self.configure(bg=palette["bg"])
        self.canvas.configure(bg=palette["bg"])
        self.content.configure(bg=palette["panel"])
        self._schedule_paint()

    def _surface_cleanup(self, event):
        if event.widget is self and self._paint_job:
            self.after_cancel(self._paint_job)
            self._paint_job = None


def install_button_styles(root, palette):
    """Nine-slice images keep ordinary ttk focus, keyboard and disabled states."""
    style = ttk.Style(root)
    scale = max(1, root.winfo_fpixels("1i") / 96)
    # Keep only the current image set when switching themes.
    generation = getattr(root, "_soft_style_generation", 0) + 1
    root._soft_style_generation = generation
    images = []
    specs = {
        "TButton": (palette["button"], palette["text"]),
        "Primary.TButton": (palette["button"], palette["jade"]),
        "Action.Primary.TButton": (palette["jade2"], palette["jade"]),
        "Danger.TButton": (palette["error_bg"], palette["error_fg"]),
        "Action.Danger.TButton": (palette["red"], "#ffffff"),
        "LogToggle.TButton": (palette["jade2"], palette["jade"]),
    }
    for index, (name, (fill, foreground)) in enumerate(specs.items()):
        states = []
        for mode in ("normal", "active", "pressed", "disabled"):
            color = (blend(fill, "#ffffff", .12) if mode == "active" else
                     blend(fill, palette["bg"], .45) if mode == "disabled" else
                     blend(fill, "#50607a", .08) if mode == "pressed" else fill)
            asset = surface_image((round(90 * scale), round(38 * scale)), palette["panel"],
                                  color, inset=round(5 * scale), radius=round(12 * scale),
                                  pressed=mode == "pressed")
            states.append(ImageTk.PhotoImage(asset, master=root))
        images.extend(states)
        element = f"Soft{generation}_{index}.button"
        style.element_create(element, "image", states[0], ("disabled", states[3]),
            ("pressed", states[2]), ("active", states[1]), border=round(15 * scale),
            padding=0, sticky="nsew")
        style.layout(name, [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [
                ("Button.focus", {"sticky": "nsew", "children": [
                    ("Button.label", {"sticky": "nsew"})]})]})]})])
        style.configure(name, foreground=foreground, padding=(12, 9),
                        font=("Microsoft YaHei UI", 10, "bold" if "Action" in name else "normal"))
        style.map(name, foreground=[("disabled", palette["muted"])])
    tab_images = [ImageTk.PhotoImage(surface_image((round(120 * scale), round(36 * scale)),
        palette["bg"], fill, inset=round(5 * scale), radius=round(12 * scale)), master=root)
        for fill in (palette["bg"], palette["panel"])]
    images.extend(tab_images)
    tab_element = f"Soft{generation}.tab"
    style.element_create(tab_element, "image", tab_images[0], ("selected", tab_images[1]),
                         border=round(15 * scale), padding=0, sticky="nsew")
    style.layout("Workspace.TNotebook.Tab", [(tab_element, {"sticky": "nsew", "children": [
        ("Notebook.padding", {"sticky": "nsew", "children": [
            ("Notebook.label", {"sticky": "nsew"})]})]})])
    return images
