"""Audio-only live interaction, independent from the chess control loop."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import heapq
import json
import os
from pathlib import Path

from runtime_status import rotate_log
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import winsound

from bailian_tts import available_voices, synthesize
from glm_client import DEFAULT_MODEL, respond
from opening_library import load_opening_library


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "live_config.json"
CHESS_EVENTS = HERE / "work" / "events.jsonl"
LIVE_LOG = HERE / "work" / "live_events.jsonl"
SPEECH_STARTED = HERE / "work" / "speech_started.json"
SPEECH_PREPARING = HERE / "work" / "speech_preparing.json"
CHESS_SPEECH_KINDS = {
    "我方走棋播报", "我方走棋解说", "对手走棋播报", "对手走棋解说",
    "关键走棋解说", "关键应手解说", "等待期长讲解", "应手预判",
    "开局定式解说",
    "沙盘推演解说", "收官解说", "获胜收官", "结算收官",
}
OWN_SPEECH_KINDS = {
    "我方走棋播报", "关键走棋解说", "沙盘推演解说",
    "获胜收官", "结算收官",
}

COMMENTARY_DENSITY = {
    # importance threshold, silence filler seconds, quiet moves before filler
    "安静": (8, 26.0, 5),
    "自然": (5, 16.0, 3),
    "热闹": (3, 9.0, 2),
}


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
    commentary_density: str = "自然"
    sandtable_enabled: bool = True


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
    def _mark_speech_preparing(payload, enabled):
        token = str(payload.get("speech_token") or "").strip()
        if not token:
            return
        try:
            SPEECH_PREPARING.parent.mkdir(parents=True, exist_ok=True)
            temporary = SPEECH_PREPARING.with_suffix(".tmp")
            temporary.write_text(json.dumps({
                "token": token,
                "enabled": bool(enabled),
                "deferred": bool(payload.get("沙盘路线")),
                "created": time.time(),
            }, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, SPEECH_PREPARING)
        except OSError:
            pass

    @staticmethod
    def _clean_text(text):
        return re.sub(r"[\r\n*_#`]+", "", str(text)).strip()

    @staticmethod
    def _own_side(payload):
        side = str(payload.get("主播执棋") or "").strip()
        if side in ("红方", "黑方"):
            return side
        notation = str(payload.get("我方计划走法") or
                       payload.get("当前走子") or "").strip()
        if notation.startswith("红方 "):
            return "红方"
        if notation.startswith("黑方 "):
            return "黑方"
        return ""

    @classmethod
    def _first_person_text(cls, text, payload):
        """Keep internal side facts, but never read them like a neutral robot."""
        cleaned = cls._clean_text(text)
        cleaned = re.sub(r"^(?:棋小雅|主播|解说)\s*[：:]\s*", "", cleaned)
        own = cls._own_side(payload)
        other = "黑方" if own == "红方" else "红方" if own == "黑方" else ""
        if own:
            cleaned = cleaned.replace(own, "咱们")
        if other:
            cleaned = cleaned.replace(other, "对面")
        return (cleaned.replace("我方", "咱们")
                .replace("对方", "对面")
                .replace("对手", "对面"))

    @classmethod
    def _short_chess_text(cls, text):
        """Keep at most two complete sentences; never cut speech mid-thought."""
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
            result.append(body + punctuation)
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
        self._mark_speech_preparing(payload, self.settings.speech_enabled)
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
            text = self._first_person_text(text, payload)
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
                self.notify("speech_status", "落子即将执行，播放预热语音", payload)
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
                self.notify("speech_status", "落子即将执行，仅显示文本", payload)
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

    def prepare_idle(self, payload, kind="等待期长讲解", direct_text=None):
        """Pre-generate low-priority speech; play only in a wait window."""
        self.cancel_idle()
        state = {
            "cancel": threading.Event(), "window": threading.Event(),
            "epoch": self.cancel_epoch, "payload": dict(payload),
            "kind": str(kind), "direct_text": direct_text,
        }
        with self.idle_lock:
            self.idle_state = state
        threading.Thread(
            target=self._prepare_idle_worker, args=(state,),
            name="chess-idle-prewarm", daemon=True).start()
        self.notify("queued", f"{kind}正在预生成", payload)
        return True

    def _prepare_idle_worker(self, state):
        audio = None
        payload = state["payload"]
        kind = state.get("kind", "等待期长讲解")
        try:
            text = state.get("direct_text") or self.llm(
                kind, {"主播设定": self.settings.persona, **payload},
                list(self.history), model=self.settings.model)
            text = self._first_person_text(text, payload)
            text = (self._short_chess_text(text) if kind == "应手预判"
                    else text[:120])
            if not text or state["cancel"].is_set():
                return
            if self.settings.speech_enabled:
                audio = self.tts(text, self.settings.voice)
            while not state["cancel"].is_set():
                if state["window"].wait(0.1):
                    break
            if state["cancel"].is_set() or state["epoch"] != self.cancel_epoch:
                return
            self.notify("reply", text, {"kind": kind, **payload})
            if audio and self.settings.speech_enabled:
                self.notify("speech_status", f"等待落子，播放{kind}", payload)
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
                self.notify("speech_status", f"{kind}仅显示文本", payload)
            self.history.extend([
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                {"role": "assistant", "content": text},
            ])
        except Exception as error:
            if not state["cancel"].is_set():
                self.notify("error", str(error)[:200], {"kind": kind})
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
                text = self._first_person_text(text, payload)[:300]
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
        self.quiet_move_count = 0
        self.next_prediction_move = 4
        self.prediction_count = 0
        self.opening_moves = []
        self.opening_announced = set()
        self.opening_library = load_opening_library()
        self.last_chess_spoken_at = time.monotonic()
        self.pending_move_events = {}
        self.closing_announced = False
        self.sandtable_active = False
        self.sandtable_timer = None
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
                rotate_log(str(LIVE_LOG), max_bytes=8 * 1024 * 1024,
                           backups=2)
                with open(LIVE_LOG, "a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
        try:
            self.notify(record)
        except Exception:
            pass
        spoken_kind = str((data or {}).get("kind") or "")
        if kind == "reply" and spoken_kind in CHESS_SPEECH_KINDS:
            self.last_chess_spoken_at = time.monotonic()
            self.quiet_move_count = 0

    def configure(self, **values):
        was_speech_enabled = self.settings.speech_enabled
        was_sandtable_enabled = self.settings.sandtable_enabled
        was_commentary_enabled = self.settings.commentary_enabled
        for key, value in values.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.settings.cooldown = max(0.0, min(30.0, float(self.settings.cooldown)))
        if self.settings.commentary_density not in COMMENTARY_DENSITY:
            self.settings.commentary_density = "自然"
        if self.settings.voice not in available_voices():
            self.settings.voice = available_voices()[0]
        save_settings(self.settings)
        if was_speech_enabled and not self.settings.speech_enabled:
            self.pipeline.stop_audio()
        if ((was_sandtable_enabled and not self.settings.sandtable_enabled) or
                (was_commentary_enabled and
                 not self.settings.commentary_enabled)):
            self._close_sandtable("功能已关闭")
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

    def _density_rule(self):
        density = getattr(getattr(self, "settings", None),
                          "commentary_density", "自然")
        return COMMENTARY_DENSITY.get(density, COMMENTARY_DENSITY["自然"])

    def _commentary_importance(self, event):
        """Score whether a move deserves airtime; update score trend once."""
        notation = str(event.get("notation") or "")
        importance = 0
        reasons = []
        if "吃" in notation:
            captured = notation.rsplit("吃", 1)[-1][:1]
            value = {"车": 9, "马": 7, "炮": 7, "相": 5, "象": 5,
                     "仕": 5, "士": 5, "兵": 4, "卒": 4}.get(captured, 6)
            importance = max(importance, value)
            reasons.append("吃子")
        analysis = event.get("analysis") or {}
        score_type = analysis.get("score_type")
        try:
            score = int(analysis.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        if event.get("number") == 1:
            self.last_engine_score = None
            self.closing_announced = False
        previous = getattr(self, "last_engine_score", None)
        if score_type in ("cp", "mate"):
            self.last_engine_score = score
        if score_type == "mate":
            importance = 10
            reasons.append("杀棋")
        elif score_type == "cp":
            if abs(score) >= 600:
                importance = max(importance, 8)
                reasons.append("胜负趋势")
            elif abs(score) >= 300:
                importance = max(importance, 5)
                reasons.append("优劣明显")
            if previous is not None:
                swing = abs(score - previous)
                if swing >= 180:
                    importance = max(importance, 7)
                    reasons.append("局势翻转")
                elif swing >= 90:
                    importance = max(importance, 4)
                    reasons.append("局势波动")
        summary = str(event.get("summary") or "")
        if any(word in summary for word in ("将死", "杀棋", "将军", "危险")):
            importance = max(importance, 8)
            reasons.append("战术危险")
        return importance, list(dict.fromkeys(reasons))

    def _needs_silence_filler(self):
        _, silence_seconds, quiet_limit = self._density_rule()
        quiet_moves = getattr(self, "quiet_move_count", 0)
        last_spoken = getattr(self, "last_chess_spoken_at", time.monotonic())
        return (quiet_moves >= quiet_limit or
                time.monotonic() - last_spoken >= silence_seconds)

    def _prediction_due(self, move_number):
        """Occasionally predict one reply without turning every move into a tip."""
        try:
            number = int(move_number or 0)
        except (TypeError, ValueError):
            return False
        next_move = int(getattr(self, "next_prediction_move", 4))
        if number < next_move:
            return False
        count = int(getattr(self, "prediction_count", 0)) + 1
        self.prediction_count = count
        # A deterministic 4/5/6-move rhythm feels occasional while keeping
        # tests and recorded replays reproducible.
        self.next_prediction_move = number + (4, 5, 6)[count % 3]
        return True

    @classmethod
    def _engine_prediction_text(cls, notation, count=1):
        """Phrase Pikafish's first reply; never infer or replace the move."""
        move = cls._spoken_notation(notation)
        variants = (
            f"我猜他可能走{move}，看看押不押得中。",
            f"这回我押{move}，八成会往这边走。",
            f"我先预判个{move}，等他揭晓答案。",
        )
        return variants[(max(1, int(count or 1)) - 1) % len(variants)]

    def _reset_opening(self):
        self.opening_moves = []
        self.opening_announced = set()

    @classmethod
    def _opening_matches(cls, moves):
        """Return conservative opening names proved by verified move text."""
        clean = [str(item or "").strip() for item in moves]
        red = {cls._spoken_notation(item) for item in clean
               if item.startswith("红方 ")}
        black = {cls._spoken_notation(item) for item in clean
                 if item.startswith("黑方 ")}
        first = cls._spoken_notation(clean[0]) if clean else ""
        matches = []

        central_cannon = first in ("炮二平五", "炮八平五")
        screen_horses = {"马2进3", "马8进7"}.issubset(black)
        if central_cannon and screen_horses:
            matches.append((
                "中炮对屏风马",
                "布局定型了，中炮对屏风马，经典老对手。"))

        if first == "兵七进一" and "炮2平3" in black:
            matches.append((
                "仙人指路对卒底炮",
                "这是仙人指路对卒底炮，开局先斗个机灵。"))
        elif first == "兵七进一" and "卒3进1" in black:
            matches.append((
                "仙人指路对挺卒",
                "仙人指路碰上挺卒，双方先把问题摆桌上。"))

        if {"兵三进一", "兵七进一"}.issubset(red):
            matches.append((
                "红方两头蛇",
                "两边兵都拱起来了，这是两头蛇的架势。"))
        if {"卒3进1", "卒7进1"}.issubset(black):
            matches.append((
                "黑方两头蛇",
                "两翼小卒一起出门，这边也摆出两头蛇。"))

        if not matches and len(clean) == 1:
            initial = {
                "炮二平五": ("中炮", "中炮开门，先把中路话语权拿住。"),
                "炮八平五": ("中炮", "中炮开门，先把中路话语权拿住。"),
                "兵七进一": ("仙人指路", "仙人指路起手，先问问这边怎么接。"),
                "相三进五": ("飞相局", "飞相局起手，先把家里收拾稳当。"),
                "相七进五": ("飞相局", "飞相局起手，先把家里收拾稳当。"),
                "马二进三": ("起马局", "这盘起马局开场，先让马儿出来上班。"),
                "马八进七": ("起马局", "这盘起马局开场，先让马儿出来上班。"),
                "炮二平六": ("过宫炮", "过宫炮开局，炮先横过来照看全场。"),
                "炮八平四": ("过宫炮", "过宫炮开局，炮先横过来照看全场。"),
                "炮二平四": ("士角炮", "士角炮起手，先从侧面搭个架子。"),
                "炮八平六": ("士角炮", "士角炮起手，先从侧面搭个架子。"),
            }.get(first)
            if initial:
                matches.append(initial)
        return matches

    def _record_opening_move(self, notation):
        moves = getattr(self, "opening_moves", None)
        if moves is None:
            self._reset_opening()
            moves = self.opening_moves
        if len(moves) >= 16 or not notation:
            return None
        moves.append(str(notation))
        announced = getattr(self, "opening_announced", set())
        self.opening_announced = announced
        library = getattr(self, "opening_library", None)
        found = library.lookup(moves) if library is not None else None
        for name, text in self._opening_matches(moves):
            if name in announced:
                continue
            announced.add(name)
            evidence = {"布局名称": name, "判定依据": list(moves)}
            if found:
                evidence.update({
                    "参考棋谱数": found.get("games"),
                    "棋谱分类": found.get("name"),
                    "开局编号": found.get("ecco"),
                    "资料来源": "CCPD大师棋谱",
                })
            self.pipeline.submit(
                2, "开局定式解说", evidence,
                direct_text=text)
            return name
        if found:
            name = str(found.get("name") or "").strip()
            canonical = name.removesuffix("局").removesuffix("系列")
            known = {
                item.removesuffix("局").removesuffix("系列")
                for item in announced
            }
            if name and canonical not in known:
                announced.add(name)
                text = f"走到这里，棋谱库把这路归在{name}。"
                self.pipeline.submit(
                    2, "开局定式解说",
                    {"布局名称": name,
                     "匹配手数": found.get("matched_plies"),
                     "参考棋谱数": found.get("games"),
                     "资料来源": "CCPD大师棋谱"},
                    direct_text=text)
                return name
        return None

    @classmethod
    def _important_reply_text(cls, notation):
        move = cls._spoken_notation(notation)
        captured = move.rsplit("吃", 1)[-1][:1] if "吃" in move else ""
        if captured == "车":
            return "大车丢了，局面得重新算。"
        if captured in ("马", "炮"):
            return f"这手拿下{captured}，局面有变化了。"
        if "吃" in move:
            return "这手先吃到东西，得重新盘一下。"
        return ""

    @staticmethod
    def _kill_confidence(event):
        """Return a conservative attack confidence, not a fake engine probability."""
        analysis = event.get("analysis") or {}
        try:
            score = int(analysis.get("score", 0))
            depth = int(analysis.get("depth", 0))
        except (TypeError, ValueError):
            return 0
        if analysis.get("score_type") == "mate":
            return 100 if score > 0 else 0
        if analysis.get("score_type") != "cp" or score <= 0:
            return 0
        # A cp score alone is not a probability.  Require useful depth and a
        # very large stable advantage, then expose it as "kill confidence".
        confidence = round(50 + max(0, score - 200) / 20)
        if depth < 12:
            confidence -= 12
        return max(0, min(95, confidence))

    @classmethod
    def _sandtable_plan(cls, event):
        notation = str(event.get("notation") or "")
        big_capture = any(f"吃{piece}" in notation for piece in "车马炮")
        confidence = cls._kill_confidence(event)
        route = []
        for item in event.get("pv", [])[:5]:
            ucci = str(item.get("ucci") or "")
            text = str(item.get("notation") or "")
            if re.fullmatch(r"[a-i][0-9][a-i][0-9]", ucci) and text:
                route.append({"ucci": ucci, "notation": text})
        executed = str(event.get("ucci") or "")
        if route and route[0]["ucci"] == executed:
            route.pop(0)
        route = route[:3]
        if len(route) < 3 or not (big_capture or confidence >= 80):
            return None
        reasons = []
        if big_capture:
            reasons.append("吃掉车马炮大子")
        if confidence >= 80:
            reasons.append(f"杀势置信度 {confidence}%")
        return {
            "action": "show",
            "title": "关键战术沙盘",
            "reason": " · ".join(reasons),
            "confidence": confidence,
            "route": route,
            "review_seconds": 6.5,
            "speech_token": event.get("speech_token"),
        }

    def _close_sandtable(self, reason):
        timer = getattr(self, "sandtable_timer", None)
        if timer is not None:
            timer.cancel()
            self.sandtable_timer = None
        if getattr(self, "sandtable_active", False):
            self.sandtable_active = False
            self._notify("sandtable", "沙盘已收起", {
                "action": "close", "reason": reason})

    def _schedule_sandtable_close(self, plan):
        timer = getattr(self, "sandtable_timer", None)
        if timer is not None:
            timer.cancel()
        delay = (len(plan.get("route", [])) * 0.52 +
                 float(plan.get("review_seconds", 6.5)) + 0.15)
        timer = threading.Timer(
            delay, self._close_sandtable, args=("回看窗口结束",))
        timer.daemon = True
        self.sandtable_timer = timer
        timer.start()

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

    @classmethod
    def _synced_move_text(cls, notation, reasons=(), number=0,
                          sandtable=False):
        """Fast first-person line: no LLM wait on the move-critical path."""
        move = cls._spoken_notation(notation)
        captured = move.rsplit("吃", 1)[-1][:1] if "吃" in move else ""
        if captured in "车马炮":
            follow = ("来，沙盘摆三手，看看怎么把优势接住。"
                      if sandtable else "这波交换挺划算。")
            return f"好家伙，咱们走{move}，直接收下这枚{captured}。{follow}"
        action = {
            "马": "跳马", "车": "开车", "炮": "架炮", "兵": "拱兵",
            "卒": "拱卒", "相": "飞相", "象": "飞象", "仕": "补仕",
            "士": "补士", "帅": "挪帅", "将": "挪将",
        }.get(move[:1], "走一手")
        reason = next((str(item) for item in reasons if item), "先把位置站住")
        return f"咱们先{action}，{move}。{reason}，接着看。"

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
        if event.get("event") == "start":
            self._reset_opening()
            return
        if event.get("event") == "game_sandbox":
            action = event.get("action")
            token = str(event.get("speech_token") or "")
            if action == "start":
                route = event.get("route") or []
                self._notify(
                    "status",
                    f"JJ游戏沙盘开始演示关键路线（{len(route)}步）")
            elif action == "entered":
                # The explanation was generated while the engine was thinking,
                # but is released only after JJ visibly entered its real
                # sandbox.  This keeps speech aligned with the demonstration.
                self.pipeline.commit_move(token)
                getattr(self, "sandbox_speech_tokens", set()).discard(token)
                self._notify("status", "沙盘已打开，开始同步推演解说")
            elif action == "end":
                if not event.get("entered"):
                    self.pipeline.discard_move(token)
                getattr(self, "sandbox_speech_tokens", set()).discard(token)
                if event.get("entered"):
                    self._notify(
                        "status",
                        f"JJ游戏沙盘{event.get('reason', '演示结束')}，已回到实战")
                else:
                    self._notify(
                        "status",
                        f"JJ游戏沙盘已跳过：{event.get('reason', '界面未确认')}")
            return
        if event.get("event") == "game_over":
            self.pipeline.cancel_idle()
            for token in list(getattr(self, "sandbox_speech_tokens", set())):
                self.pipeline.discard_move(token)
            getattr(self, "sandbox_speech_tokens", set()).clear()
            self._close_sandtable("对局结束")
            self.pending_move_events = {}
            self.closing_announced = False
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
            # This event is emitted before move_command/ADB, so it is the hard
            # lifecycle boundary requested for the previous sandbox.
            self._close_sandtable("下一次主动落子前")
            self.pipeline.cancel_idle()
            token = str(event.get("speech_token") or "")
            if token:
                pending = getattr(self, "pending_move_events", None)
                if pending is None:
                    pending = self.pending_move_events = {}
                pending[token] = dict(event)
                if len(pending) > 4:
                    oldest = next(iter(pending))
                    pending.pop(oldest, None)
            return
        if event.get("event") == "move_command":
            token = str(event.get("speech_token") or "")
            # Normal move speech starts before ADB taps.  Sandtable speech is
            # held until JJ's sandbox is visibly open.
            if token and token not in getattr(
                    self, "sandbox_speech_tokens", set()):
                self.pipeline.commit_move(token)
            return
        if event.get("event") == "move_applied":
            token = str(event.get("speech_token") or "")
            getattr(self, "pending_move_events", {}).pop(
                token, None)
            self._record_opening_move(notation)
            return
        if event.get("event") in ("move_rejected", "move_preview"):
            token = str(event.get("speech_token") or "")
            self.pipeline.discard_move(token)
            getattr(self, "sandbox_speech_tokens", set()).discard(token)
            getattr(self, "pending_move_events", {}).pop(
                str(event.get("speech_token") or ""), None)
            return
        if event.get("event") == "opponent_move":
            self.pipeline.cancel_idle()
            # If no sandbox-entered event arrived before the real reply, the
            # prepared route no longer belongs to the visible position.
            for token in list(getattr(self, "sandbox_speech_tokens", set())):
                self.pipeline.discard_move(token)
            getattr(self, "sandbox_speech_tokens", set()).clear()
            if getattr(self, "sandtable_active", False):
                timer = getattr(self, "sandtable_timer", None)
                if timer is not None:
                    timer.cancel()
                    self.sandtable_timer = None
                self._notify("sandtable", "实战落子已出现，沙盘暂停刷新", {
                    "action": "pause", "notation": notation})
            self.opponent_move_count = getattr(
                self, "opponent_move_count", 0) + 1
            self.quiet_move_count = getattr(self, "quiet_move_count", 0) + 1
            important_text = self._important_reply_text(notation)
            if important_text:
                # Keep the tactical note visible, but do not let a slow TTS
                # request for the opponent's previous move spill over our
                # next active move.  Own-move speech has the audio channel.
                self._notify("chess_note", important_text, {
                    "notation": notation, "spoken": False})
            self._record_opening_move(notation)
            return
        payload = {"我方计划走法": notation,
                   "主播执棋": ("红方" if str(event.get("side")) == "r"
                                else "黑方" if str(event.get("side")) == "b"
                                else ""),
                   "speech_token": event.get("speech_token"),
                   "子力盘点": event.get("material"),
                   "局势分析": event.get("summary"),
                   "候选方案": [
                       {"走法": item.get("move"),
                        "局面判断": item.get("assessment"),
                        "短变化": item.get("line", [])}
                       for item in event.get("candidates", [])[:3]],
                   "预测路线": [item.get("notation") for item in event.get("pv", [])[:4]]}
        sandtable = (self._sandtable_plan(event)
                     if getattr(getattr(self, "settings", None),
                                "sandtable_enabled", True) else None)
        if sandtable:
            payload["沙盘路线"] = [
                item["notation"] for item in sandtable["route"]]
            payload["沙盘触发"] = sandtable["reason"]
            payload["沙盘起因走法"] = notation
            if token := str(event.get("speech_token") or ""):
                tokens = getattr(self, "sandbox_speech_tokens", None)
                if tokens is None:
                    tokens = self.sandbox_speech_tokens = set()
                tokens.add(token)
        near_win, forced_win = self._is_near_win(event)
        importance, reasons = self._commentary_importance(event)
        if sandtable:
            importance = max(importance, 10)
            reasons.append("沙盘推演")
        threshold, _, _ = self._density_rule()
        token = str(event.get("speech_token") or "")
        if near_win and not getattr(self, "closing_announced", False):
            importance = max(importance, 8)
            reasons.append("收官")
            self.closing_announced = True
        elif (near_win and getattr(self, "closing_announced", False) and
              importance <= 8 and set(reasons).issubset({"胜负趋势"})):
            # Avoid repeating the same "almost won" line every move.  A later
            # capture, tactical swing or mate signal can still break through.
            importance = 0
        if importance >= threshold:
            self.quiet_move_count = 0
            important_payload = {
                **payload,
                "讲解重点": reasons or ["局势变化"],
                "要求": ("像主播临场点评，只讲这手为什么值得注意；"
                         "不必报完整棋谱，不用我方或对方开头；最多两句")
            }
            direct = None
            kind = "沙盘推演解说" if sandtable else "关键走棋解说"
            if near_win:
                if sandtable:
                    kind = "沙盘推演解说"
                else:
                    kind = "收官解说"
                    direct = self._closing_move_text(
                        notation, event.get("number", 0), forced_win)
            elif importance >= threshold:
                direct = self._synced_move_text(
                    notation, reasons, event.get("number", 0),
                    sandtable=bool(sandtable))
            if token:
                self.pipeline.prepare_move(kind, important_payload,
                                           direct_text=direct)
            else:
                self.pipeline.submit(1, kind, important_payload,
                                     direct_text=direct)
            return
        prediction_route = event.get("pv", [])
        predicted_reply = (prediction_route[1]
                           if len(prediction_route) >= 2 else None)
        if (not sandtable and not near_win and predicted_reply and
                self._prediction_due(event.get("number"))):
            self.quiet_move_count = 0
            self.pipeline.prepare_idle({
                **payload,
                "引擎预测应手": predicted_reply.get("notation"),
                "预测依据": "Pikafish主变化中的第一应手",
            }, kind="应手预判", direct_text=self._engine_prediction_text(
                predicted_reply.get("notation"), self.prediction_count))
            return
        self.quiet_move_count = getattr(self, "quiet_move_count", 0) + 1
        if self._needs_silence_filler():
            self.pipeline.prepare_idle({
                **payload,
                "要求": ("现在是等棋空档，不要念走法；用两句口语讲当前的主要思路"
                         "和接下来值得看的一个点，不念评分或技术字段"),
            })

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
        self._close_sandtable("程序关闭")
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
        position = self.path.stat().st_size
        while not self.closed.is_set():
            try:
                size = self.path.stat().st_size
                # EventLog may rotate the file between games.  Starting at
                # zero on a smaller replacement preserves the live feed.
                if size < position:
                    position = 0
                found = False
                with open(self.path, encoding="utf-8", errors="replace") as stream:
                    stream.seek(position)
                    for line in stream:
                        found = True
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("event") in (
                                "start",
                                "move_selected", "move_command", "move_applied", "move_rejected",
                                "move_preview", "engine_analysis", "opponent_move",
                                "game_sandbox",
                                "waiting_for_opponent", "game_over"):
                            self.callback(event)
                    position = stream.tell()
                if not found:
                    time.sleep(0.08)
            except OSError:
                # A rotation can briefly leave no active file between rename
                # and append.  Re-open on the next short polling cycle.
                position = 0
                time.sleep(0.08)

    def close(self):
        self.closed.set()
