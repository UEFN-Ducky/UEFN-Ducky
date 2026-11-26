"""macOS read-aloud: a fake AVSpeechSynthesizer, then the ``say`` fallback."""
import base64
import subprocess

from backend.voice import mac_tts as tts


class _FakeAV:
    def __init__(self, fail=False):
        self.fail, self.spoken = fail, []

    def voices(self):
        return [
            {"id": "com.apple.voice.compact.en-US.Samantha", "label": "Samantha", "lang": "en-US"},
            {"id": "com.apple.voice.compact.de-DE.Anna", "label": "Anna", "lang": "de-DE"},
        ]

    def speak(self, text, voice_id):
        if self.fail:
            raise RuntimeError("AVSpeechSynthesizer produced no audio.")
        self.spoken.append((text, voice_id))
        return b"RIFF....WAVEfmt ", "audio/wav"


_SAY_VOICES = "Albert              en_US    # Hello! My name is Albert.\nEddy (English (UK)) en_GB    # Hello! My name is Eddy.\n"


def _setup(monkeypatch, av, calls=None, say_ok=True):
    def run(command, **kwargs):
        if calls is not None:
            calls.append((command, kwargs))
        if command[:3] == ["say", "-v", "?"]:
            return subprocess.CompletedProcess(command, 0, _SAY_VOICES, "")
        if say_ok:
            with open(command[2], "wb") as f:
                f.write(b"FORM....AIFF")
        return subprocess.CompletedProcess(command, 0 if say_ok else 1, b"", b"" if say_ok else b"no voice")

    monkeypatch.setattr(tts.sys, "platform", "darwin")
    monkeypatch.setattr(tts, "_synths", lambda: [s for s in (av, tts._SaySynth()) if s])
    monkeypatch.setattr(tts.subprocess, "run", run)


def test_voice_list_comes_from_avspeech(monkeypatch):
    _setup(monkeypatch, _FakeAV())
    assert [v["label"] for v in tts._run()["voices"]] == ["Samantha", "Anna"]


def test_speaks_wav_with_chosen_voice(monkeypatch):
    av = _FakeAV()
    _setup(monkeypatch, av)
    out = tts._run("Hello arena", "Anna - Deutsch (Deutschland)")
    assert out == {"ok": True, "audio_base64": base64.b64encode(b"RIFF....WAVEfmt ").decode(), "mime": "audio/wav"}
    assert av.spoken == [("Hello arena", "com.apple.voice.compact.de-DE.Anna")]


def test_missing_voice_is_explicit(monkeypatch):
    _setup(monkeypatch, _FakeAV())
    assert tts._run("hi", "Klingon")["code"] == "voice_missing"


def test_avspeech_failure_falls_back_to_say_with_text_on_stdin(monkeypatch):
    calls = []
    _setup(monkeypatch, _FakeAV(fail=True), calls)
    text = 'Quotes " and $(commands) are text.'
    out = tts._run(text, "Eddy (English (UK))")
    assert out["mime"] == "audio/aiff" and base64.b64decode(out["audio_base64"]) == b"FORM....AIFF"
    command, kwargs = calls[-1]
    assert command[:2] == ["say", "-o"] and command[2].endswith(".aiff")
    assert command[3:] == ["-v", "Eddy (English (UK))", "-f", "-"]
    assert kwargs["input"] == text.encode()


def test_say_only_lists_voices_and_failure_is_an_error(monkeypatch):
    _setup(monkeypatch, None, say_ok=False)
    assert tts._run()["voices"][1] == {"id": "Eddy (English (UK))", "label": "Eddy (English (UK))", "lang": "en-GB"}
    assert tts._run("hi") == {"ok": False, "error": "no voice"}


def test_off_macos_is_unsupported(monkeypatch):
    monkeypatch.setattr(tts.sys, "platform", "linux")
    assert tts._run("hi")["ok"] is False
