"""Linux terminals run on a real pty (ptyprocess): output, exit codes, resize and stop."""

import os
import shutil
import sys
import time

import pytest

if sys.platform == "win32" or not shutil.which("bash"):
    pytest.skip("Linux pty terminal", allow_module_level=True)
pytest.importorskip("ptyprocess")

from frontend.ui_web.terminal import session as session_mod  # noqa: E402
from frontend.ui_web.terminal.session import TerminalSession  # noqa: E402


@pytest.fixture
def bash(tmp_path):
    s = TerminalSession(shell="bash", cwd=str(tmp_path), agent=True)
    s.spawn()
    yield s
    s.kill()


def _wait(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_printf_hi_and_exit_codes(bash):
    out = bash.run_command("printf hi", timeout_s=20)
    assert out["ok"] and out["exit_code"] == 0 and out["output_tail"] == "hi", out
    out = bash.run_command("false", timeout_s=20)
    assert out["ok"] and out["exit_code"] == 1, out
    out = bash.run_command("printf 'hé✓'", timeout_s=20)  # UTF-8 comes back whole
    assert out["output_tail"] == "hé✓", out


def test_resize_reaches_the_pty(bash):
    bash.resize(100, 40)
    out = bash.run_command("stty size", timeout_s=20)
    assert out["output_tail"] == "40 100", out


def test_a_timed_out_sleep_is_stopped(bash):
    out = bash.run_command("sleep 100", timeout_s=1)
    assert out["error"] == "command timed out"
    assert "sleep" in bash.left_running()
    assert "sleep" in bash.stop_left_running()
    assert _wait(lambda: not any(n == "sleep" for n in bash._descendants().values()))


def test_closing_ends_the_shell_and_what_it_runs(tmp_path):
    s = TerminalSession(shell="bash", cwd=str(tmp_path), agent=True)
    s.spawn()
    assert s.run_command("sleep 100 &", timeout_s=20)["ok"]
    sleeps = [p for p, n in s._descendants().items() if n == "sleep"]
    assert sleeps
    shell_pid = s._pty.pid
    started = time.monotonic()
    s.kill()
    assert time.monotonic() - started < 6
    assert not any(session_mod._posix_alive(p) for p in [shell_pid, *sleeps])


def test_a_tree_that_ignores_sigint_gets_sigkill(tmp_path):
    import subprocess

    proc = subprocess.Popen(["bash", "-c", "trap '' INT HUP; sleep 100"], start_new_session=True)
    time.sleep(0.3)
    started = time.monotonic()
    session_mod.posix_kill_tree(proc.pid, grace_s=1.0)
    assert proc.wait(timeout=5) == -9
    assert time.monotonic() - started >= 1.0


def test_the_shells_own_exit_code(tmp_path):
    codes = []
    s = TerminalSession(shell="bash", cwd=str(tmp_path), spawn_argv=["sh", "-c", "exit 3"], on_exit=codes.append)
    s.spawn()
    assert _wait(lambda: codes == [3])
    s.kill()


def test_the_proc_snapshot_sees_this_process():
    children, names = session_mod._process_snapshot()
    assert os.getpid() in children.get(os.getppid(), [])
    assert names[os.getpid()].startswith("python")
