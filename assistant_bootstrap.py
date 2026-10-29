from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime


HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
LOG_PATH = os.path.join(WORK, "assistant.log")
LOCK_PATH = os.path.join(WORK, "assistant.lock")


def write_log(message):
    os.makedirs(WORK, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as stream:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        stream.write(f"[{stamp}] {message}\n")


def show_message(title, message, error=False):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)(title, message)
        root.destroy()
    except Exception:
        pass


def acquire_single_instance():
    os.makedirs(WORK, exist_ok=True)
    lock = open(LOCK_PATH, "a+", encoding="utf-8")
    if os.name != "nt":
        return lock
    import msvcrt
    lock.seek(0)
    if not lock.read(1):
        lock.seek(0)
        lock.write("1")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return None
    return lock


def main():
    os.chdir(HERE)
    write_log("桌面助手启动")
    lock = acquire_single_instance()
    if lock is None:
        write_log("检测到已有助手实例，取消重复启动")
        show_message("JJ 象棋助手", "助手已经在运行，请查看任务栏中的控制面板。")
        return 0
    try:
        sys.argv = [os.path.join(HERE, "control_panel.py"), "--autostart"]
        import control_panel
        control_panel.main()
        write_log("控制面板正常退出")
        return 0
    except Exception:
        details = traceback.format_exc()
        write_log("未处理异常：\n" + details)
        show_message("JJ 象棋助手启动失败",
                     "程序发生异常，详细信息已写入：\n" + LOG_PATH,
                     error=True)
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
