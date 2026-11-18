"""Extract a video's audio track as a small mono mp3 (``web_video`` transcribes it)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.agent.video import frames

MAX_MP3_BYTES = 24 * 1024 * 1024


def extract_mp3(ffmpeg: Path, video: Path, out: Path, timeout_s: float = 120) -> Path:
    frames.require_media_access(video, out)
    args = [
        str(ffmpeg), "-nostdin", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", "48k", "-f", "mp3", "-y", str(out),
    ]
    try:
        proc = frames._runner(args, timeout_s)
    except subprocess.TimeoutExpired as exc:
        out.unlink(missing_ok=True)
        raise frames.VideoError(f"Extracting audio from {video.name!r} timed out.") from exc
    if proc.returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        detail = (proc.stderr or "").strip().splitlines()
        raise frames.VideoError(detail[-1] if detail else f"Cannot extract audio from {video.name!r}.")
    return out
