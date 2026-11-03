import tkinter as tk
import unittest
from unittest.mock import patch

from workspace_image import WorkspaceImage


class WorkspaceImageTests(unittest.TestCase):
    def test_photo_loads_on_display_and_fits_wide_and_tall_spaces_without_cropping(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        try:
            view = WorkspaceImage(root, background="#ffffff")
            self.assertIsNone(view._source)
            with patch.object(view, "winfo_ismapped", return_value=True):
                for width, height in ((900, 300), (400, 700)):
                    with patch.object(view, "winfo_width", return_value=width), patch.object(
                            view, "winfo_height", return_value=height):
                        view._fit_image()
                    self.assertIsNotNone(view._photo)
                    shown_w, shown_h = view._photo.width(), view._photo.height()
                    self.assertLessEqual(shown_w, width)
                    self.assertLessEqual(shown_h, height)
                    self.assertAlmostEqual(shown_w / shown_h,
                        view._source.width / view._source.height, delta=.01)
                source = view._source
                view._schedule_fit()
                view.destroy()
                self.assertIsNone(view._fit_job)
                self.assertIsNone(view._photo)
                self.assertGreater(source.width, 1100)
        finally:
            root.destroy()
