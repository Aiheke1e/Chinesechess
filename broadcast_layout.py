"""Initial capture-window placement; no input or game-window mutations."""
import ctypes
from ctypes import wintypes
import os


def chess_window_bounds():
    """Read the currently visible emulator; keep both viewport and outer bounds."""
    if os.name != "nt":
        return None
    try:
        api = ctypes.windll.user32
        api.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        api.FindWindowW.restype = wintypes.HWND
        api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        api.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        api.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        api.IsIconic.argtypes = [wintypes.HWND]
        api.IsWindowVisible.argtypes = [wintypes.HWND]
        handle = api.FindWindowW(None, "雷电模拟器")
        if not handle or api.IsIconic(handle) or not api.IsWindowVisible(handle):
            return None
        rect = wintypes.RECT()
        if not api.GetWindowRect(handle, ctypes.byref(rect)):
            return None
        outer = (rect.left, rect.top, rect.right, rect.bottom)
        views = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def visit(child, unused):
            area, point = wintypes.RECT(), wintypes.POINT()
            if api.IsWindowVisible(child) and api.GetClientRect(child, ctypes.byref(area)):
                width, height = area.right, area.bottom
                if width >= 200 and height >= 300 and abs(width / height - 9 / 16) < .025:
                    if api.ClientToScreen(child, ctypes.byref(point)):
                        views.append((point.x, point.y, point.x + width, point.y + height))
            return True

        api.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
        api.EnumChildWindows(handle, visit, 0)
        viewport = max(views, key=lambda box: (box[2] - box[0]) * (box[3] - box[1]), default=outer)
        return {"outer": outer, "viewport": viewport}
    except (AttributeError, OSError, ValueError):
        return None


def popup_position(size, screen, game=None, margin=12):
    """Prefer a free area outside the whole game, including its avatar row."""
    width, height = size
    left, top, right, bottom = screen
    if game:
        x1, y1, x2, y2 = game
        aligned_x = max(left, min(x1, right - width))
        aligned_y = max(top, min(y1, bottom - height))
        candidates = [(aligned_x, y1 - height - margin), (aligned_x, y2 + margin),
                      (x2 + margin, aligned_y), (x1 - width - margin, aligned_y)]
        for x, y in candidates:
            if left <= x and top <= y and x + width <= right and y + height <= bottom:
                return x, y
    # A portrait game normally leaves free space to either side. If the game
    # fills the monitor, prefer the bottom rather than its opponent-avatar row.
    return max(left, right - width - margin), max(top, bottom - height - margin)
