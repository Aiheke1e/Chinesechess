"""Fast, non-network preflight checks for a chess livestream."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import re

from adb_bridge import current_package, list_devices, resolution
from bailian_tts import (CONFIG_PATH as TTS_CONFIG, available_voices,
                         load_api_key as load_tts_key)
from glm_client import load_api_key as load_llm_key
from pikafish import DEFAULT_EXE


@dataclass(frozen=True)
class HealthItem:
    key: str
    label: str
    level: str
    detail: str


def _item(key, label, level, detail):
    return asdict(HealthItem(key, label, level, detail))


def collect_readiness(settings, *, device_provider=list_devices,
                      package_provider=current_package,
                      resolution_provider=resolution,
                      engine_path=DEFAULT_EXE,
                      tts_config_path=TTS_CONFIG,
                      llm_key_loader=load_llm_key,
                      tts_key_loader=load_tts_key,
                      voice_loader=available_voices):
    """Collect local readiness without sending model or TTS requests."""
    items = []
    devices = []
    try:
        devices = device_provider()
    except Exception as error:
        items.append(_item("device", "模拟器", "error", str(error)[:80]))
    if devices:
        device = devices[0]
        try:
            size = resolution_provider(device)
            items.append(_item("device", "模拟器", "ok",
                               f"{device} · {size or '分辨率未知'}"))
        except Exception:
            items.append(_item("device", "模拟器", "ok", device))
        try:
            package, _ = package_provider(device)
            level = "ok" if package == "cn.jj.chess" else "warn"
            detail = "JJ 象棋在前台" if level == "ok" else "JJ 象棋未在前台"
            items.append(_item("jj", "JJ 象棋", level, detail))
        except Exception as error:
            items.append(_item("jj", "JJ 象棋", "warn", str(error)[:80]))
    elif not any(item["key"] == "device" for item in items):
        items.append(_item("device", "模拟器", "error", "未找到 ADB 设备"))

    items.append(_item(
        "engine", "Pikafish", "ok" if os.path.isfile(engine_path) else "error",
        "引擎文件已就绪" if os.path.isfile(engine_path) else "缺少引擎文件"))

    commentary = bool(settings.get("commentary_enabled"))
    try:
        key_ready = bool(llm_key_loader())
    except Exception:
        key_ready = False
    items.append(_item(
        "llm", "AI 解说", "ok" if commentary and key_ready else "warn",
        "智谱密钥与解说开关正常" if commentary and key_ready else
        ("解说开关未开启" if not commentary else "未找到智谱密钥")))

    speech = bool(settings.get("speech_enabled"))
    try:
        tts_ready = (os.path.isfile(tts_config_path) and bool(tts_key_loader()) and
                     settings.get("voice") in voice_loader())
    except (OSError, ValueError, json.JSONDecodeError):
        tts_ready = False
    items.append(_item(
        "tts", "开播语音", "ok" if speech and tts_ready else "warn",
        f"音色 {settings.get('voice')} 已就绪" if speech and tts_ready else
        ("语音开关未开启" if not speech else "百炼配置、密钥或音色不可用")))

    room = str(settings.get("room") or "").strip()
    room_ok = bool(re.fullmatch(r"\d{6,25}", room) or
                   re.fullmatch(r"https://live\.douyin\.com/\d{6,25}/?", room))
    items.append(_item(
        "room", "直播间", "ok" if room_ok else "warn",
        "直播间地址已配置" if room_ok else "尚未配置抖音直播间"))
    return items


def readiness_level(items):
    levels = {item.get("level") for item in items}
    if "error" in levels:
        return "error"
    if "warn" in levels:
        return "warn"
    return "ok"
