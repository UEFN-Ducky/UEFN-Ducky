"""Linux dictation for the desktop panel: same API and events as ``windows_speech``.

Linux has no built-in dictation engine, so the mic is captured through
PortAudio (``sounddevice``, which reaches PipeWire / PulseAudio) and the take is
sent to the app's existing Whisper path (``transcription.transcribe_audio``).

Events (polled like Windows): started, speech_started, speech_stopped, final,
error, ended.
"""

from __future__ import annotations

import base64
import io
import sys
import threading
import time
import uuid
import wave
from collections import deque
from typing import Any

_RATE = 16000
_MAX_SECONDS = 600  # ponytail: one take per session, capped at 10 min; chunk into phrases if long dictation matters
_ENDED_EVENTS = frozenset({"ended"})


def linux_speech_supported() -> bool:
    return sys.platform.startswith("linux")


_cond = threading.Condition()
_events: deque[tuple[int, dict[str, Any]]] = deque(maxlen=4000)
_seq = 0
_sessions: dict[str, dict[str, Any]] = {}


def _push(event: dict[str, Any]) -> None:
    global _seq
    with _cond:
        _seq += 1
        _events.append((_seq, event))
        _cond.notify_all()


def _open_stream(chunks: list[bytes]):
    """A started PortAudio input stream that appends 16 kHz mono int16 PCM to ``chunks``."""
    import sounddevice as sd  # noqa: PLC0415 — optional; missing PortAudio must not break import

    limit = _RATE * 2 * _MAX_SECONDS
    size = [0]

    def callback(data, _frames, _time, _status) -> None:
        if size[0] < limit:
            raw = bytes(data)
            chunks.append(raw)
            size[0] += len(raw)

    stream = sd.RawInputStream(samplerate=_RATE, channels=1, dtype="int16", callback=callback)
    stream.start()
    return stream


def _wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(pcm)
    return out.getvalue()


def _transcribe(sid: str, pcm: bytes) -> None:
    from backend.voice.transcription import transcribe_audio

    _push({"t": "speech_stopped", "sid": sid})
    if pcm:
        result = transcribe_audio(base64.b64encode(_wav(pcm)).decode("ascii"), "audio/wav")
        text = str(result.get("text") or "").strip()
        if not result.get("ok"):
            _push({"t": "error", "sid": sid, "code": "failed", "message": str(result.get("error") or "Transcription failed.")})
        elif text:
            _push({"t": "final", "sid": sid, "text": text, "confidence": "High"})
    _push({"t": "ended", "sid": sid, "status": "Success"})


def _close(sid: str) -> dict[str, Any] | None:
    with _cond:
        session = _sessions.pop(sid, None)
    if session:
        try:
            session["stream"].stop()
            session["stream"].close()
        except Exception:  # noqa: BLE001 — device may already be gone
            pass
    return session


def _unsupported() -> dict[str, Any]:
    return {"ok": False, "code": "unsupported", "error": "Linux speech is only available on Linux."}


def prewarm() -> dict[str, Any]:
    if not linux_speech_supported():
        return _unsupported()
    try:
        import sounddevice  # noqa: F401, PLC0415
    except Exception as exc:  # noqa: BLE001 — OSError when libportaudio2 is missing
        return {"ok": False, "code": "failed", "error": f"Microphone support is missing ({exc}). Install libportaudio2."}
    return {"ok": True}


def start_session(lang: str = "") -> dict[str, Any]:
    """Open the default mic; returns the session id and the event cursor to poll from."""
    if not linux_speech_supported():
        return _unsupported()
    warm = prewarm()
    if not warm["ok"]:
        return warm
    sid = uuid.uuid4().hex[:12]
    chunks: list[bytes] = []
    try:
        stream = _open_stream(chunks)
    except Exception as exc:  # noqa: BLE001 — sounddevice.PortAudioError when no input device
        return {"ok": False, "code": "no_mic", "error": f"No microphone found. Plug one in or pick it in your sound settings. ({exc})"}
    with _cond:
        _sessions[sid] = {"stream": stream, "chunks": chunks}
    _push({"t": "started", "sid": sid, "lang": lang})
    _push({"t": "speech_started", "sid": sid})
    with _cond:
        return {"ok": True, "session": sid, "cursor": _seq - 2, "lang": lang}


def poll_session(sid: str, cursor: int, wait_ms: int = 400) -> dict[str, Any]:
    """Long-poll: events after ``cursor`` for ``sid``. ``active`` is false once the session ended."""
    sid = str(sid or "")
    try:
        cur = int(cursor)
    except (TypeError, ValueError):
        cur = 0
    deadline = time.monotonic() + max(0, min(int(wait_ms or 0), 2000)) / 1000.0
    with _cond:
        while True:
            events = [ev for seq, ev in _events if seq > cur and ev.get("sid") == sid]
            if events or time.monotonic() >= deadline:
                break
            _cond.wait(max(0.0, deadline - time.monotonic()))
        next_cursor = _seq
    ended = any(ev.get("t") in _ENDED_EVENTS for ev in events)
    return {"ok": True, "events": events, "cursor": next_cursor, "active": not ended}


def stop_session(sid: str) -> dict[str, Any]:
    """Graceful stop: the take is transcribed and arrives as a final before ``ended``."""
    sid = str(sid or "")
    session = _close(sid)
    if session is None:
        _push({"t": "ended", "sid": sid, "status": "NotRunning"})
        return {"ok": True}
    pcm = b"".join(session["chunks"])
    threading.Thread(target=_transcribe, args=(sid, pcm), name="linstt-whisper", daemon=True).start()
    return {"ok": True}


def cancel_session(sid: str) -> dict[str, Any]:
    sid = str(sid or "")
    _close(sid)
    _push({"t": "ended", "sid": sid, "status": "UserCanceled"})
    return {"ok": True}


def shutdown() -> None:
    for sid in list(_sessions):
        _close(sid)
