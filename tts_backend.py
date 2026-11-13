"""Voice selection shared by settings, readiness and the speech pipeline."""
import bailian_tts
import local_tts_client
from tts_requests import limited_request

LOCAL_VOICE = "豆包（本地）"


def available_voices():
    try:
        cloud = bailian_tts.available_voices()
    except (OSError, ValueError):
        cloud = []
    return list(dict.fromkeys(cloud + [LOCAL_VOICE]))


def synthesize(text, voice="Maia"):
    if voice == LOCAL_VOICE:
        return limited_request(lambda: local_tts_client.synthesize(text))
    return bailian_tts.synthesize(text, voice)


def generation_status(voice):
    return "正在生成本地豆包语音" if voice == LOCAL_VOICE else "正在生成百炼语音"
