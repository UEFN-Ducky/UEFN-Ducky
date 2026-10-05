from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from backend.agent.video import audio
from backend.agent.video import frames as fr


def _setup(monkeypatch, tmp_path, *, has=True, ff_rc=0, mp3=b"mp3data", tr=None):
    ffmpeg, ffprobe = tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"
    monkeypatch.setattr(audio, "ensure_installed", lambda: (ffmpeg, ffprobe))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")
    calls: list[list[str]] = []
    tcalls: list[tuple] = []

    def runner(args, timeout):
        calls.append(args)
        if Path(args[0]) == ffprobe:
            return subprocess.CompletedProcess(args, 0, stdout="1\n" if has else "", stderr="")
        if ff_rc == 0:
            Path(args[-1]).write_bytes(mp3)
        return subprocess.CompletedProcess(args, ff_rc, stdout="", stderr="boom")

    def transcribe(b64, mime):
        tcalls.append((b64, mime))
        return tr if tr is not None else {"ok": True, "text": "hello world"}

    monkeypatch.setattr(fr, "_runner", runner)
    monkeypatch.setattr("backend.voice.transcription.transcribe_audio", transcribe)
    monkeypatch.setattr("backend.voice.transcription.openai_transcription_available", lambda: True)
    return video, calls, tcalls


def test_no_audio_track(monkeypatch, tmp_path):
    video, calls, tcalls = _setup(monkeypatch, tmp_path, has=False)
    r = audio.transcribe_video(video)
    assert r == audio.TranscriptResult("", "No audio track")
    assert tcalls == []
    assert "-select_streams" in calls[0]


def test_success_and_cache(monkeypatch, tmp_path):
    video, calls, tcalls = _setup(monkeypatch, tmp_path)
    r = audio.transcribe_video(video)
    assert r == audio.TranscriptResult("hello world", "")
    assert tcalls[0][1] == "audio/mpeg"
    ff = [c for c in calls if Path(c[0]).name == "ffmpeg.exe"][0]
    assert ff[1:-1] == ["-nostdin", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                        "-b:a", "48k", "-f", "mp3", "-y"]
    assert not video.with_name(video.name + ".audio.mp3").exists()
    n_calls, n_t = len(calls), len(tcalls)
    assert audio.transcribe_video(video) == audio.TranscriptResult("hello world", "")
    assert (len(calls), len(tcalls)) == (n_calls, n_t)


def test_note_is_cached(monkeypatch, tmp_path):
    video, calls, _ = _setup(monkeypatch, tmp_path, has=False)
    audio.transcribe_video(video)
    n = len(calls)
    assert audio.transcribe_video(video).note == "No audio track"
    assert len(calls) == n


def test_no_key_skips_all_ffmpeg_work(monkeypatch, tmp_path):
    video, calls, tcalls = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr("backend.voice.transcription.openai_transcription_available", lambda: False)
    assert audio.transcribe_video(video) == audio.TranscriptResult("", "No transcript — needs an OpenAI key")
    assert calls == [] and tcalls == []
    text_p, note_p = audio.transcript_paths(video)
    assert not note_p.exists() and not text_p.exists()


def test_gateway_error_is_failed_note(monkeypatch, tmp_path):
    video, _, _ = _setup(monkeypatch, tmp_path, tr={"ok": False, "error": "OpenAI HTTP 502: Bad Gateway"})
    assert audio.transcribe_video(video).note == "Transcription failed: OpenAI HTTP 502: Bad Gateway"


def test_other_transcription_error(monkeypatch, tmp_path):
    video, _, _ = _setup(monkeypatch, tmp_path, tr={"ok": False, "error": "HTTP 500"})
    assert audio.transcribe_video(video).note == "Transcription failed: HTTP 500"


def test_empty_transcript_is_cached(monkeypatch, tmp_path):
    video, calls, tcalls = _setup(monkeypatch, tmp_path, tr={"ok": False, "error": "Empty transcript"})
    assert audio.transcribe_video(video).note == "Transcription failed: Empty transcript"
    n, t = len(calls), len(tcalls)
    assert audio.transcribe_video(video).note == "Transcription failed: Empty transcript"
    assert (len(calls), len(tcalls)) == (n, t)


def test_too_long(monkeypatch, tmp_path):
    monkeypatch.setattr(audio, "MAX_MP3_BYTES", 4)
    video, _, tcalls = _setup(monkeypatch, tmp_path, mp3=b"12345")
    assert audio.transcribe_video(video).note == "Audio too long to transcribe"
    assert tcalls == []
    assert not video.with_name(video.name + ".audio.mp3").exists()


def test_ffmpeg_failure(monkeypatch, tmp_path):
    video, _, tcalls = _setup(monkeypatch, tmp_path, ff_rc=1)
    r = audio.transcribe_video(video)
    assert r.text == "" and r.note.startswith("Transcription failed:")
    assert tcalls == []


def test_runner_error_never_raises(monkeypatch, tmp_path):
    video, _, _ = _setup(monkeypatch, tmp_path)

    def boom(args, timeout):
        raise fr.VideoError("no ffmpeg")

    monkeypatch.setattr(fr, "_runner", boom)
    assert audio.transcribe_video(video).note.startswith("Transcription failed:")


def test_install_error_never_raises(monkeypatch, tmp_path):
    video, _, _ = _setup(monkeypatch, tmp_path)
    from backend.agent.video.ffmpeg_install import FfmpegInstallError

    def bad():
        raise FfmpegInstallError("offline")

    monkeypatch.setattr(audio, "ensure_installed", bad)
    assert audio.transcribe_video(video).note == "Transcription failed: offline"


def test_write_is_atomic(monkeypatch, tmp_path):
    target = tmp_path / "t.txt"

    def boom(src, dst):
        raise OSError("disk")

    monkeypatch.setattr(audio.os, "replace", boom)
    audio._write(target, "hello")
    assert not target.exists()
    assert list(tmp_path.glob("t.txt*")) == []
    monkeypatch.undo()
    audio._write(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


@pytest.mark.skipif(not os.environ.get("DUCKY_FFMPEG_DIR"), reason="needs real ffmpeg")
def test_real_ffmpeg_extracts_audio(tmp_path):
    d = Path(os.environ["DUCKY_FFMPEG_DIR"])
    ffmpeg, ffprobe = d / "ffmpeg.exe", d / "ffprobe.exe"
    video = tmp_path / "tone.mp4"
    subprocess.run(
        [str(ffmpeg), "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-y", str(video)],
        check=True,
    )
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [str(ffmpeg), "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-y", str(silent)],
        check=True,
    )
    assert audio.has_audio(ffprobe, video) is True
    assert audio.has_audio(ffprobe, silent) is False
    out = audio.extract_mp3(ffmpeg, video, tmp_path / "o.mp3")
    assert out.is_file() and out.stat().st_size > 0
