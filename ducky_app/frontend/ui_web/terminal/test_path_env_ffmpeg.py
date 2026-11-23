"""Programs run in a Ducky terminal find the ffmpeg the app bundles."""

from __future__ import annotations

import os


def test_terminals_get_duckys_ffmpeg_after_the_persons_own(monkeypatch, tmp_path) -> None:
    # Oct 12 2026: a plugin's tests ran in a terminal, outside the app, and could not find the
    # bundled encoder, so no builder could make its test export.
    from frontend.ui_web.terminal import path_env

    ffmpeg, ffprobe = tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"
    monkeypatch.setattr("backend.agent.video.ffmpeg_install.binaries", lambda: (ffmpeg, ffprobe))
    monkeypatch.setattr(path_env, "windows_merged_path", lambda: os.pathsep.join(["/mine/bin", "/Windows"]))
    env = path_env.env_with_fresh_path({})
    assert env["DUCKY_FFMPEG"] == str(ffmpeg) and env["DUCKY_FFPROBE"] == str(ffprobe)
    parts = env["PATH"].split(os.pathsep)
    assert parts[-1] == str(tmp_path) and parts[0] == "/mine/bin"  # the person's own ffmpeg wins
    assert env_twice(path_env).count(str(tmp_path)) == 1


def env_twice(path_env) -> str:
    return path_env.env_with_fresh_path(path_env.env_with_fresh_path({}))["PATH"]


def test_a_terminal_gets_one_path_entry(monkeypatch, tmp_path) -> None:
    # Oct 22 2026: "Path" and "PATH" both reached the terminal, and Git could not start its
    # credential helper there: every push died with exit 128 and no message.
    from frontend.ui_web.terminal import path_env

    monkeypatch.setattr("backend.agent.video.ffmpeg_install.binaries", lambda: (tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"))
    monkeypatch.setattr(path_env, "windows_merged_path", lambda: "/Windows")
    env = path_env.env_with_fresh_path({"Path": "C:/old", "PATH": "C:/old", "HOME": "C:/me"})
    assert [k for k in env if k.lower() == "path"] == ["PATH"]
    assert env["PATH"] == os.pathsep.join(["/Windows", str(tmp_path)]) and env["HOME"] == "C:/me"


def test_no_ffmpeg_no_change(monkeypatch) -> None:
    from frontend.ui_web.terminal import path_env

    monkeypatch.setattr("backend.agent.video.ffmpeg_install.binaries", lambda: None)
    monkeypatch.setattr(path_env, "windows_merged_path", lambda: "/Windows")
    env = path_env.env_with_fresh_path({})
    assert "DUCKY_FFMPEG" not in env and env["PATH"] == "/Windows"
