"""Pure matchers for UEFN save-dialog dismiss — no editor, no Win32 click."""

from __future__ import annotations

import sys
import types

import pytest

from backend.tools.core.uefn_modal import (
    SAVE_LISTENER_COMMANDS,
    is_confirm_button,
    is_save_dialog_title,
)


@pytest.fixture(autouse=True)
def windows(monkeypatch):
    """Fake UEFN windows on screen: the watchdog's snapshot reads ``state["up"]``."""
    from backend.tools.core import uefn_modal

    state = {"up": set()}
    monkeypatch.setattr(uefn_modal, "_uefn_window_hwnds", lambda: set(state["up"]))
    monkeypatch.setattr(uefn_modal, "_ducky_windows", set())
    return state


def test_save_dialog_titles():
    assert is_save_dialog_title("Save Content")
    assert is_save_dialog_title("Checkout Packages")
    assert is_save_dialog_title("Save Changes")
    assert not is_save_dialog_title("Delete Actors")
    assert not is_save_dialog_title("")
    assert not is_save_dialog_title("Unreal Editor")


def test_confirm_buttons_never_dont_save():
    assert is_confirm_button("Save")
    assert is_confirm_button("&Yes")
    assert is_confirm_button("Save All")
    assert is_confirm_button("OK")
    assert not is_confirm_button("Don't Save")
    assert not is_confirm_button("&Don't Save")
    assert not is_confirm_button("Cancel")
    assert not is_confirm_button("No")
    assert not is_confirm_button("Save As...")
    assert not is_confirm_button("Delete")


def test_save_listener_commands():
    assert "save_current_level" in SAVE_LISTENER_COMMANDS
    assert "save_all_dirty" in SAVE_LISTENER_COMMANDS
    assert "execute_python" not in SAVE_LISTENER_COMMANDS


def test_title_rejects_pickers_where_enter_commits_a_path():
    assert not is_save_dialog_title("Save Level As")
    assert not is_save_dialog_title("Export Unsaved Options")
    assert not is_save_dialog_title("Import Save Packages")
    assert not is_save_dialog_title("Rename Unsaved Asset")
    # "as" is a whole word — "Assets" must still match.
    assert is_save_dialog_title("Checkout Assets")


def test_auto_dismiss_rate_limits_after_a_press(monkeypatch):
    from backend.tools.core import uefn_modal

    monkeypatch.setattr(uefn_modal, "_last_auto_press_at", 0.0)
    monkeypatch.setattr(
        uefn_modal,
        "dismiss_uefn_save_modal",
        lambda **_: {"ok": True, "sent_enter": True, "method": "enter_posted"},
    )
    assert uefn_modal.auto_dismiss_save_modal()["method"] == "enter_posted"
    assert uefn_modal.auto_dismiss_save_modal() is None  # inside the cooldown


def test_auto_dismiss_without_dialog_starts_no_cooldown(monkeypatch):
    from backend.tools.core import uefn_modal

    monkeypatch.setattr(uefn_modal, "_last_auto_press_at", 0.0)
    calls = []

    def fake(**_):
        calls.append(1)
        return {"ok": True, "clicked": False, "sent_enter": False, "reason": "no save dialog"}

    monkeypatch.setattr(uefn_modal, "dismiss_uefn_save_modal", fake)
    assert uefn_modal.auto_dismiss_save_modal() is None
    assert uefn_modal.auto_dismiss_save_modal() is None
    assert len(calls) == 2  # not throttled: every poll looks again


def test_watchdog_presses_while_caller_blocks_then_stops(monkeypatch):
    import time

    from backend.tools.core import uefn_modal

    monkeypatch.setattr(uefn_modal.sys, "platform", "win32")
    calls: list[int] = []

    def fake(**_):
        calls.append(1)
        if len(calls) == 1:
            return {"sent_enter": True, "method": "enter_posted", "window_title": "Save Content"}
        return None

    monkeypatch.setattr(uefn_modal, "auto_dismiss_save_modal", fake)
    monkeypatch.setattr(uefn_modal, "auto_press_lone_button_popups", lambda **_: [])  # never real windows
    seen: list[dict] = []

    def until(predicate, timeout=30.0):
        """Wait on the condition, not on a fixed sleep.

        A 2 s budget with a 0.02 s poll is plenty of wall-clock but says nothing
        about whether a daemon thread was *scheduled*; on a loaded machine this
        test failed there while the code was fine. A generous timeout costs
        nothing when things work and removes the flake when they are slow.
        """
        deadline = time.time() + timeout
        while not predicate() and time.time() < deadline:
            time.sleep(0.01)
        return predicate()

    with uefn_modal.save_modal_watchdog("test", poll_sec=0.02, on_press=seen.append) as events:
        assert until(lambda: bool(events)), "watchdog never pressed"
        # Keeps polling while the caller is still blocked.
        assert until(lambda: len(calls) > 1), "watchdog stopped polling before the block ended"
    assert events and events[0]["label"] == "test" and events[0]["window_title"] == "Save Content"
    assert seen == events
    # Join can return while one tick is still in flight. Wait it out, then
    # the thread must stay quiet — that is the "stopped with the block" check.
    final = len(calls)
    assert final > 1
    # A tick already in flight can land after join. Wait until the count is
    # quiet, instead of assuming one 0.3 s gap is enough on a loaded machine.
    deadline = time.time() + 2.0
    quiet_since = None
    while time.time() < deadline:
        if len(calls) == final:
            if quiet_since is None:
                quiet_since = time.time()
            elif time.time() - quiet_since >= 0.5:
                break
        else:
            final = len(calls)
            quiet_since = None
        time.sleep(0.05)
    assert len(calls) == final


def test_watchdog_block_waits_for_the_press_in_flight(monkeypatch):
    """Leaving the block must not let a press land afterwards.

    The watchdog used to only set its stop flag, so a thread already inside
    auto_dismiss_save_modal() was free to press Enter after the caller had
    finished — into whatever dialog the user opened next.
    """
    import threading
    import time

    from backend.tools.core import uefn_modal

    monkeypatch.setattr(uefn_modal.sys, "platform", "win32")
    entered = threading.Event()
    release = threading.Event()
    finished: list[float] = []

    def slow_press(**_):
        entered.set()
        release.wait(30.0)
        finished.append(time.time())
        return {"sent_enter": True, "method": "enter_posted", "window_title": "Save Content"}

    monkeypatch.setattr(uefn_modal, "auto_dismiss_save_modal", slow_press)
    monkeypatch.setattr(uefn_modal, "auto_press_lone_button_popups", lambda **_: [])

    with uefn_modal.save_modal_watchdog("slow", poll_sec=0.01):
        assert entered.wait(30.0), "watchdog never started its press"
        release.set()  # the press is now in flight as the block exits
    left_block_at = time.time()

    assert finished, "the in-flight press never completed"
    assert finished[0] <= left_block_at, "the block exited before the press it started finished"


# --------------------------------------------------------------------------- the person's popups
#
# The person works in the same UEFN. A Save dialog they opened themselves got Enter
# from the watchdog of whatever Ducky call happened to run (even a read), and every
# press pulled the dialog to the front.


def _skips_seen(monkeypatch, uefn_modal) -> list:
    monkeypatch.setattr(uefn_modal.sys, "platform", "win32")
    seen: list = []
    monkeypatch.setattr(uefn_modal, "auto_dismiss_save_modal", lambda *, skip: seen.append(skip))
    monkeypatch.setattr(uefn_modal, "auto_press_lone_button_popups", lambda *, skip: [])
    return seen


def _one_tick(uefn_modal, label="read") -> None:
    import time

    with uefn_modal.save_modal_watchdog(label, poll_sec=30.0):
        time.sleep(0.05)  # the first check runs right away


def test_a_popup_up_before_the_call_is_the_persons(windows, monkeypatch):
    from backend.tools.core import uefn_modal

    seen = _skips_seen(monkeypatch, uefn_modal)
    windows["up"] = {10}  # the person opened Save Content, then the agent asked something
    _one_tick(uefn_modal)
    assert seen and seen[0] == frozenset({10})


def test_a_popup_an_earlier_ducky_call_left_is_still_pressed(windows, monkeypatch):
    from backend.tools.core import uefn_modal

    seen = _skips_seen(monkeypatch, uefn_modal)
    real_hwnds = uefn_modal._uefn_window_hwnds
    calls = {"n": 0}

    def hwnds():
        calls["n"] += 1
        return {1} if calls["n"] == 1 else {1, 20}  # the first call's push opened popup 20

    monkeypatch.setattr(uefn_modal, "_uefn_window_hwnds", hwnds)
    _one_tick(uefn_modal, "push")
    monkeypatch.setattr(uefn_modal, "_uefn_window_hwnds", real_hwnds)
    windows["up"] = {1, 20}
    _one_tick(uefn_modal, "status")  # waits behind popup 20: Ducky's, so it may press it
    assert seen[-1] == frozenset({1})


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 callbacks")
def test_the_dismiss_never_touches_a_skipped_window(monkeypatch):
    from backend.tools.core import uefn_modal

    posted: list = []
    gone: set = set()

    def post(hwnd, msg, wparam, lparam):
        posted.append(hwnd)
        gone.add(hwnd)
        return 1

    fake = types.SimpleNamespace(
        EnumChildWindows=lambda *a: True,
        IsWindow=lambda h: h not in gone,
        IsWindowVisible=lambda h: h not in gone,
        PostMessageW=post,
        GetForegroundWindow=lambda: 0,
    )
    monkeypatch.setattr(uefn_modal, "_user32", lambda: fake)
    dialog = {"hwnd": 10, "title": "Save Content", "cls": "UnrealWindow", "owned": True}
    monkeypatch.setattr(uefn_modal, "_enum_uefn_windows", lambda: [dict(dialog)])
    out = uefn_modal._dismiss_win32(allow_bare_enter=False, skip=frozenset({10}))
    assert out["reason"] == "no save dialog" and posted == []
    out = uefn_modal._dismiss_win32(allow_bare_enter=False)
    assert out["method"] == "enter_posted" and posted == [10, 10]


@pytest.fixture()
def desk(monkeypatch):
    """A fake desktop for the give-back: which window is in front, which exist."""
    from backend.tools.core import uefn_modal

    state = {"fg": 0, "alive": {5, 9}, "forced": []}
    monkeypatch.setattr(uefn_modal.sys, "platform", "win32")
    monkeypatch.setattr(uefn_modal, "_user32", lambda: types.SimpleNamespace(
        GetForegroundWindow=lambda: state["fg"],
        IsWindow=lambda h: h in state["alive"],
        IsWindowVisible=lambda h: h in state["alive"],
    ))

    def force(hwnd, *, restore=True):
        state["forced"].append((hwnd, restore))
        state["fg"] = hwnd
        return True

    monkeypatch.setattr(uefn_modal, "_force_foreground", force)
    return state


def test_the_foreground_goes_back_to_the_person_without_unmaximizing(desk):
    from backend.tools.core import uefn_modal

    desk["fg"] = 9  # UEFN, after the popup closed
    assert uefn_modal.give_foreground_back(5, popup=77) is True
    assert desk["forced"] == [(5, False)] and desk["fg"] == 5


def test_nothing_is_forced_when_there_is_nothing_to_give_back(desk):
    from backend.tools.core import uefn_modal

    desk["fg"] = 5
    assert uefn_modal.give_foreground_back(5) is True  # still in front
    assert uefn_modal.give_foreground_back(0) is False  # unknown
    assert uefn_modal.give_foreground_back(77, popup=77) is False  # the popup itself
    assert uefn_modal.give_foreground_back(4) is False  # closed since
    assert desk["forced"] == []
