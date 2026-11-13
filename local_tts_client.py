"""Loopback-only client for the separate Qwen GPU environment."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.error import URLError, HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4
import wave

from tts_requests import check_synthesis_valid

LOCAL_ROOT = Path(os.environ.get("CHESS_LOCAL_TTS_ROOT", r"D:\AI\Qwen3-TTS-Local"))
SERVICE_URL = "http://127.0.0.1:7863"
AUDIO_DIR = Path(__file__).resolve().parent / "work/local_tts_audio"
_start_lock = threading.Lock()
_opener = build_opener(ProxyHandler({}))


def _json(path, data=None, *, timeout=3):
    request = Request(SERVICE_URL + path, data=None if data is None else
                      json.dumps(data,ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type":"application/json"})
    with _opener.open(request,timeout=timeout) as response:
        return json.load(response)


def _service_state():
    try:
        data = _json("/gradio_api/info")
        if "/chess_speech" not in data.get("named_endpoints",{}):
            raise RuntimeError("本地语音服务版本较旧，请关闭本地语音窗口后重新启动象棋助手")
        return True
    except (URLError, TimeoutError, OSError):
        return False


def local_readiness():
    if not (LOCAL_ROOT / ".venv/Scripts/python.exe").is_file():
        return False, "未找到本地语音环境"
    if not (LOCAL_ROOT / "models/Qwen3-TTS-12Hz-0.6B-Base/model.safetensors").is_file():
        return False, "本地语音模型缺失"
    try:
        folder = LOCAL_ROOT / "voices/presets"
        preset = json.loads((folder / "doubao.json").read_text(encoding="utf-8"))
        audio = (folder / preset["file"]).resolve()
        if not audio.is_relative_to(folder.resolve()) or not audio.is_file() or not preset.get("text"):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        return False, "豆包参考声音尚未配置"
    return True, "豆包本地模型与参考声音已就绪"


def ensure_service(*, timeout=150):
    """Reuse a live model, or start it once. Never start chess or broadcasting."""
    check_synthesis_valid()
    if _service_state():
        return
    with _start_lock:
        if _service_state():
            return
        python = LOCAL_ROOT / ".venv/Scripts/python.exe"
        app = LOCAL_ROOT / "app.py"
        if not python.is_file() or not app.is_file():
            raise RuntimeError("本地语音环境不存在，请检查 D:\\AI\\Qwen3-TTS-Local")
        logs = LOCAL_ROOT / "logs"
        logs.mkdir(exist_ok=True)
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        environment["QWEN_TTS_BACKEND"] = "fast"
        with (logs / "chess-service.log").open("ab") as output:
            process = subprocess.Popen([str(python),"-X","utf8","-u",str(app),"--no-browser"],
                cwd=LOCAL_ROOT,env=environment,stdout=output,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            check_synthesis_valid()
            if _service_state():
                return
            if process.poll() not in (None, 0):
                raise RuntimeError("本地语音服务启动失败，请查看本地语音目录 logs/chess-service.log")
            time.sleep(.35)
        raise RuntimeError("本地语音模型加载超时，请检查显存后重试")


def _split_text(text, limit=180):
    remaining = str(text or "").strip()
    if not remaining or len(remaining) > 600:
        raise ValueError("本地播报文案需为1～600个字符")
    chunks = []
    while remaining:
        length = min(len(remaining),limit)
        if len(remaining) > limit:
            boundaries = list(re.finditer(r"[。！？；，、,.!?;\n]",remaining[:limit]))
            if boundaries:
                length = boundaries[-1].end()
        chunks.append(remaining[:length])
        remaining = remaining[length:]
    return chunks


def _generate(text, voice_id="doubao"):
    check_synthesis_valid()
    result = _json("/gradio_api/call/chess_speech", {"data":[text,voice_id]}, timeout=8)
    event_id = result.get("event_id", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", event_id):
        raise RuntimeError("本地语音服务返回的任务编号无效")
    event, payload = "", None
    deadline = time.monotonic() + 100
    with _opener.open(SERVICE_URL + "/gradio_api/call/chess_speech/" + event_id,timeout=100) as response:
        for raw in response:
            check_synthesis_valid()
            if time.monotonic() > deadline:
                raise RuntimeError("本地语音合成超时")
            line = raw.decode("utf-8").strip()
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event in ("complete", "error"):
                value = json.loads(line[6:])
                if event == "error":
                    raise RuntimeError("本地语音合成失败，请确认豆包参考声音已配置，并查看本地语音页面")
                payload = value
                break
    if not payload or len(payload) != 2 or payload[1].get("voice_id") != voice_id or payload[1].get("backend") != "local":
        raise RuntimeError("本地语音服务没有返回所选音色")
    url = payload[0].get("url", "")
    parsed, expected = urlsplit(url), urlsplit(SERVICE_URL)
    if (parsed.scheme,parsed.hostname,parsed.port) != (expected.scheme,expected.hostname,expected.port):
        raise RuntimeError("本地语音返回了非本机音频地址")
    check_synthesis_valid()
    with _opener.open(url,timeout=15) as response:
        audio = response.read(20*1024*1024 + 1)
    if len(audio) > 20*1024*1024:
        raise RuntimeError("本地音频超过大小限制")
    return audio


def synthesize(text, *, voice_id="doubao"):
    chunks = _split_text(text)
    ensure_service()
    parts, params = [], None
    for chunk in chunks:
        audio = _generate(chunk) if voice_id == "doubao" else _generate(chunk, voice_id)
        with wave.open(io.BytesIO(audio),"rb") as stream:
            current = (stream.getnchannels(),stream.getsampwidth(),stream.getframerate())
            if current[0] != 1 or current[1] != 2 or stream.getcomptype() != "NONE" or stream.getnframes() == 0:
                raise RuntimeError("本地语音未生成有效的PCM音频")
            if params is not None and current != params:
                raise RuntimeError("本地分段音频格式不一致")
            params = current
            parts.append(stream.readframes(stream.getnframes()))
        check_synthesis_valid()
    AUDIO_DIR.mkdir(parents=True,exist_ok=True)
    target = AUDIO_DIR / ("speech_" + uuid4().hex + ".wav")
    with wave.open(str(target),"wb") as output:
        output.setnchannels(params[0]);output.setsampwidth(params[1]);output.setframerate(params[2])
        for part in parts:
            output.writeframes(part)
    return str(target)
