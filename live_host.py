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
from urllib.request import Request, urlopen
import winsound
import wave

from bailian_tts import available_voices, synthesize
from glm_client import DEFAULT_MODEL, respond
from opening_library import load_opening_library
from commentary_text import complete_note, connected_move_note, waiting_note, confirmed_win_note
from mate_patterns import build_finisher, confirmed_finisher_note, finisher_copy, MATE_OPENINGS
from chat_selection import ChatSelector, viewer_key
from interaction_text import welcome_line, spoken_reply, gift_reply
from commentary_style import CommentaryStyle, DEFAULT_PERSONA, LEGACY_PERSONAS, piece_amount, humor_enabled


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "live_config.json"
CHESS_EVENTS = HERE / "work" / "events.jsonl"
LIVE_LOG = HERE / "work" / "live_events.jsonl"
SPEECH_STARTED = HERE / "work" / "speech_started.json"
SPEECH_PREPARING = HERE / "work" / "speech_preparing.json"
SPEECH_PRIORITY = HERE / "work" / "speech_priority.json"
CHESS_SPEECH_KINDS = {
    "我方走棋播报", "我方走棋解说", "对手走棋播报", "对手走棋解说",
    "关键走棋解说", "关键应手解说", "等待期长讲解", "应手预判",
    "开局定式解说", "绝杀长解说",
    "沙盘推演解说", "QUEEN式等待期解说", "收官解说", "获胜收官", "结算收官",
}
INTERACTION_SPEECH_KINDS = {"弹幕回复", "欢迎观众", "礼物答谢"}
OWN_SPEECH_KINDS = {
    "我方走棋播报", "关键走棋解说", "沙盘推演解说",
    "获胜收官", "结算收官", "绝杀长解说",
}

COMMENTARY_DENSITY = {
    # importance threshold, silence filler seconds, quiet moves before filler
    "安静": (8, 26.0, 5),
    "自然": (5, 16.0, 3),
    "热闹": (3, 9.0, 2),
}


def room_input(value):
    """Validate pasted input without making network requests."""
    value = str(value or "").strip()
    if re.fullmatch(r"\d{6,25}", value):
        return value
    urls = re.findall(r"https?://[^\s<>\[\]，。]+", value)
    if len(urls) != 1:
        raise ValueError("请输入抖音直播间链接或数字房间号")
    parsed = urlsplit(urls[0])
    if parsed.scheme != "https" or parsed.hostname not in (
            "live.douyin.com", "v.douyin.com", "webcast.amemv.com"):
        raise ValueError("请输入抖音直播间链接或数字房间号")
    if parsed.hostname == "live.douyin.com" and not re.fullmatch(
            r"/\d{6,25}/?", parsed.path):
        raise ValueError("直播间链接中没有有效房间号")
    if parsed.hostname == "webcast.amemv.com" and not re.fullmatch(
            r"/douyin/webcast/reflow/\d{6,25}/?", parsed.path):
        raise ValueError("分享链接没有有效直播间编号")
    if parsed.hostname == "v.douyin.com" and not re.fullmatch(
            r"/[A-Za-z0-9_-]+/?", parsed.path):
        raise ValueError("请输入完整的抖音直播分享短链接")
    return urls[0]


def normalize_room(value):
    value = room_input(value)
    if value.isdigit():
        return value
    parsed = urlsplit(value)
    if parsed.hostname == "v.douyin.com":
        try:
            request = Request(value, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=10) as response:
                parsed = urlsplit(response.url)
                page = response.read(1024 * 1024).decode("utf-8", "ignore")
        except Exception as error:
            raise ValueError("抖音短链接解析失败，请确认链接仍有效") from error
    if parsed.hostname == "webcast.amemv.com":
        match = re.fullmatch(r"/douyin/webcast/reflow/(\d{6,25})/?", parsed.path)
        if not match:
            raise ValueError("分享链接没有有效直播间编号")
        if "page" not in locals():
            try:
                request = Request(value, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(request, timeout=10) as response:
                    page = response.read(1024 * 1024).decode("utf-8", "ignore")
            except Exception as error:
                raise ValueError("直播分享页无法读取") from error
        # Reflow's path is the internal room ID; this connector needs the
        # anchor's webRid, which the share page embeds in its room data.
        web_rid = re.search(r'webRid\\?":\\?"(\d{6,25})', page)
        if not web_rid:
            raise ValueError("分享页未提供网页版直播间编号，请复制直播间网页地址")
        return web_rid.group(1)
    if parsed.hostname != "live.douyin.com":
        raise ValueError("短链接没有跳转到可识别的抖音直播间")
    room = parsed.path.strip("/").split("/")[0]
    if not re.fullmatch(r"\d{6,25}", room):
        raise ValueError("直播间链接中没有有效房间号")
    return room


@dataclass
class LiveSettings:
    room: str = ""
    model: str = DEFAULT_MODEL
    voice: str = "Maia"
    prefix: str = ""  # Legacy config field; automatic sampling never requires it.
    cooldown: float = 1.0
    persona: str = DEFAULT_PERSONA
    introduction: str = "大家好，我是棋小雅。这里会一边下象棋，一边和大家聊棋、聊生活，欢迎来到直播间。"
    welcome_enabled: bool = True
    gift_enabled: bool = True
    speech_enabled: bool = False
    commentary_enabled: bool = True
    commentary_density: str = "自然"
    sandtable_enabled: bool = True
    queen_commentary_enabled: bool = True


def load_settings():
    settings = LiveSettings()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    for key in asdict(settings):
        if key in data:
            setattr(settings, key, data[key])
    if settings.persona in LEGACY_PERSONAS:
        settings.persona = DEFAULT_PERSONA
    return settings


def save_settings(settings):
    temporary = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False,
                                        indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, CONFIG_PATH)
    finally:
        temporary.unlink(missing_ok=True)


class SpeechPipeline:
    def _recently_spoken(self, text):
        normalized = re.sub(r"[\s，。！？、；：,.!?]", "", text)
        if not normalized:
            return True
        recent = [item.get("content", "") for item in list(self.history)[-12:]
                  if item.get("role") == "assistant"]
        return any(re.sub(r"[\s，。！？、；：,.!?]", "", old) == normalized
                   for old in recent)
    @staticmethod
    def _chess_line_valid(text, kind, payload):
        if kind not in CHESS_SPEECH_KINDS:
            return True
        generic = ("改善棋子位置", "棋子更活跃", "子力更活跃",
                   "看对面怎么应对", "看看对面怎么应对", "看人家怎么应对",
                   "肯定有好戏", "挺有意思", "这步很关键",
                   "直接威胁", "更加活跃", "更佳位置", "更灵活",
                   "感觉", "稍显", "效果差点", "来得直接", "一举两得",
                   "主要是为了吃掉", "主要是为了形成将军")
        if any(term in text for term in generic):
            return False
        if any(term in text for term in ("候选", "备选", "首选", "也可以考虑",
                "两种走法", "另一个选择", "另一种选择", "猜", "押", "八成", "蒙对")):
            return False
        notation = str(payload.get("我方计划走法") or "")
        move = LiveController._spoken_notation(notation)
        short_call = kind in ("关键走棋解说", "我方走棋播报", "我方走棋解说")
        if short_call and notation and "，吃" not in notation and any(
                term in text for term in (
                    "收下", "拿下", "吃掉", "吃到", "这波交换")):
            return False
        if short_call and "，吃" in notation:
            captured = notation.rsplit("吃", 1)[-1][:1]
            if any(term in text for term in ("吃", "拿下", "收下")) and captured not in text:
                return False
        # Short move calls only discuss the current move.  Reject the most
        # common hallucination class before it can enter TTS: describing a
        # rook/cannon/elephant move as a horse jump (or vice versa).
        if kind == "关键走棋解说" and move:
            piece = move[:1]
            action_words = {
                "马": ("跳马", "马腿", "跃马", "回马"),
                "车": ("平车", "提车", "出车", "退车", "大车"),
                "炮": ("架炮", "平炮", "抬炮", "退炮", "炮位"),
                "兵卒": ("拱兵", "拱卒", "过河兵", "小兵", "小卒"),
                "相象": ("飞相", "飞象", "补相", "补象"),
                "仕士": ("补仕", "补士"),
            }
            for allowed, words in action_words.items():
                if piece not in allowed and any(word in text for word in words):
                    return False
        return True
    """Generate text/TTS in background and acknowledge move-specific playback."""
    def __init__(self, settings, notify, llm=respond, tts=synthesize):
        self.settings, self.notify = settings, notify
        self.llm, self.tts = llm, tts
        self.closed = threading.Event()
        self.suspended = False
        self.chess_suspended = False
        self.interactions_suspended = False
        self.condition = threading.Condition()
        self.worker_lock = threading.Lock()
        self.jobs = []
        self.sequence = 0
        self.cancel_epoch = 0
        self.chess_epoch = 0
        self.history = deque(maxlen=8)
        self.style = CommentaryStyle()
        self.prepared = {}
        self.prepared_lock = threading.Lock()
        self.playback_lock = threading.Lock()
        self.high_speech_lock = threading.Lock()
        self.playback_state_lock = threading.Lock()
        self.current_playback = None
        self.active_chat = False
        self.active_chat_payload = None
        self.active_chat_cancel_epoch = 0
        self.chat_priority_total = 0.0
        self.chat_priority_started = None
        self.high_mates = 0
        self.chat_epoch = 0
        self.idle_lock = threading.Lock()
        self.idle_state = None
        self.generator = None
        self._ensure_worker()

    def _ensure_worker(self):
        with self.worker_lock:
            if self.closed.is_set() or getattr(self, "suspended", False):
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
        if kind in CHESS_SPEECH_KINDS:
            payload = {**payload, "_chess_epoch": getattr(self, "chess_epoch", 0)}
        if kind == "欢迎观众":
            direct_text = welcome_line(payload.get("观众"))
        with self.condition:
            if (self.closed.is_set() or getattr(self, "suspended", False)
                    or (kind in CHESS_SPEECH_KINDS and getattr(self, "chess_suspended", False))
                    or (kind in INTERACTION_SPEECH_KINDS and getattr(self, "interactions_suspended", False))):
                return False
            if kind in INTERACTION_SPEECH_KINDS:
                payload = {**payload, "_chat_epoch": self.chat_epoch}
            if kind == "弹幕回复":
                if self.active_chat or any(job[4] == kind for job in self.jobs):
                    return False
                self.active_chat = True
                self.active_chat_payload = payload
                self.active_chat_cancel_epoch = self.cancel_epoch
                self.chat_priority_started = time.monotonic()
            self.sequence += 1
            job = (priority, self.sequence, time.monotonic(), self.cancel_epoch,
                   kind, payload, direct_text)
            if kind == "弹幕回复":
                self._mark_chat_priority(True)
                threading.Thread(target=self._chat_worker, args=(job,),
                                 name="audience-reply", daemon=True).start()
                self.notify("queued", "弹幕回复已加入播报队列", {"kind": kind, **payload})
                return True
            # Once our next move is known, any queued older chess narration is
            # already behind the board.  Keep chat/gifts, but let the latest
            # move replace stale chess play-by-play.
            if kind in OWN_SPEECH_KINDS:
                self.jobs = [job for job in self.jobs
                             if job[4] not in CHESS_SPEECH_KINDS]
                heapq.heapify(self.jobs)
            heapq.heappush(self.jobs, (
                -1 if self._is_mate(kind, payload) else max(2, priority),
                self.sequence, time.monotonic(), self.cancel_epoch,
                kind, payload, direct_text))
            # Keep important jobs and cap backlog so speech never trails minutes behind.
            if len(self.jobs) > 12:
                self.jobs = sorted(self.jobs)[:12]
                heapq.heapify(self.jobs)
            self.condition.notify()
        self.notify("queued", f"{kind}已加入播报队列",
                    {"kind": kind, **payload})
        return True

    def can_reply_chat(self):
        with self.condition:
            available = (not self.closed.is_set() and not getattr(self, "suspended", False)
                         and not getattr(self, "interactions_suspended", False)
                         and not self.active_chat)
        return available and not self._mate_busy()

    @staticmethod
    def _is_mate(kind, payload=None):
        return kind == "绝杀长解说" or bool((payload or {}).get("绝杀长播报"))

    def _mate_busy(self):
        if getattr(self, "chess_suspended", False):
            return False
        with self.condition:
            queued = (self.high_mates or (
                        getattr(self, "active_chess_epoch", None) == self.chess_epoch
                        and self._is_mate(getattr(self, "active_kind", None)))
                      or (getattr(self, "current_chess_epoch", None) == self.chess_epoch
                          and self._is_mate(getattr(self, "current_kind", None)))
                      or any(self._is_mate(job[4], job[5]) for job in self.jobs))
        with self.prepared_lock:
            prepared = any(self._is_mate(state.get("kind")) for state in self.prepared.values())
        return bool(queued or prepared)

    def _chat_busy(self):
        with self.condition:
            return bool(self.active_chat and self.active_chat_cancel_epoch == self.cancel_epoch
                        and self._chat_valid("弹幕回复", self.active_chat_payload or {}))

    def _has_turn(self, kind, payload):
        if self._is_mate(kind, payload):
            return True
        return not self._mate_busy() and (kind == "弹幕回复" or not self._chat_busy())

    def _state_live(self, state):
        return (state.get("approved", True) is not False and state["epoch"] == self.cancel_epoch
                and not self.closed.is_set() and not getattr(self, "suspended", False)
                and not (state.get("cancel") and state["cancel"].is_set())
                and not ("chess_epoch" in state and getattr(self, "chess_suspended", False))
                and state.get("chess_epoch", self.chess_epoch) == self.chess_epoch)

    def _wait_turn(self, kind, payload, state):
        state.setdefault("priority_clock", self._chat_priority_clock())
        while self._state_live(state) and self._chat_valid(kind, payload):
            now = time.monotonic()
            # Pause the ordinary move's start timeout only for the chat turn,
            # with a bounded budget. Never acknowledge an unplayed move.
            clock = self._chat_priority_clock()
            if kind != "弹幕回复" and not self._is_mate(kind, payload):
                extension = min(max(0, clock - state["priority_clock"]),
                                max(0, 45 - state.get("priority_delay", 0)))
                if "deadline" in state:
                    state["deadline"] += extension
                state["priority_delay"] = state.get("priority_delay", 0) + extension
            state["priority_clock"] = clock
            if now >= state.get("deadline", float("inf")):
                return False
            if self._has_turn(kind, payload):
                return True
            if kind != "弹幕回复" and self._mate_busy():
                return False
            self.closed.wait(.03)
        return False

    def _play_in_turn(self, audio, payload, state, text, kind, mode, acknowledge=False):
        while self._wait_turn(kind, payload, state):
            if not self.playback_lock.acquire(timeout=.03):
                continue
            try:
                if not self._has_turn(kind, payload):
                    continue
                with self.playback_state_lock:
                    self.current_playback, self.current_kind = mode, kind
                    self.current_chess_epoch = state.get("chess_epoch")
                try:
                    played = self._play_synced_audio(audio, payload, state, text, kind, acknowledge)
                    if played or state.get("audio_started") or not self._state_live(state):
                        return played
                finally:
                    with self.playback_state_lock:
                        self.current_playback, self.current_kind = None, None
            finally:
                self.playback_lock.release()
        return False

    def _mark_chat_priority(self, active):
        self._write_speech_marker(SPEECH_PRIORITY, {"active": bool(active), "created": time.time()})

    def _chat_priority_clock(self):
        with self.condition:
            return self.chat_priority_total + (time.monotonic() - self.chat_priority_started
                if self.chat_priority_started is not None else 0)

    def _finish_chat_priority(self):
        with self.condition:
            if self.chat_priority_started is not None:
                self.chat_priority_total += time.monotonic() - self.chat_priority_started
                self.chat_priority_started = None
            self._mark_chat_priority(False)

    def _chat_worker(self, job):
        try:
            self._run_job(job)
        finally:
            with self.condition:
                if self.active_chat_payload is job[5]:
                    self._finish_chat_priority()
                    self.active_chat = False
                    self.active_chat_payload = None
                self.condition.notify_all()

    def activity_snapshot(self):
        with self.condition:
            queued = len(self.jobs)
            working = bool(getattr(self, "active_kind", None) or self.active_chat)
        with self.prepared_lock:
            preparing = len(self.prepared)
        with self.playback_state_lock:
            playing = bool(getattr(self, "audio_active", False))
        return {"queued": queued, "working": working, "preparing": preparing,
                "playing": playing, "suspended": bool(getattr(self, "suspended", False))}

    def cancel_chats(self):
        """Cancel room speech only, including welcomes and gift thanks."""
        with self.condition:
            self.chat_epoch += 1
            self.jobs = [job for job in self.jobs if job[4] not in INTERACTION_SPEECH_KINDS]
            heapq.heapify(self.jobs)
            self._finish_chat_priority()
            self.active_chat = False
            self.active_chat_payload = None
            self.condition.notify_all()

    def pause_interactions(self):
        with self.condition:
            self.interactions_suspended = True
            self.cancel_chats()

    def resume_interactions(self):
        with self.condition:
            self.interactions_suspended = False

    def pause_chess(self):
        self.chess_suspended = True
        self.invalidate_chess()

    def resume_chess(self):
        self.chess_suspended = False

    def _chat_valid(self, kind, payload):
        return (time.monotonic() <= payload.get("_expires_at", float("inf"))
                and (kind not in INTERACTION_SPEECH_KINDS or (
                    not getattr(self, "interactions_suspended", False)
                    and payload.get("_chat_epoch", self.chat_epoch) == self.chat_epoch)))

    def clear(self, stop_audio=False):
        self.cancel_idle()
        self._finish_chat_priority()
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
            self.jobs = [job for job in self.jobs if job[4] not in CHESS_SPEECH_KINDS]
            if newest is not None:
                priority, sequence, _, epoch, kind, payload, direct = newest
                self.jobs.append((priority, sequence, time.monotonic(),
                                  epoch, kind, payload, direct))
            heapq.heapify(self.jobs)
            self.condition.notify_all()
        self.notify("speech_status", "已清除排队旧讲解；当前句说完后跳到最新", {})

    @staticmethod
    def _write_speech_marker(path, data):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            pass

    @staticmethod
    def _mark_speech_started(payload):
        token = str(payload.get("speech_token") or "").strip()
        if not token or token.endswith(":sandbox"):
            return
        SpeechPipeline._write_speech_marker(SPEECH_STARTED, {
            "token": token, "status": "started", "started": time.time()})

    @staticmethod
    def _mark_speech_failed(payload, reason):
        if payload.get("speech_token") and not str(payload["speech_token"]).endswith(":sandbox"):
            SpeechPipeline._write_speech_marker(SPEECH_STARTED, {
                "token": payload["speech_token"], "status": "failed", "message": str(reason)})

    @staticmethod
    def _mark_speech_finished(payload):
        token = str(payload.get("speech_token") or "").strip()
        if token and not token.endswith(":sandbox"):
            SpeechPipeline._write_speech_marker(SPEECH_STARTED, {
                "token": token, "status": "finished", "finished": time.time()})

    @staticmethod
    def _mark_speech_preparing(payload, enabled):
        token = str(payload.get("speech_token") or "").strip()
        if not token or token.endswith(":sandbox"):
            return
        SpeechPipeline._write_speech_marker(SPEECH_PREPARING, {
                "token": token,
                "enabled": bool(enabled),
                "deferred": False,
                "wait_finished": bool(payload.get("绝杀长播报")),
                "created": time.time(),
            })

    def invalidate_chess(self):
        """A new board supersedes every pending narration of the old board."""
        self.chess_epoch += 1
        self.cancel_idle()
        with self.condition:
            self.high_mates = 0
            self.jobs = [job for job in self.jobs if job[4] not in CHESS_SPEECH_KINDS]
            heapq.heapify(self.jobs)
        with self.prepared_lock:
            states = list(self.prepared.values())
            self.prepared.clear()
        for state in states:
            state["approved"] = False
            state["decision"].set()

    @staticmethod
    def _audio_duration(audio):
        try:
            with wave.open(str(audio), "rb") as stream:
                return stream.getnframes() / stream.getframerate()
        except OSError:
            return 0.0
        except (EOFError, wave.Error) as error:
            raise RuntimeError("语音文件格式无效，未启动播放") from error

    def _play_synced_audio(self, audio, payload, state, text, kind, acknowledge=True):
        # Caller owns the playback lock.  Publish the acknowledgement only
        # AFTER Windows accepts playback; never before waiting for the lock.
        valid = lambda: (self._state_live(state)
                         and self.settings.speech_enabled
                         and self._chat_valid(kind, payload)
                         and self._has_turn(kind, payload))
        if (not valid() or not self._chat_valid(kind, payload)
                or time.monotonic() >= state.get("deadline", float("inf"))):
            return False
        duration = self._audio_duration(audio)
        winsound.PlaySound(str(audio), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        state["audio_started"] = True
        with self.playback_state_lock:
            self.audio_active = True
        try:
            self.notify("reply", text, {"kind": kind, **payload})
            self.style.remember(text)
            if acknowledge:
                self._mark_speech_started(payload)
            until = time.monotonic() + duration
            while time.monotonic() < until:
                if not valid():
                    winsound.PlaySound(None, winsound.SND_PURGE)
                    return False
                self.closed.wait(min(0.03, max(0, until - time.monotonic())))
            if not valid():
                return False
            if acknowledge and payload.get("绝杀长播报"):
                self._mark_speech_finished(payload)
            return True
        finally:
            with self.playback_state_lock:
                self.audio_active = False

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
                .replace("对方", "对面"))

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

    @classmethod
    def _complete_chess_text(cls, text, max_chars=180):
        """Bound long commentary at a sentence boundary, never mid-sentence."""
        cleaned = cls._clean_text(text)
        if len(cleaned) <= max_chars:
            return cleaned
        parts = re.findall(r"[^。！？!?；;]+[。！？!?；;]?", cleaned)
        result = ""
        for part in parts:
            if result and len(result + part) > max_chars:
                break
            result += part
        return result or cleaned

    def prepare_move(self, kind, payload, direct_text=None,
                     replace_older=True):
        """Pre-generate one move's text/audio; play only after commit_move."""
        token = str(payload.get("speech_token") or "").strip()
        if not token or not self._ensure_worker():
            return False
        state = {"decision": threading.Event(), "approved": None,
                 "epoch": self.cancel_epoch, "chess_epoch": self.chess_epoch, "kind": kind}
        with self.prepared_lock:
            if (self.closed.is_set() or getattr(self, "suspended", False)
                    or getattr(self, "chess_suspended", False)):
                return False
            older = list(self.prepared.values()) if replace_older else []
            if replace_older:
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
        self.notify("queued", "走棋解说正在并行预生成", payload)
        return True

    def _prepare_move_worker(self, token, kind, payload, state, direct_text=None):
        audio = None
        try:
            self.notify("speech_status", "正在准备绝杀收尾" if payload.get("绝杀长播报")
                        else "正在预生成走棋短解说", payload)
            text = direct_text or self.llm(
                kind, {"主播设定": self.settings.persona, **payload},
                list(self.history), model=self.settings.model)
            text = self._first_person_text(text, payload)
            text = self.style.decorate(kind, text, payload, self.settings.persona)
            text = (self._complete_chess_text(text, max_chars=220)
                    if payload.get("绝杀长播报") else self._short_chess_text(text))
            if not text or not self._chess_line_valid(text, kind, payload):
                self._mark_speech_failed(payload, "没有可播报的有效台词")
                return
            if self.settings.speech_enabled:
                self.notify("speech_status", "正在预热走棋语音", payload)
                audio = self.tts(text, self.settings.voice)
            if not state["decision"].wait(30.0) or not state["approved"]:
                return
            if (state["epoch"] != self.cancel_epoch or self.closed.is_set() or
                    getattr(self, "suspended", False)):
                return
            if audio and self.settings.speech_enabled:
                self.cancel_idle()
                if not self._play_in_turn(audio, payload, state, text, kind, "high", acknowledge=True):
                    return
                self._prune_audio(Path(audio).parent)
            else:
                if not self._wait_turn(kind, payload, state):
                    return
                self.notify("reply", text, {"kind": kind, **payload})
                self.style.remember(text)
                self.notify("speech_status", "仅显示文本，未启动语音", payload)
            self.history.extend([
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                {"role": "assistant", "content": text}])
            self.notify("speech_status", "等待下一条内容", payload)
        except Exception as error:
            if state.get("approved") is not False and state["epoch"] == self.cancel_epoch:
                self._mark_speech_failed(payload, str(error)[:200])
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

    def commit_move(self, token, deadline=None):
        if deadline is not None:
            with self.prepared_lock:
                state = self.prepared.get(str(token))
                if state is not None:
                    state["deadline"] = float(deadline)
                    state["priority_clock"] = self._chat_priority_clock()
        return self._decide_move(token, True)

    def discard_move(self, token):
        return self._decide_move(token, False)

    def play_high(self, kind, payload, direct_text):
        """Play a mandatory short move call without waiting for the LLM."""
        text = self._short_chess_text(direct_text)
        if (not text or self.closed.is_set() or getattr(self, "suspended", False)
                or getattr(self, "chess_suspended", False)):
            return False
        if not self._chess_line_valid(text, kind, payload):
            return False
        self.cancel_idle()
        payload = dict(payload)
        if kind in CHESS_SPEECH_KINDS:
            payload["_chess_epoch"] = self.chess_epoch
        if self._is_mate(kind, payload):
            with self.condition:
                self.high_mates += 1
        threading.Thread(
            target=self._play_high_worker,
            args=(kind, dict(payload), text, self.cancel_epoch),
            name="chess-high-speech", daemon=True).start()
        return True

    def _play_high_worker(self, kind, payload, text, epoch=None):
        epoch = self.cancel_epoch if epoch is None else epoch
        try:
            if epoch != self.cancel_epoch or self.closed.is_set():
                return
            state = {"epoch": epoch, "approved": True}
            if kind in CHESS_SPEECH_KINDS:
                state["chess_epoch"] = payload.get("_chess_epoch", self.chess_epoch)
            if not self.settings.speech_enabled:
                if not self._wait_turn(kind, payload, state):
                    return
                self.notify("reply", text, {"kind": kind, **payload})
                self.notify("speech_status", "高优走步仅显示文本", payload)
                return
            self.notify("speech_status", "正在生成高优走步语音", payload)
            audio = self.tts(text, self.settings.voice)
            if not self._play_in_turn(audio, payload, state, text, kind, "high"):
                return
            self._prune_audio(Path(audio).parent)
        except Exception as error:
            if self._state_live(state):
                self.notify("error", str(error)[:200], {"kind": kind})
        finally:
            if self._is_mate(kind, payload):
                with self.condition:
                    if payload.get("_chess_epoch") == self.chess_epoch:
                        self.high_mates = max(0, self.high_mates - 1)
            self.notify("speech_status", "等待下一条内容", payload)

    def prepare_idle(self, payload, kind="等待期长讲解", direct_text=None):
        """Pre-generate low-priority speech; play only in a wait window."""
        if (self.closed.is_set() or getattr(self, "suspended", False)
                or getattr(self, "chess_suspended", False)):
            return False
        self.cancel_idle()
        state = {
            "cancel": threading.Event(), "window": threading.Event(),
            "epoch": self.cancel_epoch, "payload": dict(payload),
            "chess_epoch": self.chess_epoch,
            "kind": str(kind), "direct_text": direct_text,
        }
        with self.idle_lock:
            if (self.closed.is_set() or getattr(self, "suspended", False)
                    or getattr(self, "chess_suspended", False)):
                return False
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
            text = complete_note(text)
            text = self.style.decorate(kind, text, payload, self.settings.persona)
            if not self._chess_line_valid(text, kind, payload):
                return
            if kind in CHESS_SPEECH_KINDS and self._recently_spoken(text):
                return
            if not text or state["cancel"].is_set():
                return
            if self.settings.speech_enabled:
                audio = self.tts(text, self.settings.voice)
            while not state["cancel"].is_set():
                if state["window"].wait(0.1):
                    break
            if state["cancel"].is_set() or state["epoch"] != self.cancel_epoch:
                return
            if audio and self.settings.speech_enabled:
                self.notify("speech_status", f"等待落子，播放{kind}", payload)
                if not self._play_in_turn(audio, payload, state, text, kind, "low"):
                    return
                self._prune_audio(Path(audio).parent)
            else:
                if not self._wait_turn(kind, payload, state):
                    return
                self.notify("reply", text, {"kind": kind, **payload})
                self.style.remember(text)
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
        # The playback owner stops its own cancelled clip while holding the
        # playback lock. A global purge here could cut a new audience reply.
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
                job = heapq.heappop(self.jobs)
                self.active_kind = job[4]
                self.active_chess_epoch = job[5].get("_chess_epoch")
            try:
                self._run_job(job)
            finally:
                with self.condition:
                    self.active_kind = None

    def _run_job(self, job):
        priority, sequence, created, epoch, kind, payload, direct = job
        completed = False
        state = {"epoch": epoch, "approved": True}
        if kind in CHESS_SPEECH_KINDS:
            state["chess_epoch"] = payload.get("_chess_epoch", 0)
        try:
            if not self._state_live(state) or not self._chat_valid(kind, payload):
                return
            if kind in CHESS_SPEECH_KINDS and time.monotonic() - created > 10:
                return
            if not self._wait_turn(kind, payload, state):
                return
            self.notify("speech_status", f"正在组织{kind}话术", payload)
            history = list(self.history) if kind in CHESS_SPEECH_KINDS else []
            style_hint = ({"最近用过的比喻": self.style.topics()} if kind in INTERACTION_SPEECH_KINDS else {})
            text = direct or self.llm(kind, {"主播设定": self.settings.persona, **style_hint, **payload},
                                      history, model=self.settings.model)
            text = self._first_person_text(text, payload)[:300]
            text = self.style.decorate(kind, text, payload, self.settings.persona)
            if kind == "弹幕回复":
                text = spoken_reply(text, payload=payload)
            elif kind == "礼物答谢":
                text = gift_reply(text, payload, self.style)
            if not text or not self._chess_line_valid(text, kind, payload):
                return
            if not self._state_live(state) or not self._chat_valid(kind, payload):
                return
            if self.settings.speech_enabled:
                self.notify("speech_status", "正在生成百炼语音", payload)
                audio = self.tts(text, self.settings.voice)
                mode = "low" if kind in CHESS_SPEECH_KINDS else "interaction"
                if not self._play_in_turn(audio, payload, state, text, kind, mode):
                    return
                self._prune_audio(Path(audio).parent)
            else:
                if not self._wait_turn(kind, payload, state):
                    return
                self.notify("reply", text, {"kind": kind, **payload})
                self.style.remember(text)
                self.notify("speech_status", "仅文本模式：未调用 TTS", payload)
            completed = True
            if kind in CHESS_SPEECH_KINDS:
                self.history.extend([{"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                                     {"role": "assistant", "content": text}])
            self.notify("speech_status", "等待下一条内容", payload)
            self.closed.wait(max(0.0, float(self.settings.cooldown)))
        except Exception as error:
            if self._state_live(state) and self._chat_valid(kind, payload):
                self.notify("error", str(error)[:200], {"kind": kind})
        finally:
            if kind == "弹幕回复":
                self.notify("interaction_done", "本条弹幕互动结束", {**payload, "completed": completed})

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
        self.suspended = True
        self.clear(stop_audio=True)
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
        self.suspended = False
        self.chess_suspended = False
        self.connector = None
        self.connector_epoch = 0
        self.connector_lock = threading.RLock()
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
        self.pending_prediction = None
        self.reply_context = None
        self.recent_chess_topics = deque(maxlen=6)
        self.sandbox_speech_tokens = set()
        self.sandbox_speech_by_move = {}
        self.closing_announced = False
        self.sandtable_active = False
        self.sandtable_timer = None
        self.log_lock = threading.Lock()
        self.gift_lock = threading.Lock()
        self.pending_gifts = {}
        self.gift_timers = {}
        self.pipeline = SpeechPipeline(self.settings, self._notify)
        self.speech_style = self.pipeline.style
        self.tailer = ChessEventTailer(CHESS_EVENTS, self.submit_chess)
        self.tailer.start()
        self.chat_selector = ChatSelector()
        self.chat_closed = threading.Event()
        self.chat_stats_at = 0.0
        threading.Thread(target=self._chat_loop, name="live-chat-selector", daemon=True).start()

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
        self.pipeline.resume_interactions()
        self.enabled = True
        self._notify("status", "弹幕互动已开启")

    def pause(self):
        self.enabled = False
        self.chat_selector.clear()
        self.pipeline.pause_interactions()
        with self.gift_lock:
            for timer in self.gift_timers.values():
                timer.cancel()
            self.gift_timers.clear()
            self.pending_gifts.clear()
        self._notify("status", "弹幕互动已暂停")

    def stop_chess_tasks(self):
        """Stop board narration without changing room connection or interaction."""
        self.chess_suspended = True
        self.pipeline.pause_chess()
        self.commentary_analyses = {}
        self.last_confirmed_analysis = None
        self.last_commentary_reply = None
        self.pending_move_events.clear()
        self.pending_prediction = None
        self.reply_context = None
        self.sandbox_speech_tokens.clear()
        self.sandbox_speech_by_move.clear()
        self._close_sandtable("下棋已暂停")

    def resume_chess_tasks(self):
        self.chess_suspended = False
        self.pipeline.resume_chess()

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
                     "仕": 5, "士": 5, "兵": 5, "卒": 5}.get(captured, 6)
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
                if previous is None or abs(previous) < 600:
                    importance = max(importance, 5)
                    reasons.append("胜负趋势")
            elif abs(score) >= 300:
                # Announce the threshold crossing once.  The old rule kept
                # every later quiet move permanently above the natural speech
                # threshold after one side became better.
                if previous is None or abs(previous) < 300:
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
        if not move:
            return ""
        if "，吃" in move:
            action, target = move.rsplit("，吃", 1)
            return f"对手可能走{action}，吃掉咱们{piece_amount(target[:1])}。"
        variants = (
            f"对手可能走{move}。",
            f"对手这边可能接{move}。",
            f"对手有可能走{move}。",
        )
        return variants[(max(1, int(count or 1)) - 1) % len(variants)]

    def _reset_opening(self):
        self.opening_moves = []
        self.opening_announced = set()

    def _speech_flair(self):
        style = getattr(self, "speech_style", None)
        if not isinstance(style, CommentaryStyle):
            style = self.speech_style = CommentaryStyle()
        return style

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

    def _record_opening_move(self, notation, number=None):
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
            text = self._speech_flair().opening(name, text,
                getattr(getattr(self, "settings", None), "persona", DEFAULT_PERSONA))
            announced.add(name)
            evidence = {"布局名称": name, "判定依据": list(moves),
                        "解说对应走法": notation, "解说对应手数": number}
            if found:
                evidence.update({
                    "参考棋谱数": found.get("games"),
                    "棋谱分类": found.get("name"),
                    "开局编号": found.get("ecco"),
                    "资料来源": "CCPD大师棋谱",
                })
            # The opening label already explains this move.  Drop a prepared
            # waiting-window paraphrase for the same move so "中炮开门" is not
            # immediately followed by another generic middle-control line.
            cancel_idle = getattr(self.pipeline, "cancel_idle", None)
            if callable(cancel_idle):
                cancel_idle()
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
                text = f"这盘走到{name}这一路了。"
                cancel_idle = getattr(self.pipeline, "cancel_idle", None)
                if callable(cancel_idle):
                    cancel_idle()
                self.pipeline.submit(
                    2, "开局定式解说",
                    {"布局名称": name,
                     "解说对应走法": notation, "解说对应手数": number,
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
            return "咱们丢了一辆大车，局面得重新算。"
        if captured:
            return f"咱们一枚{captured}被吃，得重新盘一下。"
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
        from board import Board
        from game_sandbox import build_plan
        notation = str(event.get("notation") or "")
        try:
            board = Board.from_fen(event["fen_before"])
        except (KeyError, ValueError, TypeError):
            return None
        plan = build_plan(
            notation, event.get("analysis") or {},
            [(item.get("ucci"), item.get("notation"))
             for item in event.get("pv", [])],
            event.get("ucci"), board=board)
        if not plan:
            return None
        return {
            "action": "show",
            "title": "关键战术沙盘",
            "reason": plan["reason"],
            "confidence": plan["confidence"],
            "route": plan["route"],
            "target": plan["target"],
            "target_kind": plan["target_kind"],
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
    def _closing_move_text(cls, notation, number=0, forced=False, reasons=()):
        move = cls._spoken_notation(notation)
        if forced:
            if "，吃" in move:
                return cls._synced_move_text(notation, reasons, number) + "咱们按这条线收官。"
            if "形成将军" in reasons:
                return f"{move}，将军，收官这段咱们不抢跑。"
            variants = (f"{move}，杀棋路线接着走，收官这段不抢跑。",
                        f"{move}，杀棋路线有了，收官的压轴题认真做。")
        else:
            variants = (
                f"{move}，目前局面占优，咱们继续走稳。",
                f"{move}，优势还在，先把眼前这手下好。",
            )
        return variants[(max(1, int(number or 1)) - 1) % len(variants)]

    @classmethod
    def _synced_move_text(cls, notation, reasons=(), number=0,
                          sandtable=False):
        """Fast factual line: exact capture or a piece-specific quiet action."""
        move = cls._spoken_notation(notation)
        captured = move.rsplit("吃", 1)[-1][:1] if "吃" in move else ""
        base_move = move.split("，吃", 1)[0]
        if captured:
            amount = piece_amount(captured)
            calls = (f"吃掉{amount}", f"把这{amount[1:]}拿掉", f"先收掉这{amount[1:]}")
            index = (int(number or 1) - 1) % len(calls)
            if "形成将军" in reasons:
                return f"{base_move}，吃{captured}带将。"
            return f"{base_move}，{calls[index]}。"
        return cls._quiet_move_text(base_move, number, reasons)

    @staticmethod
    def _fact_phrase(fact):
        return {
            "争夺中路": "把子力往中路集中",
            "马向中路靠拢": "马往中路靠一靠",
            "马向前跃进": "马向前跳出",
            "马回撤调整": "回马调整位置",
            "车横向换线": "平车换线",
            "车向前推进": "把车向前送",
            "车回撤调整": "把车收回来",
            "炮横向换线": "平炮换个方向",
            "炮向前推进": "把炮向前送",
            "炮回撤调整": "炮先回撤",
            "兵刚刚过河": "这枚兵刚过河，之后可以横走了",
            "卒刚刚过河": "这枚卒刚过河，之后可以横走了",
            "让过河兵参与进攻": "过河的小兵继续向前",
            "过河兵向中路靠拢": "过河兵向中路靠拢",
            "过河兵横向换线": "过河兵横着换一路",
            "飞相调整防线": "飞相调整防线",
            "飞象调整防线": "飞象调整防线",
            "补仕守住宫门": "补仕照看宫门",
            "补士守住宫门": "补士照看宫门",
            "先解除将军": "先解将，保住将帅",
            "形成将军": "这手将军，对面得先解将",
        }.get(str(fact), "")

    @classmethod
    def _quiet_move_text(cls, move, number=0, reasons=()):
        """One move call plus a visible fact; never invent a tactical purpose."""
        reasons = tuple(str(item) for item in reasons)
        for fact in ("先解除将军", "形成将军", "争夺中路"):
            if fact in reasons:
                return f"{move}，{cls._fact_phrase(fact)}。"
        # Advancing/retreating/changing file is already in the notation.
        # Add only a new, verified fact; do not pad the same move with synonyms.
        informative = {"马向中路靠拢", "兵刚刚过河", "卒刚刚过河", "过河兵向中路靠拢"}
        for fact in reasons:
            if fact in informative:
                phrase = cls._fact_phrase(fact)
                if phrase:
                    return f"{move}，{phrase}。"
        return f"{move}。"

    @classmethod
    def _idle_move_text(cls, notation, evidence=None, number=0):
        """Explain a concrete point during the wait without calling the move again."""
        evidence = evidence or {}
        note = waiting_note(evidence)
        if note:
            return note
        reply = cls._spoken_notation(evidence.get("最可能应手"))
        if reply:
            return complete_note(f"对手可能走{reply}。")
        return ""

    @staticmethod
    def _is_near_win(event):
        analysis = event.get("analysis") or {}
        try:
            score = int(analysis.get("score", 0))
        except (TypeError, ValueError):
            return False, False
        if analysis.get("score_type") == "mate":
            return score > 0, score > 0
        # A large positional score is still not a forced win.  Premature
        # celebration is worse than silence, so closing talk needs a mate line.
        return False, False

    def _victory_text(self):
        self.victory_count = getattr(self, "victory_count", 0) + 1
        if not humor_enabled(getattr(getattr(self, "settings", None), "persona", DEFAULT_PERSONA)):
            return "这盘拿下，下一局继续。"
        variants = (
            ("戏台", "拿下！这盘的收尾戏演完，掌声现在可以放出来了。"),
            ("上班", "稳稳收下，棋子们辛苦了，这盘可以打卡下班。"),
            ("财务", "这盘拿下，账本合上，咱们接着下。"),
            ("家门", "这盘拿下，棋盘收拾好，下一位棋友请进。"),
            ("考试", "拿下！这道题做完了，下一盘咱们继续答卷。"),
        )
        return self._speech_flair().pick("获胜收官", variants, required=True)

    def submit_chess(self, event):
        if getattr(self, "suspended", False) or getattr(self, "chess_suspended", False):
            return
        if not getattr(getattr(self, "settings", None),
                       "commentary_enabled", True):
            return
        if event.get("event") == "start":
            self._reset_opening()
            self.pending_prediction = None
            self.reply_context = None
            self.recent_chess_topics.clear()
            self.closing_announced = False
            self.commentary_analyses = {}
            self.last_confirmed_analysis = None
            self.last_commentary_reply = None
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
                sandbox_token = getattr(
                    self, "sandbox_speech_by_move", {}).pop(token, "")
                if sandbox_token:
                    self.pipeline.commit_move(sandbox_token)
                    getattr(self, "sandbox_speech_tokens", set()).discard(
                        sandbox_token)
                self._notify("status", "沙盘已打开，开始同步推演解说")
            elif action == "end":
                if not event.get("entered"):
                    sandbox_token = getattr(
                        self, "sandbox_speech_by_move", {}).pop(token, "")
                    if sandbox_token:
                        self.pipeline.discard_move(sandbox_token)
                        getattr(self, "sandbox_speech_tokens", set()).discard(
                            sandbox_token)
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
            last_move = getattr(self, "last_confirmed_analysis", None)
            win_note = (confirmed_finisher_note(event, last_move)
                        or confirmed_win_note(event, last_move))
            self.commentary_analyses = {}
            self.last_confirmed_analysis = None
            self.last_commentary_reply = None
            self.pipeline.cancel_idle()
            invalidate = getattr(self.pipeline, "invalidate_chess", None)
            if callable(invalidate):
                invalidate()
            for token in list(getattr(self, "sandbox_speech_tokens", set())):
                self.pipeline.discard_move(token)
            getattr(self, "sandbox_speech_tokens", set()).clear()
            getattr(self, "sandbox_speech_by_move", {}).clear()
            self._close_sandtable("对局结束")
            self.pending_move_events = {}
            self.pending_prediction = None
            self.reply_context = None
            self.closing_announced = False
            if event.get("result") == "won":
                victory_payload = dict(event)
                if win_note:
                    victory_payload.update(解说对应手数=last_move.get("number"),
                                           解说对应走法=last_move.get("notation"))
                self.pipeline.submit(
                    0, "获胜收官", victory_payload,
                    direct_text=win_note or self._victory_text())
            elif (event.get("result") == "finished" and
                  event.get("reason") in (
                       "jj_result_screen", "delayed_jj_result_screen",
                       "rank_promotion_screen")):
                self.pipeline.submit(
                    0, "结算收官", event,
                    direct_text="这盘已经结束，等下一局棋盘。")
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
            invalidate = getattr(self.pipeline, "invalidate_chess", None)
            if callable(invalidate):
                invalidate()
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
            # Every real move gets its own call before ADB taps. Verified
            # finishers wait for completion; sandbox tokens stay independent.
            if token:
                deadline = event.get("speech_deadline")
                if deadline is None:
                    self.pipeline.commit_move(token)
                else:
                    self.pipeline.commit_move(token, deadline=deadline)
            return
        if event.get("event") == "speech_cancelled":
            self.pipeline.discard_move(str(event.get("speech_token") or ""))
            self.pipeline.cancel_idle()
            return
        if event.get("event") == "move_applied":
            token = str(event.get("speech_token") or "")
            self.last_confirmed_analysis = getattr(self, "commentary_analyses", {}).pop(token, None)
            getattr(self, "pending_move_events", {}).pop(
                token, None)
            self._record_opening_move(notation, event.get("number"))
            return
        if event.get("event") in ("move_rejected", "move_preview"):
            token = str(event.get("speech_token") or "")
            getattr(self, "commentary_analyses", {}).pop(token, None)
            self.last_confirmed_analysis = None
            self.pipeline.discard_move(token)
            sandbox_token = getattr(
                self, "sandbox_speech_by_move", {}).pop(token, "")
            if sandbox_token:
                self.pipeline.discard_move(sandbox_token)
                getattr(self, "sandbox_speech_tokens", set()).discard(
                    sandbox_token)
            getattr(self, "pending_move_events", {}).pop(
                str(event.get("speech_token") or ""), None)
            return
        if event.get("event") == "opponent_move":
            self.last_commentary_reply = dict(event)
            self.pipeline.cancel_idle()
            invalidate = getattr(self.pipeline, "invalidate_chess", None)
            if callable(invalidate):
                invalidate()
            # If no sandbox-entered event arrived before the real reply, the
            # prepared route no longer belongs to the visible position.
            for token in list(getattr(self, "sandbox_speech_tokens", set())):
                self.pipeline.discard_move(token)
            getattr(self, "sandbox_speech_tokens", set()).clear()
            getattr(self, "sandbox_speech_by_move", {}).clear()
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
            prediction = getattr(self, "pending_prediction", None)
            self.pending_prediction = None
            if prediction:
                matched = str(event.get("ucci") or "") == prediction["ucci"]
                self.reply_context = {
                    "预判走法": prediction["notation"],
                    "实际应手": notation, "预判兑现": matched,
                }
            else:
                self.reply_context = None
            important_text = self._important_reply_text(notation)
            if important_text:
                # Keep the tactical note visible, but do not let a slow TTS
                # request for the opponent's previous move spill over our
                # next active move.  Own-move speech has the audio channel.
                self._notify("chess_note", important_text, {
                    "notation": notation, "spoken": False})
            self._record_opening_move(notation)
            return
        if event.get("dry_run"):
            return
        continuation = connected_move_note(
            event, getattr(self, "last_confirmed_analysis", None),
            getattr(self, "last_commentary_reply", None))
        if event.get("event") == "engine_analysis" and event.get("speech_token"):
            self.commentary_analyses = {event["speech_token"]: dict(event)}
        payload = {"我方计划走法": notation,
                   # Diagnostic-only metadata.  The control panel displays it
                   # beside the transcript label; neither field is appended to
                   # the spoken text or sent to TTS as a separate sentence.
                   "解说对应手数": event.get("number"),
                   "解说对应走法": notation,
                   "主播执棋": ("红方" if str(event.get("side")) == "r"
                                else "黑方" if str(event.get("side")) == "b"
                                else ""),
                   "speech_token": event.get("speech_token"),
                   "子力盘点": event.get("material"),
                   "局势分析": event.get("summary"),
                   "QUEEN式证据": {key: value for key, value in
                       (event.get("queen_evidence") or {}).items() if key != "候选取舍"},
                   "预测路线": [item.get("notation") for item in event.get("pv", [])[:4]]}
        finisher = build_finisher(
            event, getattr(self, "last_confirmed_analysis", None),
            getattr(self, "last_commentary_reply", None))
        if finisher:
            playful = humor_enabled(getattr(getattr(self, "settings", None), "persona", DEFAULT_PERSONA))
            opening = self._speech_flair().pick("绝杀开场", MATE_OPENINGS, required=True) if playful else ""
            finisher["text"] = finisher_copy(finisher["name"], finisher["notation"],
                event.get("number", 1), opening=opening, playful=playful)
            payload.update({"绝杀长播报": True, "杀法名称": finisher["name"],
                            "我方计划走法": finisher["notation"],
                            "解说对应走法": finisher["notation"]})
            token = event.get("speech_token")
            if token:
                self.commentary_analyses[token]["finisher"] = finisher
                self.pipeline.prepare_move("绝杀长解说", payload,
                                           direct_text=finisher["text"])
            else:
                self.pipeline.submit(0, "绝杀长解说", payload,
                                     direct_text=finisher["text"])
            self.reply_context = None
            self.pipeline.cancel_idle()
            return
        reply_context = getattr(self, "reply_context", None)
        if reply_context:
            payload["上一手预判复盘"] = reply_context
            self.reply_context = None
        payload["最近已讲话题"] = list(getattr(self, "recent_chess_topics", ()))
        sandtable = (self._sandtable_plan(event)
                     if getattr(getattr(self, "settings", None),
                                "sandtable_enabled", True) else None)
        if sandtable:
            payload["沙盘路线"] = [
                item["notation"] for item in sandtable["route"]]
            payload["沙盘触发"] = sandtable["reason"]
            payload["沙盘起因走法"] = notation
            payload["沙盘战术落点"] = sandtable["target"]
        near_win, forced_win = self._is_near_win(event)
        importance, reasons = self._commentary_importance(event)
        evidence = event.get("queen_evidence") or {}
        factual_reasons = list(evidence.get("选择理由") or [])
        current_tactic = ("，吃" in notation or
                          bool({"形成将军", "先解除将军"}.intersection(factual_reasons)) or
                          (event.get("analysis") or {}).get("score_type") == "mate")
        if current_tactic:
            # A previous opponent move must not displace this move's capture,
            # check or escape. Its context is consumed, never replayed later.
            reply_context = None
            payload.pop("上一手预判复盘", None)
        if sandtable:
            importance = max(importance, 10)
            reasons.append("沙盘推演")
        threshold, _, _ = self._density_rule()
        token = str(event.get("speech_token") or "")
        if near_win and not getattr(self, "closing_announced", False):
            importance = max(importance, 8)
            reasons.append("收官")
            self.closing_announced = True
        elif near_win and getattr(self, "closing_announced", False):
            # A mate score can persist for several moves.  Say the closing line
            # once; later captures may still get an exact factual call, but do
            # not repeat "victory is at the door" every turn.
            near_win = False
            reasons = [item for item in reasons
                       if item not in ("杀棋", "胜负趋势", "战术危险")]
            if "吃" in notation:
                captured = notation.rsplit("吃", 1)[-1][:1]
                importance = {"车": 9, "马": 7, "炮": 7, "相": 5,
                              "象": 5, "仕": 5, "士": 5,
                              "兵": 5, "卒": 5}.get(captured, 6)
            else:
                importance = 0
        if {"形成将军", "先解除将军"}.intersection(factual_reasons):
            importance = max(importance, threshold)
        if importance >= threshold:
            self.quiet_move_count = 0
            important_payload = {
                **payload,
                "讲解重点": reasons or ["局势变化"],
                "要求": ("像主播临场点评，只讲这手为什么值得注意；"
                         "不必报完整棋谱，不用我方或对方开头；最多两句")
            }
            direct = None
            kind = "关键走棋解说"
            if near_win:
                if not sandtable:
                    kind = "收官解说"
                    direct = self._closing_move_text(
                        notation, event.get("number", 0), forced_win, factual_reasons)
            if direct is None and importance >= threshold:
                direct = continuation or self._synced_move_text(
                    notation, factual_reasons + reasons,
                    event.get("number", 0),
                    sandtable=False)
            if token:
                if sandtable:
                    # The move call and sandbox explanation are independent:
                    # failed sandbox entry must never silence the real move.
                    sandbox_token = f"{token}:sandbox"
                    sandbox_payload = {
                        **important_payload,
                        "speech_token": sandbox_token,
                    }
                    self.sandbox_speech_tokens.add(sandbox_token)
                    self.sandbox_speech_by_move[token] = sandbox_token
                    self.pipeline.prepare_move(
                        "沙盘推演解说", sandbox_payload,
                        direct_text=(
                            "来，摆一条可能的接法。对手照这条线应，咱们就能"
                            f"{self._spoken_notation(sandtable['target']).replace('，吃', '吃')}。"),
                        replace_older=True)
                    self.pipeline.prepare_move(
                        kind, important_payload, direct_text=direct,
                        replace_older=False)
                else:
                    self.pipeline.prepare_move(
                        kind, important_payload, direct_text=direct)
            else:
                self.pipeline.submit(1, kind, important_payload,
                                     direct_text=direct)
            settings = getattr(self, "settings", None)
            note = (waiting_note(evidence) if settings is not None and
                    getattr(settings, "queen_commentary_enabled", True) and
                    not sandtable and evidence.get("值得展开") else "")
            if note:
                self.pipeline.prepare_idle({
                    **payload,
                    "要求": "只补充已验证路线中的新看点，不重复报招，不强行比较候选；最多两句。",
                }, kind="QUEEN式等待期解说", direct_text=note)
            elif reply_context:
                self.pipeline.prepare_idle({
                    **self._reply_review_payload(payload, reply_context),
                    "要求": "只用上一手预判复盘的事实，轻轻回应一次；不编棋局影响",
                }, kind="QUEEN式等待期解说",
                    direct_text=self._prediction_followup(reply_context))
            self._remember_chess_topic(notation)
            return
        if (token and getattr(getattr(self, "settings", None), "speech_enabled", False)
                and not event.get("dry_run")):
            self.pipeline.prepare_move("我方走棋播报", payload,
                                       direct_text=self._synced_move_text(
                                           notation, factual_reasons, event.get("number", 0)))
        if reply_context:
            self.pipeline.prepare_idle(self._reply_review_payload(payload, reply_context),
                kind="QUEEN式等待期解说",
                direct_text=self._prediction_followup(reply_context))
            self._remember_chess_topic(notation)
            return
        prediction_route = event.get("pv", [])
        predicted_reply = (prediction_route[1]
                           if len(prediction_route) >= 2 else None)
        if (not sandtable and not near_win and predicted_reply and
                self._prediction_due(event.get("number"))):
            self.pending_prediction = {
                "ucci": str(predicted_reply.get("ucci") or ""),
                "notation": str(predicted_reply.get("notation") or ""),
            }
            self.quiet_move_count = 0
            self.pipeline.prepare_idle({
                **payload,
                "引擎预测应手": predicted_reply.get("notation"),
                "预测依据": "Pikafish主变化中的第一应手",
            }, kind="应手预判", direct_text=self._engine_prediction_text(
                predicted_reply.get("notation"), self.prediction_count))
            self._remember_chess_topic(notation)
            return
        self.quiet_move_count = getattr(self, "quiet_move_count", 0) + 1
        if self._needs_silence_filler():
            idle_text = self._idle_move_text(
                notation, event.get("queen_evidence") or {},
                event.get("number", 0))
            if idle_text:
                self.pipeline.prepare_idle({
                **payload,
                "要求": ("现在是等棋空档，不要念走法；用两句口语讲当前的主要思路"
                         "和接下来值得看的一个点，不念评分或技术字段"),
                }, direct_text=idle_text)
        self._remember_chess_topic(notation)

    @staticmethod
    def _reply_review_payload(payload, context):
        """A quiet-turn callback describes the opponent reply, not our new move."""
        return {
            "主播执棋": payload.get("主播执棋"),
            "speech_token": str(payload.get("speech_token") or "") + ":reply-review",
            "解说对应手数": None,
            "解说对应走法": context.get("实际应手"),
            "上一手预判复盘": dict(context),
            "讲解范围": "上一手对手应手",
        }

    @classmethod
    def _prediction_followup(cls, context):
        actual = cls._spoken_notation(context.get("实际应手"))
        if not actual:
            return ""
        index = sum(ord(char) for char in actual)
        if context.get("预判兑现"):
            variants = (
                f"对手走了{actual}，这条路线接上了。",
                f"对手{actual}，和刚才分析的路线一致。",
                f"{actual}落下来了，继续看这条线。",
            )
        elif "，吃" in actual:
            return complete_note(f"对手这手{actual}，咱们得认真接了。")
        else:
            variants = (
                f"对手这手{actual}，走的是另一条路。",
                f"嚯，{actual}，对手没按这条路来。",
                f"对手走了{actual}，这位棋友有自己的剧本。",
                f"这手{actual}，对手另有安排，咱们接着拆招。",
            )
        return complete_note(variants[index % len(variants)])

    def _remember_chess_topic(self, notation):
        topics = getattr(self, "recent_chess_topics", None)
        if topics is None:
            topics = self.recent_chess_topics = deque(maxlen=6)
        topics.append(notation)

    def receive(self, event, force=False):
        epoch = event.get("connector_epoch")
        if epoch is not None and epoch != self.connector_epoch:
            return
        kind = event.get("type", "chat")
        if kind == "status":
            if event.get("state") in ("disconnected", "ended"):
                self._reset_chat_room()
            self._notify("connection", event.get("text", ""), event)
            return
        if kind == "audience":
            if self.chat_selector.update_online(event.get("online")):
                self._publish_chat_stats()
            return
        if getattr(self, "suspended", False) and force:
            return
        user = str(event.get("user") or "观众")[:30]
        text = str(event.get("text") or "").strip()[:300]
        now = time.monotonic()
        key = str(event.get("id") or f"{kind}:{user}:{text}")
        if now - getattr(self, "seen_pruned_at", 0) >= 5:
            self.seen = {k: timestamp for k, timestamp in self.seen.items()
                         if now - timestamp < 90}
            self.seen_pruned_at = now
        if key in self.seen:
            return
        self.seen[key] = now
        while len(self.seen) > 2000:
            self.seen.pop(next(iter(self.seen)))
        # The incoming feed is complete even when interaction is paused or the
        # message is filtered out of speech selection. Test inputs stay local.
        if not force and kind in ("chat", "gift", "member", "social", "like"):
            self._notify("room_message", "", event)
        if getattr(self, "suspended", False):
            if kind == "chat" and hasattr(self, "chat_selector"):
                self.chat_selector.offer(event, eligible=False)
            return
        if kind == "gift":
            self._notify(kind, f"{user}：{text}", event)
        if kind == "chat" and not force:
            self.chat_selector.offer({**event, "user": user, "text": text}, eligible=bool(self.enabled))
            return
        if not (self.enabled or force):
            return
        if kind == "gift" and self.settings.gift_enabled:
            self._queue_gift(user, event.get("gift") or text,
                             event.get("count", 1), force=force)
        elif kind == "chat":
            self.pipeline.submit(1, "弹幕回复", {"观众": user, "弹幕": text,
                                 "互动标识": f"test-{time.time_ns()}"})
        elif (kind == "member" and self.settings.welcome_enabled and
              viewer_key(event) not in self.welcomed and
              now - self.last_welcome >= 60 and self.pipeline.can_reply_chat()):
            self.welcomed.add(viewer_key(event))
            self.last_welcome = now
            self.pipeline.submit(4, "欢迎观众", {"观众": user,
                                 "_expires_at": now + 10}, direct_text=welcome_line(user))

    def _publish_chat_stats(self, force=False):
        now = time.monotonic()
        if force or now - self.chat_stats_at >= 2:
            self.chat_stats_at = now
            self._notify("room_stats", "直播间实时统计", {
                **self.chat_selector.stats(), "connector_epoch": getattr(self, "connector_epoch", None)})

    def _select_chat(self):
        self._publish_chat_stats()
        with self.chat_selector.lock:
            if (not self.enabled or getattr(self, "suspended", False)
                    or not self.pipeline.can_reply_chat()):
                return
            selected = self.chat_selector.take()
            if selected:
                self.pipeline.submit(3, "弹幕回复", {
                    "观众": selected["user"], "弹幕": selected["text"],
                    "弹幕编号": selected["id"],
                    "互动标识": f"chat-{time.time_ns()}",
                    "_expires_at": selected["received"] + self.chat_selector.MAX_AGE})

    def _chat_loop(self):
        while not self.chat_closed.wait(0.5):
            try:
                self._select_chat()
            except Exception as error:
                now = time.monotonic()
                if now - getattr(self, "chat_error_at", 0) >= 30:
                    self.chat_error_at = now
                    self._notify("error", "弹幕抽选异常：" + type(error).__name__)

    def _reset_chat_room(self):
        if hasattr(self, "chat_selector"):
            with self.chat_selector.lock:
                self.chat_selector.reset()
                self.pipeline.cancel_chats()
            self._publish_chat_stats(force=True)

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

    def connect(self, room, cancelled=None):
        with self.connector_lock:
            if cancelled and cancelled():
                return
            self.connector_epoch += 1
            epoch = self.connector_epoch
            previous, self.connector = self.connector, None
        self._reset_chat_room()
        self._terminate_connector(previous)
        self._notify("connection", "正在解析直播间分享链接…", {"connector_epoch": epoch})
        room = normalize_room(room)
        runtime = Path(r"D:\environment\shuzhiren\runtime\douyin-env\Scripts\python.exe")
        if not runtime.is_file():
            raise RuntimeError("抖音弹幕连接器运行环境不存在")
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        with self.connector_lock:
            if epoch != self.connector_epoch or (cancelled and cancelled()):
                return  # Disconnected while the short link was resolving.
            process = subprocess.Popen(
                [str(runtime), "-u", str(HERE / "douyin_bridge.py"), "--room", room],
                cwd=str(HERE), env=environment, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
            self.connector = process
            self.settings.room = room
            save_settings(self.settings)
            self._notify("connection", f"正在连接抖音直播间 {room}", {"connector_epoch": epoch})
            threading.Thread(target=self._read_connector,
                             args=(process, epoch), daemon=True).start()

    def _read_connector(self, process, epoch):
        last_error = None
        ended_text = None
        try:
            for line in process.stdout:
                if epoch != self.connector_epoch:
                    break
                if line.startswith("CHESS_LIVE_EVENT ") and len(line) < 10000:
                    try:
                        event = json.loads(line[len("CHESS_LIVE_EVENT "):])
                        if event.get("type") == "status":
                            if event.get("state") == "error":
                                last_error = event.get("text")
                            elif event.get("state") == "connected":
                                last_error = None
                            elif event.get("state") == "ended":
                                ended_text = event.get("text") or "直播已结束"
                        self.receive({**event, "connector_epoch": epoch})
                    except (ValueError, TypeError):
                        pass
            process.wait()
        finally:
            if process.stdout:
                process.stdout.close()
            if epoch == self.connector_epoch:
                self._reset_chat_room()
                self._notify("connection", ended_text or ("连接已停止：" + last_error if last_error
                             else "抖音连接器已停止，请重新连接"),
                             {"connector_epoch": epoch,
                              "state": "ended" if ended_text else "error" if last_error else "disconnected"})

    def disconnect(self, silent=False):
        with self.connector_lock:
            self.connector_epoch += 1
            process, self.connector = self.connector, None
        self._reset_chat_room()
        self._terminate_connector(process)
        if not silent:
            self._notify("connection", "已断开抖音直播间",
                         {"connector_epoch": self.connector_epoch, "state": "disconnected"})

    @staticmethod
    def _terminate_connector(process):
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def close(self):
        self.suspended = True
        self.chat_closed.set()
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
                                "move_selected", "move_command", "move_applied", "move_rejected", "speech_cancelled",
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
