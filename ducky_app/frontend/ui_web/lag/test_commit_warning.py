"""An agent's vague or sprawling commit still runs, and its result says why it should not."""

from __future__ import annotations

from frontend.ui_web.terminal import commit_guard
from frontend.ui_web.terminal.manager import TerminalManager


class _Session:
    shell = "bash"
    cwd = "C:/repo"

    def __init__(self) -> None:
        self.ran: list[str] = []

    def is_busy(self) -> bool:
        return False

    def run_command(self, command: str, **_kw) -> dict:
        self.ran.append(command)
        return {"ok": True, "exit_code": 0, "output_tail": "[main abc123] General fixes"}

    def read_output_tail(self, **_kw) -> str:
        return ""


def _manager(monkeypatch, warn):
    mgr, session = TerminalManager(), _Session()
    monkeypatch.setattr(mgr, "get_session", lambda _sid: session)
    monkeypatch.setattr(commit_guard, "check", lambda command, cwd, conv_id, **kw: "")
    monkeypatch.setattr(commit_guard, "warn", warn)
    return mgr, session


NOTE = "commit_guard: name the one change this commit makes (not 'general fixes')."


def test_vague_commit_runs_and_carries_the_warning(monkeypatch) -> None:
    seen = []
    mgr, session = _manager(monkeypatch, lambda command, cwd, conv_id, **kw: seen.append((command, cwd, conv_id)) or NOTE)
    out = mgr.run_agent_command("s1", 'git commit -m "General fixes"', conv_id="c1", auto_approve=True,
                                command_timeout_s=5)
    assert out["ok"] is True and out["exit_code"] == 0
    assert out["commit_warning"] == NOTE
    assert session.ran == ['git commit -m "General fixes"']
    assert seen == [('git commit -m "General fixes"', "C:/repo", "c1")]


def test_a_good_commit_has_no_warning(monkeypatch) -> None:
    mgr, _session = _manager(monkeypatch, lambda *a, **kw: "")
    out = mgr.run_agent_command("s1", 'git commit -m "Pace terminal output"', auto_approve=True, command_timeout_s=5)
    assert out["ok"] is True and "commit_warning" not in out


def test_a_broken_warn_never_blocks_the_command(monkeypatch) -> None:
    def boom(*_a, **_kw):
        raise RuntimeError("git not found")

    mgr, session = _manager(monkeypatch, boom)
    out = mgr.run_agent_command("s1", 'git commit -m "x"', auto_approve=True, command_timeout_s=5)
    assert out["ok"] is True and "commit_warning" not in out and session.ran
