"""A waited agent run tells the chat's tray row how long it should take."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from frontend.ui_web.terminal import run_durations
from frontend.ui_web.terminal.manager import TerminalManager


@pytest.fixture
def mgr(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    m = TerminalManager()
    session = Mock(cwd=str(tmp_path), shell="powershell")
    session.is_busy.return_value = False
    session.is_alive.return_value = True
    session.run_command.return_value = {"ok": True, "exit_code": 0, "output_tail": "done"}
    monkeypatch.setattr(m, "get_session", lambda sid: session if sid == "s1" else None)
    events: list[dict] = []
    m.set_push(events.append)
    m.expects = lambda: [(e["expected_ms"], e["estimated"]) for e in events if e["type"] == "run_expect"]  # type: ignore[attr-defined]
    m.events = events  # type: ignore[attr-defined]
    return m


def _run(m: TerminalManager, command: str, **kw) -> dict:
    out = m.run_agent_command("s1", command, source="sandbox_run", conv_id="chat-a", auto_approve=True, **kw)
    assert out["ok"], out
    return out


def test_a_stated_length_is_published_and_cleared_at_the_end(mgr):
    _run(mgr, "python load_200.py 200 120", expect_minutes=2)
    assert mgr.expects() == [(120_000, False), (0, False)]
    assert all(e["conv_id"] == "chat-a" for e in mgr.events if e["type"] == "run_expect")


def test_without_one_the_median_of_past_runs_is_an_estimate(mgr, tmp_path):
    _run(mgr, "python bench.py --seed 0")
    assert mgr.expects() == []  # nothing known before a run ended
    store = tmp_path / "UEFN-Ducky" / "run_durations.json"
    assert len(json.loads(store.read_text())["sandbox_run|python bench.py --seed "]) == 1  # the run was timed
    store.unlink()
    for n, seconds in enumerate((3.0, 5.0, 4.0)):  # the mocked runs are instant: real lengths
        run_durations.record("sandbox_run", f"python bench.py --seed {n}", seconds)  # numbers stripped: one command
    _run(mgr, "python bench.py --seed 9")
    assert mgr.expects() == [(4000, True), (0, False)]
    mgr.events.clear()
    _run(mgr, "python other.py")
    assert mgr.expects() == []


def test_only_the_last_five_runs_count(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for seconds in (100, 100, 100, 1, 2, 3, 4, 5):
        run_durations.record("ducky_terminal_run", "npm run build", seconds)
    assert run_durations.expected_ms("ducky_terminal_run", "npm run build") == (3000, True)
    assert run_durations.expected_ms("sandbox_run", "npm run build") == (0, False)  # per tool
