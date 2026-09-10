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
    with uefn_modal.save_modal_watchdog("test", poll_sec=0.02, on_press=seen.append) as events:
        deadline = time.time() + 2.0
        while not events and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)  # keeps polling while the caller is still blocked
    assert events and events[0]["label"] == "test" and events[0]["window_title"] == "Save Content"
    assert seen == events
    polled = len(calls)
    assert polled > 1
    time.sleep(0.1)
    assert len(calls) == polled  # thread stopped with the block
