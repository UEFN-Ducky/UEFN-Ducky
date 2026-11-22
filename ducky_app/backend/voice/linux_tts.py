"""Offline Linux speech synthesis (same API and result shape as ``windows_tts``).

The UI plays the returned WAV itself (read-along, speed, pause), so this needs an
engine that writes audio instead of playing it: espeak-ng, the engine
speech-dispatcher uses by default on Ubuntu and Fedora. ``spd-say`` can only play
to the speakers, so it is not used.
"""
from __future__ import annotations

import base64
import shutil
import subprocess
import sys
from typing import Any

_MISSING = "No offline voice engine found. Install espeak-ng (sudo apt install espeak-ng / sudo dnf install espeak-ng)."


def _engine() -> str:
    return shutil.which("espeak-ng") or shutil.which("espeak") or ""


def _voices(engine: str) -> list[dict[str, str]]:
    proc = subprocess.run([engine, "--voices"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    voices = []
    # Columns: Pty Language Age/Gender VoiceName File [Other Languages]
    for line in proc.stdout.splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 5:
            voices.append({"id": cols[1], "label": cols[3].replace("_", " "), "lang": cols[1]})
    return voices


def _pick(voices: list[dict[str, str]], wanted: str) -> dict[str, str] | None:
    low = wanted.lower()
    for v in voices:
        if low in (v["id"].lower(), v["label"].lower()):
            return v
    # Chromium labels append " - <language>" to the same voice.
    return next((v for v in voices if low.startswith(v["label"].lower() + " -")), None)


def _run(text: str = "", voice_id: str = "") -> dict[str, Any]:
    if not sys.platform.startswith("linux"):
        return {"ok": False, "error": "Linux speech is only available on Linux."}
    engine = _engine()
    if not engine:
        return {"ok": False, "code": "voice_missing", "error": _MISSING}
    text = str(text or "")[:12000]
    wanted = str(voice_id or "")[:256]
    try:
        voices = _voices(engine)
        if not text:
            return {"ok": True, "voices": voices}
        command = [engine, "--stdout"]
        if wanted:
            hit = _pick(voices, wanted)
            if not hit:
                return {"ok": False, "code": "voice_missing", "error": f"Linux voice '{wanted}' is not installed."}
            command += ["-v", hit["id"]]
        # Text goes on stdin, never on the command line.
        proc = subprocess.run(command + ["--stdin"], input=text.encode("utf-8"), capture_output=True, timeout=60)
        if proc.returncode or not proc.stdout.startswith(b"RIFF"):
            return {"ok": False, "error": proc.stderr.decode("utf-8", "replace").strip()[:400] or "Linux speech failed."}
        return {"ok": True, "audio_base64": base64.b64encode(proc.stdout).decode("ascii"), "mime": "audio/wav"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc) or "Linux speech failed."}


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
