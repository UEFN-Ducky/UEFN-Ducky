from __future__ import annotations

import subprocess

import pytest

from backend.agent.video import frames as fr
from backend.agent.video import ffmpeg_install as fi


def test_frame_times_are_centered_in_equal_slices():
    assert fr.frame_times(10.0, 4) == [1.25, 3.75, 6.25, 8.75]
    assert fr.frame_times(0.0, 5) == [0.0]
    assert fr.frame_times(3.0, 0) == []


def test_run_maps_oserror_to_video_error(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("locked")

    monkeypatch.setattr(fr.subprocess, "run", boom)
    with pytest.raises(fr.VideoError, match="Could not run ffmpeg"):
        fr._run(["ffmpeg"], 5)


def test_run_oserror_message_depends_on_bundled(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("locked")

    monkeypatch.setattr(fr.subprocess, "run", boom)
    monkeypatch.setattr(fi, "status", lambda: {"bundled": True})
    with pytest.raises(fr.VideoError, match="bundled ffmpeg — restart UEFN-Ducky, or reinstall it"):
        fr._run(["ffmpeg"], 5)
    monkeypatch.setattr(fi, "status", lambda: {"bundled": False})
    with pytest.raises(fr.VideoError, match="Settings → Videos → Remove"):
        fr._run(["ffmpeg"], 5)


def test_run_is_non_interactive_and_tolerates_bad_bytes(monkeypatch):
    seen = {}

    def fake(args, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(fr.subprocess, "run", fake)
    fr._run(["ffmpeg"], 5)
    assert seen["errors"] == "replace" and seen["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize(
    "out, expected",
    [("N/A\n12.5\n", 12.5), ("N/A\nN/A\n", 0.0), ("inf\nnan\n-1\n", 0.0), ("", 0.0)],
)
def test_probe_duration_tolerates_na(monkeypatch, tmp_path, out, expected):
    monkeypatch.setattr(fr, "_runner", lambda args, timeout: subprocess.CompletedProcess(args, 0, stdout=out, stderr=""))
    video = tmp_path / "clip.webm"
    video.write_bytes(b"v")
    assert fr.probe_duration(tmp_path / "ffprobe.exe", video) == expected


def test_unreadable_video(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "_runner", lambda args, timeout: subprocess.CompletedProcess(args, 1, stdout="", stderr=""))
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"x")
    with pytest.raises(fr.VideoError, match="Cannot read video 'broken.mp4'"):
        fr.probe_duration(tmp_path / "ffprobe.exe", video)
