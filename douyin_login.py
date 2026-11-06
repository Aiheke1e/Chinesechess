"""Interactive official-site login in an isolated local browser profile."""
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from douyin_auth import douyin_cookie_header, has_login, save_cookie


HERE = Path(__file__).resolve().parent


def browser_executable():
    for base, relative in ((os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Microsoft/Edge/Application/msedge.exe"),
                           (os.environ.get("ProgramFiles", r"C:\Program Files"), "Microsoft/Edge/Application/msedge.exe"),
                           (os.environ.get("ProgramFiles", r"C:\Program Files"), "Google/Chrome/Application/chrome.exe")):
        candidate = Path(base) / relative
        if candidate.is_file():
            return candidate
    raise RuntimeError("未找到 Edge 或 Chrome，无法打开抖音登录窗口")


def cdp_call(socket, method, request_id):
    socket.send(json.dumps({"id": request_id, "method": method}))
    while True:
        response = json.loads(socket.recv())
        if response.get("id") == request_id:
            if response.get("error"):
                raise RuntimeError("无法读取专用登录窗口的状态")
            return response.get("result") or {}


def login(timeout=300, notify=lambda text: None):
    import websocket
    browser = browser_executable()
    folder = HERE / "work" / "douyin_login"
    folder.mkdir(parents=True, exist_ok=True)
    # A separate profile never opens, reads or closes the user's normal tabs.
    profile = Path(tempfile.mkdtemp(prefix="profile-", dir=folder))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    process = subprocess.Popen([str(browser), f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
        f"--remote-allow-origins={origin}", "--no-first-run", "--no-default-browser-check",
        "--new-window", "https://live.douyin.com/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    opener = build_opener(ProxyHandler({}))
    channel = None
    notify("请在新打开的抖音官方页面完成登录，完成后会自动保存并关闭专用窗口")
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False
            try:
                if channel is None:
                    with opener.open(origin + "/json/version", timeout=2) as response:
                        endpoint = json.load(response).get("webSocketDebuggerUrl", "")
                    target = urlsplit(endpoint)
                    if target.scheme != "ws" or target.hostname != "127.0.0.1" or target.port != port:
                        raise RuntimeError("专用登录窗口地址无效")
                    channel = websocket.create_connection(endpoint, timeout=3, origin=origin,
                                                          http_no_proxy=["127.0.0.1"])
                cookies = cdp_call(channel, "Storage.getCookies", 1).get("cookies") or []
                header = douyin_cookie_header(cookies)
                if has_login(header):
                    save_cookie(header)
                    return True
            except (OSError, ValueError, websocket.WebSocketException):
                if channel is not None:
                    channel.close()
                    channel = None
            time.sleep(.5)
        return False
    finally:
        if channel is not None:
            try:
                channel.send(json.dumps({"id": 2, "method": "Browser.close"}))
            except (OSError, websocket.WebSocketException):
                pass
            channel.close()
        if process.poll() is None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        resolved_root, resolved_profile = folder.resolve(), profile.resolve()
        if (resolved_profile != resolved_root and resolved_profile.is_relative_to(resolved_root)
                and resolved_profile.name.startswith("profile-")):
            shutil.rmtree(resolved_profile, ignore_errors=True)


def main():
    def emit(state, text):
        print("CHESS_DOUYIN_LOGIN " + json.dumps({"state": state, "text": text}, ensure_ascii=False), flush=True)
    try:
        ok = login(notify=lambda text: emit("waiting", text))
        emit("saved" if ok else "cancelled", "抖音登录信息已保存" if ok else "抖音登录已取消或超时")
    except Exception:
        emit("error", "抖音登录未完成，请重新打开登录窗口重试")


if __name__ == "__main__":
    main()
