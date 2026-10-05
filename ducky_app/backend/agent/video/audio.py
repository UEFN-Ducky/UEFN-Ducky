"""Extract a video's audio track and transcribe it (best-effort, cached next to the video)."""

from __future__ import annotations

import base64
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.agent.video import frames
from backend.agent.video.ffmpeg_install import FfmpegInstallError, ensure_installed

MAX_MP3_BYTES = 24 * 1024 * 1024

NOTE_NO_KEY = "No transcript — needs an OpenAI key"
NOTE_NO_AUDIO = "No audio track"
NOTE_TOO_LONG = "Audio too long to transcribe"


@dataclass(frozen=True)
class TranscriptResult:
    text: str  # "" when unavailable
    note: str  # "" on success, else a user-facing reason


def transcript_paths(video: Path) -> tuple[Path, Path]:
    return (
        video.with_name(f"{video.name}.transcript.txt"),
        video.with_name(f"{video.name}.transcript-note.txt"),
    )


def has_audio(ffprobe: Path, video: Path, timeout_s: float = 15) -> bool:
    args = [
        str(ffprobe), "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
        "-of", "csv=p=0", str(video),
    ]
    try:
        proc = frames._runner(args, timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise frames.VideoError(f"Cannot read video {video.name!r}.") from exc
    if proc.returncode != 0:
        raise frames.VideoError(f"Cannot read video {video.name!r}.")
    return bool((proc.stdout or "").strip())


def extract_mp3(ffmpeg: Path, video: Path, out: Path, timeout_s: float = 120) -> Path:
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


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def transcribe_video(video: Path, on_extracted: Callable[[], None] | None = None) -> TranscriptResult:
    """Never raises: every failure becomes a note. Only deterministic outcomes are cached.

    ``on_extracted`` runs once the local ffmpeg work is done, before the network call."""
    text_p, note_p = transcript_paths(video)
    cached = _read(text_p)
    if cached:
        return TranscriptResult(cached, "")
    cached_note = _read(note_p)
    if cached_note:
        return TranscriptResult("", cached_note)
    from backend.voice.transcription import openai_transcription_available, transcribe_audio

    if not openai_transcription_available():
        return TranscriptResult("", NOTE_NO_KEY)  # not cached: the user may add a key later
    mp3 = video.with_name(f"{video.name}.audio.mp3")
    try:
        try:
            ffmpeg, ffprobe = ensure_installed()
            if not has_audio(ffprobe, video):
                _write(note_p, NOTE_NO_AUDIO)
                return TranscriptResult("", NOTE_NO_AUDIO)
            extract_mp3(ffmpeg, video, mp3)
            size = mp3.stat().st_size
            if size > MAX_MP3_BYTES:
                _write(note_p, NOTE_TOO_LONG)
                return TranscriptResult("", NOTE_TOO_LONG)
            b64 = base64.b64encode(mp3.read_bytes()).decode("ascii")
        except (FfmpegInstallError, frames.VideoError, OSError, subprocess.SubprocessError) as exc:
            return TranscriptResult("", f"Transcription failed: {exc}")
    finally:
        mp3.unlink(missing_ok=True)
        if on_extracted is not None:
            on_extracted()
    try:
        res = transcribe_audio(b64, "audio/mpeg")
    except Exception as exc:  # best-effort: never break the send path
        return TranscriptResult("", f"Transcription failed: {exc}")
    if not res.get("ok"):
        err = str(res.get("error") or "unknown error")
        note = f"Transcription failed: {err}"
        if err.strip().lower() == "empty transcript":
            _write(note_p, note)  # deterministic: don't bill the same silent audio twice
        return TranscriptResult("", note)
    text = str(res.get("text") or "").strip()
    _write(text_p, text)
    return TranscriptResult(text, "")
