"""The agent loop refuses out-of-lane writes before dispatch, with a file_guard event."""

from __future__ import annotations

import asyncio

import pytest

from backend.agent import tools
from backend.workspace import events, identity, lanes
from backend.workspace.identity import RunContext


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    lanes.reset_for_tests()
    events.reset_for_tests()
    monkeypatch.setattr("backend.agent.coding_agents.plans.plan_mutator_block_reason", lambda name: None)

    # The loop turns dispatch exceptions into error results; the marker in the
    # error text proves the call got past the gate.
    def _boom():
        raise RuntimeError("DISPATCH_REACHED")

    monkeypatch.setattr(tools, "_ensure_mcp", _boom)
    yield
    lanes.reset_for_tests()
    events.reset_for_tests()


def _run(name: str, args: dict):
    return asyncio.run(tools.execute_tool(name, args))


def test_out_of_lane_write_is_refused_before_dispatch_in_enforce_mode() -> None:
    lanes.set_mode_source(lambda: "enforce")
    seen: list[dict] = []
    events.register_sink(seen.append)
    token = identity.bind(RunContext(run_id="r", conv_id="hacker", lane=("Content/Verse/Shop/**",), leader_conv_id="lead"))
    try:
        result = _run("workspace_write_file", {"relative_path": "Content/Verse/Hub/hub.verse", "content": "x"})
    finally:
        identity.reset(token)
    assert not result.ok and "Out of lane" in result.error and "Do not retry" in result.hint
    assert seen and seen[0]["type"] == "file_guard" and seen[0]["kind"] == "lane_denied"
    assert seen[0]["conv_id"] == "hacker" and seen[0]["path"] == "Content/Verse/Hub/hub.verse"


def test_in_lane_and_shadow_reach_dispatch() -> None:
    lanes.set_mode_source(lambda: "shadow")
    token = identity.bind(RunContext(run_id="r", conv_id="hacker", lane=("Content/Verse/Shop/**",)))
    try:
        result = _run("workspace_write_file", {"relative_path": "Content/Verse/Hub/hub.verse", "content": "x"})
    finally:
        identity.reset(token)
    assert "DISPATCH_REACHED" in result.error
    lanes.set_mode_source(lambda: "enforce")
    token = identity.bind(RunContext(run_id="r", conv_id="hacker", lane=("Content/Verse/Shop/**",)))
    try:
        result = _run("workspace_write_file", {"relative_path": "Content/Verse/Shop/shop.verse", "content": "x"})
    finally:
        identity.reset(token)
    assert "DISPATCH_REACHED" in result.error


def test_unlaned_caller_is_never_gated() -> None:
    lanes.set_mode_source(lambda: "enforce")
    result = _run("workspace_write_file", {"relative_path": "Content/Verse/Hub/hub.verse", "content": "x"})
    assert "DISPATCH_REACHED" in result.error
