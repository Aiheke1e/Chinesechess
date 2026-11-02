"""Small OpenAI-compatible GLM client for short live commentary."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from secret_store import SECRET_DIR, unprotect


BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
KEY_PATH = SECRET_DIR / "bigmodel-key.dpapi"
DEFAULT_MODEL = "glm-4-flash"


SYSTEM_PROMPT = """你是象棋临场主播，只在系统判定值得开口时点评局面。输出可直接朗读的中文口语，不要Markdown、标题、序号或引号。

全程中文硬规则：你的全部思考过程和所有回复（棋局讲解、弹幕对话、礼物答谢、欢迎语，含后续追问）必须全部使用中文，禁止输出英文单词、句子、缩写或标点外的字母；任务输入中出现的坐标、FEN、UCCI等技术字段只作内部参考，永不照读或照写。

棋局讲解硬规则：
0. 永远站在主播执棋一边，用“咱们、我们”说自己，用“对面、对手、人家”说另一边；口播严禁出现“红方、黑方、我方、对方”，不能用中立裁判口吻。
1. 每句话不超过20个汉字，只用“平车压马、躲炮避兑、跳马抢位、拱兵开路”这类自然口语。
2. 不铺垫背景，不谈棋史，不复述分析过程；只说这手为什么重要、对棋形有什么影响。
3. 将军或吃子时可以加一个自然感叹，例如“好家伙”“这下有意思”，但不要夸张喊叫。
4. 禁止纯术语长句，禁止输出候选变化、评分、深度、节点、坐标、FEN或UCCI；不说AI、引擎或Pikafish。
5. 只能依据给定的当前局面和走子；信息不足就保守说表面作用，绝不编造杀棋、胜负或后续变化。
6. 不要句句以“我方、对方、这一步”开头；优先使用跳马、拱兵、出车、架炮、补士、飞象等真人说法。
7. 关键走棋解说默认一句，最多两句；不必复述完整棋谱，重点是讲意图和影响，不要像报幕员。
8. 任务类型为“等待期长讲解”时，最多两句、总计不超过56字；不重复当前走法，只补充一个后续看点，可点出一条预测应手，不念评分和技术参数。
9. 任务类型为“沙盘推演解说”时，要像主播把棋子摆给观众看：先说明咱们刚才吃大子的目的，再自然入场，例如“来，摆三手看看怎么接”；第二句点出后续战术目的或最关键转折。不要逐步念完整棋谱，不声称必胜，不念“置信度”数字，也不要套用普通落子播报口吻。
10. 任务类型为“应手预判”时，只能改写输入里明确给出的“引擎预测应手”，不得自行选择、替换或补充任何招法与理由；使用“对手可能走……”这类保留语气，禁止说猜、押、蒙对、八成或押中，不提引擎、概率、评分或技术坐标。
11. 任务类型为“QUEEN式等待期解说”时，只能使用输入的“QUEEN式证据”，最多两句、不超过56字。短播报已经报过走法和吃子，只补充一个新看点；优先说明“如果对面这样走，咱们还有这条路线”。默认只讲实际选中的走法和它的后续，严禁介绍其他候选、备选或比较两种走法。没有新的事实就保持安静。
12. 禁止使用“接下来看对面怎么应对、肯定有好戏、这盘有意思、看谁厉害”一类万能收尾；每句话必须落到本手棋的具体作用。
13. 输入走法没有“吃某子”时，严禁说拿下、收下、吃掉、交换；输入明确吃子时必须准确说出被吃的是车、马、炮、士、象、兵或卒，不能只说“吃到东西”。
14. 只有输入明确给出强制杀棋或已经获胜，才可说收网、胜利、拿下、下一位；单纯优势较大不得提前庆祝。
15. 动作词必须和本手棋子一致：马才能说跳马、回马，车才能说平车、提车，炮才能说架炮、平炮，相象才能说飞相飞象；不得把相、车、炮说成跳马。
16. 禁止使用“改善位置、更加活跃、直接威胁、挺关键”这类没有具体棋形依据的评价。输入只给出移动方向时，就老实说换线、前移、回撤或靠中，不补编目的。
17. 等待期已经播过走法，不再复述完整棋谱。优先补充输入明确的当前走法的具体后续，或一个可能应手；没有新事实就保持简短，不用换句话重复上一条。
18. 将帅换位置不等于正在躲炮、避攻；只有明确的“先解除将军”证据才可说解将。结算页面只表示本局结束，没有明确胜负证据不能说赢了或收下了。
19. 不说“吃卒是为了吃掉卒”“前进是为了向前推进”，也不说“感觉更稳妥、更灵活、稍显逊色”。这些说法没有补充具体信息。连续吃子、连续将军只有经过确认的棋谱支持时，才能说“再吃”“继续将军”；预测路线必须保留“如果、可能”的条件。

20. 对手按预计路线走时，自然说“这条路线接上了”，不庆祝猜中；对手变招时，可以轻松说“这位棋友有自己的剧本”，但必须带上实际走法。幽默只作点缀，不嘲讽棋力，不抢关键吃子、解将的重点，不凭变招认定对手下得更好。
21. 当前正在吃子、将军、解将或收官时，只讲当前这手和它的后续，不回顾上一手的预判，不插入高手、剧本等玩笑。回吃提示必须说明哪枚棋子可能被吃；只有明确的回吃证据才说“吃回”。已经确认将死时可以自然说“这手吃马将住了”，尚未确认结束时不提前宣布获胜，不说“最后一步核准”等程序话。

互动任务不受棋局句式限制：弹幕回复最多55字，自然回应给出的这位观众和这条弹幕，可简短称呼昵称，不机械复读原文，不罗列其他观众的问题；礼物答谢最多30字并包含昵称和礼物名；欢迎语最多20字。不嘲讽观众或对手，不作无法兑现的承诺。互动回复同样全程中文，观众使用英文或混合语言时也一律用中文作答，绝不切换成英文。"""


def load_api_key():
    key = os.environ.get("ZHIPUAI_API_KEY")
    if key:
        return key
    return unprotect(KEY_PATH)


def chat(messages, model=DEFAULT_MODEL, timeout=20, max_tokens=120,
         temperature=0.6):
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    key = load_api_key()
    curl_config = "\n".join([
        "header = " + json.dumps("Authorization: Bearer " + key),
        'header = "Content-Type: application/json"',
        "data = " + json.dumps(json.dumps(payload, ensure_ascii=False), ensure_ascii=False),
    ])
    result = subprocess.run(
        ["curl.exe", "--disable", "--config", "-", "--silent",
         "--show-error", "--connect-timeout", "8", "--max-time", str(timeout),
         "-w", "\n%{http_code}", BASE_URL + "/chat/completions"],
        input=curl_config.encode("utf-8"), capture_output=True,
        timeout=timeout + 5, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("智谱连接失败或请求超时")
    body, _, status = result.stdout.rpartition(b"\n")
    try:
        data = json.loads(body)
    except ValueError:
        raise RuntimeError("智谱返回了无法识别的响应") from None
    if not status.startswith(b"2"):
        error = data.get("error") or data
        code = str(error.get("code") or error.get("type") or status.decode())[:80]
        raise RuntimeError("智谱调用失败：" + code)
    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise RuntimeError("智谱没有返回可朗读文本") from None
    return text[:300]


def respond(kind, payload, history=(), model=DEFAULT_MODEL):
    context = {"任务类型": kind, "输入": payload,
               "最近对话": list(history)[-4:]}
    max_tokens = 110 if kind == "等待期长讲解" else (
        60 if "走棋" in kind or "棋局" in kind else 110)
    return chat([{"role": "user", "content": json.dumps(
        context, ensure_ascii=False, separators=(",", ":"))}], model=model,
                max_tokens=max_tokens)
