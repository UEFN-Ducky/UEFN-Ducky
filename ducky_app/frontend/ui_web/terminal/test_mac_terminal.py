"""macOS terminals: zsh by default, Homebrew on PATH, the process tree from psutil (no /proc)."""

import os
import shutil
import sys

import pytest

from frontend.ui_web.terminal import path_env, session, shells


def test_default_shell_is_login_shell_then_zsh_on_mac(monkeypatch):
    monkeypatch.setattr(shells.sys, "platform", "darwin")
    monkeypatch.setattr(shells, "_posix", lambda: True)
    monkeypatch.setattr(shells.shutil, "which", lambda name: None)
    monkeypatch.setattr(shells.Path, "is_file", lambda self: self.as_posix() in ("/bin/zsh", "/bin/bash"))
    monkeypatch.setenv("SHELL", "")
    assert shells.shell_label("") == "zsh"
    assert shells.resolve_shell("zsh") == ("/bin/zsh", ["/bin/zsh"])
    assert shells.agent_shell("zsh") == "zsh" and shells.agent_shell("bash") == "bash"
    assert shells.resolve_shell("zsh", agent=True)[1] == ["/bin/zsh", "-f"]
    monkeypatch.setenv("SHELL", "/bin/bash")
    assert shells.shell_label("") == "bash"


def test_mac_path_adds_homebrew_after_login_path(monkeypatch):
    monkeypatch.setattr(path_env.sys, "platform", "darwin")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    asked = []
    monkeypatch.setattr(path_env, "posix_login_path", lambda shell="bash": asked.append(shell) or "/Users/a/.nvm/bin:/usr/bin")
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "/bin"]))
    sep = os.pathsep
    monkeypatch.setattr(path_env.os, "pathsep", ":")
    try:
        merged = path_env._posix_merged_path().split(":")
    finally:
        monkeypatch.setattr(path_env.os, "pathsep", sep)
    assert asked == ["/bin/zsh"]
    assert merged[0] == "/Users/a/.nvm/bin"
    assert merged[-2:] == ["/opt/homebrew/bin", "/usr/local/bin"]


def test_psutil_snapshot_sees_this_process(monkeypatch):
    monkeypatch.setattr(session.sys, "platform", "darwin")
    children, names = session._posix_snapshot()
    assert os.getpid() in children.get(os.getppid(), [])
    assert names[os.getpid()]
    assert session._posix_alive(os.getpid())
    assert not session._posix_alive(2**22 + 12345)


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("zsh"), reason="real zsh on macOS")
def test_zsh_echo_ok(tmp_path):
    pytest.importorskip("ptyprocess")
    s = session.TerminalSession(shell="zsh", cwd=str(tmp_path), agent=True)
    s.spawn()
    try:
        out = s.run_command("zsh -c 'echo ok'", timeout_s=20)
        assert out["ok"] and out["exit_code"] == 0 and out["output_tail"] == "ok", out
    finally:
        s.kill()
