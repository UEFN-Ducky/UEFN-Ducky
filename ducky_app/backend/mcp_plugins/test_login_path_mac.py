"""login_path on macOS: a Finder-launched app gets the login shell's PATH plus Homebrew."""

from __future__ import annotations

import os

from backend.mcp_plugins import login_path as lp
from frontend.ui_web.terminal import path_env


def test_mac_login_path_starts_with_the_login_shell_and_has_homebrew(monkeypatch, tmp_path):
    sep = os.pathsep
    monkeypatch.setattr(lp.sys, "platform", "darwin")
    monkeypatch.setattr(path_env.sys, "platform", "darwin")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    asked = []
    shell_path = sep.join(["/Users/a/.nvm/bin", "/usr/bin"])
    monkeypatch.setattr(path_env, "posix_login_path", lambda shell="bash": asked.append(shell) or shell_path)
    finder = sep.join(["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    got = lp.login_path(finder, home=tmp_path).split(sep)
    assert asked == ["/bin/zsh"]
    assert got[:2] == ["/Users/a/.nvm/bin", "/usr/bin"]
    assert "/opt/homebrew/bin" in got and "/usr/local/bin" in got and "/sbin" in got
    assert len(got) == len(set(got))


def test_callers_gate_on_posix_not_linux_only():
    assert lp.IS_POSIX == (lp.sys.platform != "win32")
