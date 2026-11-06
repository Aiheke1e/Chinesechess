"""Short audience lines without invented nicknames or stage directions."""
import re
from interaction_chess import chess_comment, grounded_reply


def viewer_address(user):
    """Use only the nickname characters supplied by the room, never unmask it."""
    name = str(user or "").strip()
    masked = "*" in name
    if masked:
        name = name.split("*", 1)[0]
    name = re.sub(r"[^\w\u4e00-\u9fff]", "", name).strip("_")
    if not name or name in ("观众", "匿名用户", "用户"):
        return ""
    return name + "棋友" if masked and not name.endswith("棋友") else name


def welcome_line(user):
    return f"欢迎{viewer_address(user) or '新朋友'}进入直播间。"


def reply_hint(comment):
    if re.search(r"怎么领|哪里.*领|哪.*领取|领取|棋谱.*[哪领]|[哪领].*棋谱", comment):
        return "没有提供领取入口、活动或商品信息。不能编造链接、福利、赠品或要求送礼；不清楚领什么就先问清楚。"
    if re.search(r"气出病|气死|看得.*急|太菜|水平臭|子都看不住|棋.*[臭烂]|下[得的].*[臭烂]", comment):
        return "观众在轻松吐槽或批评，嫌棋下得不好、看得着急。气出病不是人气，不要说棋下得好或变网红；轻松接住意见。"
    if re.search(r"好听|声音.*舒服|歌.*不错", comment):
        return "观众在评价听感，不是在评价走棋。简短接话，不假装自己听过或知道是哪首歌。"
    return "只按这条弹幕的原意回答，不借用其他观众的话。"


def local_reply(comment, snapshot=None):
    """Answer missing real-world facts honestly, before any model request."""
    comment = str(comment or "")
    if (re.search(r"支架|耳机|鼠标|键盘|衣服|同款|设备", comment)
            and re.search(r"[哪那].{0,5}买|[哪那].{0,4}卖|链接|店铺|多少钱|什么|哪款|型号", comment)):
        return ("购买来源我这边没有，可不能闭眼给你报一家店。" if re.search(r"买|卖|链接|店铺|多少钱", comment) else
                "具体型号我这边没有，先不乱报品牌。")
    if re.search(r"^(?:快|帮我|给我|赶紧|麻烦|请)?\s*(?:呼叫|叫来|喊来|联系|通知).{1,18}$", comment):
        return "召唤收到，我只能在这里接话，没法替你把人叫来。"
    if re.search(r"是.{0,3}(?:特大|大师|冠军)|(?:特大|大师|冠军)(?:吗|么|不)", comment):
        return "你指的是哪位棋手？称号得有可靠资料，我先不乱认。"
    return grounded_reply(comment, snapshot)


def spoken_reply(text, limit=55, payload=None):
    payload = payload or {}
    comment = str(payload.get("弹幕") or "")
    if payload.get("_grounded_reply"):
        text = payload["_grounded_reply"]
    elif (answer := local_reply(comment)) is not None:
        # Chess claims cannot be invented by an unconstrained social reply.
        text = answer
    hint = reply_hint(comment)
    if "领取入口" in hint:
        text = ("我这边没有棋谱领取入口，可不能瞎指路，把你领到隔壁去。"
                if "棋谱" in str(payload.get("弹幕") or "") else "你想领什么？说具体一点，主播还没学会读心术呢。")
    elif re.search(r"(?:送|刷|打赏|付).{0,10}(?:礼物|钱).{0,15}(?:就|才|告诉|可以)|(?:先|需要).{0,6}(?:送礼|刷礼物|打赏|付费)", str(text)):
        text = "直接问就好，不用送礼，咱们这儿提问不收门票。"
    elif "吐槽或批评" in hint and re.search(r"夸|棋艺.*好|厉害|感染.*魅力|棋.{0,8}(?:好|漂亮|精彩)|下[得的].{0,6}好|网红|人气.{0,6}病", str(text)):
        text = "看得你着急了，意见收到，咱们少吹两句，把棋下明白。"
    elif "评价听感" in hint and re.search(r"棋招|这招棋|棋下得|我也.*听|这首歌.*(?:不错|好听)", str(text)):
        text = "合你胃口就好，耳朵这关先过了，咱们接着聊。"
    if re.search(r"(?:我|咱们|我们|给你).{0,14}(?:摆.{0,4}(?:棋|局)|和你对弈|和你下[一两]?[盘局]|给你开[一两]?[盘局]|给你走[一两]?手|给你发.{0,4}棋谱)", str(text)):
        comment = str(payload.get("弹幕") or "")
        greeting = re.search(r"晚上好|早上好|中午好|下午好|你好|早安", comment)
        text = (f"{greeting.group()}，招呼收到，咱们这场聊天算是开席了。" if greeting else
                "先把你的意思说清楚，咱们聊明白再接着看。")
    if not chess_comment(comment) and re.search(r"这[招步].{0,8}(?:狠|稳|有想法|犀利)|(?:这盘|棋局).{0,20}(?:赢|优势|热闹|玄机|好戏)|对手.{0,6}(?:偷袭|上风)|稳如老狗", str(text)):
        text = ("能把你逗乐就好，这场气氛算是到位了。" if re.search(r"笑|哈哈", comment) else
                "你看得乐呵，我这话筒就没白开。" if re.search(r"有趣|有意思|节目效果", comment) else
                "这句我没听明白，你指的是哪件事？")
    text = re.sub(r"\[[^\]\n]{1,12}\]|【[^】\n]{1,12}】", "", str(text))
    text = re.sub(r"[\U0001f000-\U0001faff\u2600-\u27ff\ufe0f\u200d]", "", text)
    text = re.sub(r"[\r\n*_#`]+", "", text).strip()
    name = viewer_address(payload.get("观众"))
    if name and re.search(r"我叫(?:什么|啥)|我的(?:名字|昵称)|看[得不]到我的?(?:名字|昵称)", comment):
        text = "看到的是你直播间显示的昵称，真实姓名我可不乱猜。"
    address = ""
    if name and text:
        lead = r"(?:(?:哎|欸|嘿|嗨|好嘞|哈哈|好的)[，,:：、！!\s]*)*"
        address_pattern = re.compile(r"^" + lead + r"(?:" + re.escape(name) +
            r"|这位棋友|这位朋友|这位观众|棋友|朋友|老师|老板|师兄|大哥|兄弟|老铁)(?:[，,:：、！!\s]+|$)")
        for _ in range(3):
            cleaned = address_pattern.sub("", text, count=1)
            if cleaned == text:
                break
            text = cleaned
        address = name + "，"
    # Keep the actual readable nickname. Exceptionally long names still leave
    # room for a short answer instead of producing just a name and full stop.
    budget = max(18, limit - len(address))
    sentences = re.findall(r"[^。！？!?]+[。！？!?]?", text)
    result = ""
    for sentence in sentences[:2]:
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


def gift_reply(text, payload, style=None):
    """Fixed thanks using the real sender and gift; no model or extra patter."""
    name = viewer_address(payload.get("观众"))
    gift = " ".join(str(payload.get("礼物") or "心意").split())
    boss = name if name.endswith("老板") else name + "老板"
    return f"感谢{boss}送的{gift}，谢谢老板。"
