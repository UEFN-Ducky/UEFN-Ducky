"""Unit tests for 503 busy backoff + cacheable commands."""

from __future__ import annotations

from backend.bridge.client import _CACHEABLE_COMMANDS, busy_backoff_sleep, _looks_like_save_lock


def test_busy_backoff_grows_then_caps():
    assert busy_backoff_sleep(0) == 0.15
    assert busy_backoff_sleep(1) == 0.3
    assert busy_backoff_sleep(2) == 0.6
    assert busy_backoff_sleep(3) == 1.0
    assert busy_backoff_sleep(10) == 1.0


def test_search_and_actors_are_cacheable():
    assert "search_assets" in _CACHEABLE_COMMANDS
    assert "list_assets" in _CACHEABLE_COMMANDS
    assert "get_all_actors" in _CACHEABLE_COMMANDS
    assert _CACHEABLE_COMMANDS["search_assets"] > 0


def test_looks_like_save_lock(monkeypatch):
    from backend.bridge import client

    monkeypatch.setattr(client, "_listener_health", lambda _port: {"current_command": "save_current_level"})
    assert _looks_like_save_lock(4200, "ping") is True
    monkeypatch.setattr(client, "_listener_health", lambda _port: {"current_command": "spawn_actor"})
    assert _looks_like_save_lock(4200, "ping") is False
    assert _looks_like_save_lock(4200, "save_all_dirty") is True


def test_busy_error_mentions_dismiss_on_save(monkeypatch):
    from backend.bridge import client

    monkeypatch.setattr(client, "_listener_health", lambda _port: {"current_command": "save_current_level"})
    msg = client._busy_error_message(4200, "ping")
    assert "dismiss_uefn_modal" in msg


def test_auto_dismiss_is_gated_on_the_dialog_not_the_command(monkeypatch):
    """A wire / build / execute_python can open the Save prompt too."""
    from backend.bridge import client
    from backend.tools.core import uefn_modal

    monkeypatch.setattr(
        uefn_modal,
        "auto_dismiss_save_modal",
        lambda: {"sent_enter": True, "method": "enter_posted", "window_title": "Save Content"},
    )
    assert client._auto_dismiss_save_modal("wire_verse_device_ref") is True
    monkeypatch.setattr(uefn_modal, "auto_dismiss_save_modal", lambda: None)
    assert client._auto_dismiss_save_modal("wire_verse_device_ref") is False
