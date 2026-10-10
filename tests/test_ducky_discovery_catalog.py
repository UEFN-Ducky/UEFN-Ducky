"""Public discovery preserves registry identity, schemas, paging and state."""
import asyncio
import json

import pytest
from mcp.types import Tool

from backend.agent import tools, run_context
from backend.tools.panel import ducky_panel as panel


@pytest.fixture
def catalog(monkeypatch):
    schema = {"type": "object", "oneOf": [{"required": ["path"]}],
              "properties": {"path": {"type": "string", "enum": ["a", "b"]}},
              "additionalProperties": False}
    rows = [Tool(name="workspace_read_file", description="Read a document", inputSchema=schema),
            Tool(name="workspace_write_file", description="Write a document", inputSchema=schema),
            Tool(name="docs__read_page", description="Read a document", inputSchema=schema,
                 _meta={"ducky_availability": {"state": "unavailable", "reason": "Server inventory unavailable; retry pending."}})]
    async def listing():
        return rows
    monkeypatch.setattr(tools, "list_mcp_tools", listing)
    monkeypatch.setattr(panel, "_discovery_cached_connections", lambda: pytest.fail("found catalog must not probe status"))
    return rows


def get(**kw):
    return json.loads(asyncio.run(panel.ducky_get_tools(**kw)))


def test_exact_schema_and_real_alias_keep_canonical_identity(catalog):
    direct = get(name="workspace_read_file")
    assert direct["inputSchema"] == catalog[0].inputSchema
    alias = get(name="mcp__uefn__workspace_read_file")
    assert alias["name"] == "workspace_read_file"
    catalog.append(Tool(name="mcp__uefn__workspace_read_file", inputSchema={"required": ["literal"]}))
    assert get(name="mcp__uefn__workspace_read_file")["inputSchema"] == {"required": ["literal"]}


def test_ranked_tokens_descriptions_and_paging(catalog):
    first = get(pattern="read", limit=1)
    assert first["total"] == 2 and first["count"] == 1 and first["next_offset"] == 1
    second = json.loads(asyncio.run(panel.ducky_find_tools(query="read", limit=1, offset=1)))
    assert second["revision"] == first["revision"]
    assert second["matches"][0]["name"] != first["matches"][0]["name"]
    assert second["next_offset"] is None
    assert get(pattern="read document")["total"] == 2
    assert get(pattern="workspace_read_file")["matches"][0]["name"] == "workspace_read_file"
    assert get(pattern="mcp__uefn__workspace_read_file")["matches"][0]["name"] == "workspace_read_file"
    empty = get(pattern="read", offset=2)
    assert empty["matches"] == [] and empty["total"] == 2
    assert "not proof" not in empty["hint"]  # an exhausted page is not a miss


def test_registration_removal_and_annotations_change_public_revision(catalog):
    from mcp.types import ToolAnnotations
    before = get(pattern="document")
    catalog.append(Tool(name="docs__new", description="New document", inputSchema={}))
    added = get(pattern="document")
    assert added["total"] == before["total"] + 1
    assert added["revision"] != before["revision"]
    catalog[-1].annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
    annotated = get(name="docs__new")
    assert annotated["revision"] != added["revision"]
    assert annotated["annotations"]["readOnlyHint"] is True
    catalog.pop()
    assert get(pattern="document")["revision"] == before["revision"]
    assert get()["total"] == 3


def test_offline_and_content_changes_remain_visible(catalog):
    old = get(name="docs__read_page")
    assert old["connection_state"] == "unavailable"
    assert "retry pending" in old["reason"]
    assert "Call with" not in old["hint"]
    catalog[2].meta["ducky_availability"]["state"] = "available"
    fresh = get(name="docs__read_page")
    assert fresh["revision"] != old["revision"]
    assert fresh["connection_state"] == "available"
    catalog[2].inputSchema["required"] = ["path"]
    assert get(name="docs__read_page")["revision"] != fresh["revision"]
    assert get(pattern="docs")["matches"][0]["inputSchema"] == catalog[2].inputSchema


@pytest.mark.parametrize("mode", ["ask", "plan"])
def test_mutators_discoverable_with_mode_reason(catalog, mode):
    token = run_context.set_mode(mode)
    try:
        row = get(name="workspace_write_file")
        assert row["allowed"] is False
        assert f"blocked in {mode.title()} mode" in row["mode_reason"]
        assert row["inputSchema"] == catalog[1].inputSchema
        assert "Call with" not in row["hint"]
        rows = get(pattern="write")["matches"]
        assert rows[0]["allowed"] is False
    finally:
        run_context.reset_mode(token)
