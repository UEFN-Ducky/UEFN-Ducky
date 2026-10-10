from types import SimpleNamespace as NS

from backend.agent.toolsets import tool_index as index


def tool(name="workspace_read_file", **kw):
    return NS(name=name, description=kw.pop("description", "Read document text"),
              inputSchema=kw.pop("inputSchema", {"type": "object", "properties": {}}), **kw)


def test_same_name_description_refreshes_prompt(monkeypatch):
    row = tool(description="old wording")
    monkeypatch.setattr(index, "_list_tools_blocking", lambda: [row])
    index.clear_tool_index_cache()
    assert "old wording" in index.tool_index_prompt_block_sync()
    row.description = "new wording"
    assert "new wording" in index.tool_index_prompt_block_sync()


def test_failed_listing_uses_latest_snapshot_and_labels_staleness(monkeypatch):
    rows = [tool("old")]
    monkeypatch.setattr(index, "_list_tools_blocking", lambda: rows)
    index.clear_tool_index_cache()
    index.tool_index_prompt_block_sync()
    rows[:] = [tool("new")]
    index.tool_index_prompt_block_sync()
    def failed():
        raise RuntimeError("private provider details")
    monkeypatch.setattr(index, "_list_tools_blocking", failed)
    text = index.tool_index_prompt_block_sync()
    assert "`new`" in text and "`old`" not in text
    assert "cached" in text and "unknown" in text
    assert "private provider details" not in text


def test_search_exact_alias_tokens_description_and_paging():
    rows = [tool("other", description="workspace read file"),
            tool("workspace_read_file", aliases=["read_document"]), tool("read_file_extra")]
    exact = index.search_tool_catalog(rows, "workspace_read_file", limit=1)
    assert exact["matches"][0]["name"] == "workspace_read_file"
    assert exact["total"] == 2 and exact["next_offset"] == 1
    assert index.search_tool_catalog(rows, "read_document")["matches"][0]["name"] == "workspace_read_file"
    assert index.search_tool_catalog(rows, "mcp__uefn__workspace_read_file")["matches"][0]["name"] == "workspace_read_file"
    assert index.search_tool_catalog(rows, "document text")["total"] == 2
    assert index.search_tool_catalog(rows, "workspace_read_file", offset=1)["matches"][0]["name"] == "other"
    assert index.search_tool_catalog(rows, "", offset=99)["matches"] == []


def test_revision_tracks_full_metadata_and_unavailable_schema():
    row = tool(connection_state="unavailable", reason="server disconnected", provider="docs")
    first = index.search_tool_catalog([row])
    assert first["matches"][0]["connection_state"] == "unavailable"
    assert first["matches"][0]["reason"] == "server disconnected"
    revision = first["revision"]
    for key, value in [("inputSchema", {"oneOf": [{"required": ["path"]}]}),
                       ("annotations", {"readOnlyHint": True}), ("provider", "new"),
                       ("connection_state", "connected"), ("revision", 9)]:
        setattr(row, key, value)
        result = index.search_tool_catalog([row])
        assert result["revision"] != revision
        revision = result["revision"]
    result["matches"][0]["inputSchema"]["oneOf"].clear()
    assert row.inputSchema["oneOf"]
    assert index.search_tool_catalog([])["revision"] != revision


def test_literal_canonical_identity_precedes_alias_and_order_is_stable():
    rows = [tool("workspace_read_file"), tool("mcp__uefn__workspace_read_file")]
    result = index.search_tool_catalog(rows, "mcp__uefn__workspace_read_file")
    assert result["matches"][0]["name"] == "mcp__uefn__workspace_read_file"
    assert index.catalog_revision(rows) == index.catalog_revision(list(reversed(rows)))
    # Unknown prefixes do not manufacture rows or grant local identity.
    assert index.search_tool_catalog(rows, "unregistered__missing")["total"] == 0
