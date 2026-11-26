"""Offline macOS speech synthesis (same API and result shape as ``linux_tts``).

The UI plays the returned audio itself (read-along, speed, pause), so speech is
rendered to a file, never to the speakers: AVSpeechSynthesizer's
``writeUtterance:toBufferCallback:`` through pyobjc, and ``say -o file.aiff`` when
pyobjc is missing or AVFoundation fails.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any

_TIMEOUT = 60


def _tmp(suffix: str) -> str:
    fd, path = tempfile.mkstemp(prefix="ducky-tts-", suffix=suffix)
    os.close(fd)
    return path


def _read_and_remove(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


class _AVSynth:
    """AVSpeechSynthesizer rendering PCM buffers into a WAV file."""

    def __init__(self) -> None:
        import AVFoundation  # noqa: PLC0415 — pyobjc-framework-AVFoundation, darwin only
        import Foundation  # noqa: PLC0415

        self.av, self.fd = AVFoundation, Foundation

    def voices(self) -> list[dict[str, str]]:
        return [
            {"id": str(v.identifier()), "label": str(v.name()), "lang": str(v.language())}
            for v in self.av.AVSpeechSynthesisVoice.speechVoices()
        ]

    def speak(self, text: str, voice_id: str) -> tuple[bytes, str]:
        av = self.av
        utterance = av.AVSpeechUtterance.speechUtteranceWithString_(text)
        if voice_id:
            utterance.setVoice_(av.AVSpeechSynthesisVoice.voiceWithIdentifier_(voice_id))
        path = _tmp(".wav")  # AVAudioFile picks the container from the extension
        url = self.fd.NSURL.fileURLWithPath_(path)
        done = threading.Event()
        state: dict[str, Any] = {}

        def on_buffer(buf) -> None:  # called on AVFoundation's own queue
            try:
                if not buf.respondsToSelector_("frameLength") or buf.frameLength() == 0:
                    done.set()  # an empty buffer marks the end
                    return
                if "file" not in state:
                    state["file"] = av.AVAudioFile.alloc().initForWriting_settings_error_(url, buf.format().settings(), None)[0]
                state["file"].writeFromBuffer_error_(buf, None)
            except Exception as exc:  # noqa: BLE001 — surface it to the caller thread
                state["error"] = exc
                done.set()

        synth = av.AVSpeechSynthesizer.alloc().init()
        synth.writeUtterance_toBufferCallback_(utterance, on_buffer)
        finished = done.wait(_TIMEOUT)
        audio_file = state.pop("file", None)
        if audio_file is not None and audio_file.respondsToSelector_("close"):
            audio_file.close()  # macOS 15+; older systems flush when the object is released
        del audio_file, synth
        data = _read_and_remove(path)
        if "error" in state:
            raise state["error"]
        if not finished or not data:
            raise RuntimeError("AVSpeechSynthesizer produced no audio.")
        return data, "audio/wav"


class _SaySynth:
    """The ``say`` command: every Mac has it."""

    # "Eddy (English (UK))  en_GB    # Hello! My name is Eddy."
    _LINE = re.compile(r"^(?P<name>.+?)\s+(?P<lang>[a-z]{2,3}(?:[_-]\w+)?)\s+#")

    def voices(self) -> list[dict[str, str]]:
        proc = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        out = []
        for line in proc.stdout.splitlines():
            m = self._LINE.match(line)
            if m:
                out.append({"id": m["name"], "label": m["name"], "lang": m["lang"].replace("_", "-")})
        return out

    def speak(self, text: str, voice_id: str) -> tuple[bytes, str]:
        path = _tmp(".aiff")
        command = ["say", "-o", path]
        if voice_id:
            command += ["-v", voice_id]
        # Text goes on stdin, never on the command line.
        proc = subprocess.run(command + ["-f", "-"], input=text.encode("utf-8"), capture_output=True, timeout=_TIMEOUT)
        data = _read_and_remove(path)
        if proc.returncode or not data.startswith(b"FORM"):
            raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()[:400] or "macOS speech failed.")
        return data, "audio/aiff"


def _synths() -> list[Any]:
    """AVSpeechSynthesizer first, ``say`` as the fallback."""
    out: list[Any] = []
    try:
        out.append(_AVSynth())
    except Exception:  # noqa: BLE001 — pyobjc not installed or AVFoundation unavailable
        pass
    if shutil.which("say"):
        out.append(_SaySynth())
    return out


def _pick(voices: list[dict[str, str]], wanted: str) -> dict[str, str] | None:
    low = wanted.lower()
    for v in voices:
        if low in (v["id"].lower(), v["label"].lower()):
            return v
    # Chromium/WebKit labels append " - <language>" to the same voice.
    return next((v for v in voices if low.startswith(v["label"].lower() + " -")), None)


def _run(text: str = "", voice_id: str = "") -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"ok": False, "error": "macOS speech is only available on macOS."}
    synths = _synths()
    if not synths:
        return {"ok": False, "code": "voice_missing", "error": "No macOS voice engine found."}
    text = str(text or "")[:12000]
    wanted = str(voice_id or "")[:256]
    failure: dict[str, Any] = {"ok": False, "error": "macOS speech failed."}
    for synth in synths:  # a voice or engine failure tries the next engine
        try:
            voices = synth.voices()
            if not text:
                return {"ok": True, "voices": voices}
            hit = _pick(voices, wanted) if wanted else None
            if wanted and not hit:
                failure = {"ok": False, "code": "voice_missing", "error": f"macOS voice '{wanted}' is not installed."}
                continue
            data, mime = synth.speak(text, hit["id"] if hit else "")
            return {"ok": True, "audio_base64": base64.b64encode(data).decode("ascii"), "mime": mime}
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError, AttributeError) as exc:
            failure = {"ok": False, "error": str(exc) or failure["error"]}
    return failure


def start(text: str, voice_id: str = "") -> dict[str, Any]:
    from frontend.ui_web.bridge_jobs import job_start

    if not str(text or "").strip():
        return {"ok": False, "error": "Text is required."}
    return job_start(lambda: _run(text, voice_id))


def voices_start() -> dict[str, Any]:
    from frontend.ui_web.bridge_jobs import job_start

    return job_start(_run)


def poll(job_id: str) -> dict[str, Any]:
    from frontend.ui_web.bridge_jobs import job_poll

    return job_poll(job_id)
