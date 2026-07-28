"""Voice: transcription + spoken-summary helpers (self-contained)."""

# Lazy re-exports — keep ``import backend.voice.transcription`` working under pytest
# without requiring package install at collection time via this __init__.

__all__ = [
    "transcribe_audio",
    "create_realtime_token",
    "build_spoken_summary_prompt",
    "summarize_for_speech",
]


def __getattr__(name: str):
    if name in ("transcribe_audio", "create_realtime_token"):
        from backend.voice.transcription import create_realtime_token, transcribe_audio

        return transcribe_audio if name == "transcribe_audio" else create_realtime_token
    if name in ("build_spoken_summary_prompt", "summarize_for_speech"):
        from backend.voice.summary import build_spoken_summary_prompt, summarize_for_speech

        return build_spoken_summary_prompt if name == "build_spoken_summary_prompt" else summarize_for_speech
    raise AttributeError(name)
