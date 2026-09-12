"""Pure matchers for UEFN save-dialog dismiss — no editor, no Win32 click."""

from __future__ import annotations

from backend.tools.core.uefn_modal import (
    SAVE_LISTENER_COMMANDS,
    is_confirm_button,
    is_save_dialog_title,
)


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

    def fake():
        calls.append(1)
        if len(calls) == 1:
            return {"sent_enter": True, "method": "enter_posted", "window_title": "Save Content"}
        return None

    monkeypatch.setattr(uefn_modal, "auto_dismiss_save_modal", fake)
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
    # The block joins the in-flight tick, so the count is final the moment it exits.
    polled = len(calls)
    assert polled > 1
    time.sleep(0.1)
    assert len(calls) == polled  # thread stopped with the block


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

    def slow_press():
        entered.set()
        release.wait(30.0)
        finished.append(time.time())
        return {"sent_enter": True, "method": "enter_posted", "window_title": "Save Content"}

    monkeypatch.setattr(uefn_modal, "auto_dismiss_save_modal", slow_press)

    with uefn_modal.save_modal_watchdog("slow", poll_sec=0.01):
        assert entered.wait(30.0), "watchdog never started its press"
        release.set()  # the press is now in flight as the block exits
    left_block_at = time.time()

    assert finished, "the in-flight press never completed"
    assert finished[0] <= left_block_at, "the block exited before the press it started finished"
