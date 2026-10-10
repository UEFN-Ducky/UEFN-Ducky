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

from frontend.ui_web.terminal.session import _DONE_RE, TerminalSession

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
    return int(match.group(1))


def test_the_command_is_submitted_with_a_bare_carriage_return():
    wrapped = _wrapped("echo hello")
    assert wrapped.endswith("\r") and not wrapped.endswith("\r\n")
    assert TerminalSession(shell="powershell", cwd=".")._wrap_agent_command("long", background=True) == "long\r"


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
    match = _DONE_RE.search("__DUCKY_DONE__-1073741819__")
    assert match and int(match.group(1)) == -1073741819


def test_bash_commands_are_unchanged():
    wrapped = TerminalSession(shell="bash", cwd=".")._wrap_agent_command("ls", background=False)
    assert wrapped == "ls; echo __DUCKY_DONE__$?__\r\n"


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
        assert "ducky-pty-ok" in result["output_tail"]
        failed = session.run_command(r"Get-Item C:\no\such\path_ducky_xyz", timeout_s=60)
        assert failed["ok"] and failed["exit_code"] == 1, failed
    finally:
        session.kill()
