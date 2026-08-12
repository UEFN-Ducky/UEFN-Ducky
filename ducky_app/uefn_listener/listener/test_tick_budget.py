"""Unit tests for tick drain budget (no Unreal required)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_CONFIG = Path(__file__).resolve().parent / "config.py"
_spec = importlib.util.spec_from_file_location("listener_config_under_test", _CONFIG)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

should_stop_tick_drain = _mod.should_stop_tick_drain


def test_always_allows_first_command():
    assert (
        should_stop_tick_drain(processed=0, batch_limit=5, elapsed_ms=999.0, budget_ms=8.0) is False
    )


def test_stops_when_batch_limit_hit():
    assert (
        should_stop_tick_drain(processed=5, batch_limit=5, elapsed_ms=1.0, budget_ms=8.0) is True
    )


def test_stops_when_budget_spent_after_first():
    assert (
        should_stop_tick_drain(processed=1, batch_limit=5, elapsed_ms=8.0, budget_ms=8.0) is True
    )
    assert (
        should_stop_tick_drain(processed=1, batch_limit=5, elapsed_ms=7.9, budget_ms=8.0) is False
    )


def test_under_budget_keeps_draining():
    assert (
        should_stop_tick_drain(processed=3, batch_limit=5, elapsed_ms=4.0, budget_ms=8.0) is False
    )
