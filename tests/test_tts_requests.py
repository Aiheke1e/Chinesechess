import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bailian_tts
import tts_requests
from live_host import LiveSettings, SpeechPipeline
from tts_requests import RequestLimiter, SynthesisCancelled, TTSRateLimitError


class TTSRequestTests(unittest.TestCase):
    def test_burst_is_serial_and_paced(self):
        limiter = RequestLimiter(interval=.015)
        starts, active = [], []
        def request():
            self.assertFalse(active)
            active.append(True)
            starts.append(time.monotonic())
            time.sleep(.003)
            active.pop()
            return "audio"
        result = []
        threads = [threading.Thread(target=lambda: result.append(limiter.run(request))) for _ in range(10)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(2)
        self.assertEqual(result, ["audio"] * 10)
        self.assertTrue(all(b - a >= .014 for a, b in zip(starts, starts[1:])))

    def test_live_move_overtakes_unsent_cues_and_cancelled_cue_makes_no_request(self):
        limiter = RequestLimiter(interval=0)
        started, release, cancelled = (threading.Event() for _ in range(3))
        calls, skipped = [], []
        def busy():
            started.set()
            release.wait(2)
        first = threading.Thread(target=lambda: limiter.run(busy))
        first.start()
        self.assertTrue(started.wait(1))
        def queued(name, priority, valid=lambda: True):
            try:
                limiter.run(lambda: calls.append(name), priority=priority, valid=valid)
            except SynthesisCancelled:
                skipped.append(name)
        threads = [threading.Thread(target=queued, args=args) for args in (
            ("step9", (3, 9)), ("step2", (3, 2)), ("old-step", (3, 1), lambda: not cancelled.is_set()),
            ("move", (2, 0)))]
        for thread in threads: thread.start()
        deadline = time.monotonic() + 1
        while len(limiter.pending) < 5 and time.monotonic() < deadline: time.sleep(.002)
        cancelled.set()
        release.set()
        first.join(1)
        for thread in threads: thread.join(1)
        self.assertEqual(calls, ["move", "step2", "step9"])
        self.assertEqual(skipped, ["old-step"])

    def test_only_rate_limit_retries_and_total_attempts_are_bounded(self):
        limiter = RequestLimiter(interval=0, backoffs=(.01, .02))
        request = Mock(side_effect=[TTSRateLimitError("Throttling.RateQuota"),
                                   TTSRateLimitError("Throttling.RateQuota"), "ok"])
        notices = Mock()
        self.assertEqual(limiter.run(request, on_retry=notices), "ok")
        self.assertEqual(request.call_count, 3)
        self.assertEqual([call.args[0] for call in notices.call_args_list], [1, 2])
        request = Mock(side_effect=TTSRateLimitError("Throttling.RateQuota"))
        with self.assertRaises(TTSRateLimitError): limiter.run(request)
        self.assertEqual(request.call_count, 3)
        request = Mock(side_effect=RuntimeError("InvalidApiKey"))
        with self.assertRaisesRegex(RuntimeError, "InvalidApiKey"): limiter.run(request)
        self.assertEqual(request.call_count, 1)

    def test_cancel_during_backoff_does_not_send_another_request(self):
        limiter = RequestLimiter(interval=0, backoffs=(.1,))
        alive = [True]
        def request():
            alive[0] = False
            raise TTSRateLimitError("Throttling.RateQuota")
        called = Mock(side_effect=request)
        with self.assertRaises(SynthesisCancelled): limiter.run(called, valid=lambda: alive[0])
        self.assertEqual(called.call_count, 1)

    def test_adapter_preserves_rate_limit_code_without_leaking_response(self):
        body = json.dumps({"code": "Throttling.RateQuota", "message": "private response",
                           "request_id": "request-1"}).encode() + b"\n429"
        with patch.object(bailian_tts, "load_api_key", return_value="secret"), patch.object(
                bailian_tts.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=body)):
            with self.assertRaises(TTSRateLimitError) as error:
                bailian_tts._request("https://example.invalid", {})
        self.assertEqual(error.exception.request_id, "request-1")
        self.assertNotIn("private response", str(error.exception))
        self.assertNotIn("secret", str(error.exception))

    def test_pipeline_prioritises_actual_move_and_cancels_old_generation_before_api(self):
        limiter = RequestLimiter(interval=0)
        started, release = threading.Event(), threading.Event()
        first = threading.Thread(target=lambda: limiter.run(lambda: (started.set(), release.wait(2))))
        first.start()
        self.assertTrue(started.wait(1))
        backend = Mock(return_value="audio.wav")
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=True), Mock(),
            tts=lambda *args: tts_requests.limited_request(backend))
        state = {"epoch": pipeline.cancel_epoch, "chess_epoch": pipeline.chess_epoch}
        skipped = threading.Event()
        def stale():
            try:
                pipeline._synthesize("旧步骤", "沙盘推演解说", {"_sandbox_cue": True, "沙盘步骤": "9"}, state)
            except SynthesisCancelled: skipped.set()
        with patch.object(tts_requests, "_limiter", limiter):
            thread = threading.Thread(target=stale)
            thread.start()
            deadline = time.monotonic() + 1
            while len(limiter.pending) < 2 and time.monotonic() < deadline: time.sleep(.002)
            state["approved"] = False
            release.set()
            first.join(1)
            thread.join(1)
        pipeline.close()
        self.assertTrue(skipped.is_set())
        backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
