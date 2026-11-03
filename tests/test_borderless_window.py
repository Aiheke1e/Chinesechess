import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from borderless_window import BorderlessControls, resize_bounds


class BorderlessGeometryTests(unittest.TestCase):
    def test_drag_moves_without_resizing(self):
        self.assertEqual(resize_bounds((100, 80, 600, 160), "", 45, -20,
            (260, 110), (1600, 600)), (145, 60, 600, 160))

    def test_all_eight_resize_directions_keep_opposite_edges_anchored(self):
        expected = {
            "n": (100, 100, 600, 140), "s": (100, 80, 600, 180),
            "w": (130, 80, 570, 160), "e": (100, 80, 630, 160),
            "nw": (130, 100, 570, 140), "ne": (100, 100, 630, 140),
            "sw": (130, 80, 570, 180), "se": (100, 80, 630, 180),
        }
        for edge, result in expected.items():
            with self.subTest(edge=edge):
                self.assertEqual(resize_bounds((100, 80, 600, 160), edge, 30, 20,
                    (260, 110), (1600, 600)), result)

    def test_limits_do_not_shift_the_opposite_edge(self):
        self.assertEqual(resize_bounds((100, 80, 600, 160), "nw", 2000, 2000,
            (260, 110), (1600, 600)), (440, 130, 260, 110))
        self.assertEqual(resize_bounds((100, 80, 600, 160), "nw", -2000, -2000,
            (260, 110), (700, 240)), (0, 0, 700, 240))

    def test_corner_release_applies_final_size_when_canvas_motion_was_not_delivered(self):
        window = Mock()
        for name, value in {"winfo_rootx":100,"winfo_rooty":80,"winfo_x":100,"winfo_y":80,
                "winfo_width":600,"winfo_height":160,"winfo_screenwidth":1920,
                "winfo_screenheight":1080,"winfo_fpixels":96,
                "minsize":(260,110),"maxsize":(1600,600)}.items():
            getattr(window, name).return_value = value
        window.grab_current.return_value = window
        controls = object.__new__(BorderlessControls)
        controls.window = window
        controls._press(SimpleNamespace(x_root=698, y_root=238))
        controls._release(SimpleNamespace(x_root=620, y_root=210))
        self.assertEqual(window.geometry.call_args.args[0], "522x132+100+80")
        self.assertIsNone(controls._start)
