"""Linux sandbox: worktree on POSIX, bash runs from work/, hidden launch display under Xvfb."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.sandbox import launch_display

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux sandbox")


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_git_worktree_is_a_plain_posix_checkout(tmp_path):
    repo, wt = tmp_path / "repo", tmp_path / "wt"
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", str(repo)], check=True)
    (repo / "a.sh").write_text("echo hi\n")
    (repo / "a.sh").chmod(0o755)
    os.symlink("a.sh", repo / "link")  # symlinks (where Windows has junctions) survive the worktree
    subprocess.run([*git, "-C", str(repo), "add", "."], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-qm", "x"], check=True)
    subprocess.run([*git, "-C", str(repo), "worktree", "add", "--detach", str(wt), "HEAD"], check=True,
                   capture_output=True)
    assert (wt / "link").is_symlink() and os.access(wt / "a.sh", os.X_OK)


def test_sandbox_run_uses_bash_from_work(tmp_path):
    from backend.sandbox.service import _from_work

    work = tmp_path / "it's work"
    (work / "sub").mkdir(parents=True)
    cmd = _from_work("bash", work, "pwd")
    out = subprocess.run(["bash", "-c", "cd / && " + cmd], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == str(work) and cmd.startswith("cd -- " + shlex.quote(str(work)))


def test_no_xvfb_never_hands_over_the_persons_display(monkeypatch):
    monkeypatch.setattr(launch_display, "_proc", None)
    monkeypatch.setattr(launch_display.shutil, "which", lambda name, path=None: None)
    env = {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0", "XAUTHORITY": "/x", "A": "1"}
    assert launch_display.hidden_env(env) == {"A": "1"}  # fails headless, never on their screen
    assert "xvfb" in launch_display.last_error.lower()


def test_fake_xvfb_displayfd_started_once(tmp_path, monkeypatch):
    fake = tmp_path / "Xvfb"
    # Python, not sh: dash redirects only fds 0-9, and under xdist the pipe's fd is higher.
    fake.write_text(f"#!{sys.executable}\nimport os, sys, time\n"
                    "os.write(int(sys.argv[2]), b'77\\n')  # argv: -displayfd <fd> …\ntime.sleep(30)\n")
    fake.chmod(0o755)
    monkeypatch.setattr(launch_display, "_proc", None)
    monkeypatch.setattr(launch_display.shutil, "which", lambda name, path=None: str(fake))
    try:
        env = launch_display.hidden_env({"WAYLAND_DISPLAY": "w", "A": "1"})
        assert env == {"A": "1", "DISPLAY": ":77", "GDK_BACKEND": "x11", "QT_QPA_PLATFORM": "xcb"}, \
            launch_display.last_error
        first = launch_display._proc
        assert launch_display.hidden_display() == ":77" and launch_display._proc is first
    finally:
        launch_display.stop()
    assert launch_display._proc is None


@pytest.mark.skipif(not shutil.which("Xvfb"), reason="needs Xvfb")
def test_launch_under_xvfb_is_hidden_and_shared(monkeypatch):
    launch_display.stop()
    # A PATH without /usr/bin (as some sessions and earlier tests leave it) still finds Xvfb.
    monkeypatch.setenv("PATH", "/nonexistent")
    try:
        # The runner's own DISPLAY / WAYLAND_DISPLAY must not reach the child.
        env = launch_display.hidden_env({**os.environ, "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"})
        display = env.get("DISPLAY", "")
        assert display not in ("", ":0") and "WAYLAND_DISPLAY" not in env, launch_display.last_error
        assert Path(f"/tmp/.X11-unix/X{display[1:]}").exists()
        assert launch_display.hidden_display() == display  # one Xvfb, reused
        code = "import tkinter as t; r = t.Tk(); r.update(); print(r.winfo_screenwidth()); r.destroy()"
        try:
            import tkinter  # noqa: F401
        except ImportError:
            return
        out = subprocess.run([sys.executable, "-c", code], env=env,
                             capture_output=True, text=True, timeout=30)
        assert out.returncode == 0 and out.stdout.strip() == "1920", out.stderr
    finally:
        launch_display.stop()
