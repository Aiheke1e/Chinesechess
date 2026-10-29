"""Fast Bailian TTS adapter reused by the chess live assistant."""
from __future__ import annotations

import json
import os
import subprocess
import struct
from pathlib import Path
from uuid import uuid4

from secret_store import SECRET_DIR, unprotect


RESOURCE_ROOT = Path(r"D:\environment\shuzhiren")
CONFIG_PATH = RESOURCE_ROOT / "bailian-tts.json"
KEY_PATH = SECRET_DIR / "bailian-key.dpapi"


def load_api_key():
    return os.environ.get("DASHSCOPE_API_KEY") or unprotect(KEY_PATH)


def available_voices():
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    configured = list((data.get("voice_models") or {}).keys())
    # The existing Bailian account/config also exposes this fast male voice
    # through the root CosyVoice endpoint used by SoulX-FlashHead.
    voices = configured + ["longanyang"]
    return list(dict.fromkeys(voices))


def repair_streaming_wav_header(path):
    """Replace the 0x7fffffff streaming size markers with the downloaded size."""
    path = Path(path)
    size = path.stat().st_size
    if size < 44 or size > 0xFFFFFFFF:
        return
    with open(path, "r+b") as stream:
        header = stream.read(44)
        if (header[:4] != b"RIFF" or header[8:12] != b"WAVE" or
                header[36:40] != b"data"):
            return
        declared_riff = struct.unpack_from("<I", header, 4)[0]
        declared_data = struct.unpack_from("<I", header, 40)[0]
        if declared_riff >= size or declared_data >= size:
            stream.seek(4)
            stream.write(struct.pack("<I", size - 8))
            stream.seek(40)
            stream.write(struct.pack("<I", size - 44))


def _request(endpoint, payload, timeout=45):
    config = "\n".join([
        "header = " + json.dumps("Authorization: Bearer " + load_api_key()),
        'header = "Content-Type: application/json"',
        "data = " + json.dumps(json.dumps(payload, ensure_ascii=False), ensure_ascii=False),
    ])
    result = subprocess.run(
        ["curl.exe", "--disable", "--config", "-", "--silent",
         "--show-error", "--connect-timeout", "8", "--max-time", str(timeout),
         "-w", "\n%{http_code}", endpoint], input=config.encode("utf-8"),
        capture_output=True, timeout=timeout + 5,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("百炼语音连接失败或超时")
    body, _, status = result.stdout.rpartition(b"\n")
    try:
        data = json.loads(body)
    except ValueError:
        raise RuntimeError("百炼返回了无法识别的响应") from None
    if not status.startswith(b"2"):
        error = data.get("error") or data
        code = str(error.get("code") or error.get("type") or status.decode())[:80]
        raise RuntimeError("百炼语音调用失败：" + code)
    return data


def synthesize(text, voice="Maia"):
    text = str(text).strip()[:600]
    if not text:
        raise ValueError("没有需要朗读的内容")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    voice_config = (config.get("voice_models") or {}).get(voice)
    if voice == "longanyang" and not voice_config:
        voice_config = config
    if not voice_config:
        raise ValueError("未配置音色：" + voice)
    payload = {"model": voice_config["model"], "input": {
        "text": text, "voice": voice, "format": "wav", "sample_rate": 24000}}
    if voice == "Maia":
        payload["input"] = {"text": text, "voice": voice,
                            "language_type": "Chinese"}
    data = _request(voice_config["endpoint"], payload)
    audio_url = data.get("output", {}).get("audio", {}).get("url")
    if not audio_url:
        raise RuntimeError("百炼没有返回音频地址")
    folder = RESOURCE_ROOT / "audio" / "chess-live"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"speech_{uuid4().hex}.wav"
    downloaded = subprocess.run(
        ["curl.exe", "--disable", "--fail", "--silent", "--show-error",
         "--location", "--connect-timeout", "8", "--max-time", "45",
         "--output", str(target), audio_url], capture_output=True, timeout=50,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if downloaded.returncode:
        target.unlink(missing_ok=True)
        raise RuntimeError("语音生成成功，但下载失败")
    repair_streaming_wav_header(target)
    return str(target)
