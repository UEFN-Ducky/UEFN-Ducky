"""tools/list shows a lean schema; the registered one stays whole for call validation."""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import FastMCP

from backend.bridge.plugin_gate import advertised_tool, lean_schema


def _server() -> FastMCP:
    mcp = FastMCP("lean-test")

    @mcp.tool()
    def plan_update(
        node_id: str,
        title: str = "",
        after: list[str] | None = None,
        status: str = "open",
        pretty: bool = False,
    ) -> str:
        return node_id

    return mcp


def _listed() -> object:
    (tool,) = asyncio.run(_server().list_tools())
    return tool


def test_noise_is_dropped_and_meaning_kept() -> None:
    tool = _listed()
    lean = lean_schema(tool.inputSchema)
    props = lean["properties"]
    assert "pretty" not in props
    # An argument called "title" stays; only the schema's own title labels go.
    assert set(props) == {"node_id", "title", "after", "status"}
    assert "title" not in lean and all("title" not in p for p in props.values())
    assert props["after"] == {"type": "array", "items": {"type": "string"}}
    assert props["status"]["default"] == "open"
    assert lean["required"] == ["node_id"]
    assert len(json.dumps(lean)) < len(json.dumps(tool.inputSchema)) * 0.7


def test_required_nullable_keeps_null_and_refs_stay() -> None:
    raw = {
        "type": "object",
        "properties": {
            "x": {"anyOf": [{"type": "string"}, {"type": "null"}], "title": "X"},
            "shape": {"$ref": "#/$defs/Shape"},
            "tags": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["x"],
        "$defs": {"Shape": {"type": "object", "title": "Shape", "properties": {"w": {"type": "integer"}}}},
    }
    lean = lean_schema(raw)
    assert lean["properties"]["x"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert lean["properties"]["shape"] == {"$ref": "#/$defs/Shape"}
    assert lean["properties"]["tags"]["additionalProperties"] == {"type": "string"}
    assert lean["$defs"]["Shape"] == {"type": "object", "properties": {"w": {"type": "integer"}}}


def test_advertised_tool_is_a_copy() -> None:
    tool = _listed()
    before = json.dumps(tool.inputSchema, sort_keys=True)
    shown = advertised_tool(tool)
    assert shown is not tool and "pretty" not in shown.inputSchema["properties"]
    assert json.dumps(tool.inputSchema, sort_keys=True) == before
    assert "pretty" in tool.inputSchema["properties"]
