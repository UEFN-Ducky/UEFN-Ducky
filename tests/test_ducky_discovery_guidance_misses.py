"""Discovery misses must not turn incomplete catalogs into absence claims."""

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def discovery(monkeypatch):
    from backend.agent import tools
    from backend.tools.panel import ducky_panel
    from backend.mcp_plugins import epic
    from backend.uefn_plugins import host

    monkeypatch.setattr(tools, "list_mcp_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(epic, "_probe_cache", {})
    monkeypatch.setattr(host, "_CONNECTION_CACHE", {})
    return ducky_panel, tools


@pytest.mark.parametrize("query", [{"name": "unreal__missing"}, {"pattern": "missing"}])
def test_ducky_discovery_guidance_offline_provider(discovery, monkeypatch, query):
    panel, _ = discovery
    from backend.uefn_plugins import host
    from backend.mcp_plugins import epic

    # Existing cached offline observations, without opening sockets.
    monkeypatch.setattr(panel, "fetch_listener_status", lambda *a, **kw: {
        "online": False,
        "epic_mcp_online": False,
        "epic_mcp_reason": "unreachable",
    })
    monkeypatch.setattr(host, "plugin_connection_rows", lambda: [
        {"id": "sample-provider", "online": False, "detail": "Offline"},
    ])
    monkeypatch.setattr(epic, "_probe_cache", {"at": time.time(), "result": {
        "epic_mcp_online": False, "epic_mcp_reason": "unreachable",
    }})
    monkeypatch.setattr(host, "_CONNECTION_CACHE", {
        "sample-provider": (time.monotonic(), {"online": False, "detail": "Offline"}),
    })
    for _ in range(2):
        result = json.loads(asyncio.run(panel.ducky_get_tools(**query)))
        assert "two misses" not in result["hint"]
        assert "not proof" in result["hint"]
        assert "No status probes or project maintenance" in result["hint"]
        assert "catalog/connection state" in result["hint"]
        assert "policy filtering" in result["hint"]
        assert result["connection_status"]["epic_mcp_online"] is False
        assert result["connection_status"]["epic_mcp_reason"] == "unreachable"
        assert result["connection_status"]["plugin_connections"][0]["online"] is False
        assert "does not establish which provider owns this query" in result["hint"]


@pytest.mark.parametrize("status", [
    {"epic_mcp_online": True, "epic_mcp_reason": ""},
    {},
])
def test_ducky_discovery_guidance_no_invented_absence_or_readiness(discovery, monkeypatch, status):
    panel, _ = discovery
    from backend.mcp_plugins import epic

    monkeypatch.setattr(epic, "_probe_cache", {"at": time.time(), "result": status})
    result = json.loads(asyncio.run(panel.ducky_get_tools(name="unknown__missing")))
    assert result["error"] == "tool not found in current catalog: unknown__missing"
    assert result["connection_status"] == (status or None)
    assert "not MCP session or tool readiness" in result["hint"]
    assert "not proof" in result["hint"]


def test_ducky_discovery_guidance_status_failure_is_unknown(discovery, monkeypatch):
    panel, _ = discovery

    def failed_status():
        raise ConnectionError("private diagnostic must not leak")

    monkeypatch.setattr(panel, "_discovery_cached_connections", failed_status)
    result = json.loads(asyncio.run(panel.ducky_get_tools(pattern="missing")))
    assert result["matches"] == []
    assert result["connection_status"] is None
    assert "availability is unknown" in result["hint"]
    assert "private diagnostic" not in json.dumps(result)


def test_ducky_discovery_guidance_found_tools_do_not_probe(discovery, monkeypatch):
    panel, tools = discovery
    from mcp.types import Tool

    schema = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}
    monkeypatch.setattr(tools, "list_mcp_tools", AsyncMock(return_value=[
        Tool(name="sample_read", description="Read a sample", inputSchema=schema),
    ]))

    def unexpected_status():
        pytest.fail("Successful discovery must not probe connections")

    monkeypatch.setattr(panel, "ducky_get_status", unexpected_status)
    monkeypatch.setattr(panel, "_discovery_cached_connections", unexpected_status)
    exact = json.loads(asyncio.run(panel.ducky_get_tools(name="sample_read")))
    assert exact["name"] == "sample_read"
    assert exact["inputSchema"]["required"] == ["value"]
    matches = json.loads(asyncio.run(panel.ducky_get_tools(pattern="sample")))
    assert [row["name"] for row in matches["matches"]] == ["sample_read"]
    assert "connection_status" not in exact
    assert "connection_status" not in matches


def test_provider_detail_does_not_leak(discovery, monkeypatch):
    panel, _ = discovery
    from backend.uefn_plugins import host

    monkeypatch.setattr(panel, "fetch_listener_status", lambda *a, **kw: {})
    monkeypatch.setattr(host, "plugin_connection_rows", lambda: [
        {"id": "sample", "online": False, "detail": "SYNTHETIC_PRIVATE_EXCEPTION"},
    ])
    monkeypatch.setattr(host, "_CONNECTION_CACHE", {
        "SYNTHETIC_PRIVATE_EXCEPTION": (time.monotonic(), {
            "id": "SYNTHETIC_PRIVATE_EXCEPTION", "label": "SYNTHETIC_PRIVATE_EXCEPTION",
            "online": False, "detail": "SYNTHETIC_PRIVATE_EXCEPTION",
        }),
    })
    result = asyncio.run(panel.ducky_get_tools(name="missing"))
    assert "SYNTHETIC_PRIVATE_EXCEPTION" not in result


def test_miss_does_not_trigger_maintenance(discovery, monkeypatch):
    panel, _ = discovery
    calls = []

    def maintenance_status(*args, **kwargs):
        calls.append("project-maintenance")
        return {}

    monkeypatch.setattr(panel, "fetch_listener_status", maintenance_status)
    asyncio.run(panel.ducky_get_tools(name="missing"))
    assert calls == []


def test_miss_does_not_block_event_loop(discovery, monkeypatch):
    panel, _ = discovery

    def slow_status():
        time.sleep(0.3)
        return "{}"

    monkeypatch.setattr(panel, "ducky_get_status", slow_status)

    async def scenario():
        start = time.monotonic()
        task = asyncio.create_task(panel.ducky_get_tools(pattern="missing"))
        await asyncio.sleep(0.01)
        delay = time.monotonic() - start
        await task
        assert delay < 0.2

    asyncio.run(scenario())


def test_cached_evidence_is_bounded_sanitized_and_expires(discovery, monkeypatch):
    panel, _ = discovery
    from backend.mcp_plugins import epic
    from backend.uefn_plugins import host

    monkeypatch.setattr(epic, "_probe_cache", {"at": time.time(), "result": {
        "epic_mcp_online": False, "epic_mcp_reason": "SYNTHETIC_PRIVATE_EXCEPTION",
    }})
    monkeypatch.setattr(host, "_CONNECTION_CACHE", {
        str(i): (time.monotonic(), {"online": False}) for i in range(100)
    })
    result = json.loads(asyncio.run(panel.ducky_get_tools(name="missing")))
    assert result["connection_status"]["epic_mcp_reason"] == "unknown"
    assert len(result["connection_status"]["plugin_connections"]) == 50
    monkeypatch.setattr(epic, "_probe_cache", {"at": time.time() - 60, "result": {
        "epic_mcp_online": False, "epic_mcp_reason": "unreachable",
    }})
    monkeypatch.setattr(host, "_CONNECTION_CACHE", {
        "sample": (time.monotonic() - 60, {"online": False}),
    })
    result = json.loads(asyncio.run(panel.ducky_get_tools(pattern="missing")))
    assert result["connection_status"] is None
    assert "availability is unknown" in result["hint"]
