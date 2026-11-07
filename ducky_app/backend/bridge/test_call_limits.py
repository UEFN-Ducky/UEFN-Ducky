"""Call time limits: Epic UEFN calls (unreal__*) stop and say which popup holds UEFN.

Nov 6 2026: 44 unreal__call_tool calls sat stuck with no limit at all.
"""

from __future__ import annotations

import threading
import time

from backend.bridge import call_limits


def test_limits_table_keeps_every_old_limit_and_adds_unreal() -> None:
    lim = call_limits.call_limit
    assert lim("unreal__call_tool", "unreal__call_tool") == 180.0
    assert lim("unreal__list_toolsets", "unreal__list_toolsets") == 60.0
    assert lim("unreal__describe_toolset", "unreal__describe_toolset") == 60.0
    assert lim("ducky_call_tool", "unreal__call_tool") == 180.0  # tighter than the 900 s plugin limit
    # Unchanged:
    assert lim("workspace_read_file", "workspace_read_file") == 30.0
    assert lim("skill_search", "skill_search") == 60.0
    assert lim("ducky_call_tool", "roblox_call") == 900.0
    assert lim("ducky_call_tool", "ducky_ask_user") is None
    assert lim("ducky_ask_user", "ducky_ask_user") is None
    assert lim("spawn_layout", "spawn_layout") is None
    assert lim("unreal__other", "unreal__other") is None


def test_unreal_timeout_names_up_to_three_popups(monkeypatch) -> None:
    monkeypatch.setattr(call_limits, "_popup_titles", lambda: ["Save Changes?", "Message Log", "A", "B"])
    msg = call_limits.timed_out_message("unreal__call_tool", 180)
    assert msg.startswith("UEFN did not answer in 180 s")
    assert "Open popup: Save Changes?, Message Log, A." in msg and "B" not in msg.split("Open popup:")[1].split(".")[0]


def test_popup_read_never_takes_longer_than_its_bound(monkeypatch) -> None:
    gate = threading.Event()
    monkeypatch.setattr(call_limits, "_popup_titles", lambda: gate.wait(10) and ["late"])
    started = time.monotonic()
    msg = call_limits.timed_out_message("unreal__call_tool", 180)
    gate.set()
    assert time.monotonic() - started < 1.5
    assert "Open popup" not in msg and msg.startswith("UEFN did not answer in 180 s")


def test_popup_read_failure_and_other_tools_keep_the_old_message(monkeypatch) -> None:
    def boom():
        raise OSError("no windows")

    monkeypatch.setattr(call_limits, "_popup_titles", boom)
    assert "Open popup" not in call_limits.timed_out_message("unreal__list_toolsets", 60)
    assert "did not answer within 30 s" in call_limits.timed_out_message("workspace_read_file", 30)
