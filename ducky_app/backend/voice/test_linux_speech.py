"""Linux dictation: a fake mic stream goes through to the Whisper transcriber."""
import base64
import io
import sys
import types
import wave

from backend.voice import linux_speech as stt
from backend.voice import transcription


class _FakeStream:
    def __init__(self, callback, **kwargs):
        self.callback, self.kwargs, self.closed = callback, kwargs, False

    def start(self):
        for _ in range(3):  # three 0.1 s blocks of "speech"
            self.callback(b"\x01\x00" * 1600, 1600, None, None)

    def stop(self):
        pass

    def close(self):
        self.closed = True


def _fake_mic(monkeypatch, fail=False):
    streams = []

    def make(**kwargs):
        if fail:
            raise OSError("Error querying device -1")
        streams.append(_FakeStream(**kwargs))
        return streams[-1]

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawInputStream=make))
    monkeypatch.setattr(stt.sys, "platform", "linux")
    return streams


def _drain(sid, cursor):
    events = []
    while True:
        out = stt.poll_session(sid, cursor, 2000)
        events += out["events"]
        cursor = out["cursor"]
        if not out["active"]:
            return events


def test_mic_take_is_transcribed_as_a_final(monkeypatch):
    streams = _fake_mic(monkeypatch)
    heard = []

    def transcribe(b64, mime):
        with wave.open(io.BytesIO(base64.b64decode(b64))) as w:
            heard.append((mime, w.getframerate(), w.getnchannels(), w.getnframes()))
        return {"ok": True, "text": "build the arena"}

    monkeypatch.setattr(transcription, "transcribe_audio", transcribe)
    started = stt.start_session("en-US")
    assert started["ok"], started
    assert streams[0].kwargs == {"samplerate": 16000, "channels": 1, "dtype": "int16"}
    assert stt.stop_session(started["session"]) == {"ok": True}
    events = _drain(started["session"], started["cursor"])
    assert [e["t"] for e in events] == ["started", "speech_started", "speech_stopped", "final", "ended"]
    assert events[3]["text"] == "build the arena"
    assert heard == [("audio/wav", 16000, 1, 4800)]
    assert streams[0].closed


def test_transcriber_error_reaches_the_poll(monkeypatch):
    _fake_mic(monkeypatch)
    monkeypatch.setattr(transcription, "transcribe_audio", lambda b64, mime: {"ok": False, "error": "no key"})
    started = stt.start_session()
    stt.stop_session(started["session"])
    events = _drain(started["session"], started["cursor"])
    assert {"t": "error", "sid": started["session"], "code": "failed", "message": "no key"} in events


def test_cancel_skips_transcription_and_no_mic_is_explicit(monkeypatch):
    _fake_mic(monkeypatch)
    monkeypatch.setattr(transcription, "transcribe_audio", lambda *a: (_ for _ in ()).throw(AssertionError("called")))
    started = stt.start_session()
    stt.cancel_session(started["session"])
    assert [e["t"] for e in _drain(started["session"], started["cursor"])][-1] == "ended"
    _fake_mic(monkeypatch, fail=True)
    assert stt.start_session()["code"] == "no_mic"


def test_off_linux_is_unsupported(monkeypatch):
    monkeypatch.setattr(stt.sys, "platform", "win32")
    assert stt.start_session()["code"] == "unsupported"
