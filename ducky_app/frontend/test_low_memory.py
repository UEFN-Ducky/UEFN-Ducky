"""A nearly full PC is named before Ducky dies of it (Oct 30 2026: 0.5 GB free at launch)."""

from __future__ import annotations

import pytest

from frontend import low_memory
from frontend.ui_web import admission


@pytest.fixture
def pc(monkeypatch):
    state = {"free": [2.0], "asked": [], "alerts": []}
    monkeypatch.delenv("DUCKY_SKIP_LOW_MEMORY_CHECK", raising=False)
    monkeypatch.setattr(admission, "free_memory_gb", lambda: state["free"].pop(0) if len(state["free"]) > 1 else state["free"][0])
    monkeypatch.setattr(admission, "biggest_users", lambda n=5: [
        {"name": "Memory Compression", "gb": 9.0}, {"name": "chrome.exe", "gb": 6.2},
        {"name": "UnrealEditorFortnite.EXE", "gb": 5.1}, {"name": "Discord.exe", "gb": 0.9}, {"name": "x.exe", "gb": 0.1}])
    monkeypatch.setattr("frontend.notifications.alert", lambda *a, **k: state["alerts"].append((a, k)) or True)
    monkeypatch.setattr(low_memory, "_count", lambda: None)
    monkeypatch.setattr(low_memory, "_last_alert", 0.0)
    return state


def _asks(state, *answers):
    replies = list(answers)

    def ask(free, names):
        state["asked"].append((round(free, 1), names))
        return replies.pop(0)
    return ask


def test_enough_memory_starts_without_asking(pc) -> None:
    assert low_memory.check_at_launch(_asks(pc)) is True and pc["asked"] == []


def test_a_full_pc_names_the_biggest_programs_and_try_again_checks_once_more(pc) -> None:
    pc["free"] = [0.5, 0.6, 3.0]
    assert low_memory.check_at_launch(_asks(pc, "try", "try")) is True
    assert pc["asked"] == [(0.5, "chrome 6.2 GB, UnrealEditorFortnite 5.1 GB, Discord 0.9 GB"),
                           (0.6, "chrome 6.2 GB, UnrealEditorFortnite 5.1 GB, Discord 0.9 GB")]


def test_continue_starts_anyway_and_cancel_does_not_start(pc) -> None:
    pc["free"] = [0.5]
    assert low_memory.check_at_launch(_asks(pc, "continue")) is True
    assert low_memory.check_at_launch(_asks(pc, "cancel")) is False


def test_a_running_ducky_raises_one_alert_per_half_hour_while_the_pc_is_full(pc, monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(low_memory.time, "monotonic", lambda: clock[0])
    low_memory.on_heartbeat({"free_gb": 2.5})
    low_memory.on_heartbeat({"free_gb": 0.4})
    clock[0] += 60
    low_memory.on_heartbeat({"free_gb": 0.3})
    assert len(pc["alerts"]) == 1
    (title, status), extra = pc["alerts"][0]
    assert status == "Your PC is almost out of memory." and "chrome 6.2 GB" in extra["detail"]
    clock[0] += low_memory.RUN_EVERY_S
    low_memory.on_heartbeat({"free_gb": 0.3})
    assert len(pc["alerts"]) == 2
