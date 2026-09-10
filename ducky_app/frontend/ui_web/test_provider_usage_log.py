"""Tests for the global 7-day per-provider usage ledger."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from frontend.ui_web import provider_usage_log as pul


@pytest.fixture(autouse=True)
def _isolate_plugin_host(monkeypatch):
    # usage_report looks up plugin families; don't boot the Store host in unit tests.
    monkeypatch.setattr(
        "backend.uefn_plugins.host.get_contributions",
        lambda: {"llm_providers": [], "llm_coding_agents": []},
    )


def test_log_call_and_report(monkeypatch, tmp_path):
    monkeypatch.setattr(pul, "default_app_data_dir", lambda: tmp_path)
    now = time.time()
    pul.log_call(
        provider="anthropic",
        model="claude-sonnet-4",
        input_tokens=1000,
        output_tokens=200,
        cache_read_tokens=800,
        cache_write_tokens=50,
        cost_usd=0.01,
        conv_id="c1",
        agent="ducky",
        ducky_label="Builder",
        ts=now,
    )
    pul.log_call(
        provider="anthropic",
        model="claude-haiku",
        input_tokens=500,
        output_tokens=100,
        conv_id="c2",
        agent="claude_code",
        ducky_label="Coder",
        ts=now - 3600,
    )
    pul.log_call(
        provider="openai",
        model="gpt-4.1",
        input_tokens=99,
        output_tokens=1,
        ts=now,
    )

    report = pul.usage_report("anthropic", days=7)
    assert report["provider"] == "anthropic"
    assert report["call_count"] == 2
    assert report["total_input"] == 1500
    assert report["total_output"] == 300
    assert report["total_cache_read"] == 800
    assert report["cache_hit_rate"] == round((800 / 1500) * 100, 1)
    assert report["cost_usd"] is not None and report["cost_usd"] >= 0.01
    assert len(report["by_day"]) == 7
    models = {m["model"] for m in report["by_model"]}
    assert "claude-sonnet-4" in models
    assert "claude-haiku" in models
    agents = {a["agent"] for a in report["by_agent"]}
    assert "ducky" in agents
    assert "claude_code" in agents
    assert report["by_agent"][0]["agent"] == "ducky"  # more tokens
    duckies = {d["label"]: d for d in report["by_ducky"]}
    assert "Builder" in duckies
    assert duckies["Builder"]["call_count"] == 1


def test_prune_drops_old_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(pul, "default_app_data_dir", lambda: tmp_path)
    now = time.time()
    pul.log_call(provider="cursor", model="default", input_tokens=10, output_tokens=1, ts=now - 10 * 86400)
    # Rewrite via a fresh call so prune runs.
    pul.log_call(provider="cursor", model="default", input_tokens=20, output_tokens=2, ts=now)
    report = pul.usage_report("cursor", days=7)
    assert report["call_count"] == 1
    assert report["total_input"] == 20


def test_ducky_usage_report(monkeypatch, tmp_path):
    monkeypatch.setattr(pul, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(pul, "_conv_lookup", lambda: {})
    monkeypatch.setattr(
        "frontend.ui_web.project_chats.list_all_conversation_metadata",
        lambda: [
            SimpleNamespace(
                id="c1",
                title="Build",
                ducky_name="Builder",
                updated=100.0,
                group_members=[],
            ),
            SimpleNamespace(
                id="c2",
                title="Other",
                ducky_name="Coder",
                updated=50.0,
                group_members=[],
            ),
        ],
    )
    now = time.time()
    pul.log_call(
        provider="anthropic",
        model="m",
        input_tokens=100,
        output_tokens=10,
        conv_id="c1",
        ducky_label="Builder",
        ts=now,
    )
    pul.log_call(
        provider="anthropic",
        model="m",
        input_tokens=50,
        output_tokens=5,
        conv_id="c2",
        ducky_label="Coder",
        ts=now,
    )
    report = pul.ducky_usage_report("Builder", days=7)
    assert report["chat_count"] == 1
    assert report["call_count"] == 1
    assert report["total_input"] == 100
    assert report["chats"][0]["conv_id"] == "c1"


def test_record_coding_agent_usage_writes_ledger(monkeypatch, tmp_path):
    """CLI coding agents skip make_provider — record_coding_agent_usage writes the ledger."""
    from backend.agent.coding_agents.runner import record_coding_agent_usage

    monkeypatch.setattr(pul, "default_app_data_dir", lambda: tmp_path)
    conv = SimpleNamespace(
        id="chat-1",
        token_usage=None,
        messages=[],
        coding_agent="codex",
        ducky_name="Builder",
        title="Build",
    )
    result = SimpleNamespace(
        usage={
            "input_tokens": 40,
            "output_tokens": 5,
            "cache_read_tokens": 10,
            "model": "o3",
            "cost_usd": 0.002,
        }
    )
    record_coding_agent_usage(conv, "codex", "o3", result)
    report = pul.usage_report("codex", days=7)
    assert report["call_count"] == 1
    assert report["total_input"] == 40
    assert report["total_output"] == 5
    assert conv.token_usage is not None
    assert conv.token_usage["total_input"] == 40


def test_record_coding_agent_usage_empty_still_logs(monkeypatch, tmp_path):
    from backend.agent.coding_agents.runner import record_coding_agent_usage

    monkeypatch.setattr(pul, "default_app_data_dir", lambda: tmp_path)
    conv = SimpleNamespace(
        id="chat-2",
        token_usage=None,
        messages=[],
        coding_agent="claude_code",
        ducky_name="Coder",
        title="Code",
    )
    record_coding_agent_usage(conv, "claude_code", "sonnet", None)
    report = pul.usage_report("claude_code", days=7)
    assert report["call_count"] == 1
    assert report["total_input"] == 1
    assert conv.token_usage is None


def test_usage_report_includes_plugin_coding_agent(monkeypatch, tmp_path):
    monkeypatch.setattr(pul, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "backend.uefn_plugins.host.get_contributions",
        lambda: {
            "llm_providers": [{"id": "anthropic", "plugin_id": "anthropic"}],
            "llm_coding_agents": [{"id": "claude_code", "plugin_id": "anthropic"}],
        },
    )
    now = time.time()
    pul.log_call(provider="anthropic", model="sonnet", input_tokens=10, output_tokens=2, ts=now)
    pul.log_call(provider="claude_code", model="sonnet", input_tokens=30, output_tokens=4, ts=now)
    pul.log_call(provider="openai", model="gpt", input_tokens=99, output_tokens=1, ts=now)
    report = pul.usage_report("anthropic", days=7)
    assert report["call_count"] == 2
    assert report["total_input"] == 40


def test_gateway_usage_helper_hits_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(pul, "default_app_data_dir", lambda: tmp_path)
    pul.log_gateway_usage(
        provider="openai",
        model="gpt-4o-mini",
        usage={"input_tokens": 120, "output_tokens": 40},
        agent="translation",
    )
    report = pul.usage_report("openai", days=7)
    assert report["call_count"] == 1
    assert report["total_input"] == 120
    assert report["total_output"] == 40
    agents = {a["agent"] for a in report["by_agent"]}
    assert "translation" in agents


def test_make_provider_wrapper_logs_stream_usage(monkeypatch, tmp_path):
    """Any gateway from make_provider must auto-log DONE usage — no caller opt-in."""
    from backend.agent.providers import make_provider
    from backend.agent.providers.base import StreamEvent, StreamEventKind
    import asyncio

    monkeypatch.setattr(pul, "default_app_data_dir", lambda: tmp_path)

    class _Fake:
        async def stream_turn(self, **_kwargs):
            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="hi")
            yield StreamEvent(
                kind=StreamEventKind.DONE,
                text="hi",
                usage={"input_tokens": 10, "output_tokens": 2},
            )

        async def test_connection(self):
            return True, "ok"

    monkeypatch.setattr(
        "backend.agent.providers.gateway_providers",
        lambda: ("openai",),
    )
    monkeypatch.setattr(
        "backend.uefn_plugins.host.get_llm_provider_registration",
        lambda _name: {"factory": lambda *_a, **_k: _Fake()},
    )

    provider = make_provider("openai", "sk-test", "gpt-4o-mini")

    async def _run():
        async for _ in provider.stream_turn(system="s", messages=[], tools=[]):
            pass

    asyncio.run(_run())
    report = pul.usage_report("openai", days=7)
    assert report["call_count"] == 1
    assert report["total_input"] == 10
    assert report["total_output"] == 2


def test_make_provider_wrapper_logs_when_stream_raises(monkeypatch, tmp_path):
    """Cancelled / errored streams still hit the ledger (no DONE event)."""
    from backend.agent.providers import make_provider
    from backend.agent.providers.base import StreamEvent, StreamEventKind
    import asyncio

    monkeypatch.setattr(pul, "default_app_data_dir", lambda: tmp_path)

    class _Fake:
        async def stream_turn(self, **_kwargs):
            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="hi")
            raise RuntimeError("boom")

        async def test_connection(self):
            return True, "ok"

    monkeypatch.setattr(
        "backend.agent.providers.gateway_providers",
        lambda: ("openai",),
    )
    monkeypatch.setattr(
        "backend.uefn_plugins.host.get_llm_provider_registration",
        lambda _name: {"factory": lambda *_a, **_k: _Fake()},
    )

    provider = make_provider("openai", "sk-test", "gpt-4o-mini")

    async def _run():
        try:
            async for _ in provider.stream_turn(system="hello", messages=[], tools=[]):
                pass
        except RuntimeError:
            pass

    asyncio.run(_run())
    report = pul.usage_report("openai", days=7)
    assert report["call_count"] == 1
    assert report["total_input"] >= 1
