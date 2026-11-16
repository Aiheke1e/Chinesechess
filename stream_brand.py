"""Livestream identity and the stable operator name for the comment display."""
from pathlib import Path
import re

HOST_NAME = "豆包"
CHANNEL_NAME = "豆包象棋"
ACCOUNT_NAME = "豆包象棋"
AI_DISCLOSURE = "AI虚拟主播"
APP_TITLE = f"{CHANNEL_NAME} · AI直播助手"
AREA_A_TITLE = f"{CHANNEL_NAME} · 互动展示"
DEFAULT_AVATAR = Path(__file__).resolve().parent / "assets" / "doubao-chess-red-scarf-v3.png"
AVATAR_PRESETS = (
    ("水墨国风", DEFAULT_AVATAR.with_name("doubao-chess-ink-v4.png")),
    ("黏土Q版", DEFAULT_AVATAR.with_name("doubao-chess-clay-v4.png")),
    ("国潮剪纸", DEFAULT_AVATAR.with_name("doubao-chess-papercut-v4.png")),
    ("像素棋手", DEFAULT_AVATAR.with_name("doubao-chess-pixel-v4.png")),
    ("耳机主播", DEFAULT_AVATAR.with_name("doubao-chess-headset-v4.png")),
    ("红围脖手绘", DEFAULT_AVATAR),
    ("日常外套", DEFAULT_AVATAR.with_name("doubao-chess-casual-avatar-v2.png")),
    ("长发头像", DEFAULT_AVATAR.with_name("xiaoyi-douyin-avatar-v1.png")),
    ("电脑前下棋", DEFAULT_AVATAR.with_name("doubao-chess-computer.png")),
    ("初版棋手", DEFAULT_AVATAR.with_name("doubao-chess-avatar.png")),
    ("暖心3D", DEFAULT_AVATAR.with_name("doubao-chess-warm-3d-v1.png")),
    ("清新手绘", DEFAULT_AVATAR.with_name("doubao-chess-handpaint-v1.png")),
    ("软萌黏土", DEFAULT_AVATAR.with_name("doubao-chess-clay-v5.png")),
    ("国潮暖笑", DEFAULT_AVATAR.with_name("doubao-chess-guochao-v1.png")),
    ("柔和水彩", DEFAULT_AVATAR.with_name("doubao-chess-watercolor-v1.png")),
)
DEFAULT_INTRODUCTION = f"大家好，我是{HOST_NAME}，你的AI象棋搭子。棋路认真算，包袱顺嘴来，有问题直接发弹幕就好。"
LEGACY_INTRODUCTION = "大家好，我是棋小雅。这里会一边下象棋，一边和大家聊棋、聊生活，欢迎来到直播间。"
LEGACY_INTRODUCTIONS = {
    LEGACY_INTRODUCTION,
    "大家好呀，我是豆包。欢迎来到豆包象棋，咱们一边看棋一边聊，有问题直接发弹幕就好。",
    "大家好，我是小弈，你的AI象棋搭子。棋路认真算，包袱顺嘴来，有问题直接发弹幕就好。",
}


def migrate_host_identity(text):
    """Update explicit self-introductions, preserving custom style/tool references."""
    for old_channel in ("小弈象棋", "豆包象棋"):
        text = text.replace(f"{old_channel}直播间", f"{CHANNEL_NAME}直播间")
        text = text.replace(f"欢迎来到{old_channel}", f"欢迎来到{CHANNEL_NAME}")
    return re.sub(r"(你叫|我是|自称)(?:棋小雅|小弈|豆包)(?=$|[，,。.!！：:、\s])",
                  lambda match: match[1] + HOST_NAME, text)
