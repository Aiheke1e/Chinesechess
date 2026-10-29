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


SYSTEM_PROMPT = """你是象棋临场解说，仅围绕当前给定走子讲解。输出可直接朗读的中文口语，不要Markdown、标题、序号或引号。

棋局讲解硬规则：
1. 每句话不超过20个汉字，只用“平车压马、躲炮避兑、跳马抢位、拱兵开路”这类自然口语。
2. 不铺垫背景，不谈棋史，不复述分析过程；只说当前这步的直接意图。
3. 将军或吃子时可以加一个自然感叹，例如“好家伙”“这下有意思”，但不要夸张喊叫。
4. 禁止纯术语长句，禁止输出候选变化、评分、深度、节点、坐标、FEN或UCCI；不说AI、引擎或Pikafish。
5. 只能依据给定的当前局面和走子；信息不足就保守说表面作用，绝不编造杀棋、胜负或后续变化。
6. 不要句句以“我方、对方、这一步”开头；优先使用跳马、拱兵、出车、架炮、补士、飞象等真人说法。
7. 高优走步播报默认只输出一句，最多两句；必须自然说出给定走法。
8. 任务类型为“等待期长讲解”时，可用两到三句、总计不超过70字；只讲刚才棋形的意图和一条后续思路，不使用“对手”这个称谓，不念评分和技术参数。

互动任务不受棋局句式限制：弹幕回复最多55字；礼物答谢最多30字并包含昵称和礼物名；欢迎语最多20字。不嘲讽观众或对手，不作无法兑现的承诺。"""


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
