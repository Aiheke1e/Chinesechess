"""Borderless capture windows with invisible drag/resize hit areas."""
import ctypes
from ctypes import wintypes
import os

from soft_ui import ui_scale


def resize_bounds(rect, edge, dx, dy, minimum, maximum):
    """Keep the opposite edge anchored when a resize reaches its limits."""
    x, y, width, height = rect
    old_width, old_height = width, height
    if not edge:
        return x + dx, y + dy, width, height
    if "w" in edge or "e" in edge:
        width = max(minimum[0], min(maximum[0], width + (-dx if "w" in edge else dx)))
        if "w" in edge:
            x += old_width - width
    if "n" in edge or "s" in edge:
        height = max(minimum[1], min(maximum[1], height + (-dy if "n" in edge else dy)))
        if "n" in edge:
            y += old_height - height
    return x, y, width, height


def _capture_window_style(window):
    """Keep the captionless Tk window a normal Windows capture target."""
    if os.name != "nt":
        return
    api = ctypes.windll.user32
    api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    api.GetAncestor.restype = wintypes.HWND
    api.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    api.GetWindowLongW.restype = wintypes.LONG
    api.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    api.SetWindowLongW.restype = wintypes.LONG
    api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    handle = api.GetAncestor(window.winfo_id(), 2)  # GA_ROOT, Tk's outer frame
    style = api.GetWindowLongW(handle, -20)  # GWL_EXSTYLE
    wanted = (style & ~0x80) | 0x40000  # Remove TOOLWINDOW, add APPWINDOW.
    if wanted != style:
        api.SetWindowLongW(handle, -20, wanted)
        # No move, resize, stacking change or activation; refresh the frame.
        api.SetWindowPos(handle, None, 0, 0, 0, 0, 0x37)


class BorderlessControls:
    def __init__(self, window, content):
        self.window = window
        self._start = None
        self._tag = f"CaptureDrag_{id(self)}"
        self._bindings = {
            "<Motion>": self._motion, "<ButtonPress-1>": self._press,
            "<B1-Motion>": self._drag, "<ButtonRelease-1>": self._release,
        }
        for sequence, callback in self._bindings.items():
            window.bind_class(self._tag, sequence, callback)
        pending = [window, content]
        visited = set()
        while pending:
            widget = pending.pop()
            if widget in visited:
                continue
            visited.add(widget)
            widget.bindtags((self._tag, *widget.bindtags()))
            pending.extend(widget.winfo_children())
        window.bind("<Map>", self._mapped, add="+")
        window.bind("<Destroy>", self._cleanup, add="+")

    def _mapped(self, event):
        if event.widget is self.window:
            _capture_window_style(self.window)

    def _edge(self, event):
        window = self.window
        x, y = event.x_root - window.winfo_rootx(), event.y_root - window.winfo_rooty()
        margin = round(7 * ui_scale(window))
        horizontal = "w" if x < margin else "e" if x >= window.winfo_width() - margin else ""
        vertical = "n" if y < margin else "s" if y >= window.winfo_height() - margin else ""
        return vertical + horizontal

    def _motion(self, event):
        if self._start is not None:
            return self._drag(event)
        edge = self._edge(event)
        cursor = ("size_nw_se" if edge in ("nw", "se") else
                  "size_ne_sw" if edge in ("ne", "sw") else
                  "sb_h_double_arrow" if edge in ("w", "e") else
                  "sb_v_double_arrow" if edge in ("n", "s") else "fleur")
        event.widget.configure(cursor=cursor)

    def _press(self, event):
        window = self.window
        self._start = (event.x_root, event.y_root,
            (window.winfo_x(), window.winfo_y(), window.winfo_width(), window.winfo_height()), self._edge(event))
        window.grab_set()
        return "break"

    def _drag(self, event):
        if self._start is None:
            return
        mouse_x, mouse_y, rect, edge = self._start
        window = self.window
        screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
        max_w, max_h = window.maxsize()
        max_w = min(max_w, rect[0] + rect[2] if "w" in edge else screen_w - rect[0])
        max_h = min(max_h, rect[1] + rect[3] if "n" in edge else screen_h - rect[1])
        x, y, width, height = resize_bounds(rect, edge, event.x_root - mouse_x,
            event.y_root - mouse_y, window.minsize(), (max_w, max_h))
        if not edge:
            x, y = max(0, min(x, screen_w - width)), max(0, min(y, screen_h - height))
        window.geometry(f"{width}x{height}{x:+d}{y:+d}")
        return "break"

    def _release(self, event):
        if self._start is not None:
            self._drag(event)
        self._start = None
        if self.window.grab_current() is self.window:
            self.window.grab_release()
        return "break"

    def _cleanup(self, event):
        if event.widget is self.window:
            for sequence in self._bindings:
                self.window.unbind_class(self._tag, sequence)
            self._bindings.clear()
            self._start = None
            self.window = None
