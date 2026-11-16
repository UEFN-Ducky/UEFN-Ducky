"""Pick the fastest H.264 encoder that actually works with this PC's ffmpeg (hardware first)."""

from __future__ import annotations

import re
import subprocess
import threading

from backend.agent.video import ffmpeg_install

# Hardware first, then software. The bundled ffmpeg has no libx264, only libopenh264.
CANDIDATES = ("h264_nvenc", "h264_amf", "h264_qsv", "libx264", "libopenh264")
FALLBACK = "libopenh264"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_lock = threading.Lock()
_cached: str | None = None


def parse_encoders(output: str) -> set[str]:
    """Encoder names from ``ffmpeg -encoders`` text (flag column, name, description)."""
    names: set[str] = set()
    for line in output.splitlines():
        m = re.match(r"^\s*[VAS][A-Z.]{5}\s+(\S+)", line)
        if m:
            names.add(m.group(1))
    return names


def _trial(ffmpeg: str, enc: str) -> bool:
    """One-frame encode: a listed encoder can still fail (no GPU, no driver).

    256x256 yuv420p: NVENC and friends reject tiny frames (64x64 failed on a working NVENC).
    """
    try:
        return subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=s=256x256:d=0.04", "-frames:v", "1", "-pix_fmt", "yuv420p",
             "-c:v", enc, "-f", "null", "-"],
            capture_output=True, text=True, errors="replace", timeout=30, check=False, creationflags=_NO_WINDOW,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _probe() -> str:
    ffmpeg = str(ffmpeg_install.ensure_installed()[0])
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, errors="replace", timeout=30, check=False, creationflags=_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return FALLBACK
    have = parse_encoders(out)
    return next((e for e in CANDIDATES if e in have and _trial(ffmpeg, e)), FALLBACK)


def pick_encoder() -> str:
    """First H.264 encoder that is listed and passes a trial encode; probed once, cached."""
    global _cached
    with _lock:
        if _cached is None:
            _cached = _probe()
        return _cached


def reset_cache() -> None:
    global _cached
    with _lock:
        _cached = None
