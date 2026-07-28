"""Tests for batch Whisper transcription helpers."""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

from backend.voice.transcription import (
    VoiceError,
    _multipart_body,
    create_realtime_token,
    transcribe_audio,
)


def test_multipart_contains_model_and_file():
    body = _multipart_body(
        fields={"model": "whisper-1"},
        file_field="file",
        filename="dictation.webm",
        file_bytes=b"AUDIO",
        file_mime="audio/webm",
        boundary="bound123",
    )
    text = body.decode("latin-1")
    assert 'name="model"' in text
    assert "whisper-1" in text
    assert 'filename="dictation.webm"' in text
    assert "AUDIO" in text
    assert body.endswith(b"--bound123--\r\n")


def test_transcribe_missing_key():
    with patch(
        "backend.voice.transcription._openai_key",
        side_effect=VoiceError("No OpenAI API key — open Settings"),
    ):
        result = transcribe_audio(base64.b64encode(b"x").decode("ascii"), "audio/webm")
    assert result["ok"] is False
    assert "OpenAI" in result["error"]


def test_transcribe_audio_ok():
    audio_b64 = base64.b64encode(b"fake-audio").decode("ascii")
    logged: list[dict] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"text": "hello duck"}).encode("utf-8")

    with patch("backend.voice.transcription._openai_key", return_value="sk-test"):
        with patch("backend.voice.transcription.urlopen", return_value=_Resp()):
            with patch(
                "backend.voice.transcription._log_voice_usage",
                side_effect=lambda **kw: logged.append(kw),
            ):
                result = transcribe_audio(audio_b64, "audio/webm")
    assert result["ok"] is True
    assert result["text"] == "hello duck"
    assert logged and logged[0]["model"]


def test_transcribe_empty_audio():
    with patch("backend.voice.transcription._openai_key", return_value="sk-test"):
        result = transcribe_audio("", "audio/webm")
    assert result["ok"] is False
    assert "empty" in result["error"].lower()


def test_create_realtime_token_ok():
    payload = {"value": "ek_test_token", "expires_at": 123}
    logged: list[dict] = []

    with patch("backend.voice.transcription._openai_key", return_value="sk-test"):
        with patch("backend.voice.transcription._post_json", return_value=payload):
            with patch(
                "backend.voice.transcription._log_voice_usage",
                side_effect=lambda **kw: logged.append(kw),
            ):
                result = create_realtime_token()
    assert result["ok"] is True
    assert result["value"] == "ek_test_token"
    assert "realtime" in result["ws_url"]
    assert logged and "transcribe" in logged[0]["model"]


def test_create_realtime_token_missing_key():
    with patch(
        "backend.voice.transcription._openai_key",
        side_effect=VoiceError("No OpenAI API key — open Settings"),
    ):
        result = create_realtime_token()
    assert result["ok"] is False
    assert "OpenAI" in result["error"]


def test_summary_prompt_verbatim_short():
    from backend.voice.summary import build_spoken_summary_prompt

    assert build_spoken_summary_prompt("Short reply.") is None


def test_summary_prompt_long():
    from backend.voice.summary import build_spoken_summary_prompt

    long = "x" * 250
    prompt = build_spoken_summary_prompt(long)
    assert prompt is not None
    system, user = prompt
    assert "spoken voice" in system.lower()
    assert "never read code" in system.lower()
    assert long in user


def test_summary_prompt_code_block_always_summarizes():
    from backend.voice.summary import build_spoken_summary_prompt

    short_code = "```\nprint('hi')\n```"
    prompt = build_spoken_summary_prompt(short_code)
    assert prompt is not None
    system, _user = prompt
    assert "never read code" in system.lower()
