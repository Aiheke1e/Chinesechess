"""Livestream identity and the stable operator name for the comment display."""
from pathlib import Path
import re

HOST_NAME = "小弈"
CHANNEL_NAME = "小弈象棋"
ACCOUNT_NAME = "小弈｜AI象棋"
AI_DISCLOSURE = "AI虚拟主播"
APP_TITLE = f"{CHANNEL_NAME} · AI直播助手"
AREA_A_TITLE = f"{CHANNEL_NAME} · A区"
DEFAULT_AVATAR = Path(__file__).resolve().parent / "assets" / "xiaoyi-douyin-avatar-v1.png"
DEFAULT_INTRODUCTION = f"大家好，我是{HOST_NAME}，你的AI象棋搭子。棋路认真算，包袱顺嘴来，有问题直接发弹幕就好。"
LEGACY_INTRODUCTION = "大家好，我是棋小雅。这里会一边下象棋，一边和大家聊棋、聊生活，欢迎来到直播间。"
LEGACY_INTRODUCTIONS = {
    LEGACY_INTRODUCTION,
    "大家好呀，我是豆包。欢迎来到豆包象棋，咱们一边看棋一边聊，有问题直接发弹幕就好。",
}


def migrate_host_identity(text):
    """Update explicit self-introductions, preserving custom style/tool references."""
    text = text.replace("豆包象棋直播间", f"{CHANNEL_NAME}直播间")
    text = text.replace("欢迎来到豆包象棋", f"欢迎来到{CHANNEL_NAME}")
    return re.sub(r"(你叫|我是|自称)(?:棋小雅|豆包)(?=$|[，,。.!！：:、\s])",
                  lambda match: match[1] + HOST_NAME, text)
