"""Linux read-aloud through a fake espeak-ng: voices, WAV out, text on stdin only."""
import base64
import subprocess

from backend.voice import linux_tts as tts

_VOICES = (
    "Pty Language       Age/Gender VoiceName          File                 Other Languages\n"
    " 5  en-us           --/M      English_(America)  gmw/en-US            (en 3)\n"
    " 5  de              --/M      German             gmw/de\n"
)


def _fake(monkeypatch, calls, wav=b"RIFF....WAVEfmt "):
    def run(command, **kwargs):
        calls.append((command, kwargs))
        if "--voices" in command:
            return subprocess.CompletedProcess(command, 0, _VOICES, "")
        return subprocess.CompletedProcess(command, 0, wav, b"")

    monkeypatch.setattr(tts.sys, "platform", "linux")
    monkeypatch.setattr(tts, "_engine", lambda: "/usr/bin/espeak-ng")
    monkeypatch.setattr(tts.subprocess, "run", run)


def test_voice_list(monkeypatch):
    _fake(monkeypatch, [])
    assert tts._run()["voices"] == [
        {"id": "en-us", "label": "English (America)", "lang": "en-us"},
        {"id": "de", "label": "German", "lang": "de"},
    ]


def test_speaks_wav_with_chosen_voice_and_text_on_stdin(monkeypatch):
    calls = []
    _fake(monkeypatch, calls)
    text = 'Quotes " and $(commands) are text.'
    out = tts._run(text, "German - Deutsch")
    assert out == {"ok": True, "audio_base64": base64.b64encode(b"RIFF....WAVEfmt ").decode(), "mime": "audio/wav"}
    command, kwargs = calls[-1]
    assert command == ["/usr/bin/espeak-ng", "--stdout", "-v", "de", "--stdin"]
    assert kwargs["input"] == text.encode()
    assert kwargs["timeout"] == 60


def test_missing_voice_and_engine_are_explicit(monkeypatch):
    _fake(monkeypatch, [])
    assert tts._run("hi", "Klingon")["code"] == "voice_missing"
    monkeypatch.setattr(tts, "_engine", lambda: "")
    assert tts._run("hi")["code"] == "voice_missing"


def test_engine_failure_is_an_error(monkeypatch):
    _fake(monkeypatch, [], wav=b"")
    assert tts._run("hi") == {"ok": False, "error": "Linux speech failed."}
