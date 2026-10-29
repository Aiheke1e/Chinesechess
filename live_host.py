"""Audio-only live interaction, independent from the chess control loop."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import heapq
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import winsound

from bailian_tts import available_voices, synthesize
from glm_client import DEFAULT_MODEL, respond


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "live_config.json"
CHESS_EVENTS = HERE / "work" / "events.jsonl"
LIVE_LOG = HERE / "work" / "live_events.jsonl"
SPEECH_STARTED = HERE / "work" / "speech_started.json"
CHESS_SPEECH_KINDS = {
    "我方走棋播报", "我方走棋解说", "对手走棋播报", "对手走棋解说",
    "等待期长讲解", "收官解说", "获胜收官", "结算收官",
}
OWN_SPEECH_KINDS = {"我方走棋播报", "获胜收官", "结算收官"}


def normalize_room(value):
    value = str(value or "").strip()
    if re.fullmatch(r"\d{6,25}", value):
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or parsed.hostname != "live.douyin.com":
        raise ValueError("请输入抖音直播间链接或数字房间号")
    room = parsed.path.strip("/").split("/")[0]
    if not re.fullmatch(r"\d{6,25}", room):
        raise ValueError("直播间链接中没有有效房间号")
    return room


@dataclass
class LiveSettings:
    room: str = ""
    model: str = DEFAULT_MODEL
    voice: str = "Maia"
    prefix: str = "问主播"
    cooldown: float = 1.0
    persona: str = ("你叫棋小雅，是一位专业但不端着的中国象棋主播。"
                    "像和熟人边下边聊，少念书面报告，多用跳马、拱兵、"
                    "出车、架炮这类自然说法；偶尔抖个轻包袱，幽默但不尬，"
                    "不嘲讽对手和观众，也不要句句重复我方、对方。")
    introduction: str = "大家好，我是棋小雅。这里会一边下象棋，一边和大家聊棋、聊生活，欢迎来到直播间。"
    welcome_enabled: bool = True
    gift_enabled: bool = True
    speech_enabled: bool = False
    commentary_enabled: bool = True


def load_settings():
    settings = LiveSettings()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    for key in asdict(settings):
        if key in data:
            setattr(settings, key, data[key])
    return settings


def save_settings(settings):
    CONFIG_PATH.write_text(json.dumps(asdict(settings), ensure_ascii=False,
                                      indent=2) + "\n", encoding="utf-8")


class SpeechPipeline:
    """Generate text/TTS in background; never blocks chess input."""
    def __init__(self, settings, notify, llm=respond, tts=synthesize):
        self.settings, self.notify = settings, notify
        self.llm, self.tts = llm, tts
        self.closed = threading.Event()
        self.condition = threading.Condition()
        self.worker_lock = threading.Lock()
        self.jobs = []
        self.sequence = 0
        self.cancel_epoch = 0
        self.history = deque(maxlen=8)
        self.prepared = {}
        self.prepared_lock = threading.Lock()
        self.playback_lock = threading.Lock()
        self.high_speech_lock = threading.Lock()
        self.playback_state_lock = threading.Lock()
        self.current_playback = None
        self.idle_lock = threading.Lock()
        self.idle_state = None
        self.generator = None
        self._ensure_worker()

    def _ensure_worker(self):
        with self.worker_lock:
            if self.closed.is_set():
                return False
            if self.generator is not None and self.generator.is_alive():
                return True
            restarting = self.generator is not None
            self.generator = threading.Thread(
                target=self._worker_entry, name="chess-live-speech", daemon=True)
            self.generator.start()
        if restarting:
            self.notify("speech_status", "语音线程已自动恢复", {})
        return True

    def submit(self, priority, kind, payload, direct_text=None):
        if not self._ensure_worker():
            return False
        with self.condition:
            self.sequence += 1
            # Once our next move is known, any queued older chess narration is
            # already behind the board.  Keep chat/gifts, but let the latest
            # move replace stale chess play-by-play.
            if kind in OWN_SPEECH_KINDS:
                self.jobs = [job for job in self.jobs
                             if job[4] not in CHESS_SPEECH_KINDS]
                heapq.heapify(self.jobs)
            heapq.heappush(self.jobs, (
                priority, self.sequence, time.monotonic(), self.cancel_epoch,
                kind, payload, direct_text))
            # Keep important jobs and cap backlog so speech never trails minutes behind.
            if len(self.jobs) > 12:
                self.jobs = sorted(self.jobs)[:12]
                heapq.heapify(self.jobs)
            self.condition.notify()
        self.notify("queued", f"{kind}已加入播报队列",
                    {"kind": kind, **payload})
        return True

    def clear(self, stop_audio=False):
        self.cancel_idle()
        with self.condition:
            self.jobs.clear()
            self.cancel_epoch += 1
        with self.prepared_lock:
            states = list(self.prepared.values())
            self.prepared.clear()
        for state in states:
            state["approved"] = False
            state["decision"].set()
        if stop_audio:
            self.stop_audio()

    def jump_to_latest(self):
        """Drop queued old speech without cutting the sentence being spoken."""
        with self.condition:
            newest = max(
                (job for job in self.jobs if job[4] in CHESS_SPEECH_KINDS),
                key=lambda job: job[1], default=None)
            self.jobs.clear()
            if newest is not None:
                priority, sequence, _, epoch, kind, payload, direct = newest
                self.jobs.append((priority, sequence, time.monotonic(),
                                  epoch, kind, payload, direct))
                heapq.heapify(self.jobs)
            self.condition.notify_all()
        self.notify("speech_status", "已清除排队旧讲解；当前句说完后跳到最新", {})

    @staticmethod
    def _mark_speech_started(payload):
        token = str(payload.get("speech_token") or "").strip()
        if not token:
            return
        try:
            SPEECH_STARTED.parent.mkdir(parents=True, exist_ok=True)
            temporary = SPEECH_STARTED.with_suffix(".tmp")
            temporary.write_text(json.dumps({
                "token": token, "started": time.time()
            }, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, SPEECH_STARTED)
        except OSError:
            pass

    @staticmethod
    def _clean_text(text):
        return re.sub(r"[\r\n*_#`]+", "", str(text)).strip()

    @classmethod
    def _short_chess_text(cls, text):
        """Keep chess commentary natural and cap every sentence at 20 chars."""
        cleaned = cls._clean_text(text)
        if not cleaned:
            return ""
        parts = re.findall(r"[^。！？!?；;]+[。！？!?；;]?", cleaned)
        result = []
        for part in parts[:2]:
            body = part.rstrip("。！？!?；;").strip(" ，,")
            if not body:
                continue
            punctuation = part[-1] if part[-1] in "。！？!?" else "。"
            result.append(body[:20] + punctuation)
        return "".join(result)

    def prepare_move(self, kind, payload, direct_text=None):
        """Pre-generate one move's text/audio; play only after commit_move."""
        token = str(payload.get("speech_token") or "").strip()
        if not token or not self._ensure_worker():
            return False
        state = {"decision": threading.Event(), "approved": None,
                 "epoch": self.cancel_epoch}
        with self.prepared_lock:
            older = list(self.prepared.values())
            self.prepared.clear()
            self.prepared[token] = state
        for old in older:
            old["approved"] = False
            old["decision"].set()
        threading.Thread(
            target=self._prepare_move_worker,
            args=(token, kind, dict(payload), state, direct_text),
            name="chess-move-prewarm", daemon=True).start()
        self.notify("queued", "走棋短解说正在并行预生成", payload)
        return True

    def _prepare_move_worker(self, token, kind, payload, state, direct_text=None):
        audio = None
        try:
            self.notify("speech_status", "正在预生成走棋短解说", payload)
            text = direct_text or self.llm(
                kind, {"主播设定": self.settings.persona, **payload},
                list(self.history), model=self.settings.model)
            text = self._short_chess_text(text)
            if not text:
                return
            tts_error = None
            if self.settings.speech_enabled:
                try:
                    self.notify("speech_status", "正在预热走棋语音", payload)
                    audio = self.tts(text, self.settings.voice)
                except Exception as error:
                    tts_error = str(error)[:200]
            # Commit may arrive before generation finishes.  The decision flag
            # is sticky, so generation and board validation remain independent.
            if not state["decision"].wait(30.0) or not state["approved"]:
                return
            if state["epoch"] != self.cancel_epoch:
                return
            self.notify("reply", text, {"kind": kind, **payload})
            if tts_error:
                self.notify("error", tts_error, {"kind": kind})
            elif audio and self.settings.speech_enabled:
                self.notify("speech_status", "落子已确认，播放预热语音", payload)
                self._mark_speech_started(payload)
                self.cancel_idle()
                with self.high_speech_lock:
                    with self.playback_lock:
                        with self.playback_state_lock:
                            self.current_playback = "high"
                        try:
                            winsound.PlaySound(str(audio), winsound.SND_FILENAME)
                        finally:
                            with self.playback_state_lock:
                                self.current_playback = None
                self._prune_audio(Path(audio).parent)
            else:
                self.notify("speech_status", "落子已确认，仅显示文本", payload)
            self.history.extend([
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                {"role": "assistant", "content": text}])
            self.notify("speech_status", "等待下一条内容", payload)
        except Exception as error:
            self.notify("error", str(error)[:200], {"kind": kind})
        finally:
            with self.prepared_lock:
                if self.prepared.get(token) is state:
                    self.prepared.pop(token, None)
            if audio and not state.get("approved"):
                try:
                    Path(audio).unlink(missing_ok=True)
                except OSError:
                    pass

    def _decide_move(self, token, approved):
        token = str(token or "").strip()
        with self.prepared_lock:
            state = self.prepared.get(token)
        if state is None:
            return False
        state["approved"] = bool(approved)
        state["decision"].set()
        return True

    def commit_move(self, token):
        return self._decide_move(token, True)

    def discard_move(self, token):
        return self._decide_move(token, False)

    def play_high(self, kind, payload, direct_text):
        """Play a mandatory short move call without waiting for the LLM."""
        text = self._short_chess_text(direct_text)
        if not text:
            return False
        self.cancel_idle()
        threading.Thread(
            target=self._play_high_worker,
            args=(kind, dict(payload), text),
            name="chess-high-speech", daemon=True).start()
        return True

    def _play_high_worker(self, kind, payload, text):
        try:
            self.notify("reply", text, {"kind": kind, **payload})
            if not self.settings.speech_enabled:
                self.notify("speech_status", "高优走步仅显示文本", payload)
                return
            self.notify("speech_status", "正在生成高优走步语音", payload)
            audio = self.tts(text, self.settings.voice)
            with self.high_speech_lock:
                with self.playback_lock:
                    with self.playback_state_lock:
                        self.current_playback = "high"
                    try:
                        winsound.PlaySound(str(audio), winsound.SND_FILENAME)
                    finally:
                        with self.playback_state_lock:
                            self.current_playback = None
            self._prune_audio(Path(audio).parent)
        except Exception as error:
            self.notify("error", str(error)[:200], {"kind": kind})
        finally:
            self.notify("speech_status", "等待下一条内容", payload)

    def prepare_idle(self, payload):
        """Pre-generate long analysis, but play only in a real wait window."""
        self.cancel_idle()
        state = {
            "cancel": threading.Event(), "window": threading.Event(),
            "epoch": self.cancel_epoch, "payload": dict(payload),
        }
        with self.idle_lock:
            self.idle_state = state
        threading.Thread(
            target=self._prepare_idle_worker, args=(state,),
            name="chess-idle-prewarm", daemon=True).start()
        self.notify("queued", "等待期长讲解正在预生成", payload)
        return True

    def _prepare_idle_worker(self, state):
        audio = None
        payload = state["payload"]
        try:
            text = self.llm(
                "等待期长讲解", {"主播设定": self.settings.persona, **payload},
                list(self.history), model=self.settings.model)
            text = self._clean_text(text)[:120]
            if not text or state["cancel"].is_set():
                return
            if self.settings.speech_enabled:
                audio = self.tts(text, self.settings.voice)
            while not state["cancel"].is_set():
                if state["window"].wait(0.1):
                    break
            if state["cancel"].is_set() or state["epoch"] != self.cancel_epoch:
                return
            self.notify("reply", text, {"kind": "等待期长讲解", **payload})
            if audio and self.settings.speech_enabled:
                self.notify("speech_status", "等待落子，播放低优长讲解", payload)
                with self.playback_lock:
                    if state["cancel"].is_set():
                        return
                    with self.playback_state_lock:
                        self.current_playback = "low"
                    try:
                        winsound.PlaySound(str(audio), winsound.SND_FILENAME)
                    finally:
                        with self.playback_state_lock:
                            self.current_playback = None
                self._prune_audio(Path(audio).parent)
            else:
                self.notify("speech_status", "等待期长讲解仅显示文本", payload)
            self.history.extend([
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                {"role": "assistant", "content": text},
            ])
        except Exception as error:
            if not state["cancel"].is_set():
                self.notify("error", str(error)[:200], {"kind": "等待期长讲解"})
        finally:
            with self.idle_lock:
                if self.idle_state is state:
                    self.idle_state = None
            if audio and state["cancel"].is_set():
                try:
                    Path(audio).unlink(missing_ok=True)
                except OSError:
                    pass

    def open_idle_window(self):
        with self.idle_lock:
            state = self.idle_state
        if state is None or state["cancel"].is_set():
            return False
        state["window"].set()
        self.notify("speech_status", "已进入等待落子空窗", state["payload"])
        return True

    def cancel_idle(self):
        with self.idle_lock:
            state = self.idle_state
        if state is not None:
            state["cancel"].set()
            state["window"].set()
        with self.playback_state_lock:
            playing_low = self.current_playback == "low"
        if playing_low:
            winsound.PlaySound(None, winsound.SND_PURGE)
        return state is not None

    def _worker_entry(self):
        try:
            self._generate()
        except BaseException as error:
            if not self.closed.is_set():
                try:
                    self.notify("error", "语音工作线程异常停止：" +
                                type(error).__name__, {})
                except Exception:
                    pass

    def _generate(self):
        while not self.closed.is_set():
            with self.condition:
                ready = self.condition.wait_for(
                    lambda: self.jobs or self.closed.is_set(), 0.5)
                if self.closed.is_set():
                    break
                if not ready or not self.jobs:
                    continue
                (priority, sequence, created, epoch, kind, payload,
                 direct) = heapq.heappop(self.jobs)
            if epoch != self.cancel_epoch:
                continue
            if (kind in CHESS_SPEECH_KINDS and
                    time.monotonic() - created > 10.0):
                self.notify("speech_status", "已跳过过期棋局讲解", payload)
                continue
            try:
                self.notify("speech_status", f"正在组织{kind}话术", payload)
                text = direct or self.llm(
                    kind, {"主播设定": self.settings.persona, **payload},
                    list(self.history), model=self.settings.model)
                text = self._clean_text(text)[:300]
                if not text:
                    continue
                if epoch != self.cancel_epoch:
                    continue
                self.notify("reply", text, {"kind": kind, **payload})
                if self.settings.speech_enabled:
                    self.notify("speech_status", "正在生成百炼语音", payload)
                    audio = self.tts(text, self.settings.voice)
                    self.notify("speech_status", "正在播放", payload)
                    self._mark_speech_started(payload)
                    with self.playback_lock:
                        winsound.PlaySound(str(audio), winsound.SND_FILENAME)
                    self._prune_audio(Path(audio).parent)
                else:
                    self.notify("speech_status", "仅文本模式：未调用 TTS", payload)
                self.history.extend([{"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                                     {"role": "assistant", "content": text}])
                self.notify("speech_status", "等待下一条内容", payload)
                deadline = time.monotonic() + max(0.0, float(self.settings.cooldown))
                while not self.closed.is_set() and time.monotonic() < deadline:
                    time.sleep(0.05)
            except Exception as error:
                self.notify("error", str(error)[:200], {"kind": kind})

    @staticmethod
    def _prune_audio(folder, keep=20):
        try:
            files = sorted(folder.glob("speech_*.wav"), key=lambda p: p.stat().st_mtime,
                           reverse=True)
            for path in files[keep:]:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def close(self):
        self.cancel_idle()
        self.closed.set()
        with self.condition:
            self.condition.notify_all()
        winsound.PlaySound(None, winsound.SND_PURGE)

    @staticmethod
    def stop_audio():
        winsound.PlaySound(None, winsound.SND_PURGE)


class LiveController:
    def __init__(self, notify):
        self.notify = notify
        self.settings = load_settings()
        self.enabled = False
        self.connector = None
        self.connector_epoch = 0
        self.seen = {}
        self.welcomed = set()
        self.last_welcome = 0.0
        self.last_engine_score = None
        self.opponent_move_count = 0
        self.victory_count = 0
        self.log_lock = threading.Lock()
        self.gift_lock = threading.Lock()
        self.pending_gifts = {}
        self.gift_timers = {}
        self.pipeline = SpeechPipeline(self.settings, self._notify)
        self.tailer = ChessEventTailer(CHESS_EVENTS, self.submit_chess)
        self.tailer.start()

    def _notify(self, kind, text, data=None):
        record = {"time": time.strftime("%H:%M:%S"), "kind": kind,
                  "text": str(text), "data": data or {}}
        try:
            LIVE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with self.log_lock:
                with open(LIVE_LOG, "a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
        try:
            self.notify(record)
        except Exception:
            pass

    def configure(self, **values):
        was_speech_enabled = self.settings.speech_enabled
        for key, value in values.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.settings.cooldown = max(0.0, min(30.0, float(self.settings.cooldown)))
        if self.settings.voice not in available_voices():
            self.settings.voice = available_voices()[0]
        save_settings(self.settings)
        if was_speech_enabled and not self.settings.speech_enabled:
            self.pipeline.stop_audio()
        self._notify("status", "互动设置已保存")

    def enable(self):
        self.enabled = True
        self._notify("status", "弹幕互动已开启；棋局讲解会始终生成")

    def pause(self):
        self.enabled = False
        self.pipeline.clear(stop_audio=True)
        self._notify("status", "弹幕互动已暂停；后续棋局台词仍会生成")

    def jump_to_latest(self):
        self.pipeline.jump_to_latest()
        self._notify("status", "已清除旧播报，语音将跟随最新局面")

    def _complex_chess_event(self, event):
        notation = str(event.get("notation") or "")
        if "吃" in notation:
            return True
        analysis = event.get("analysis") or {}
        if analysis.get("score_type") == "mate":
            return True
        try:
            score = int(analysis["score"])
        except (TypeError, ValueError):
            return False
        except KeyError:
            return False
        if event.get("number") == 1:
            self.last_engine_score = None
        previous = getattr(self, "last_engine_score", None)
        self.last_engine_score = score
        return (abs(score) >= 600 or
                (previous is not None and abs(score - previous) >= 180))

    @staticmethod
    def _spoken_notation(notation):
        text = str(notation or "").strip()
        for prefix in ("红方 ", "黑方 "):
            if text.startswith(prefix):
                return text[len(prefix):]
        return text

    @classmethod
    def _casual_move_text(cls, notation, number=0):
        """Turn formal notation into short, varied streamer speech."""
        move = cls._spoken_notation(notation)
        piece = move[:1]
        choices = {
            "马": (
                f"这手跳马，{move}，先让这匹马活动起来。",
                f"马儿蹦起来，{move}，别老在家里吃草。",
                f"来个{move}，这匹马开始上班了。",
            ),
            "兵": (
                f"小兵往前拱，{move}，步子不大，态度先摆出来。",
                f"先走个{move}，小兵也得出来见见世面。",
                f"这步是{move}，稳稳往前顶一下。",
            ),
            "卒": (
                f"小卒往前拱，{move}，一步一步来。",
                f"先走个{move}，小卒过日子主打一个踏实。",
                f"这步是{move}，先往前顶一下。",
            ),
            "车": (
                f"大车开出来，{move}，重家伙开始活动。",
                f"这手出车，{move}，车可不能一直停在车库里。",
                f"走个{move}，给大车换条线路。",
            ),
            "炮": (
                f"这门炮动一动，{move}，先把炮架起来。",
                f"来一手{move}，炮台换个位置继续观察。",
                f"走个{move}，这门炮先找找角度。",
            ),
            "相": (
                f"补一手相，{move}，家里先收拾利索。",
                f"这步{move}，相走田，阵脚稳一稳。",
            ),
            "象": (
                f"补一手象，{move}，先把阵脚稳住。",
                f"这步{move}，象走田，家里照看一下。",
            ),
            "仕": (f"补个仕，{move}，先把家门看好。",),
            "士": (f"补个士，{move}，家门口多一个保安。",),
            "帅": (f"老帅挪一步，{move}，亲自换个位置。",),
            "将": (f"老将动一动，{move}，这回亲自挪窝。",),
        }
        variants = choices.get(piece, (
            f"这手走{move}，先看看局面怎么回应。",
            f"来，{move}，棋先这么下。",
        ))
        return variants[(max(1, int(number or 1)) - 1) % len(variants)]

    @classmethod
    def _casual_opponent_text(cls, notation, count=1):
        move = cls._spoken_notation(notation)
        variants = (
            f"那边回了个{move}，先记在心里，不急着下结论。",
            f"人家也没闲着，走的是{move}，咱们接着看。",
            f"这边应了一手{move}，有点意思，先往下走。",
        )
        return variants[(max(1, int(count or 1)) - 1) % len(variants)]

    @classmethod
    def _high_move_text(cls, notation):
        """Mandatory sub-three-second move call: action only, no side labels."""
        move = cls._spoken_notation(notation)
        action = {
            "马": "跳马", "车": "出车", "炮": "架炮",
            "兵": "拱兵", "卒": "拱卒", "相": "飞相", "象": "飞象",
            "仕": "补仕", "士": "补士", "帅": "动帅", "将": "动将",
        }.get(move[:1], "走子")
        return f"{action}，{move}。"

    @classmethod
    def _closing_move_text(cls, notation, number=0, forced=False):
        move = cls._spoken_notation(notation)
        if forced:
            variants = (
                f"这手{move}，网差不多收紧了。不过最后一步没落袋，香槟咱先别开。",
                f"走完{move}，胜利已经在门口敲门了，稳住，别自己给它关门外。",
            )
        else:
            variants = (
                f"这手{move}，局面已经很舒服了。不出意外应该能收下，不过棋没结束，先不贷款庆祝。",
                f"走个{move}，优势差不多攥在手里了。稳稳收官，别在终点线前表演平地摔。",
                f"这步{move}之后，赢面挺大。有请下一位挑战者先排队，这盘咱还差个句号。",
            )
        return variants[(max(1, int(number or 1)) - 1) % len(variants)]

    @staticmethod
    def _is_near_win(event):
        analysis = event.get("analysis") or {}
        try:
            score = int(analysis.get("score", 0))
        except (TypeError, ValueError):
            return False, False
        if analysis.get("score_type") == "mate":
            return score > 0, score > 0
        return analysis.get("score_type") == "cp" and score >= 600, False

    def _victory_text(self):
        self.victory_count = getattr(self, "victory_count", 0) + 1
        variants = (
            "拿下！这盘赢得不算轻松，四舍五入也算一场险胜。",
            "稳稳收下。有请下一位受害者——开个玩笑，下一位挑战者请入场。",
            "结束，棋盘已经打扫干净。下一位朋友，里面请。",
        )
        return variants[(self.victory_count - 1) % len(variants)]

    def submit_chess(self, event):
        if not getattr(getattr(self, "settings", None),
                       "commentary_enabled", True):
            return
        if event.get("event") == "game_over":
            self.pipeline.cancel_idle()
            if event.get("result") == "won":
                self.pipeline.submit(
                    0, "获胜收官", event,
                    direct_text=self._victory_text())
            elif (event.get("result") == "finished" and
                  event.get("reason") in (
                      "jj_result_screen", "delayed_jj_result_screen")):
                self.pipeline.submit(
                    0, "结算收官", event,
                    direct_text="结算页出来了，看样子这盘是收下了。行，棋盘擦一擦，接着迎客。")
            return
        notation = str(event.get("notation") or "").strip()
        if event.get("event") == "waiting_for_opponent":
            self.pipeline.open_idle_window()
            return
        if not notation:
            return
        if event.get("event") == "move_selected":
            self.pipeline.cancel_idle()
            payload = {
                "当前局面": event.get("fen_before"),
                "当前走子": notation,
                "speech_token": event.get("speech_token"),
                "要求": "只说这步直接意图；每句20字内；必须自然带出走法",
            }
            self.pipeline.prepare_move(
                "我方走棋播报", payload,
                direct_text=self._high_move_text(notation))
            return
        if event.get("event") == "move_applied":
            self.pipeline.commit_move(event.get("speech_token"))
            return
        if event.get("event") in ("move_rejected", "move_preview"):
            self.pipeline.discard_move(event.get("speech_token"))
            return
        if event.get("event") == "opponent_move":
            self.pipeline.cancel_idle()
            self.opponent_move_count = getattr(
                self, "opponent_move_count", 0) + 1
            payload = {"识别走法": notation,
                       "要求": "三秒内只播棋形动作，不使用阵营称谓"}
            self.pipeline.play_high(
                "对面落子短播报", payload,
                direct_text=self._high_move_text(notation))
            return
        payload = {"我方计划走法": notation,
                   "speech_token": event.get("speech_token"),
                   "子力盘点": event.get("material"),
                   "局势分析": event.get("summary"),
                   "候选方案": [
                       {"走法": item.get("move"),
                        "局面判断": item.get("assessment"),
                        "短变化": item.get("line", [])}
                       for item in event.get("candidates", [])[:3]],
                   "预测路线": [item.get("notation") for item in event.get("pv", [])[:4]]}
        if event.get("speech_token"):
            # The short call is already preheated. A separate explanation is
            # allowed to play only in the explicit opponent-wait window.
            self.pipeline.prepare_idle({
                **payload,
                "要求": ("作为等待落子时的自然补充，讲清刚才落子的意图和一条后续思路；"
                         "不用阵营称谓，不复述评分、深度或技术字段；控制在三句内"),
            })
            return
        near_win, forced_win = self._is_near_win(event)
        if near_win:
            self.pipeline.submit(
                1, "收官解说", payload,
                direct_text=self._closing_move_text(
                    notation, event.get("number", 0), forced_win))
        elif self._complex_chess_event(event):
            self.pipeline.submit(1, "我方走棋解说", payload)
        elif not event.get("speech_token"):
            # Compatibility with event logs produced before move_selected
            # gained the speech-sync token.
            self.pipeline.submit(
                1, "我方走棋播报", payload,
                direct_text=self._casual_move_text(
                    notation, event.get("number", 0)))

    def receive(self, event, force=False):
        kind = event.get("type", "chat")
        if kind == "status":
            self._notify("connection", event.get("text", ""), event)
            return
        user = str(event.get("user") or "观众")[:30]
        text = str(event.get("text") or "").strip()[:300]
        now = time.monotonic()
        key = str(event.get("id") or f"{kind}:{user}:{text}")
        self.seen = {k: timestamp for k, timestamp in self.seen.items()
                     if now - timestamp < 90}
        if key in self.seen:
            return
        self.seen[key] = now
        self._notify(kind, f"{user}：{text}", event)
        if not (self.enabled or force):
            return
        if kind == "gift" and self.settings.gift_enabled:
            self._queue_gift(user, event.get("gift") or text,
                             event.get("count", 1), force=force)
        elif kind == "chat":
            prefix = self.settings.prefix.strip()
            if prefix and not text.startswith(prefix) and not force:
                return
            question = text.removeprefix(prefix).lstrip(" ：:,，") if prefix else text
            if question:
                # Same priority as move commentary: preserve the real arrival
                # order so a viewer question can naturally enter between moves.
                self.pipeline.submit(1, "弹幕回复", {"观众": user, "弹幕": question})
        elif (kind == "member" and self.settings.welcome_enabled and
              (event.get("user_id") or user) not in self.welcomed and
              now - self.last_welcome >= 8):
            self.welcomed.add(event.get("user_id") or user)
            self.last_welcome = now
            self.pipeline.submit(4, "欢迎观众", {"观众": user})

    def _queue_gift(self, user, gift, count, force=False):
        key = (str(user), str(gift))
        try:
            count = max(1, int(count))
        except (TypeError, ValueError):
            count = 1
        with self.gift_lock:
            previous, previous_force = self.pending_gifts.get(key, (0, False))
            self.pending_gifts[key] = (max(previous, count),
                                       bool(previous_force or force))
            timer = self.gift_timers.pop(key, None)
            if timer:
                timer.cancel()
            timer = threading.Timer(1.2, self._flush_gift, args=(key,))
            timer.daemon = True
            self.gift_timers[key] = timer
            timer.start()

    def _flush_gift(self, key):
        with self.gift_lock:
            pending = self.pending_gifts.pop(key, None)
            self.gift_timers.pop(key, None)
        if pending is None:
            return
        count, force = pending
        if self.enabled or force:
            self.pipeline.submit(0, "礼物答谢", {
                "观众": key[0], "礼物": key[1], "数量": count})

    def test_chat(self, user, text):
        self.receive({"type": "chat", "id": f"test-{time.time_ns()}",
                      "user": user or "测试观众", "text": text}, force=True)

    def test_gift(self, user="测试观众", gift="小心心"):
        self.receive({"type": "gift", "id": f"test-{time.time_ns()}",
                      "user": user, "text": f"送出 {gift} × 1",
                      "gift": gift, "count": 1}, force=True)

    def speak_intro(self):
        self.pipeline.submit(0, "自我介绍", {}, direct_text=self.settings.introduction)

    def connect(self, room):
        room = normalize_room(room)
        self.disconnect(silent=True)
        runtime = Path(r"D:\environment\shuzhiren\runtime\douyin-env\Scripts\python.exe")
        if not runtime.is_file():
            raise RuntimeError("抖音弹幕连接器运行环境不存在")
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        process = subprocess.Popen(
            [str(runtime), "-u", str(HERE / "douyin_bridge.py"), "--room", room],
            cwd=str(HERE), env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        self.connector = process
        self.connector_epoch += 1
        epoch = self.connector_epoch
        self.settings.room = room
        save_settings(self.settings)
        threading.Thread(target=self._read_connector,
                         args=(process, epoch), daemon=True).start()
        self._notify("connection", "正在连接抖音直播间")

    def _read_connector(self, process, epoch):
        try:
            for line in process.stdout:
                if epoch != self.connector_epoch:
                    break
                if line.startswith("CHESS_LIVE_EVENT ") and len(line) < 10000:
                    try:
                        self.receive(json.loads(line[len("CHESS_LIVE_EVENT "):]))
                    except (ValueError, TypeError):
                        pass
            process.wait()
        finally:
            if process.stdout:
                process.stdout.close()
            if epoch == self.connector_epoch:
                self._notify("connection", "抖音连接器已停止")

    def disconnect(self, silent=False):
        self.connector_epoch += 1
        process, self.connector = self.connector, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        if not silent:
            self._notify("connection", "已断开抖音直播间")

    def close(self):
        self.disconnect(silent=True)
        with self.gift_lock:
            for timer in self.gift_timers.values():
                timer.cancel()
            self.gift_timers.clear()
            self.pending_gifts.clear()
        self.tailer.close()
        self.pipeline.close()


class ChessEventTailer(threading.Thread):
    def __init__(self, path, callback):
        super().__init__(daemon=True)
        self.path, self.callback = Path(path), callback
        self.closed = threading.Event()

    def run(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        with open(self.path, encoding="utf-8", errors="replace") as stream:
            stream.seek(0, os.SEEK_END)
            while not self.closed.is_set():
                line = stream.readline()
                if not line:
                    time.sleep(0.08)
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event") in (
                        "move_selected", "move_applied", "move_rejected",
                        "move_preview", "engine_analysis", "opponent_move",
                        "waiting_for_opponent", "game_over"):
                    self.callback(event)

    def close(self):
        self.closed.set()
