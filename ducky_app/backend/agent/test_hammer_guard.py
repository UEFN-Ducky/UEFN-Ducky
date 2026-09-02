"""Hammer guard: third identical failure returns the STOP payload; success resets."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import backend.agent.hammer_guard as hg
import backend.agent.tools as tools


@pytest.fixture(autouse=True)
def _clean_guard():
    hg.reset_all()
    yield
    hg.reset_all()


def test_normalize_strips_digits_and_quotes():
    a = hg.normalize_error('Actor "Button_7" not found at index 12')
    b = hg.normalize_error("Actor 'Button_9' not found at index 3")
    assert a == b == "actor not found at index"
    assert len(hg.normalize_error("x" * 500)) == 200


def test_streak_counts_and_resets():
    assert hg.note_failure("t", "boom 1") == 1
    assert hg.note_failure("t", "boom 2") == 2
    assert hg.note_failure("t", "boom 3") == 3
    assert hg.should_stop(3)
    hg.note_success("t")
    assert hg.note_failure("t", "boom 4") == 1
    assert hg.note_failure("t", "different") == 1  # different error resets
    assert hg.note_failure("other", "different") == 1  # different tool resets


def test_streaks_are_per_conversation():
    tok = hg.bind_conversation("conv-A")
    try:
        hg.note_failure("t", "e")
        hg.note_failure("t", "e")
    finally:
        hg.reset_conversation(tok)
    tok = hg.bind_conversation("conv-B")
    try:
        assert hg.note_failure("t", "e") == 1
    finally:
        hg.reset_conversation(tok)


def _patch_dispatch(monkeypatch, fake_call, *, stats_seen=None):
    monkeypatch.setattr("backend.bridge.configured_listener_port", lambda: 4200)
    monkeypatch.setattr("backend.bridge.listener_get_health", lambda _p, timeout=1.0: {"ok": True})
    monkeypatch.setattr("backend.uefn_plugins.host.is_plugin_host_only_tool", lambda _n: False)
    mcp = MagicMock()
    mcp.call_tool = fake_call
    fake_tool = SimpleNamespace(
        parameters={"type": "object", "properties": {"entity": {"type": "string"}}, "required": ["entity"]},
        description="Add a component.",
    )
    mcp._tool_manager = SimpleNamespace(_tools={"add_entity_component": fake_tool})
    monkeypatch.setattr(tools, "_ensure_mcp", lambda: mcp)
    if stats_seen is not None:
        stats = types.ModuleType("backend.tools.verse.verse_stats")
        stats.record_tool_failure = lambda tool, msg: stats_seen.append((tool, msg))  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "backend.tools.verse.verse_stats", stats)


def test_third_identical_failure_returns_stop_payload(monkeypatch):
    seen: list[tuple[str, str]] = []

    async def _always_fails(name: str, args: dict):
        raise RuntimeError("invalid component class 'foo_component'")

    _patch_dispatch(monkeypatch, _always_fails, stats_seen=seen)
    args = {"entity": "E1", "component": "foo_component"}

    r1 = asyncio.run(tools.execute_tool("add_entity_component", args))
    r2 = asyncio.run(tools.execute_tool("add_entity_component", args))
    assert not r1.ok and not r2.ok
    assert "STOP:" not in r1.error and "STOP:" not in r2.error

    r3 = asyncio.run(tools.execute_tool("add_entity_component", args))
    assert not r3.ok
    payload = json.loads(r3.error)
    assert payload["STOP"] == hg.STOP_TEXT
    assert "failed 3 times" in payload["STOP"]
    assert "invalid component class" in payload["error"]
    assert payload["input_schema"]["required"] == ["entity"]
    assert r3.hint == hg.STOP_TEXT
    assert len(seen) == 3 and seen[0][0] == "add_entity_component"


def test_success_resets_streak(monkeypatch):
    state = {"n": 0}

    async def _fails_twice_then_ok(name: str, args: dict):
        state["n"] += 1
        if state["n"] == 3:
            return [SimpleNamespace(text='{"ok": true}')]
        raise RuntimeError("bad args")

    _patch_dispatch(monkeypatch, _fails_twice_then_ok)
    for _ in range(2):
        assert not asyncio.run(tools.execute_tool("add_entity_component", {})).ok
    assert asyncio.run(tools.execute_tool("add_entity_component", {})).ok
    # Two more failures after the success are a fresh streak, not the third.
    r = asyncio.run(tools.execute_tool("add_entity_component", {}))
    assert not r.ok and "STOP:" not in r.error
    r = asyncio.run(tools.execute_tool("add_entity_component", {}))
    assert not r.ok and "STOP:" not in r.error
    r = asyncio.run(tools.execute_tool("add_entity_component", {}))
    assert "STOP:" in r.error


def test_schema_falls_back_to_docstring(monkeypatch):
    async def _fails(name: str, args: dict):
        raise RuntimeError("nope")

    _patch_dispatch(monkeypatch, _fails)
    mcp = tools._ensure_mcp()
    mcp._tool_manager._tools["weird_tool"] = SimpleNamespace(
        parameters=None, description="Docstring only.", fn=None
    )
    for _ in range(2):
        asyncio.run(tools.execute_tool("weird_tool", {}))
    r = asyncio.run(tools.execute_tool("weird_tool", {}))
    assert json.loads(r.error)["input_schema"] == "Docstring only."
