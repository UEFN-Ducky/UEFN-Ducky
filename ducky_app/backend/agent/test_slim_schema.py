from __future__ import annotations

from backend.agent.tools import _slim_schema, mcp_tool_to_gemini


class _FakeTool:
    def __init__(self, schema: dict) -> None:
        self.name = "ducky_create_plan"
        self.description = "Create a plan."
        self.inputSchema = schema


def test_slim_schema_strips_additional_properties() -> None:
    # Gemini 400: Unknown name "additional_properties" at items / nested object.
    raw = {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {"id": {"type": "string"}},
                },
            },
            "meta": {
                "type": "object",
                "additional_properties": True,
                "$schema": "https://json-schema.org/draft/2020-12/schema",
            },
        },
    }
    slim = _slim_schema(raw)
    dumped = str(slim)
    assert "additionalProperties" not in dumped
    assert "additional_properties" not in dumped
    assert "$schema" not in slim["properties"]["meta"]
    assert slim["properties"]["nodes"]["items"]["type"] == "object"
    assert slim["properties"]["nodes"]["items"]["properties"]["id"]["type"] == "string"


def test_mcp_tool_to_gemini_omits_additional_properties() -> None:
    tool = _FakeTool(
        {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
                "extra": {"type": "object", "additionalProperties": {"type": "string"}},
            },
        }
    )
    payload = mcp_tool_to_gemini(tool)
    dumped = str(payload)
    assert "additionalProperties" not in dumped
    assert "additional_properties" not in dumped
    assert payload["name"] == "ducky_create_plan"
    assert payload["parameters"]["properties"]["nodes"]["items"]["type"] == "object"
