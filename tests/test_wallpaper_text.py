import tkinter as tk
from tkinter import ttk
import unittest
from PIL import Image, ImageTk

from app_wallpaper import AppWallpaper
from wallpaper_text import WallpaperText


class WallpaperTextTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.geometry("480x300")
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.wallpaper = AppWallpaper(self.root, base_color="white", transparency=0)
        self.wallpaper._source = Image.new("RGB", (480, 300), "#4080a0")
        self.text = WallpaperText(self.root, wallpaper=self.wallpaper, font=("Arial", 12),
                                  padx=14, pady=10, bd=0, highlightthickness=0, wrap="word")
        self.text.pack(fill="both", expand=True, padx=12, pady=8)

    def tearDown(self):
        self.root.destroy()
        self.assertFalse(self.errors)

    def render(self):
        self.root.update()
        self.root.update_idletasks()
        self.wallpaper._render()
        self.text._draw()

    def test_native_editing_tags_selection_and_scrolling_survive_the_image_layer(self):
        self.text.tag_configure("speaker", foreground="green", font=("Arial", 12, "bold"))
        self.text.insert("end", "viewer: ", "speaker")
        self.text.insert("end", "hello\n" + "a wrapping log line " * 15 + "\n" * 40 + "latest entry")
        self.text.tag_add("sel", "1.0", "1.6")
        self.render()
        canvas = self.text._display
        self.assertEqual(self.text.get("sel.first", "sel.last"), "viewer")
        visible_text = [canvas.itemcget(item, "text") for item in canvas.find_withtag("text_content")
                        if canvas.type(item) == "text"]
        self.assertTrue(any("viewer" in value for value in visible_text))
        self.text.see("end")
        self.render()
        self.assertGreater(self.text.yview()[0], 0)
        visible_text = [canvas.itemcget(item, "text") for item in canvas.find_withtag("text_content")
                        if canvas.type(item) == "text"]
        self.assertIn("latest entry", visible_text)
        self.text.configure(state="disabled")
        self.assertIn("latest entry", self.text.get("1.0", "end"))

    def test_background_covers_text_padding_and_updates_without_fading_text(self):
        self.text.insert("end", "opaque text")
        self.render()
        canvas = self.text._display
        self.assertEqual((canvas.winfo_rootx(), canvas.winfo_rooty()),
                         (self.text.winfo_rootx(), self.text.winfo_rooty()))
        self.assertEqual((canvas.winfo_width(), canvas.winfo_height()),
                         (self.text.winfo_width(), self.text.winfo_height()))
        before = self.text.cget("fg")
        self.wallpaper.set_transparency(100)
        self.render()
        patch = ImageTk.getimage(self.wallpaper._surfaces[canvas]["photo"])
        self.assertEqual(patch.getpixel((0, 0))[:3], (255, 255, 255))
        self.assertEqual(self.text.cget("fg"), before)
        self.assertEqual(float(self.root.attributes("-alpha")), 1)

    def test_labels_and_switches_keep_native_values_and_theme_colors(self):
        self.text.configure(height=4)
        self.text.pack_configure(fill="x", expand=False)
        name = tk.StringVar(self.root, value="viewer one")
        label = tk.Label(self.root, textvariable=name, fg="purple")
        label.pack(side="bottom")
        enabled = tk.BooleanVar(self.root, value=False)
        switch = ttk.Checkbutton(self.root, text="Speech", variable=enabled)
        switch.pack(side="bottom")
        self.wallpaper.cover_widgets(self.root)
        self.render()
        name.set("viewer two")
        switch.invoke()
        for adapter in self.wallpaper._labels:
            adapter._draw()
            items = adapter.canvas.find_withtag("text_content")
            text_items = [item for item in items if adapter.canvas.type(item) == "text"]
            if adapter.label is label:
                self.assertEqual(adapter.canvas.itemcget(text_items[0], "text"), "viewer two")
                self.assertEqual(adapter.canvas.itemcget(text_items[0], "fill"), "purple")
                x, y = adapter.canvas.coords(text_items[0])
                self.assertAlmostEqual(x, label.winfo_width() / 2)
                self.assertAlmostEqual(y, label.winfo_height() / 2)
            elif adapter.label is switch:
                self.assertTrue(enabled.get())
                self.assertEqual(adapter.canvas.itemcget(items[0], "fill"), "#269766")
