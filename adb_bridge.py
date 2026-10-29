# adb_bridge.py —— 通过 ADB 与安卓模拟器交互 (截图 + 输入)
# 安卓版 JJ 象棋与 Windows 版共用同一套识别(board.py) + AI(engine.py)
# 区别只在"眼睛"(adb screencap) 和 "手"(adb input) 走 ADB 通道
import os
import subprocess
import time
import tempfile

ADB = os.environ.get("ADB", r"D:\environment\Android\Sdk\platform-tools\adb.exe")
NO_CONSOLE = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def adb(args, timeout=30, device=None):
    cmd = [ADB]
    if device:
        cmd += ["-s", device]
    cmd += args
    return subprocess.run(cmd, capture_output=True, timeout=timeout,
                          creationflags=NO_CONSOLE)


def list_devices():
    r = adb(["devices"])
    lines = r.stdout.decode("utf-8", "ignore").splitlines()[1:]
    devices = []
    for line in lines:
        parts = line.strip().split("\t")
        if len(parts) >= 2 and parts[1].strip() == "device":
            devices.append(parts[0])
    return devices


def wait_device(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        devs = list_devices()
        if devs:
            return devs[0]
        time.sleep(2)
    return None


def install_apk(path, device=None):
    print(f"[adb] 安装 {path}")
    r = adb(["install", "-r", path], timeout=120, device=device)
    out = r.stdout.decode("utf-8", "ignore") + r.stderr.decode("utf-8", "ignore")
    print(out)
    return "Success" in out


def launch(package, activity, device=None):
    adb(["shell", "am", "start", "-n", f"{package}/{activity}"], device=device)


def screenshot(device=None, out_path=None):
    """截图并返回 numpy 数组(BGR)。用 exec-out 直出 png 避免临时文件。"""
    import cv2
    import numpy as np
    r = adb(["exec-out", "screencap", "-p"], timeout=20, device=device)
    if r.returncode != 0:
        return None
    raw = r.stdout
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if out_path and img is not None:
        cv2.imwrite(out_path, img)
    return img


def tap(x, y, device=None):
    adb(["shell", "input", "tap", str(int(x)), str(int(y))], device=device)


def swipe(fx, fy, tx, ty, duration_ms=120, device=None):
    """模拟人类拖拽: 起点按下, 移动, 松开。雷电/安卓接受 input swipe。"""
    adb(["shell", "input", "swipe", str(int(fx)), str(int(fy)),
         str(int(tx)), str(int(ty)), str(int(duration_ms))], device=device)


def current_package(device=None):
    # Avoid relying on a device shell pipe/grep implementation.
    r = adb(["shell", "dumpsys", "window", "windows"], timeout=20, device=device)
    out = r.stdout.decode("utf-8", "ignore")
    # 形如 mCurrentFocus=Window{... u0 com.tencent.../Activity}
    import re
    focus = next((line for line in out.splitlines() if "mCurrentFocus" in line), out)
    m = re.search(r'([\w.]+)/([\w.$]+)', focus)
    return (m.group(1), m.group(2)) if m else (None, None)


def resolution(device=None):
    """返回 (w,h) 像素"""
    r = adb(["shell", "wm", "size"], device=device)
    out = r.stdout.decode("utf-8", "ignore")
    import re
    m = re.search(r'(\d+)x(\d+)', out)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


if __name__ == "__main__":
    d = wait_device(10)
    print("device:", d)
    if d:
        print("res:", resolution(d))
        img = screenshot(d)
        print("shot shape:", None if img is None else img.shape)
