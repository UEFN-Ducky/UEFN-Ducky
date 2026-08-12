"""Unit tests for 503 busy backoff + cacheable commands."""

from __future__ import annotations

from backend.bridge.client import _CACHEABLE_COMMANDS, busy_backoff_sleep


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
