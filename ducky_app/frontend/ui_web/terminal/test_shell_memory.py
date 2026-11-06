"""Private MB of each agent shell this PC has, after 20 commands. Prints them; asserts no MB.

Nov 6 2026 (this PC): see the step notes. Run with ``-s`` to read the numbers.
"""

from __future__ import annotations

import sys

import pytest

from frontend.ui_web.terminal.session import TerminalSession
from frontend.ui_web.terminal.shells import resolve_shell

psutil = pytest.importorskip("psutil")
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows shells")


def _private_mb(pid: int) -> float:
    root = psutil.Process(pid)
    total = 0
    for proc in [root, *root.children(recursive=True)]:
        try:
            total += proc.memory_info().private
        except psutil.Error:
            pass
    return total / 2**20


def _shells() -> list[tuple[str, str, list[str] | None]]:
    rows: list[tuple[str, str, list[str] | None]] = [("powershell (agent, light)", "powershell", None)]
    try:
        bash, _argv = resolve_shell("bash")
    except FileNotFoundError:
        return rows
    rows.append(("git bash (agent, --noprofile --norc)", "bash", [bash, "--login", "--noprofile", "--norc"]))
    return rows


@pytest.mark.parametrize("label,shell,argv", _shells(), ids=lambda v: v if isinstance(v, str) else "")
def test_measure_agent_shell_private_mb(tmp_path, label, shell, argv):
    session = TerminalSession(shell=shell, cwd=str(tmp_path), spawn_argv=argv, agent=True)
    try:
        session.spawn()
    except Exception as exc:  # noqa: BLE001 - no console host here
        pytest.skip(f"cannot start {label}: {exc}")
    try:
        for _ in range(20):
            assert session.run_command("echo hi", timeout_s=60)["ok"]
        print(f"\n{label}: {_private_mb(int(session._pty.pid)):.1f} MB private")
    finally:
        session.kill()
