"""mac_alert: an osascript alert that closes itself, its text passed as argv."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from frontend import mac_alert


def _run(monkeypatch, stdout: str) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv) or SimpleNamespace(stdout=stdout))
    return calls


def test_the_pressed_button_is_its_index_and_the_alert_gives_up_by_itself(monkeypatch) -> None:
    calls = _run(monkeypatch, "Don't send\n")
    assert mac_alert.ask('Say "hi"', "a\\b", ["Send report", "Don't send", "Reload"], wait_s=300) == 1
    argv = calls[0]
    assert argv[0] == "osascript" and "giving up after" in argv[2]
    assert argv[3:] == ['Say "hi"', "a\\b", "300", "Send report", "Don't send", "Reload"]  # argv, not spliced


def test_no_answer_or_no_osascript_is_minus_one(monkeypatch) -> None:
    _run(monkeypatch, "")  # gave up
    assert mac_alert.ask("t", "m") == -1

    def missing(*a, **k):
        raise FileNotFoundError("osascript")

    monkeypatch.setattr(subprocess, "run", missing)
    assert mac_alert.ask("t", "m") == -1
