"""Focus windows under the launcher: open, raise and close go out as launcher events."""

from __future__ import annotations

import time

from frontend.native_host import inventory, launcher_windows
from frontend.native_switch import NATIVE_CHILD_ENV
from frontend.ui_web import focus_windows, window_bounds


class _Broker:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def _send_event(self, name, data):
        self.events.append((name, data))
        return True


class _BackendWindow:
    def __init__(self) -> None:
        self._dialogs = _Broker()


def _wait_for(events, name, timeout_s=3.0):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        hits = [d for n, d in events if n == name]
        if hits:
            return hits
        time.sleep(0.02)
    return []


def test_focus_window_opens_and_closes_through_the_launcher(monkeypatch):
    monkeypatch.setenv(NATIVE_CHILD_ENV, "1")
    monkeypatch.setattr(window_bounds, "get_bounds", lambda _key: {"x": 10, "y": 20, "width": 800, "height": 600})
    monkeypatch.setattr(focus_windows, "_log_close", lambda *_a: None)
    main = _BackendWindow()
    focus_windows.configure(api=None, base_url="http://127.0.0.1:5000/", main_window=main)
    events = main._dialogs.events
    try:
        focus_windows.open_focus_window("chat:abc", "Chat", solo=True)
        opened = [d for n, d in events if n == "window_open"]
        assert len(opened) == 1
        wid = opened[0]["wid"]
        assert wid.startswith("focus-")
        assert opened[0]["url"] == f"http://127.0.0.1:5000?focus=chat%3Aabc&title=Chat&wid={wid}"
        assert (opened[0]["x"], opened[0]["y"], opened[0]["w"], opened[0]["h"]) == (10, 20, 800, 600)
        assert ("window_raise", {"wid": wid}) in events

        focus_windows.close_focus_window("chat:abc")
        assert _wait_for(events, "window_close") == [{"wid": wid}]
        assert focus_windows.list_focus_window_ids() == []
    finally:
        focus_windows.close_all_focus_windows()
        focus_windows.configure(api=None, base_url="", main_window=None)


def test_python_window_path_is_unchanged(monkeypatch):
    monkeypatch.delenv(NATIVE_CHILD_ENV, raising=False)
    assert not launcher_windows.active()
    assert launcher_windows.sender_of(object()) is None


def test_focus_window_calls_are_in_the_lean_cut():
    for name in ("open_focus_window", "close_focus_window", "raise_focus_window", "open_focus_window_at_point"):
        assert inventory.INVENTORY[name]["handler"] == "python", name
