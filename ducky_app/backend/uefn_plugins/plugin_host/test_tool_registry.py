"""Plugin MCP tools: ownership, intent rows, the run allowlist, call slots."""

from __future__ import annotations

import threading

import pytest

from backend.uefn_plugins.plugin_host import state, tool_registry


@pytest.fixture
def recorded():
    tool_registry._record_plugin_api_tool("reg-probe", "reg_probe_do", intent=r"\bprobe\b")
    yield "reg_probe_do"
    with state._LOCK:
        state._API_TOOL_REGISTRY.pop("reg-probe", None)
        state._API_INTENT_PATTERNS.pop("reg-probe", None)
        state._CONTRIBUTIONS["agent_tools"].pop("reg-probe", None)
        state._PLUGIN_TOOL_NAMES.discard("reg_probe_do")
        state._PLUGIN_TOOL_OWNER.pop("reg_probe_do", None)
        state._TOOL_LAST_OWNER.pop("reg_probe_do", None)
        state._PLUGIN_HOST_ONLY_TOOLS.discard("reg_probe_do")
        tool_registry._rebuild_intent_row("reg-probe", set())


def test_a_recorded_tool_has_an_owner_and_an_intent_row(recorded) -> None:
    assert tool_registry.plugin_for_tool(recorded) == "reg-probe"
    assert recorded in tool_registry.store_plugin_tool_names()
    [row] = [r for r in state._PLUGIN_INTENT_ROWS if r[0] == "reg-probe"]
    assert row[1].search("run the probe") and recorded in row[2]
    assert recorded in state._PLUGIN_HOST_ONLY_TOOLS  # listener=False by default


def test_a_gone_tool_says_its_plugin_failed(recorded) -> None:
    with state._LOCK:
        state._PLUGIN_TOOL_OWNER.pop(recorded, None)
        state._LOAD_FAILURES["reg-probe"] = "ImportError: boom"
    try:
        reason = tool_registry.plugin_tool_unavailable_reason(recorded)
        assert "failed to load: ImportError: boom" in reason
    finally:
        state._LOAD_FAILURES.pop("reg-probe", None)


def test_core_tools_belong_to_ducky() -> None:
    assert tool_registry.plugin_for_tool("") == "ducky"
    assert tool_registry.plugin_for_tool("workspace_read_file") == "ducky"


def test_the_run_allowlist_scopes_plugin_tools(monkeypatch) -> None:
    monkeypatch.setattr(tool_registry, "is_plugin_enabled", lambda pid: True)
    tool_registry.set_active_uefn_agent_plugin_ids(["allowed", "allowed"])
    try:
        assert state._ACTIVE_UEFN_AGENT_IDS.get() == ("allowed",)
        assert tool_registry.uefn_agent_tools_allowed("allowed") is True
    finally:
        tool_registry.set_active_uefn_agent_plugin_ids(None)
    assert tool_registry.uefn_agent_tools_allowed("anything") is True


def test_manifest_tool_concurrency_is_bounded() -> None:
    assert tool_registry.manifest_tool_concurrency({}) == state.DEFAULT_TOOL_CONCURRENCY
    assert tool_registry.manifest_tool_concurrency({"tool_concurrency": True}) == state.DEFAULT_TOOL_CONCURRENCY
    assert tool_registry.manifest_tool_concurrency({"tool_concurrency": 500}) == 64


def test_a_nested_call_reuses_its_slot() -> None:
    with state._LOCK:
        state._TOOL_CONCURRENCY["slot-probe"] = 1
        state._TOOL_SLOTS.pop("slot-probe", None)
    done = threading.Event()
    try:
        with tool_registry.plugin_tool_slot("slot-probe"):
            with tool_registry.plugin_tool_slot("slot-probe"):  # same thread: no deadlock
                done.set()
        assert done.is_set()
    finally:
        with state._LOCK:
            state._TOOL_CONCURRENCY.pop("slot-probe", None)
            state._TOOL_SLOTS.pop("slot-probe", None)
