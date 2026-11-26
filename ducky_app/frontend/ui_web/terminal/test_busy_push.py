"""Busy/idle flips are pushed (terminal_busy), at most 2 a second per session."""

from __future__ import annotations

import math
import time
from types import SimpleNamespace

from frontend.ui_web.terminal.manager import TerminalManager
from frontend.ui_web.terminal.session import TerminalSession


def _mgr() -> tuple[TerminalManager, TerminalSession, list[dict]]:
    mgr, events = TerminalManager(), []
    mgr.set_push(events.append)
    session = TerminalSession(shell="bash", cwd="C:/repo")
    session._on_busy = lambda busy: mgr._busy_changed(session.id, busy)
    # A real-shaped entry: the manager outlives the test, and other managers' reapers read it.
    mgr._sessions[session.id] = SimpleNamespace(session=session, chat="", run_owned=False)  # type: ignore[assignment]
    return mgr, session, events


def _busy(events: list[dict]) -> list[bool]:
    return [e["busy"] for e in events if e["type"] == "terminal_busy"]


def test_a_command_start_and_end_push_two_events() -> None:
    _, session, events = _mgr()
    session.set_busy(True)
    session.set_busy(True)  # no flip, no push
    session.set_busy(False)
    time.sleep(0.7)
    assert _busy(events) == [True, False]
    assert all(e["session_id"] == session.id for e in events)


def test_fifty_flips_in_a_second_push_at_most_two() -> None:
    _, session, events = _mgr()
    started = time.monotonic()
    for n in range(50):
        session.set_busy(n % 2 == 0)
        time.sleep(0.016)
    # 2 a second: a loaded runner whose 50 short sleeps take 3.7 s may push 2 per second it took.
    took = time.monotonic() - started
    assert len(_busy(events)) <= 2 * max(1, math.ceil(took))
    time.sleep(0.6)
    assert _busy(events)[-1] is False  # the last value always arrives


def test_a_closed_terminal_pushes_nothing() -> None:
    mgr, session, events = _mgr()
    mgr._sessions.pop(session.id)
    session.set_busy(True)
    assert _busy(events) == []
