import io
import json
import queue
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import wave

import local_tts_client as client
import tts_backend
from tts_requests import SynthesisCancelled, synthesis_context
from health_check import collect_readiness
from live_host import LiveController, LiveSettings
from control_panel import ControlPanel


def wav_bytes(rate=24000, samples=2400):
    result = io.BytesIO()
    with wave.open(result,"wb") as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(rate)
        stream.writeframes(b"\x40\x01" * samples)
    return result.getvalue()


class LocalTTSIntegrationTests(unittest.TestCase):
    def test_local_voice_uses_common_cancellation_gate_and_never_calls_cloud(self):
        with patch.object(client,"synthesize",return_value="local.wav") as local, \
             patch.object(tts_backend.bailian_tts,"synthesize") as cloud:
            self.assertEqual(tts_backend.synthesize("你好",tts_backend.LOCAL_VOICE),"local.wav")
            cloud.assert_not_called()
            with synthesis_context(valid=lambda:False):
                with self.assertRaises(SynthesisCancelled):
                    tts_backend.synthesize("过期消息",tts_backend.LOCAL_VOICE)
            local.assert_called_once_with("你好")

    def test_cloud_voices_keep_existing_adapter(self):
        with patch.object(tts_backend.bailian_tts,"synthesize",return_value="cloud.wav") as cloud:
            self.assertEqual(tts_backend.synthesize("你好","Maia"),"cloud.wav")
            cloud.assert_called_once_with("你好","Maia")

    def test_local_voice_does_not_depend_on_cloud_config(self):
        with patch.object(tts_backend.bailian_tts,"available_voices",side_effect=FileNotFoundError):
            self.assertEqual(tts_backend.available_voices(),[tts_backend.LOCAL_VOICE])

    def test_reuses_running_service_without_loading_another_gpu_model(self):
        with patch.object(client,"_service_state",return_value=True), \
             patch.object(client.subprocess,"Popen") as launch:
            client.ensure_service()
            launch.assert_not_called()

    def test_sse_heartbeat_is_not_completion_and_voice_is_verified(self):
        payload=[{"url":client.SERVICE_URL+"/gradio_api/file=sample.wav"},
                 {"voice_id":"doubao","backend":"local"}]
        events=io.BytesIO(("event: heartbeat\ndata: null\n\nevent: complete\ndata: "+json.dumps(payload)+"\n\n").encode())
        with patch.object(client,"_json",return_value={"event_id":"sample-event"}), \
             patch.object(client._opener,"open",side_effect=[events,io.BytesIO(wav_bytes())]):
            self.assertEqual(client._generate("你好"),wav_bytes())

    def test_remote_audio_url_cannot_be_accepted_as_local_synthesis(self):
        payload=[{"url":"https://example.invalid/audio.wav"},{"voice_id":"doubao","backend":"local"}]
        events=io.BytesIO(("event: complete\ndata: "+json.dumps(payload)+"\n").encode())
        with patch.object(client,"_json",return_value={"event_id":"sample"}), \
             patch.object(client._opener,"open",return_value=events) as opened:
            with self.assertRaisesRegex(RuntimeError,"非本机"):
                client._generate("你好")
            self.assertEqual(opened.call_count,1)

    def test_long_text_is_preserved_and_merged_into_one_playable_wav(self):
        text="棋友们，咱们接着看棋。"*30
        chunks=client._split_text(text)
        self.assertEqual("".join(chunks),text)
        self.assertTrue(all(len(part)<=180 for part in chunks))
        with tempfile.TemporaryDirectory() as folder, patch.object(client,"AUDIO_DIR",Path(folder)), \
             patch.object(client,"ensure_service"), patch.object(client,"_generate",return_value=wav_bytes()) as generate:
            output=client.synthesize(text)
            with wave.open(output,"rb") as stream:
                self.assertEqual(stream.getnframes(),2400*len(chunks))
            self.assertEqual(generate.call_count,len(chunks))

    def test_mid_generation_cancellation_never_produces_playback_file(self):
        alive=[True]
        def finish_late(text):
            alive[0]=False
            return wav_bytes()
        with tempfile.TemporaryDirectory() as folder, patch.object(client,"AUDIO_DIR",Path(folder)), \
             patch.object(client,"ensure_service"), patch.object(client,"_generate",side_effect=finish_late), \
             synthesis_context(valid=lambda:alive[0]):
            with self.assertRaises(SynthesisCancelled):client.synthesize("旧回复")
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_missing_reference_is_reported_without_using_demo_voice(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(client,"LOCAL_ROOT",Path(folder)):
            (Path(folder)/".venv/Scripts").mkdir(parents=True)
            (Path(folder)/".venv/Scripts/python.exe").touch()
            model=Path(folder)/"models/Qwen3-TTS-12Hz-0.6B-Base/model.safetensors"
            model.parent.mkdir(parents=True);model.touch()
            ready,detail=client.local_readiness()
            self.assertFalse(ready)
            self.assertIn("参考声音",detail)

    def test_readiness_for_local_voice_does_not_require_cloud_key(self):
        cloud_key=Mock(side_effect=AssertionError("Must not read cloud key"))
        items=collect_readiness({"voice":tts_backend.LOCAL_VOICE,"speech_enabled":True},
            device_provider=lambda:[],engine_path=__file__,llm_key_loader=lambda:"key",
            tts_key_loader=cloud_key,local_tts_check=lambda:(True,"已就绪"))
        self.assertEqual(next(item for item in items if item["key"]=="tts")["level"],"ok")
        cloud_key.assert_not_called()

    def test_switching_voice_cancels_old_prepared_speech(self):
        controller=object.__new__(LiveController)
        controller.settings=LiveSettings(voice="Maia")
        controller.pipeline=Mock();controller._notify=Mock()
        with patch("live_host.available_voices",return_value=["Maia",tts_backend.LOCAL_VOICE]), \
             patch("live_host.save_settings"):
            controller.configure(voice=tts_backend.LOCAL_VOICE)
        controller.pipeline.clear.assert_called_once_with(stop_audio=True)

    def test_startup_only_initializes_tts_and_reports_errors_via_ui_queue(self):
        panel=object.__new__(ControlPanel);panel.output=queue.Queue()
        with patch("control_panel.ensure_local_tts",side_effect=RuntimeError("加载失败")) as start:
            panel._start_local_tts()
            start.assert_called_once_with()
        self.assertEqual(panel.output.get_nowait(),("local_tts_startup_error","加载失败"))


if __name__ == "__main__":
    unittest.main()
