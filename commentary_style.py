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
    "中炮": (("音量", "中炮开门，中路的话筒咱们先拿。"), ("家门", "中炮起手，先到中路打个招呼。")),
    "仙人指路": (("快递", "仙人指路起手，小兵先递个话。"), ("戏台", "仙人指路，小兵先来开个场。")),
    "飞相局": (("家门", "飞相局起手，家里的事先料理好。"), ("上班", "飞相局开场，走田字的先来报到。")),
    "起马局": (("上班", "起马局开场，马儿先打个卡。"), ("戏台", "起马局起手，马儿先领个镜头。")),
    "过宫炮": (("家门", "过宫炮开场，炮先串个门。"), ("戏台", "过宫炮起手，炮先换个机位。")),
    "士角炮": (("戏台", "士角炮起手，先从宫角搭个台。"), ("位置", "士角炮开场，侧面的座位先坐坐。")),
    "中炮对屏风马": (("戏台", "中炮对屏风马，两位老熟人又碰头了。"), ("上班", "中炮对屏风马，经典搭档今天也出勤。")),
    "仙人指路对卒底炮": (("快递", "仙人指路对卒底炮，兵刚递话，炮就来接。"), ("戏台", "仙人指路对卒底炮，这个开场两边都接上了。")),
    "仙人指路对挺卒": (("戏台", "仙人指路碰上挺卒，两边小个子先来对话。"), ("上班", "仙人指路对挺卒，两边小个子先报到。")),
    "红方两头蛇": (("上班", "两翼兵都拱起来，两头蛇摆上，两边一起忙。"), ("戏台", "两边兵都拱起来了，两头蛇也要露个脸。")),
    "黑方两头蛇": (("上班", "两翼小卒一起出门，两头蛇也开始忙了。"), ("戏台", "两边小卒都拱起来，这边两头蛇也亮相了。")),
}


# Families prevent a series of different lines from repeating the same joke.
FLOURISHES = {
    "吃车": (
        ("停车", "这辆车先收着，停车费就免了。"),
        ("财务", "这笔战略投资，咱们先签收。"),
        ("快递", "车已签收，这单不退货。"),
        ("戏台", "车的戏份，咱们接着演。"),
    ),
    "吃马": (
        ("上班", "这匹马今天提前下班。"),
        ("财务", "这笔马的账，咱们先记上。"),
        ("快递", "马已签收，草料咱们备着。"),
        ("戏台", "这匹马的戏份，到这儿歇会儿。"),
    ),
    "吃炮": (
        ("音量", "这门炮的音量，咱们来调。"),
        ("快递", "炮已签收，包装就不留了。"),
        ("财务", "这笔炮的账，记得清清楚楚。"),
        ("上班", "这门炮今天提前收工。"),
    ),
    "吃子": (
        ("财务", "账本添一笔，咱们不客气。"),
        ("快递", "这一单，咱们先签收。"),
        ("戏台", "这枚子的戏份，先到这里。"),
        ("上班", "这枚子先休息，咱们接着忙。"),
    ),
    "解将": (
        ("家门", "家门口的事，咱们先处理。"),
        ("音量", "这声将军，咱们接住了。"),
        ("上班", "先忙正事，段子等会儿。"),
        ("戏台", "这段惊险戏，咱们稳着演。"),
    ),
    "将军": (
        ("家门", "敲门了，这声招呼得接。"),
        ("音量", "这声将军，咱们报清楚。"),
        ("戏台", "这一幕，轮到对面接戏。"),
        ("上班", "这声将军，对面得先处理。"),
    ),
    "中路": (
        ("位置", "中路这位置，咱们先坐坐。"),
        ("戏台", "镜头给中路，主角往这儿站。"),
        ("上班", "往中路凑凑，开个小会。"),
        ("音量", "中路的话筒，咱们也得拿。"),
    ),
    "马": (
        ("上班", "马儿迈个日字，今天也算出勤。"),
        ("戏台", "马儿换个站位，镜头跟上。"),
        ("快递", "马儿换条线，业务接着跑。"),
        ("位置", "马儿挪挪窝，棋盘没躺椅。"),
    ),
    "车": (
        ("停车", "大车换个位置，方向盘握稳。"),
        ("上班", "大车动一动，别光占车位。"),
        ("戏台", "大车一动，镜头也得跟上。"),
        ("快递", "大车换条线路，咱们接着跑。"),
    ),
    "炮": (
        ("音量", "炮位调一调，话筒也摆正。"),
        ("戏台", "炮台换个机位，镜头跟上。"),
        ("上班", "炮也得活动，不能只管点名。"),
        ("位置", "炮位先调好，椅子挪一挪。"),
    ),
    "兵卒": (
        ("戏台", "个头小，也得有自己的镜头。"),
        ("上班", "小个子挪一步，出勤也算数。"),
        ("位置", "小个子也要有位置，慢慢挪。"),
        ("财务", "步子虽小，这笔咱们也记着。"),
    ),
    "相象": (
        ("上班", "走个田字，今天也算出勤。"),
        ("戏台", "走田字的，也该给点镜头。"),
        ("位置", "田字走得端正，站位也讲究。"),
    ),
    "仕士": (
        ("家门", "家门口的岗位，也得有人站。"),
        ("上班", "宫里的岗位，认真值个班。"),
        ("戏台", "宫里也换个站位，镜头跟上。"),
    ),
    "帅将": (
        ("戏台", "主角亲自挪位，镜头给足。"),
        ("位置", "主角换个座位，咱们接着下。"),
        ("上班", "主角亲自活动，也算出勤。"),
    ),
}


class CommentaryStyle:
    def __init__(self):
        self.lock = threading.Lock()
        self.recent_keys = deque(maxlen=16)
        self.recent_families = deque(maxlen=4)
        self.spoken_families = deque(maxlen=4)
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

    def topics(self):
        with self.lock:
            return list(dict.fromkeys(family for family in self.spoken_families if family))

    def decorate(self, kind, text, payload, persona):
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
        facts = set((payload.get("QUEEN式证据") or {}).get("选择理由") or [])
        if "先解除将军" in facts:
            scene = "解将"
        elif "形成将军" in facts:
            scene = "将军"
        elif "，吃" in move:
            target = move.rsplit("吃", 1)[-1][:1]
            scene = "吃" + target if "吃" + target in FLOURISHES else "吃子"
        elif "争夺中路" in facts or "马向中路靠拢" in facts:
            scene = "中路"
        else:
            scene = {"马": "马", "车": "车", "炮": "炮", "兵": "兵卒", "卒": "兵卒",
                     "相": "相象", "象": "相象", "仕": "仕士", "士": "仕士", "帅": "帅将", "将": "帅将"}.get(move[:1], "")
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
