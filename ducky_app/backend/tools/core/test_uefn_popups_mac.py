"""On macOS the Win32 popup and Fortnite-client tools never touch the screen: they answer
with the way to the account's PC (window_view.uefn_on_pc). Runs on any OS by forcing darwin."""

from __future__ import annotations

import sys

import pytest

from backend.tools.core import app_popups, uefn_modal, uefn_popups
from backend.tools.uefn import fortnite_client

PC = [{"key_id": "k_pc", "name": "Tas's PC"}]


@pytest.fixture(autouse=True)
def mac(monkeypatch):
    from frontend import ducky_link

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ducky_link, "list_duckies", lambda: {"ok": True, "devices": [
        {"keyId": "k_pc", "name": "Tas's PC", "live": True, "mine": False}]})


@pytest.mark.parametrize("call", [
    lambda: uefn_popups.press_button(123, 1),
    lambda: uefn_modal.dismiss_uefn_save_modal(),
    fortnite_client.client_status,
    lambda: fortnite_client.client_wait(5),
    fortnite_client.client_capture,
    lambda: fortnite_client.client_move("W", 1.0),
])
def test_uefn_tools_on_a_mac_open_the_pcs_ducky(call) -> None:
    out = call()
    assert out["ok"] is False and out["open_remote"] == PC
    assert out["error"].startswith("UEFN runs on Windows. Open your PC’s Ducky (Duckies → Tas's PC)")


def test_nothing_is_listed_or_guarded_on_a_mac(monkeypatch) -> None:
    assert uefn_popups.list_popups() == []
    assert uefn_modal.find_uefn_save_dialog() is None
    assert "your PC" in uefn_popups.blocked_call_hint()
    monkeypatch.setattr(uefn_popups, "_guard_started", False)
    uefn_popups.start_popup_guard()
    assert uefn_popups._guard_started is False


def test_app_popups_say_why_on_a_mac() -> None:
    assert app_popups.unsupported_reason() == app_popups.MAC_REASON
    assert app_popups.list_popups("blender") == []
    assert app_popups.press(1, 1) == {"ok": False, "error": app_popups.MAC_REASON}
    assert app_popups.stall_report("blender")["error"] == app_popups.MAC_REASON
