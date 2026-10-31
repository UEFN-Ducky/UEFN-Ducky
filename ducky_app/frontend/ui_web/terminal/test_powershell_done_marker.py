"""An agent's PowerShell command reports its exit code, so it never waits until it times out.

The old marker wrote "$LASTEXITCODE__" inside a string, which PowerShell reads as a
variable named LASTEXITCODE__: it printed no code, the done pattern never matched,
and every agent or workflow command in a PowerShell terminal hung until its timeout.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from frontend.ui_web.terminal.session import _DONE_RE, TerminalSession, agent_command_output, plain_terminal_text

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
needs_powershell = pytest.mark.skipif(
    sys.platform != "win32" or not POWERSHELL, reason="needs Windows PowerShell"
)


def _wrapped(command: str) -> str:
    return TerminalSession(shell="powershell", cwd=".")._wrap_agent_command(command, background=False)


def _run(script: str) -> str:
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=90,
    )
    return result.stdout


def _reported_code(script: str) -> int:
    out = _run(script)
    match = _DONE_RE.search(out)
    assert match, out
    return int(match.group(2))


def test_the_command_is_submitted_with_a_bare_carriage_return():
    wrapped = _wrapped("echo hello")
    assert wrapped.endswith("\r") and not wrapped.endswith("\r\n")
    background = TerminalSession(shell="powershell", cwd=".")._wrap_agent_command("long", background=True)
    assert "; Invoke-Expression 'long'; foreach" in background and background.endswith("; \r")


@needs_powershell
def test_a_trailing_comment_or_quotes_never_eat_the_done_marker():
    assert _reported_code(_wrapped("echo hi  # a note").rstrip("\r")) == 0
    # A failed statement doesn't skip the next one, as when the command is typed.
    out = _run(_wrapped("no_such_command_ducky; Write-Output after-it").rstrip("\r"))
    assert "after-it" in out and _DONE_RE.search(out).group(2) == "0", out
    out = _run(_wrapped("Write-Output \"it's, it’s\"").rstrip("\r"))
    assert "it's, it’s" in out and _DONE_RE.search(out), out


@needs_powershell
def test_a_cd_or_variable_from_an_agent_command_stays(tmp_path):
    out = _run(_wrapped(f"Set-Location '{tmp_path}'; $kept = 7").rstrip("\r") + "; Write-Output \"at=$((Get-Location).Path) kept=$kept\"")
    assert f"at={tmp_path} kept=7" in out, out


def test_the_typed_line_itself_never_looks_done():
    assert _DONE_RE.search(_wrapped("echo hello")) is None


@needs_powershell
def test_a_cmdlet_that_succeeds_reports_0():
    assert _reported_code(_wrapped("echo hello").rstrip("\r")) == 0


@needs_powershell
def test_a_cmdlet_that_fails_reports_1():
    assert _reported_code(_wrapped(r"Get-Item C:\no\such\path_ducky_xyz").rstrip("\r")) == 1


@needs_powershell
def test_a_native_command_reports_its_own_exit_code():
    assert _reported_code(_wrapped("cmd /c exit 3").rstrip("\r")) == 3


@needs_powershell
def test_an_earlier_native_failure_is_not_reported_for_a_later_success():
    assert _reported_code("cmd /c exit 5; " + _wrapped("echo fine").rstrip("\r")) == 0


def test_a_negative_windows_exit_code_still_counts_as_done():
    match = _DONE_RE.search("__DUCKY_DONE_ab12cd34__-1073741819__")
    assert match and match.group(1) == "ab12cd34" and int(match.group(2)) == -1073741819


def test_bash_commands_print_a_begin_mark_the_echo_cannot_spell():
    session = TerminalSession(shell="bash", cwd=".")
    session._wrap_agent_command("pwd", background=False)
    wrapped = session._wrap_agent_command("ls", background=False)
    tag = session.run_tag
    assert wrapped == (
        f"printf '__DUCKY_%s__\\n' BEGIN_{tag}; GIT_PAGER=cat GH_PAGER=cat GIT_EDITOR=true GH_PROMPT_DISABLED=1 eval ls; "
        f"echo __DUCKY_DONE_{tag}__$?__\r\n"
    )
    assert "__DUCKY_BEGIN_" not in wrapped and "__DUCKY_BEGIN_" not in _wrapped("ls")


def test_every_run_gets_its_own_tag():
    session = TerminalSession(shell="powershell", cwd=".")
    first = session._wrap_agent_command("echo a", background=False)
    tag = session.run_tag
    second = session._wrap_agent_command("echo b", background=False)
    assert len(tag) == 8 and session.run_tag != tag
    assert f'"BEGIN_{tag}"' in first and f"__DUCKY_DONE_{tag}__" in first and tag not in second


def test_the_first_bash_command_drops_gits_winpty_aliases_once():
    # Oct 31 2026: Git's aliases.sh ran node through winpty, which never exits in a ConPTY
    # terminal, and every agent `node ...` waited out its whole timeout.
    session = TerminalSession(shell="bash", cwd=".")
    first = session._wrap_agent_command("node -v", background=False)
    assert first.startswith("for __d in node ") and 'unalias "$__d"' in first
    assert first.index("unalias") < first.index("eval")
    assert not session._wrap_agent_command("node -v", background=False).startswith("for __d")
    assert not _wrapped("node -v").startswith("for __d")


def test_a_bash_comment_or_bang_stays_inside_the_command():
    session = TerminalSession(shell="bash", cwd=".")
    wrapped = session._wrap_agent_command('git commit -m "fix!"  # done', background=False)
    assert f"eval 'git commit -m \"fix!\"  # done'; echo __DUCKY_DONE_{session.run_tag}__$?__" in wrapped
    background = session._wrap_agent_command("npm run dev", background=True)
    assert background == "(GIT_PAGER=cat GH_PAGER=cat GIT_EDITOR=true GH_PROMPT_DISABLED=1 eval 'npm run dev') &\r\n"


@needs_powershell
def test_an_agent_command_gets_no_pager_or_editor_and_the_shell_keeps_its_own():
    # Oct 22 2026: `git log` waited in less and `git commit` in vim until the agent timed out.
    script = (
        "$env:GIT_PAGER = 'mine'; Remove-Item Env:GIT_EDITOR, Env:GH_PAGER -ErrorAction SilentlyContinue; "
        + _wrapped("cmd /c echo inside=%GIT_PAGER%/%GIT_EDITOR%/%GH_PAGER%").rstrip("\r")
        + "; Write-Output \"after=$env:GIT_PAGER/$env:GIT_EDITOR/$env:GH_PAGER\""
    )
    out = _run(script)
    assert "inside=cat/true/cat" in out and "after=mine//" in out, out


def test_the_agent_reads_only_the_command_output_without_escape_codes():
    # Shaped like a real PowerShell console: PSReadLine colors and redraws the typed
    # line (cursor moves, then the line again), then the output, then the next prompt.
    session = TerminalSession(shell="powershell", cwd=".")
    typed = session._wrap_agent_command("echo hello", background=False).rstrip("\r")
    tag = session.run_tag
    raw = (
        "\x1b[1t\x1b[?1004hPS C:\\p> \x1b[?25l\x1b[1;64H\x1b[93mecho\x1b[39;49m"
        + typed.replace("echo", "\x1b[93mecho\x1b[0m") + "\x1b[2;107H\x1b[?25h"
        + f"\r\n__DUCKY_BEGIN_{tag}__\r\nhello\r\n\x1b[32mworld\x1b[0m\r\n"
        + f"__DUCKY_DONE_{tag}__0__\r\n\x1b]0;PowerShell\x07PS C:\\p> "
    )
    assert agent_command_output(raw, tag) == "hello\nworld"


def test_a_redrawn_progress_line_shows_what_was_written_last():
    raw = "__DUCKY_BEGIN_aa11__\r\n 10%\r 55%\r100% done\r\n__DUCKY_DONE_aa11__0__\r\n"
    assert agent_command_output(raw) == "100% done"


def test_an_earlier_command_never_answers_for_a_command_still_running():
    raw = (
        "__DUCKY_BEGIN_aa11__\r\nold output\r\n__DUCKY_DONE_aa11__0__\r\nPS> "
        "__DUCKY_BEGIN_bb22__\r\nstill building...\r\n"
    )
    assert agent_command_output(raw) == "still building..."
    assert agent_command_output(raw, "bb22") == "still building..."


def test_a_timed_out_commands_late_done_marker_never_ends_the_next_one():
    # Oct 31 2026: a timed-out command's late done marker finished the next command, which
    # then reported the old command's output and exit code.
    raw = (
        "__DUCKY_BEGIN_aa11__\r\nslow output\r\n"
        "__DUCKY_BEGIN_bb22__\r\n__DUCKY_DONE_aa11__0__\r\nnext output\r\n__DUCKY_DONE_bb22__3__\r\n"
    )
    assert agent_command_output(raw, "bb22") == "__DUCKY_DONE_aa11__0__\nnext output"


@needs_powershell
def test_a_real_powershell_terminal_finishes_and_reports_success(tmp_path):
    session = TerminalSession(shell="powershell", cwd=str(tmp_path))
    try:
        session.spawn()
    except Exception as exc:  # noqa: BLE001 - no console host available here
        pytest.skip(f"cannot start a PowerShell terminal here: {exc}")
    try:
        result = session.run_command("echo ducky-pty-ok", timeout_s=60)
        assert result["ok"], result
        assert result["exit_code"] == 0
        assert result["output_tail"] == "ducky-pty-ok", result
        failed = session.run_command(r"Get-Item C:\no\such\path_ducky_xyz", timeout_s=60)
        assert failed["ok"] and failed["exit_code"] == 1, failed
    finally:
        session.kill()


@needs_powershell
@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_git_log_and_a_commit_without_a_message_never_wait_on_a_pager_or_editor(tmp_path, monkeypatch):
    monkeypatch.delenv("GIT_EDITOR", raising=False)
    monkeypatch.delenv("GIT_PAGER", raising=False)
    git = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, check=True)  # noqa: E731
    git("init", "-q")
    for i in range(40):  # more than a screen, so git would start less
        git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", f"c{i}")
    session = TerminalSession(shell="powershell", cwd=str(tmp_path))
    session.resize(80, 10)
    try:
        session.spawn()
    except Exception as exc:  # noqa: BLE001 - no console host available here
        pytest.skip(f"cannot start a PowerShell terminal here: {exc}")
    try:
        log = session.run_command("git log --oneline", timeout_s=30)
        assert log["ok"] and log["exit_code"] == 0 and log["output_tail"].count("\n") >= 39, log
        commit = session.run_command("git -c user.name=t -c user.email=t@example.invalid commit --allow-empty", timeout_s=30)
        assert commit["ok"] and commit["exit_code"] == 1 and "empty commit message" in commit["output_tail"], commit
    finally:
        session.kill()


@pytest.fixture
def ducky_ignores_ctrl_c():
    """This process ignores Ctrl+C, as Ducky does: its launchers start it with
    CREATE_NEW_PROCESS_GROUP, and every terminal shell inherited that."""
    import ctypes

    from frontend.ui_web.terminal.session import _ignores_ctrl_c

    was = _ignores_ctrl_c()
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
    yield
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, was)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console flags")
def test_a_shell_starts_with_ctrl_c_on_and_ducky_keeps_its_own(ducky_ignores_ctrl_c):
    from frontend.ui_web.terminal.session import _ignores_ctrl_c, ctrl_c_on_for_children

    with ctrl_c_on_for_children():
        assert not _ignores_ctrl_c()
    assert _ignores_ctrl_c()


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows terminals")
@pytest.mark.parametrize(
    "shell,command",
    [
        ("powershell", "ping -n 60 127.0.0.1"),
        ("powershell", "Start-Sleep -Seconds 60"),
        ("bash", "ping -n 60 127.0.0.1"),
        ("bash", "sleep 60"),
    ],
)
def test_ctrl_c_from_the_person_stops_the_command_and_ends_the_agents_wait(tmp_path, monkeypatch, ducky_ignores_ctrl_c, shell, command):
    # Oct 22 2026: Ctrl+C never stopped a PowerShell command (nor a Windows program under Git
    # Bash), and when the shell dropped the rest of the line, done marker included, the agent
    # waited out its whole timeout after the person had stopped its command.
    import threading
    import time

    monkeypatch.delenv("GIT_PAGER", raising=False)
    session = TerminalSession(shell=shell, cwd=str(tmp_path))
    try:
        session.spawn()
    except Exception as exc:  # noqa: BLE001 - no console host or Git Bash here
        pytest.skip(f"cannot start a {shell} terminal here: {exc}")
    try:
        assert session.run_command("echo ready", timeout_s=60)["ok"]
        threading.Timer(3.0, lambda: session.write("\x03")).start()
        started = time.time()
        result = session.run_command(command, timeout_s=120)
        assert time.time() - started < 15, result
        stopped = not result["ok"] and "Ctrl+C" in result["error"]
        assert stopped or result.get("exit_code") not in (None, 0), result
        assert session.run_command("echo after", timeout_s=60)["output_tail"] == "after"
        if shell == "powershell":
            # The person's own settings are back after a line Ctrl+C cut short.
            session.write('Write-Output "pager=[$env:GIT_PAGER]"\r')
            time.sleep(3)
            assert "\npager=[]" in plain_terminal_text(session.read_output_tail(4000))
    finally:
        session.kill()


@needs_powershell
def test_a_timed_out_command_still_running_gets_no_typed_commands(tmp_path):
    session = TerminalSession(shell="powershell", cwd=str(tmp_path))
    try:
        session.spawn()
    except Exception as exc:  # noqa: BLE001 - no console host available here
        pytest.skip(f"cannot start a PowerShell terminal here: {exc}")
    try:
        assert session.run_command("echo ready", timeout_s=60)["ok"]
        slow = session.run_command("ping -n 20 127.0.0.1", timeout_s=2)
        assert slow["error"] == "command timed out", slow
        refused = session.run_command("echo next", timeout_s=10)
        assert not refused["ok"] and "ping.exe" in refused["error"] and "ducky_terminal_close" in refused["hint"], refused
    finally:
        session.kill()


def _bash_session(tmp_path) -> TerminalSession:
    session = TerminalSession(shell="bash", cwd=str(tmp_path))
    try:
        session.spawn()
    except Exception as exc:  # noqa: BLE001 - no Git Bash here
        pytest.skip(f"cannot start a bash terminal here: {exc}")
    return session


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("node"), reason="needs Windows, Git Bash and node")
def test_node_in_a_bash_terminal_finishes_for_the_agent_and_the_person(tmp_path):
    # Oct 31 2026: Git's alias ran node through winpty, which outlives node in this terminal;
    # a two-second test waited ten minutes, twice in one harness race.
    import time

    session = _bash_session(tmp_path)
    try:
        time.sleep(3)  # the first prompt has run PROMPT_COMMAND
        session.write("type node\r")
        time.sleep(2)
        typed = [line for line in plain_terminal_text(session.read_output_tail(4000)).splitlines() if line.startswith("node is")]
        assert typed and "winpty" not in typed[-1], typed
        started = time.time()
        result = session.run_command('node -e "console.log(6*7)"', timeout_s=60)
        assert result["ok"] and result["exit_code"] == 0 and result["output_tail"] == "42", result
        assert time.time() - started < 20
    finally:
        session.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows terminals")
def test_a_program_git_bash_started_is_seen_after_a_timeout_and_its_late_marker_is_ignored(tmp_path):
    # Git Bash leaves `sleep` (any program it runs from usr/bin, winpty too) no living parent,
    # so the process tree never showed it: a timed-out one counted as gone, and the next
    # command got its late done marker.
    import time

    session = _bash_session(tmp_path)
    try:
        assert session.run_command("echo ready", timeout_s=60)["ok"]
        slow = session.run_command("sleep 4; echo slow-done", timeout_s=1)
        assert slow["error"] == "command timed out", slow
        assert "sleep.exe" in session.left_running()
        refused = session.run_command("echo next", timeout_s=10)
        assert not refused["ok"] and "sleep.exe" in refused["error"], refused
        time.sleep(5)  # the slow command ends now and prints its done marker
        after = session.run_command("echo next", timeout_s=30)
        assert after["ok"] and after["output_tail"] == "next", after
    finally:
        session.kill()
