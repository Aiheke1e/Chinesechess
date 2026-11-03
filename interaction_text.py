"""Short audience lines without invented nicknames or stage directions."""
import re


def welcome_line(user):
    name = str(user or "").strip()
    if "*" in name or not re.search(r"[\w\u4e00-\u9fff]", name):
        name = "这位棋友"
    return f"欢迎{name[:30]}进入直播间。"


def reply_hint(comment):
    if re.search(r"气出病|气死|看得.*急|太菜|棋.*[臭烂]|下[得的].*[臭烂]", comment):
        return "观众在轻松吐槽或批评，不是夸奖。接住吐槽，不说谢谢夸奖、棋艺很好。"
    if re.search(r"好听|声音.*舒服|歌.*不错", comment):
        return "观众在评价听感，不是在评价走棋。简短接话，不假装自己听过或知道是哪首歌。"
    return "只按这条弹幕的原意回答，不借用其他观众的话。"


def spoken_reply(text, limit=55, payload=None):
    payload = payload or {}
    hint = reply_hint(str(payload.get("弹幕") or ""))
    if "吐槽或批评" in hint and re.search(r"夸|棋艺.*好|厉害|感染.*魅力", str(text)):
        text = "看得你着急了，咱们少点惊险，多点稳当。"
    elif "评价听感" in hint and re.search(r"棋招|这招棋|棋下得|我也.*听|这首歌.*(?:不错|好听)", str(text)):
        text = "合你胃口就好，听着舒服最重要。"
    name = str(payload.get("观众") or "")
    if "*" in name:
        prefix = re.sub(r"[^\w\u4e00-\u9fff]", "", name.split("*", 1)[0])
        if prefix:
            text = re.sub(re.escape(prefix) + r"(?:棋友|朋友|小朋友)", "这位棋友", str(text))
    text = re.sub(r"\[[^\]\n]{1,12}\]|【[^】\n]{1,12}】", "", str(text))
    text = re.sub(r"[\U0001f000-\U0001faff\u2600-\u27ff\ufe0f\u200d]", "", text)
    text = re.sub(r"[\r\n*_#`]+", "", text).strip()
    sentences = re.findall(r"[^。！？!?]+[。！？!?]?", text)
    result = ""
    for sentence in sentences:
        if len(result + sentence) > limit:
            break
        result += sentence
    if not result and text:
        result = text[:limit].rsplit("，", 1)[0].rstrip("，,。！？!?") + "。"
    return result
