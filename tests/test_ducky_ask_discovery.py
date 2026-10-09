"""Ask discovery remains visible, passive, and explicit about permission."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from backend.agent import run_context, tools
from backend.tools.panel import ducky_panel


@pytest.mark.parametrize("mode", ["ask", "agent", "plan"])
def test_visible_schema_and_search_permission(monkeypatch, mode):
    catalog = [NS(name=n, description="sample", inputSchema={"type": "object", "properties": {"path": {"type": "string"}}}) for n in ("workspace_read_file", "workspace_write_file")]
    async def listing():
        return catalog
    monkeypatch.setattr(tools, "list_mcp_tools", listing)
    token = run_context.set_mode(mode)
    try:
        for name in ("workspace_read_file", "workspace_write_file"):
            row = json.loads(asyncio.run(ducky_panel.ducky_get_tools(name=name)))
            assert row["name"] == name and row["inputSchema"]["properties"]["path"]
            if mode == "ask":
                assert row["allowed"] is (name == "workspace_read_file")
                if not row["allowed"]:
                    assert "blocked in Ask mode" in row["hint"]
                    assert "Call with" not in row["hint"]
        search = json.loads(asyncio.run(ducky_panel.ducky_get_tools(pattern="workspace", limit=1)))
        assert search["count"] == 1
        if mode == "ask":
            assert "allowed" in search["matches"][0]
        missing = json.loads(asyncio.run(ducky_panel.ducky_get_tools(name="absent")))
        assert not missing["ok"]
        assert "not proof" in missing["hint"]
        assert "inputSchema" not in missing
    finally:
        run_context.reset_mode(token)
