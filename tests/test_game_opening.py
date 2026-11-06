from collections import deque
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from board import START_FEN
from broadcast_flow import BroadcastFlow
from control_panel import ControlPanel
from live_host import ChessEventTailer, LiveController, LiveSettings, SpeechPipeline


class GameOpeningTests(unittest.TestCase):
    def test_twelve_openings_rotate_across_games_and_use_the_confirmed_side(self):
        for side, label in (("r", "红"), ("b", "黑"), (None, None)):
            flow, lines = BroadcastFlow(), []
            for _ in range(12):
                flow.start({})
                event = {"fen": START_FEN, "my_color": side, "own_moves": 0, "new_game": True}
                lines.append(flow.position(event))
                self.assertEqual(flow.position(event), "")
            self.assertEqual(len(set(lines)), 12)
            self.assertTrue(all(len(line) <= 30 for line in lines))
            if label:
                self.assertTrue(all("执" + label in line for line in lines))
            else:
                self.assertFalse(any("执红" in line or "执黑" in line for line in lines))
            self.assertFalse(any(word in "".join(lines) for word in ("赢定", "必胜", "又吓跑", "猜中")))

    def test_midgame_takeover_does_not_use_or_consume_a_new_game_line(self):
        flow = BroadcastFlow()
        flow.start({})
        self.assertIn("继续当前这盘", flow.position({"fen": START_FEN, "own_moves": 0, "new_game": False}))
        self.assertEqual(flow.open_sequence, 0)
        flow.start({"dry_run": True})
        self.assertEqual(flow.position({"fen": START_FEN, "new_game": True}), "")

    def test_real_event_file_position_reaches_tts_and_spoken_log_once(self):
        heard, records = threading.Event(), []
        def notify(kind, text, data):
            if kind == "reply":
                records.append((kind, text, data)); heard.set()
        settings = LiveSettings(speech_enabled=True, cooldown=0)
        pipeline = SpeechPipeline(settings, notify,
            llm=Mock(side_effect=AssertionError("开场不调用模型")), tts=Mock(return_value="mock.wav"))
        controller = object.__new__(LiveController)
        controller.settings, controller.pipeline = settings, pipeline
        controller._notify = Mock(); controller._reset_opening = Mock()
        controller.recent_chess_topics = deque()
        with tempfile.TemporaryDirectory() as folder, \
             patch("live_host.winsound.PlaySound"), \
             patch.object(pipeline, "_audio_duration", return_value=.01), \
             patch.object(pipeline, "_prune_audio"):
            path = Path(folder) / "events.jsonl"
            path.touch()
            tailer = ChessEventTailer(path, controller.submit_chess)
            tailer.start()
            try:
                time.sleep(.1)  # Establish the tailer's initial end offset.
                event = {"event": "position", "fen": START_FEN, "my_color": "b",
                         "own_moves": 0, "new_game": True}
                with path.open("a", encoding="utf-8") as stream:
                    for item in ({"event": "start", "dry_run": False}, event, event):
                        stream.write(json.dumps(item) + "\n")
                self.assertTrue(heard.wait(2), "确认新棋盘的position事件没有进入语音链路")
                tailer.close(); tailer.join(1)
                self.assertEqual(len(records), 1)
                _, text, data = records[0]
                self.assertIn("执黑", text)
                self.assertEqual(data["kind"], "新局开场")
                self.assertEqual(text, pipeline.tts.call_args.args[0])
                header, body = ControlPanel._speech_log_entry({"kind": "reply", "text": text, "data": data})
                self.assertEqual((header, body), ("新局开始", text))
                pipeline.llm.assert_not_called()
            finally:
                tailer.close(); tailer.join(1); pipeline.close()

    def test_first_move_preserves_the_opening_but_next_game_cancels_it(self):
        with patch.object(SpeechPipeline, "_ensure_worker", return_value=True), patch("live_host.winsound.PlaySound"):
            pipeline = SpeechPipeline(LiveSettings(), Mock())
            try:
                pipeline.reset_opening_speech()
                pipeline.submit(1, "新局开场", {}, direct_text="新一盘，咱们执红。")
                pipeline.invalidate_chess()
                self.assertEqual([job[4] for job in pipeline.jobs], ["新局开场"])
                pipeline.reset_opening_speech()
                self.assertEqual(pipeline.jobs, [])
            finally:
                pipeline.close()

    def test_an_opening_still_synthesizing_at_game_end_cannot_play_late(self):
        ready, release, done = threading.Event(), threading.Event(), threading.Event()
        records = []
        def tts(*args):
            ready.set(); release.wait(2); return "mock.wav"
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True, cooldown=0),
            lambda kind, text, data: records.append((kind, text)), tts=tts)
        job = (2, 1, time.monotonic(), pipeline.cancel_epoch, "新局开场", {"_opening_epoch": 0}, "新一盘，咱们执红。")
        def run():
            try: pipeline._run_job(job)
            finally: done.set()
        with patch("live_host.winsound.PlaySound") as sound:
            thread = threading.Thread(target=run); thread.start()
            try:
                self.assertTrue(ready.wait(1))
                pipeline.reset_opening_speech(); release.set()
                self.assertTrue(done.wait(1))
                self.assertFalse(any(kind == "reply" for kind, _ in records))
                self.assertFalse(any(call.args[0] is not None for call in sound.call_args_list))
            finally:
                release.set(); thread.join(1); pipeline.close()


if __name__ == "__main__":
    unittest.main()
