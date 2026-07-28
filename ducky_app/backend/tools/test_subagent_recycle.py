"""Helpers for subagent reuse / recycle (handoff → fresh twin)."""

from __future__ import annotations

from backend.tools.ducky_panel import build_recycle_spawn_message, next_subagent_title


def test_next_subagent_title_bumps_version():
    assert next_subagent_title("Arena Level") == "Arena Level (v2)"
    assert next_subagent_title("Arena Level (v2)") == "Arena Level (v3)"
    assert next_subagent_title("Arena Level (v10)") == "Arena Level (v11)"


def test_build_recycle_spawn_message_includes_handoff_and_continue():
    text = build_recycle_spawn_message("Built the pads.", "Wire the triggers next.")
    assert "Handoff from predecessor" in text
    assert "Built the pads." in text
    assert "Wire the triggers next." in text
    assert "FRESH version" in text


def test_build_recycle_spawn_message_handoff_only():
    text = build_recycle_spawn_message("Done the mesh.")
    assert "Done the mesh." in text
    assert "Continue with this task" not in text
