"""Listener gate is opt-in: host tools never probe; editor tools fail fast offline."""

from __future__ import annotations

import asyncio

import pytest

from types import SimpleNamespace
from unittest.mock import MagicMock

import backend.agent.tools as tools

pytestmark = pytest.mark.usefixtures("unrestricted_tools")


def test_host_vs_editor_classification(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.uefn_plugins.host.is_plugin_host_only_tool",
        lambda _n: False,
    )
    assert tools.is_host_only_tool("workspace_write_file")
    assert tools.is_host_only_tool("list_verse_digests")
    assert tools.is_host_only_tool("ducky_spawn_chat")
    assert tools.is_host_only_tool("blender_status")
    assert tools.is_host_only_tool("web_search")
    assert tools.is_host_only_tool("web_fetch")
    assert not tools.is_host_only_tool("spawn_actor")
    assert not tools.is_host_only_tool("create_material")

    assert not tools._requires_uefn_listener("workspace_write_file")
    assert not tools._requires_uefn_listener("blender_status")
    assert not tools._requires_uefn_listener("ducky_spawn_chat")
    assert not tools._requires_uefn_listener("list_verse_digests")
    assert not tools._requires_uefn_listener("ducktactoe_move")
    assert not tools._requires_uefn_listener("web_search")
    assert not tools._requires_uefn_listener("web_fetch")
    assert tools._requires_uefn_listener("spawn_actor")
    assert tools._requires_uefn_listener("save_current_level")
    assert tools._requires_uefn_listener("create_material")


def _patch_mcp_call(monkeypatch, fake_call) -> None:
    mcp = MagicMock()
    mcp.call_tool = fake_call
    monkeypatch.setattr(tools, "_ensure_mcp", lambda: mcp)


def test_host_tools_never_probe_listener(monkeypatch) -> None:
    health_calls: list[int] = []

    def _boom_health(port: int, timeout: float = 1.0):
        health_calls.append(port)
        return None

    monkeypatch.setattr("backend.bridge.listener_get_health", _boom_health)
    monkeypatch.setattr("backend.bridge.configured_listener_port", lambda: 4200)
    monkeypatch.setattr(
        "backend.uefn_plugins.host.is_plugin_host_only_tool",
        lambda _n: False,
    )

    async def _fake_call(name: str, args: dict):
        return [SimpleNamespace(text='{"ok": true}')]

    _patch_mcp_call(monkeypatch, _fake_call)

    for name in ("workspace_write_file", "blender_status", "ducky_spawn_chat", "list_verse_digests"):
        result = asyncio.run(tools.execute_tool(name, {}))
        assert result.ok, (name, result.error)

    assert health_calls == []


def test_editor_tools_fail_fast_offline_without_calling_tool(monkeypatch) -> None:
    monkeypatch.setattr("backend.bridge.configured_listener_port", lambda: 4200)
    monkeypatch.setattr("backend.bridge.listener_get_health", lambda _port, timeout=1.0: None)
    monkeypatch.setattr(
        "backend.uefn_plugins.host.is_plugin_host_only_tool",
        lambda _n: False,
    )

    called: list[str] = []

    async def _fake_call(name: str, args: dict):
        called.append(name)
        return [SimpleNamespace(text="should not run")]

    _patch_mcp_call(monkeypatch, _fake_call)

    result = asyncio.run(tools.execute_tool("spawn_actor", {"asset_path": "/Game/X"}))
    assert not result.ok
    assert "listener offline" in result.error.lower()
    assert "Do NOT retry" in (result.hint or "")
    assert result.duration_ms < 2000
    assert called == [], "offline editor tools must not enter mcp.call_tool / port discovery"


def test_editor_tools_run_when_listener_healthy(monkeypatch) -> None:
    monkeypatch.setattr("backend.bridge.configured_listener_port", lambda: 4200)
    monkeypatch.setattr(
        "backend.bridge.listener_get_health",
        lambda _port, timeout=1.0: {"status": "ok"},
    )
    monkeypatch.setattr(
        "backend.uefn_plugins.host.is_plugin_host_only_tool",
        lambda _n: False,
    )

    async def _fake_call(name: str, args: dict):
        assert name == "spawn_actor"
        return [SimpleNamespace(text='{"ok": true, "label": "X"}')]

    _patch_mcp_call(monkeypatch, _fake_call)

    result = asyncio.run(tools.execute_tool("spawn_actor", {"asset_path": "/Game/X"}))
    assert result.ok, result.error


def test_desktop_plugin_host_only_tools_skip_listener_gate(monkeypatch) -> None:
    """Store desktop plugins with listener=False (Duck-Tac-Toe cache) skip the gate."""
    health_calls: list[int] = []
    monkeypatch.setattr(
        "backend.bridge.listener_get_health",
        lambda port, timeout=1.0: health_calls.append(port) or None,
    )
    monkeypatch.setattr("backend.bridge.configured_listener_port", lambda: 4200)
    monkeypatch.setattr(
        "backend.uefn_plugins.host.is_plugin_host_only_tool",
        lambda n: n == "ducktactoe_move",
    )

    async def _fake_call(name: str, args: dict):
        return [SimpleNamespace(text='{"ok": true, "played": 4}')]

    _patch_mcp_call(monkeypatch, _fake_call)

    assert not tools._requires_uefn_listener("ducktactoe_move")
    result = asyncio.run(tools.execute_tool("ducktactoe_move", {"cell": 4}))
    assert result.ok, result.error
    assert health_calls == []


def test_plugin_tools_need_uefn_only_when_they_say_so(monkeypatch) -> None:
    # A plugin author who never thought about UEFN gets a tool that runs with UEFN closed.
    import backend.uefn_plugins.host as host

    monkeypatch.setattr(host, "ensure_plugins_loaded", lambda *a, **k: None)
    for name, fresh in (("_API_TOOL_REGISTRY", {}), ("_PLUGIN_TOOL_NAMES", set()), ("_PLUGIN_TOOL_OWNER", {}),
                        ("_TOOL_LAST_OWNER", {}), ("_PLUGIN_HOST_ONLY_TOOLS", set()),
                        ("_PLUGIN_INTENT_ROWS", []), ("_CONTRIBUTIONS", {**host._CONTRIBUTIONS, "agent_tools": {}})):
        monkeypatch.setattr(host, name, fresh)
    host._record_plugin_api_tool("demo", "demo_list")
    host._record_plugin_api_tool("demo", "demo_place", listener=True)
    assert host.is_plugin_host_only_tool("demo_list")
    assert not host.is_plugin_host_only_tool("demo_place")


def test_offline_hint_says_keep_going() -> None:
    assert "keep going with the rest" in tools._LISTENER_OFFLINE_HINT


def test_a_tool_whose_plugin_failed_to_reload_says_so_not_listener_offline(monkeypatch) -> None:
    # Oct 11 2026: a team's plugin failed to reload mid-edit; its tools fell through to the
    # editor gate, read "UEFN listener offline", and six agents chased the listener.
    import backend.uefn_plugins.host as host

    probes: list[int] = []
    monkeypatch.setattr("backend.bridge.listener_get_health", lambda port, timeout=1.0: probes.append(port))
    monkeypatch.setattr("backend.bridge.configured_listener_port", lambda: 4200)
    monkeypatch.setattr(host, "_plugin_label", lambda pid: "Island Media Kit")
    monkeypatch.setitem(host._TOOL_LAST_OWNER, "imk_project_get", "island-media-kit")
    monkeypatch.setitem(host._LOAD_FAILURES, "island-media-kit", "ValueError: Action registry differs.")
    monkeypatch.delitem(host._PLUGIN_TOOL_OWNER, "imk_project_get", raising=False)
    called: list[str] = []

    async def _fake_call(name: str, args: dict):
        called.append(name)
        return [SimpleNamespace(text="should not run")]

    _patch_mcp_call(monkeypatch, _fake_call)

    result = asyncio.run(tools.execute_tool("imk_project_get", {"project_id": "p1"}))
    assert not result.ok
    assert "Island Media Kit plugin, which failed to load: ValueError: Action registry differs." in result.error
    assert "listener offline" not in result.error
    assert probes == [] and called == []

    monkeypatch.setitem(host._PLUGIN_TOOL_OWNER, "imk_project_get", "island-media-kit")
    assert host.plugin_tool_unavailable_reason("imk_project_get") == ""  # loaded again


def test_listener_offline_names_plugins_that_failed_to_load_for_an_unknown_tool(monkeypatch) -> None:
    import backend.uefn_plugins.host as host

    monkeypatch.setattr("backend.bridge.listener_get_health", lambda port, timeout=1.0: None)
    monkeypatch.setattr("backend.bridge.configured_listener_port", lambda: 4200)
    monkeypatch.setattr(host, "is_plugin_host_only_tool", lambda _n: False)
    monkeypatch.setattr(host, "plugin_load_failures", lambda: {"Island Media Kit": "ValueError: bad registry."})
    mcp = MagicMock()
    mcp._tool_manager._tools = {"spawn_actor": object()}
    monkeypatch.setattr(tools, "_ensure_mcp", lambda: mcp)

    result = asyncio.run(tools.execute_tool("imk_render_create", {}))
    assert "listener offline" in result.error
    assert "Island Media Kit: ValueError: bad registry." in result.error

    served = asyncio.run(tools.execute_tool("spawn_actor", {}))
    assert served.error == "UEFN listener offline on port 4200"


def test_a_guessed_tool_name_is_unknown_not_listener_offline(monkeypatch) -> None:
    # Oct 26 2026: imk_media_search (no such tool) read "UEFN listener offline", and the
    # agent told the person Island Media Kit needed UEFN.
    import backend.uefn_plugins.host as host

    probes: list[int] = []
    monkeypatch.setattr("backend.bridge.listener_get_health", lambda port, timeout=1.0: probes.append(port))
    monkeypatch.setattr(host, "plugin_load_failures", lambda: {})
    mcp = MagicMock()
    mcp._tool_manager._tools = {"imk_project_list": object(), "imk_media_list": object()}
    monkeypatch.setattr(tools, "_ensure_mcp", lambda: mcp)

    result = asyncio.run(tools.execute_tool("imk_media_search", {"query": "promo"}))
    assert not result.ok
    assert "listener" not in result.error.lower()
    assert "imk_media_list, imk_project_list" in result.error
    assert probes == []

    # A guess with no real prefix gets what it means, from tool search.
    import backend.search.query as search_query

    monkeypatch.setattr(search_query, "search_tools",
                        lambda text, allowed, limit=5: [{"tool": "imk_media_list"}] if "video" in text else [])
    result = asyncio.run(tools.execute_tool("find_video", {}))
    assert "Closest by meaning: imk_media_list." in result.error and "listener" not in result.error.lower()


def test_ducky_s_own_workflow_tools_never_wait_for_uefn():
    # Oct 17 2026: a lead's find_workflows was refused with "UEFN listener offline".
    from backend.agent.tools import _requires_uefn_listener

    for name in ("find_workflows", "run_workflow", "get_workflow", "list_workflows", "save_workflow"):
        assert not _requires_uefn_listener(name), name
    assert _requires_uefn_listener("spawn_actor")


def test_an_error_answer_is_a_failure_not_a_success():
    # Oct 17 2026: "Error: Ducky AI pictures are not set up on this site yet." showed as a success.
    from backend.agent.tools import MARKDOWN_TOOLS, _looks_like_tool_failure

    assert _looks_like_tool_failure("some_plugin_tool", "Error: pictures are not set up on this site yet.")
    assert _looks_like_tool_failure("some_plugin_tool", "ERROR: listener offline")
    assert not _looks_like_tool_failure("some_plugin_tool", '{"ok": true, "downloaded": ["a.png"]}')
    if MARKDOWN_TOOLS:
        assert not _looks_like_tool_failure(next(iter(MARKDOWN_TOOLS)), "Error: is a word in this guide")
