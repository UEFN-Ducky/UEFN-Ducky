from types import SimpleNamespace as NS

import pytest
from frontend.ui_web import mcp_catalog as catalog


@pytest.fixture
def host(monkeypatch):
    rows = [NS(name="sample", description="old", parameters={"type": "object", "properties": {"x": {"type": "string"}}}, is_async=False)]
    monkeypatch.setattr("backend.agent.tools._ensure_mcp", lambda: NS(_tool_manager=NS(list_tools=lambda: rows)))
    monkeypatch.setattr("backend.uefn_plugins.host.plugins_ready", lambda: False)
    catalog._WORKFLOW_CACHE.clear()
    return rows


def test_workflow_same_count_schema_and_registration_changes(host):
    first = catalog.build_workflow_tool_catalog()
    host[0].parameters["properties"] = {"new": {"type": "number"}}
    host[0].description = "new description"
    second = catalog.build_workflow_tool_catalog()
    assert second["tools"][0]["parameters"][0]["name"] == "new"
    assert second["tools"][0]["inputSchema"] == host[0].parameters
    assert second["revision"] != first["revision"]
    host[0].name = "replacement"
    assert catalog.build_workflow_tool_catalog()["tools"][0]["name"] == "replacement"
    host[0].is_async = True
    assert catalog.build_workflow_tool_catalog()["total"] == 0


def test_workflow_caller_cannot_poison_cache(host):
    first = catalog.build_workflow_tool_catalog()
    first["tools"][0]["parameters"].clear()
    assert catalog.build_workflow_tool_catalog()["tools"][0]["parameters"]


def test_settings_catalog_preserves_schema_state_and_ranked_paging(monkeypatch):
    monkeypatch.setattr(catalog, "plugin_destructive_tool_names", lambda: set())
    monkeypatch.setattr(catalog, "is_plugin_tool", lambda n: "__" in n)
    monkeypatch.setattr(catalog, "_tool_in_plan", lambda n: False)
    monkeypatch.setattr(catalog, "is_host_only_tool", lambda n: False)
    monkeypatch.setattr("backend.uefn_plugins.host.uefn_agent_tool_rows", lambda: [])
    monkeypatch.setattr("backend.uefn_plugins.host.uefn_plugin_tool_group_rows", lambda: [])
    schema = {"type": "object", "oneOf": [{"required": ["path"]}], "additionalProperties": False}
    rows = [NS(name="docs__read_page", description="Read a page", inputSchema=schema,
               annotations={"readOnlyHint": True}, connection_state="unavailable", reason="disconnected"),
            NS(name="other", description="docs read page", inputSchema={})]
    async def listing(**kw):
        return rows
    monkeypatch.setattr(catalog, "list_mcp_tools", listing)
    first = catalog.build_mcp_catalog(query="docs__read_page", limit=1)
    assert first["total"] == 2 and first["count"] == 1 and first["next_offset"] == 1
    row = first["tools"][0]
    assert row["name"] == "docs__read_page" and row["inputSchema"] == schema
    assert row["connection_state"] == "unavailable" and row["reason"] == "disconnected"
    assert row["annotations"] == {"readOnlyHint": True}
    second = catalog.build_mcp_catalog(query="docs__read_page", offset=1)
    assert second["tools"][0]["name"] == "other" and second["revision"] == first["revision"]
    rows.pop()
    assert catalog.build_mcp_catalog()["revision"] != first["revision"]
