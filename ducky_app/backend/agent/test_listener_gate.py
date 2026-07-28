"""Listener gate is opt-in: host tools never probe; editor tools fail fast offline."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import backend.agent.tools as tools


def test_host_vs_editor_classification(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.uefn_plugins.host.is_plugin_host_only_tool",
        lambda _n: False,
    )
    assert tools.is_host_only_tool("workspace_write_file")
    assert tools.is_host_only_tool("list_verse_digests")
    assert tools.is_host_only_tool("ducky_spawn_chat")
    assert tools.is_host_only_tool("blender_status")
    assert not tools.is_host_only_tool("spawn_actor")
    assert not tools.is_host_only_tool("create_material")

    assert not tools._requires_uefn_listener("workspace_write_file")
    assert not tools._requires_uefn_listener("blender_status")
    assert not tools._requires_uefn_listener("ducky_spawn_chat")
    assert not tools._requires_uefn_listener("list_verse_digests")
    assert not tools._requires_uefn_listener("ducktactoe_move")
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
