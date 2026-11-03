"""Short audience lines without invented nicknames or stage directions."""
import re


def viewer_address(user):
    """Use only the nickname characters supplied by the room, never unmask it."""
    name = str(user or "").strip()
    masked = "*" in name
    if masked:
        name = name.split("*", 1)[0]
    name = re.sub(r"[^\w\u4e00-\u9fff]", "", name).strip("_")[:24]
    if not name or name in ("观众", "匿名用户", "用户"):
        return ""
    return name + "棋友" if masked and not name.endswith("棋友") else name


def welcome_line(user):
    return f"欢迎{viewer_address(user) or '新朋友'}进入直播间。"


def reply_hint(comment):
    if re.search(r"怎么领|哪里.*领|哪.*领取|领取|棋谱.*[哪领]|[哪领].*棋谱", comment):
        return "没有提供领取入口、活动或商品信息。不能编造链接、福利、赠品或要求送礼；不清楚领什么就先问清楚。"
    if re.search(r"气出病|气死|看得.*急|太菜|棋.*[臭烂]|下[得的].*[臭烂]", comment):
        return "观众在轻松吐槽或批评，不是夸奖。接住吐槽，不说谢谢夸奖、棋艺很好。"
    if re.search(r"好听|声音.*舒服|歌.*不错", comment):
        return "观众在评价听感，不是在评价走棋。简短接话，不假装自己听过或知道是哪首歌。"
    return "只按这条弹幕的原意回答，不借用其他观众的话。"


def spoken_reply(text, limit=55, payload=None):
    payload = payload or {}
    hint = reply_hint(str(payload.get("弹幕") or ""))
    if "领取入口" in hint:
        text = ("棋谱的领取入口，我这边还没有信息，以直播间公布的说明为准。"
                if "棋谱" in str(payload.get("弹幕") or "") else "你想领什么？说具体一点。")
    elif re.search(r"(?:送|刷|打赏|付).{0,10}(?:礼物|钱).{0,15}(?:就|才|告诉|可以)|(?:先|需要).{0,6}(?:送礼|刷礼物|打赏|付费)", str(text)):
        text = "直接把问题说清楚就好，不用送礼。"
    elif "吐槽或批评" in hint and re.search(r"夸|棋艺.*好|厉害|感染.*魅力", str(text)):
        text = "看得你着急了，咱们少点惊险，多点稳当。"
    elif "评价听感" in hint and re.search(r"棋招|这招棋|棋下得|我也.*听|这首歌.*(?:不错|好听)", str(text)):
        text = "合你胃口就好，听着舒服最重要。"
    text = re.sub(r"\[[^\]\n]{1,12}\]|【[^】\n]{1,12}】", "", str(text))
    text = re.sub(r"[\U0001f000-\U0001faff\u2600-\u27ff\ufe0f\u200d]", "", text)
    text = re.sub(r"[\r\n*_#`]+", "", text).strip()
    name = viewer_address(payload.get("观众"))
    address = ""
    if name and text:
        text = re.sub(r"^" + re.escape(name) + r"(?:[，,:：、！!\s]+|$)", "", text, count=1)
        text = re.sub(r"^(?:这位棋友|这位朋友|这位观众|棋友|朋友)[，,:：、！!\s]+", "", text)
        address = name + "，"
    budget = max(1, limit - len(address))
    sentences = re.findall(r"[^。！？!?]+[。！？!?]?", text)
    result = ""
    for sentence in sentences:
        if len(result + sentence) > budget:
            break
        result += sentence
    if not result and text:
        clipped = text[:budget - 1]
        boundary = clipped.rfind("，")
        if boundary >= budget // 2:
            clipped = clipped[:boundary]
        result = clipped.rstrip("，,。！？!?") + "。"
    return address + result if result else ""
