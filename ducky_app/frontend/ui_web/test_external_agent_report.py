"""Test the external-coding-agent context report (real backend + estimate breakdown)."""

from __future__ import annotations

from types import SimpleNamespace

from frontend.ui_web.context_tokens import _external_agent_report, compute_context_usage


def _settings():
    return SimpleNamespace(
        coding_agents={"claude_code": {"enabled": True, "permission_mode": "acceptEdits"}},
        agent_provider="anthropic",
        agent_model="",
        uefn_project_root="",
        tool_result_format="toon",
        memory_keep_last_messages=20,
    )


def test_external_report_uses_real_session_and_usage(monkeypatch):
    from frontend.ui_web import context_tokens as ct

    # Label + permission defaults normally come from the installed Store plugin —
    # pin them so the test doesn't depend on this machine's plugin state.
    monkeypatch.setattr(
        "backend.agent.coding_agents.base.CODING_AGENT_LABELS",
        {"claude_code": "Claude Code"},
    )
    monkeypatch.setattr(
        "backend.uefn_plugins.host.get_coding_agent_registration",
        lambda agent_id: {"settings_defaults": {"permission_mode": "acceptEdits"}},
    )

    # Pin estimates so this machine's installed skill packs don't swallow the remainder.
    monkeypatch.setattr(ct, "_tool_definition_report_sync", lambda *a, **k: (800, []))
    monkeypatch.setattr(
        ct,
        "_deployed_skill_token_report",
        lambda *a, **k: (400, [{"label": "uefn", "tokens": 400}], ["uefn"]),
    )
    monkeypatch.setattr(
        ct,
        "_conversation_report",
        lambda *a, **k: (0, 120, [], [{"label": "user #1", "tokens": 120}]),
    )

    conv = SimpleNamespace(
        coding_agent="claude_code",
        upstream_session_id="claude_code:sess-abc",
        messages=[{"role": "user", "content": "hello from test"}],
        coding_agent_stats={
            "model": "claude-sonnet-4",
            "context_tokens": 5320,
            "num_turns": 3,
            "cost_usd": 0.05,
        },
        # Cumulative input+cache (5620) exceeds the stored last-step window (5320),
        # so resolve_context_window_tokens trusts the adapter's stored window.
        token_usage={
            "total_input": 120,
            "total_output": 340,
            "total_cache_read": 5500,
            "total_cache_write": 0,
            "calls": [
                {
                    "input_tokens": 120,
                    "output_tokens": 340,
                    "cache_read_tokens": 5500,
                    "cache_write_tokens": 0,
                    "provider": "claude_code",
                    "model": "claude-sonnet-4",
                    "cost_usd": 0.05,
                }
            ],
        },
        context_summary="",
        context_summary_through=0,
        context_summary_tokens=0,
    )
    report = _external_agent_report(conv, _settings(), "claude_code", "sonnet", include_content=False)
    info = report["agent_info"]
    assert info["label"] == "Claude Code"
    assert info["model"] == "claude-sonnet-4"
    assert info["session_active"] is True
    assert info["num_turns"] == 3
    assert info["permission_mode"] == "acceptEdits"
    # Real session window, not an embedded-prompt estimate.
    assert report["used_tokens"] == 5320
    assert report["context_limit"] >= 100_000
    # Real API usage flows through with authoritative cost.
    assert report["input_tokens"] == 120
    assert report["output_tokens"] == 340
    assert report["cost_usd"] == 0.05
    by_id = {b["id"]: b["tokens"] for b in report["breakdown"]}
    assert by_id["mcp_tools"] == 800
    assert by_id["skill"] == 400
    assert by_id["conversation"] == 120
    # Remainder of agent-reported window after known segments (5320 - 1320).
    assert by_id["agent_internals"] == 4000


def test_external_report_before_first_run():
    conv = SimpleNamespace(
        coding_agent="codex",
        upstream_session_id="",
        coding_agent_stats=None,
        token_usage=None,
        messages=[],
        context_summary="",
        context_summary_through=0,
        context_summary_tokens=0,
    )
    report = _external_agent_report(conv, _settings(), "codex", "default", include_content=False)
    info = report["agent_info"]
    assert info["session_active"] is False
    assert info["has_run"] is False
    assert report["used_tokens"] == 0
    assert report["cost_usd"] is None


def test_compute_context_usage_forwards_agent_info(monkeypatch):
    """Regression: agent_info must reach the UI (AgentInfoSection)."""
    from frontend.ui_web import context_tokens as ct

    fake = {
        "used_tokens": 10,
        "context_limit": 100,
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
        "breakdown": [],
        "omitted": [],
        "agent_info": {"coding_agent": "cursor", "label": "Cursor", "model": "default"},
    }
    monkeypatch.setattr(ct, "compute_context_report", lambda *a, **k: fake)
    out = compute_context_usage("c1", "m1")
    assert out["agent_info"]["coding_agent"] == "cursor"
