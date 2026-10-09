"""Discovery misses must not turn incomplete catalogs into absence claims."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def discovery(monkeypatch):
    from backend.agent import tools
    from backend.tools.panel import ducky_panel

    monkeypatch.setattr(tools, "list_mcp_tools", AsyncMock(return_value=[]))
    return ducky_panel, tools


@pytest.mark.parametrize("query", [{"name": "unreal__missing"}, {"pattern": "missing"}])
def test_ducky_discovery_guidance_offline_provider(discovery, monkeypatch, query):
    panel, _ = discovery
    from backend.uefn_plugins import host

    # Exercise the existing status path with an offline probe, without sockets.
    monkeypatch.setattr(panel, "fetch_listener_status", lambda *a, **kw: {
        "online": False,
        "epic_mcp_online": False,
        "epic_mcp_reason": "unreachable",
    })
    monkeypatch.setattr(host, "plugin_connection_rows", lambda: [
        {"id": "sample-provider", "online": False, "detail": "Offline"},
    ])
    for _ in range(2):
        result = json.loads(asyncio.run(panel.ducky_get_tools(**query)))
        assert "two misses" not in result["hint"]
        assert "not proof" in result["hint"]
        assert "ducky_get_status" in result["hint"]
        assert "catalog/connection state" in result["hint"]
        assert "policy filtering" in result["hint"]
        assert result["connection_status"]["epic_mcp_online"] is False
        assert result["connection_status"]["epic_mcp_reason"] == "unreachable"
        assert result["connection_status"]["plugin_connections"][0]["online"] is False
        assert "does not establish which provider owns this query" in result["hint"]


@pytest.mark.parametrize("status", [
    {"epic_mcp_online": True, "epic_mcp_reason": "", "plugin_connections": []},
    {},
])
def test_ducky_discovery_guidance_no_invented_absence_or_readiness(discovery, monkeypatch, status):
    panel, _ = discovery
    monkeypatch.setattr(panel, "ducky_get_status", lambda: json.dumps(status))
    result = json.loads(asyncio.run(panel.ducky_get_tools(name="unknown__missing")))
    assert result["error"] == "tool not found in current catalog: unknown__missing"
    assert result["connection_status"] == status
    assert "not MCP session or tool readiness" in result["hint"]
    assert "not proof" in result["hint"]


def test_ducky_discovery_guidance_status_failure_is_unknown(discovery, monkeypatch):
    panel, _ = discovery

    def failed_status():
        raise ConnectionError("private diagnostic must not leak")

    monkeypatch.setattr(panel, "ducky_get_status", failed_status)
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
    exact = json.loads(asyncio.run(panel.ducky_get_tools(name="sample_read")))
    assert exact["name"] == "sample_read"
    assert exact["inputSchema"]["required"] == ["value"]
    matches = json.loads(asyncio.run(panel.ducky_get_tools(pattern="sample")))
    assert [row["name"] for row in matches["matches"]] == ["sample_read"]
    assert "connection_status" not in exact
    assert "connection_status" not in matches
