"""ducky_spawn_chat: the agent its caller names decides where a bare model name runs."""

from __future__ import annotations

import inspect


def test_the_named_agent_decides_where_a_bare_model_runs(monkeypatch) -> None:
    """Nov 11 2026: coding_agent="ducky", model="gemini-3.8-flash" was seated on Cursor (its
    list has that name too) because the argument was thrown away."""
    from backend.agent import model_pricing
    from backend.tools.panel import ducky_panel as dp

    monkeypatch.setattr(model_pricing, "infer_provider", lambda m: "gemini" if m.startswith("gemini") else "")
    assert dp._seat_model("gemini-3.8-flash", "ducky") == "gemini:gemini-3.8-flash"  # the model's own API
    assert dp._seat_model("gemini-3.8-flash", "cursor") == "cursor:gemini-3.8-flash"
    assert dp._seat_model("flash", "Gemini-CLI") == "gemini_cli:flash"
    assert dp._seat_model("gemini-3.8-flash") == "gemini-3.8-flash"  # no agent named: as before
    assert dp._seat_model("codex:gpt-6", "ducky") == "codex:gpt-6"  # an exact pick is never rewritten
    assert dp._seat_model("", "ducky") == ""  # no model: the profile's own
    assert dp._seat_model("mystery-1", "ducky") == "mystery-1"  # no known API for it: resolved as before
    # "ducky" as the default made a caller's "ducky" look like no choice at all.
    assert inspect.signature(dp.ducky_spawn_chat).parameters["coding_agent"].default == ""
