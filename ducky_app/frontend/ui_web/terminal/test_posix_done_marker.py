"""An agent command in a Linux bash, zsh or fish terminal reports its own exit code and output."""

import shutil
import sys
import time

import pytest

from frontend.ui_web.terminal.session import TerminalSession, _fish_literal, _fish_wrap

_SHELLS = [s for s in ("bash", "zsh", "fish") if sys.platform != "win32" and shutil.which(s)]


def test_fish_quoting_keeps_backslashes_and_quotes():
    assert _fish_literal(r"echo 'a\b'") == r"'echo \'a\\b\''"
    line = _fish_wrap("false", "abc12345", background=False)
    assert line.endswith("printf '__DUCKY_DONE_%s__%s__\\n' abc12345 $status\n")
    assert "__DUCKY_DONE_abc12345__" not in line  # the shell's echo of the line never matches
    assert _fish_wrap("sleep 1", "abc12345", background=True).endswith(" </dev/null &\n")  # never reads the tty


@pytest.fixture(params=_SHELLS or [pytest.param("none", marks=pytest.mark.skip("no POSIX shell"))])
def shell(request, tmp_path):
    pytest.importorskip("ptyprocess")
    s = TerminalSession(shell=request.param, cwd=str(tmp_path), agent=True)
    s.spawn()
    time.sleep(0.5)  # the first prompt
    yield s
    s.kill()


def test_false_is_1_and_true_is_0(shell):
    for command, code in (("false", 1), ("true", 0), ("exit_with() { return 7; }; exit_with" if shell.shell != "fish"
                                                         else "function exit_with; return 7; end; exit_with", 7)):
        out = shell.run_command(command, timeout_s=20)
        assert out["ok"] and out["exit_code"] == code, (shell.shell, command, out)


def test_output_quotes_and_cd_stay(shell, tmp_path):
    out = shell.run_command("printf '%s %s\\n' \"it's\" 'a\\b'", timeout_s=20)
    assert out["output_tail"] == "it's a\\b", (shell.shell, out)
    (tmp_path / "sub").mkdir()
    shell.run_command("cd sub", timeout_s=20)
    out = shell.run_command("pwd", timeout_s=20)
    assert out["output_tail"].endswith("/sub"), (shell.shell, out)


def test_agent_env_is_for_the_command_only(shell):
    out = shell.run_command("echo $GIT_PAGER", timeout_s=20)
    assert out["output_tail"] == "cat", (shell.shell, out)


def test_a_background_run_returns_at_once_and_prints_later(shell):
    # fish 4 first waits on its terminal queries (no xterm.js answers them here); a waited command
    # returns once it reads input, so the background one is typed into a shell that reads it.
    assert shell.run_command("true", timeout_s=30)["ok"]
    assert shell.run_command("sleep 0.3; echo BG_FINISHED", background=True)["status"] == "running"
    deadline = time.monotonic() + 10
    while "BG_FINISHED" not in shell.read_output_tail() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert "BG_FINISHED" in shell.read_output_tail()
