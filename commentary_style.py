"""Short, factual streamer flourishes; no engine/model work in move calls."""
from collections import deque
import re
import threading
from stream_brand import HOST_NAME, CHANNEL_NAME


DEFAULT_PERSONA = (
    f"你叫{HOST_NAME}，是{CHANNEL_NAME}直播间的AI象棋搭子。口吻亲切自然、轻快灵动，像和熟人边看棋边聊。"
    "懂棋、会接梗，带一点俏皮和自嘲，先把当前走法或观众的问题说清楚，再顺嘴抖个贴合现场的短包袱。"
    "可以自然说好嘞、欸、咱们，但不机械堆口头禅，不撒娇卖萌，不说教，也不过度自夸。"
    "精彩处加戏，普通时候利索；尊重对手和观众，不冒充官方账号，不编造局面、胜负、操作或福利。"
    "被问身份时如实说明是独立运营的AI虚拟主播，不冒充豆包官方，不声称与字节跳动、抖音或豆包存在官方、授权或合作关系，不冒认模型和音色来源。"
)
LEGACY_PERSONAS = {
    "你叫小弈，是小弈象棋直播间独立原创的AI象棋搭子。口吻亲切自然、轻快灵动，像和熟人边看棋边聊。"
    "懂棋、会接梗，带一点俏皮和自嘲，先把当前走法或观众的问题说清楚，再顺嘴抖个贴合现场的短包袱。"
    "可以自然说好嘞、欸、咱们，但不机械堆口头禅，不撒娇卖萌，不说教，也不过度自夸。"
    "精彩处加戏，普通时候利索；尊重对手和观众，不冒充官方账号，不编造局面、胜负、操作或福利。"
    "被问身份时如实说明是AI虚拟主播，不自称豆包，不声称与字节跳动、抖音或豆包存在官方、授权或合作关系，不冒认模型和音色来源。",
    "你叫豆包，是豆包象棋直播间的AI象棋搭子。口吻亲切自然、轻快灵动，像和熟人边看棋边聊。"
    "懂棋、会接梗，带一点俏皮和自嘲，先把当前走法或观众的问题说清楚，再顺嘴抖个贴合现场的短包袱。"
    "可以自然说好嘞、欸、咱们，但不机械堆口头禅，不撒娇卖萌，不说教，也不过度自夸。"
    "精彩处加戏，普通时候利索；尊重对手和观众，不冒充官方账号，不编造局面、胜负、操作或福利。",
    "你叫棋小雅，是个懂棋、嘴贫、会接梗的象棋主播。像和熟人边下边聊，稍有点得意，遇到难处也会拿自己开玩笑。先把当前走法或观众的问题说清楚，再顺嘴抖一个贴合现场的短包袱；精彩处加戏，普通时候利索。幽默围着棋形、棋子和主播自己展开，尊重对手和观众，绝不编造局面、胜负或福利。",
    "你叫棋小雅，是一位专业但不端着的中国象棋主播。讲棋清楚，偶尔用生活化比喻和轻松包袱，幽默但不尬，不嘲讽对手和观众。",
    "你叫棋小雅，是一位专业但不端着的中国象棋主播。像和熟人边下边聊，少念书面报告，多用跳马、拱兵、出车、架炮这类自然说法；偶尔抖个轻包袱，幽默但不尬，不嘲讽对手和观众，也不要句句重复我方、对方。",
}


def humor_enabled(persona):
    return not re.search(r"严肃|只报走法|纯棋谱|(?:不要|禁止|别|不|少)(?:用|讲|说|开|抖|玩)?(?:幽默|笑话|段子|玩笑|包袱|梗|骚话)", str(persona))


def piece_amount(piece):
    return {"车": "一辆车", "马": "一匹马", "炮": "一门炮"}.get(piece, f"一枚{piece}")


OPENING_LINES = {
    "中炮": (("布局", "中炮开局，炮先摆到中路。"), ("熟棋", "又见中炮，这个开场大家熟。")),
    "仙人指路": (("布局", "仙人指路，先挺兵，再布置大子。"), ("熟棋", "仙人指路开场，先走个兵。")),
    "飞相局": (("布局", "飞相局，先把相飞起来。"), ("熟棋", "这是飞相局的开场。")),
    "起马局": (("布局", "起马局，先把马跳出来。"), ("熟棋", "这盘先跳马，起马局。")),
    "过宫炮": (("布局", "过宫炮，炮从宫前换到另一侧。"), ("熟棋", "炮先过宫，这盘是过宫炮。")),
    "士角炮": (("布局", "炮摆到士角，这是士角炮。"), ("熟棋", "士角炮开场，先看后续出子。")),
    "中炮对屏风马": (("熟棋", "中炮对屏风马，经典阵势又来了。"), ("布局", "双马已经摆好，这是中炮对屏风马。")),
    "仙人指路对卒底炮": (("布局", "这是仙人指路对卒底炮。"), ("熟棋", "挺兵碰上卒底炮，这个开场有看头。")),
    "仙人指路对挺卒": (("布局", "仙人指路对挺卒，两边先动兵卒。"), ("熟棋", "两边先挺兵卒，仙人指路对挺卒。")),
    "红方两头蛇": (("布局", "三、七路兵都挺起来了，两头蛇布局。"), ("熟棋", "两翼兵一挺，两头蛇摆出来了。")),
    "黑方两头蛇": (("布局", "三、七路卒都挺起来了，两头蛇布局。"), ("熟棋", "两翼卒都动了，这是两头蛇。")),
}


# Families prevent a series of different lines from repeating the same joke.
FLOURISHES = {
    "吃车": (
        ("客气", "嘴上客气，棋盘上可不客气。"),
        ("提醒", "先收好，别转头又送回去。"),
        ("得意", "嘴角可以上扬，落子还得稳。"),
        ("稳住", "好家伙，咱们先别高兴过头。"),
    ),
    "吃马": (
        ("得意", "这一口挺香，先别上头。"),
        ("客气", "该吃就吃，咱们不装客气。"),
        ("提醒", "吃到是本事，别又送回去。"),
        ("稳住", "笑归笑，下一手还得算。"),
    ),
    "吃炮": (
        ("客气", "这一下，咱们就不推辞了。"),
        ("得意", "有点想笑了，先忍住。"),
        ("提醒", "先收着，棋可还没完。"),
        ("稳住", "手稳住，别光顾着乐。"),
    ),

}


class CommentaryStyle:
    def __init__(self):
        self.lock = threading.Lock()
        self.recent_keys = deque(maxlen=16)
        self.recent_families = deque(maxlen=4)
        self.spoken_families = deque(maxlen=4)
        self.recent_openers = deque(maxlen=6)
        self.turn = 0
        self.move_calls = 0
        self.last_move_flourish = -3

    def pick(self, scope, options, *, required=False):
        with self.lock:
            blocked = set(self.recent_families) | set(self.spoken_families)
            fresh = [(family, line) for family, line in options
                     if (scope, line) not in self.recent_keys and family not in blocked]
            if not fresh:
                last_lines = list(self.recent_keys)[-3:]
                fresh = [(family, line) for family, line in options
                         if (scope, line) not in last_lines and family not in blocked]
            if not fresh and required:
                fresh = [(family, line) for family, line in options if (scope, line) not in self.recent_keys]
                fresh = fresh or list(options)
            if not fresh:
                self.recent_families.append(None)
                return ""
            family, line = fresh[self.turn % len(fresh)]
            self.turn += 1
            self.recent_keys.append((scope, line))
            self.recent_families.append(family)
            return line

    def remember(self, text):
        words = {"上班": ("上班", "下班", "出勤", "收工", "值班"),
                 "家门": ("家门", "退房", "物业", "出口"),
                 "戏台": ("剧本", "接戏", "镜头", "戏份", "主角"),
                 "财务": ("投资", "账本", "账", "财务"),
                 "快递": ("签收", "快递", "退货", "包装"),
                 "停车": ("停车", "车位", "方向盘"),
                 "音量": ("音量", "话筒"), "位置": ("座位", "椅子", "站位"),
                 "考试": ("考试", "解题", "压轴题", "答卷")}
        families = [key for key, terms in words.items() if any(term in text for term in terms)]
        with self.lock:
            self.spoken_families.extend(families or [None])
            opener = re.search(r"(?:^|[，。！？])\s*(好嘞|哎呀|哈哈|好的|欸|哎)[，。！？\s]", text)
            self.recent_openers.append(opener.group(1) if opener else None)

    def topics(self):
        with self.lock:
            return list(dict.fromkeys(family for family in self.spoken_families if family))

    def openers(self):
        with self.lock:
            return list(dict.fromkeys(word for word in self.recent_openers if word))

    def decorate(self, kind, text, payload, persona):
        if payload.get("_sandbox_continuation"):
            return text
        if kind == "弹幕回复":
            opener = re.match(r"^(好嘞|哎呀|哈哈|好的|欸|哎)[，。！？\s]+", text)
            if opener and opener.group(1) in self.openers():
                return text[opener.end():] or text
            return text
        if not humor_enabled(persona):
            return text
        review = payload.get("上一手预判复盘") or {}
        if kind == "QUEEN式等待期解说" and review:
            actual = re.sub(r"^(红方|黑方)\s+", "", str(review.get("实际应手") or ""))
            if actual and "，吃" not in actual:
                options = (("路线", f"对手走了{actual}，这条线接上了。"),
                           ("戏台", f"对手{actual}，这段接戏咱们跟上。"),
                           ("考试", f"{actual}落下来了，咱们接着解这道题。")) if review.get("预判兑现") else (
                           ("戏台", f"对手走了{actual}，临场换了个剧本。"),
                           ("路线", f"嚯，{actual}，对面换路了，咱们跟上。"),
                           ("考试", f"这手{actual}，对面换了道题，咱们接着解。"))
                return self.pick("应手跟进", options, required=True)
        if kind not in ("关键走棋解说", "我方走棋播报", "我方走棋解说"):
            return text
        with self.lock:
            self.move_calls += 1
            call = self.move_calls
            if call - self.last_move_flourish < 3:
                return text
        notation = str(payload.get("我方计划走法") or "")
        move = re.sub(r"^(红方|黑方)\s+", "", notation)
        if not re.match(r"[车马炮兵卒相象仕士帅将].+[进退平]", move) or len(text) > 30:
            return text
        if len(re.findall(r"[^。！？!?]+[。！？!?]", text)) >= 2:
            return text
        evidence = payload.get("QUEEN式证据") or {}
        facts = set(evidence.get("选择理由") or [])
        # A capture followed by a verified recapture is an exchange, not an
        # occasion to sound as though we just won a free piece.
        if "先解除将军" in facts or evidence.get("应手回吃本子"):
            return text
        if "，吃" in move:
            target = move.rsplit("吃", 1)[-1][:1]
            scene = "吃" + target if target in "车马炮" else ""
        else:
            return text
        if not scene:
            return text
        joke = self.pick(scene, FLOURISHES.get(scene, ()))
        if joke and len(text) + len(joke) <= 46:
            with self.lock:
                self.last_move_flourish = call
            return text.rstrip("。") + "。" + joke
        return text

    def opening(self, name, fallback, persona):
        if name not in OPENING_LINES:
            return fallback
        plain = "这边摆出了两头蛇。" if "两头蛇" in name else f"这是{name}。"
        if not humor_enabled(persona):
            return plain
        return self.pick("开局:" + name, OPENING_LINES[name]) or plain
