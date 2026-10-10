"""Cumulative CLI accounting at the real conversation/ledger boundary."""
from types import SimpleNamespace

import pytest

from backend.agent.coding_agents.runner import record_coding_agent_usage
from frontend.ui_web import provider_usage_log


@pytest.fixture
def recorder(monkeypatch):
    rows = []
    monkeypatch.setattr(provider_usage_log, "log_call", lambda **kw: rows.append(kw))
    conv = SimpleNamespace(id="test", token_usage=None, coding_agent_stats={}, messages=[])
    def record(inp, cached, out, thread="one", agent="codex"):
        usage = dict(input_tokens=inp, cache_read_tokens=cached, output_tokens=out,
                     context_tokens=inp+cached)
        if thread is not None:
            usage["cumulative_thread"] = thread
        record_coding_agent_usage(conv, agent, "model", SimpleNamespace(usage=usage))
    return conv, rows, record


def test_three_turns_only_record_deltas(recorder):
    conv, rows, record = recorder
    record(40, 60, 5)
    record(70, 130, 12)
    record(90, 210, 20)
    assert [(r["input_tokens"], r["cache_read_tokens"], r["output_tokens"]) for r in rows] == [(40,60,5),(30,70,7),(20,80,8)]
    assert conv.token_usage["total_input"] == 90
    assert conv.token_usage["total_cache_read"] == 210
    assert conv.token_usage["total_output"] == 20
    assert conv.coding_agent_stats["usage_cursor"] == dict(thread_id="one", input=90, cached=210, output=20)
    assert conv.coding_agent_stats["context_tokens"] == 300


@pytest.mark.parametrize("thread,totals", [("two", (80,100,30)), ("one", (10,100,30)), ("one", (80,10,30)), ("one", (80,100,1))])
def test_new_thread_or_decreased_total_resets_baseline(recorder, thread, totals):
    conv, rows, record = recorder
    record(40,60,5)
    record(*totals, thread=thread)
    assert tuple(rows[-1][k] for k in ("input_tokens", "cache_read_tokens", "output_tokens")) == totals
    assert conv.token_usage["total_input"] == 40 + totals[0]


def test_identical_total_never_adds_tokens(recorder):
    conv, rows, record = recorder
    record(40,60,5)
    record(40,60,5)
    assert conv.token_usage["total_input"] == 40
    assert len(conv.token_usage["calls"]) == 1
    assert rows[-1]["input_tokens"] == rows[-1]["output_tokens"] == rows[-1]["cache_read_tokens"] == 0


@pytest.mark.parametrize("agent", ["claude_code", "cursor"])
def test_other_agents_keep_per_turn_usage_and_codex_baseline(recorder, agent):
    conv, rows, record = recorder
    record(40,60,5)
    cursor = conv.coding_agent_stats["usage_cursor"].copy()
    record(3,4,2,thread=None,agent=agent)
    record(3,4,2,thread=None,agent=agent)
    assert conv.token_usage["total_input"] == 46
    assert conv.token_usage["total_cache_read"] == 68
    assert conv.coding_agent_stats["usage_cursor"] == cursor
    record(50,70,6)
    assert rows[-1]["input_tokens"] == 10


def test_existing_inflated_history_is_not_rewritten(recorder):
    conv, rows, record = recorder
    conv.token_usage = dict(total_input=4760000, total_output=20, total_cache_read=10, total_cache_write=0, calls=[])
    record(40,60,5)
    assert conv.token_usage["total_input"] == 4760040
