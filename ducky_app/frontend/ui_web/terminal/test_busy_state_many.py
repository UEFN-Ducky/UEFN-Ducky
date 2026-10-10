"""The header asks every few seconds whether each terminal runs a command."""

from __future__ import annotations

from frontend.ui_web.terminal import manager as manager_module
from frontend.ui_web.terminal.manager import TerminalManager
from frontend.ui_web.terminal.session import TerminalSession


class _Pty:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def isalive(self) -> bool:
        return True


def _session(pid: int) -> TerminalSession:
    session = TerminalSession(shell="bash", cwd=".")
    session._pty = _Pty(pid)
    return session


def test_all_terminals_share_one_walk_of_the_process_list(monkeypatch) -> None:
    """Each terminal used to take its own snapshot of every process on the PC (~10 ms each)."""
    sessions = {"idle": _session(10), "npm": _session(20), "pwsh": _session(30)}
    manager = TerminalManager()
    monkeypatch.setattr(manager, "get_session", sessions.get)
    walks: list[int] = []

    def snapshot() -> tuple[dict[int, list[int]], dict[int, str]]:
        walks.append(1)
        # The shell 20 runs npm; 10 and 30 sit at their prompt.
        return {20: [21]}, {10: "bash.exe", 20: "bash.exe", 21: "npm.exe", 30: "pwsh.exe"}

    monkeypatch.setattr(manager_module, "_process_snapshot", snapshot)
    out = manager.busy_state_many(["idle", "npm", "pwsh", "gone", "npm"])
    assert len(walks) == 1
    states = out["states"]
    assert list(states) == ["idle", "npm", "pwsh", "gone"]
    assert [states[sid].get("running") for sid in ("idle", "npm", "pwsh")] == [False, True, False]
    assert states["gone"] == {"ok": False, "error": "session not found"}


def test_no_walk_when_every_terminal_runs_an_agent_command(monkeypatch) -> None:
    session = _session(10)
    session.set_busy(True)
    manager = TerminalManager()
    monkeypatch.setattr(manager, "get_session", {"agent": session}.get)
    walks: list[int] = []
    monkeypatch.setattr(manager_module, "_process_snapshot", lambda: walks.append(1) or ({}, {}))
    out = manager.busy_state_many(["agent"])
    assert out["states"]["agent"]["running"] is True and out["states"]["agent"]["busy"] is True
    assert walks == []
