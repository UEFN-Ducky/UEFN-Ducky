"""Regression tests for batching high-frequency agent events across pywebview."""

from __future__ import annotations

from frontend.ui_web.panel_api import _coalesce_agent_events


def test_coalesces_adjacent_stream_deltas_for_same_run() -> None:
    events = [
        {"type": "thinking", "text": "one ", "conv_id": "c", "run_id": "r"},
        {"type": "thinking", "text": "two", "conv_id": "c", "run_id": "r"},
        {"type": "text_delta", "text": "answer ", "conv_id": "c", "run_id": "r"},
        {"type": "text_delta", "text": "text", "conv_id": "c", "run_id": "r"},
    ]

    assert _coalesce_agent_events(events) == [
        {"type": "thinking", "text": "one two", "conv_id": "c", "run_id": "r"},
        {"type": "text_delta", "text": "answer text", "conv_id": "c", "run_id": "r"},
    ]


def test_preserves_tool_boundaries_and_different_runs() -> None:
    events = [
        {"type": "thinking", "text": "before", "conv_id": "c", "run_id": "r"},
        {"type": "tool", "text": "read", "conv_id": "c", "run_id": "r"},
        {"type": "thinking", "text": "after", "conv_id": "c", "run_id": "r"},
        {"type": "thinking", "text": "other", "conv_id": "c", "run_id": "r2"},
    ]

    assert _coalesce_agent_events(events) == events


def test_keeps_only_latest_adjacent_status_for_same_run() -> None:
    events = [
        {"type": "status", "text": "starting", "conv_id": "c", "run_id": "r"},
        {"type": "status", "text": "working", "conv_id": "c", "run_id": "r"},
    ]

    assert _coalesce_agent_events(events) == [events[-1]]
