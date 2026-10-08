"""Windows Speech runs without browser synthesis or an API key."""
import base64
import io
import json
import subprocess
import sys
import wave

import pytest

from backend.voice import windows_tts as tts


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Speech")
def test_installed_voice_synthesizes_real_wav():
    voices = tts._run()
    assert voices["ok"], voices
    if not voices["voices"]:
        pytest.skip("No Windows voices installed")
    voice = voices["voices"][0]["id"]
    result = tts._run("Windows speech playback is ready.", voice)
    assert result["ok"], result
    with wave.open(io.BytesIO(base64.b64decode(result["audio_base64"]))) as audio:
        assert audio.getnframes() > audio.getframerate()
        assert audio.getsampwidth() == 2
    missing = tts._run("Read this.", "Ducky nonexistent voice")
    assert missing["ok"] is False
    assert missing["code"] == "voice_missing"


def test_text_is_json_stdin_and_helper_is_hidden(monkeypatch):
    captured = {}
    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, '{"ok":true,"audio_base64":"d2F2"}', "")
    monkeypatch.setattr(tts.sys, "platform", "win32")
    monkeypatch.setattr(tts.subprocess, "run", run)
    text = 'Quotes " and $(commands) are spoken as text.'
    assert tts._run(text)["ok"]
    assert json.loads(captured["input"])["text"] == text
    assert text not in " ".join(captured["command"])
    assert captured["timeout"] == 60
    assert captured["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_helper_failure_is_an_explicit_error(monkeypatch):
    monkeypatch.setattr(tts.sys, "platform", "win32")
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "Speech unavailable"))
    assert tts._run("Read it.") == {"ok": False, "error": "Speech unavailable"}
