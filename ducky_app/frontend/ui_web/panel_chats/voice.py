"""Voice for chats: Whisper / Realtime transcription, Windows text-to-speech and dictation,
Windows speech settings, and a reply's short spoken summary."""

from __future__ import annotations

from typing import Any


def voice_transcribe_audio(self, b64_audio: str, mime: str = "audio/webm") -> dict[str, Any]:
    """Batch Whisper transcription (prefer via bridge_job_start)."""
    from backend.voice.transcription import transcribe_audio

    return transcribe_audio(str(b64_audio or ""), str(mime or "audio/webm"))


def voice_create_realtime_token(self) -> dict[str, Any]:
    """Mint an ephemeral Realtime client secret for browser streaming STT."""
    from backend.voice.transcription import create_realtime_token

    return create_realtime_token()


def voice_win_tts_start(self, text: str, voice_id: str = "") -> dict[str, Any]:
    from backend.voice.windows_tts import start

    return start(str(text or ""), str(voice_id or ""))


def voice_win_tts_voices_start(self) -> dict[str, Any]:
    from backend.voice.windows_tts import voices_start

    return voices_start()


def voice_win_tts_poll(self, job_id: str) -> dict[str, Any]:
    from backend.voice.windows_tts import poll

    return poll(str(job_id or ""))


def voice_win_stt_prewarm(self) -> dict[str, Any]:
    """Spawn the Windows speech helper early so the first mic press starts fast."""
    from backend.voice.windows_speech import prewarm

    return prewarm()


def voice_win_stt_start(self, lang: str = "") -> dict[str, Any]:
    """Start Windows dictation (desktop mic, no key); returns session + poll cursor."""
    from backend.voice.windows_speech import start_session

    return start_session(str(lang or ""))


def voice_win_stt_poll(self, session: str, cursor: int = 0, wait_ms: int = 400) -> dict[str, Any]:
    """Long-poll Windows dictation events (interim / final / ended)."""
    from backend.voice.windows_speech import poll_session

    return poll_session(str(session or ""), cursor, wait_ms)


def voice_win_stt_stop(self, session: str) -> dict[str, Any]:
    from backend.voice.windows_speech import stop_session

    return stop_session(str(session or ""))


def voice_win_stt_cancel(self, session: str) -> dict[str, Any]:
    from backend.voice.windows_speech import cancel_session

    return cancel_session(str(session or ""))


def voice_open_windows_settings(self, page: str) -> dict[str, Any]:
    """Open Windows Settings → speech / microphone privacy so the user can turn it on."""
    from backend.voice.windows_speech import open_windows_settings

    return open_windows_settings(str(page or ""))


def voice_summarize_reply(self, assistant_text: str, model: str = "") -> dict[str, Any]:
    """Short spoken summary via the cheap voice model (prefer via bridge_job_start)."""
    from backend.voice.summary import summarize_for_speech

    return summarize_for_speech(str(assistant_text or ""), model=str(model or ""))
