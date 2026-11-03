"""Desktop display setup, called before Tk creates any windows."""
import ctypes
import os


def enable_high_dpi():
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        try:
            set_context = user32.SetProcessDpiAwarenessContext
        except AttributeError:
            return bool(user32.SetProcessDPIAware())
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_int
        # System-aware suits Tk's fixed point-font scaling; do not change it
        # after a window exists or override a host's existing awareness mode.
        return bool(set_context(ctypes.c_void_p(-2)))
    except (AttributeError, OSError):
        return False
