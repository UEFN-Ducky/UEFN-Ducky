"""Wrong argument names and string-typed values get repaired before dispatch.
Payloads are the shapes from the Nov 2 2026 tool-failure log."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from backend.agent import tools
from backend.agent.tools import ToolCallResult, repair_args


def _schema(required: list[str], **props: object) -> dict:
    return {"type": "object", "properties": props, "required": required}


STR = {"type": "string"}
INT = {"type": "integer"}
SEARCH = _schema(["pattern"], pattern=STR, path=STR, max_results=INT)
EDIT = _schema(["old_text", "new_text"], old_text=STR, new_text=STR, path=STR, relative_path=STR)
REPLACE_LINES = _schema(["start_line", "end_line", "new_text"], start_line=INT, end_line=INT, new_text=STR, path=STR)
CHAT = _schema(["conv_id"], conv_id=STR, project={"anyOf": [STR, {"type": "null"}]})
ASSET = _schema(["asset_path"], asset_path=STR)
READ = _schema([], path=STR, relative_path=STR, start_line=INT)
CALL = _schema(["name"], name=STR, arguments={"anyOf": [{"type": "object"}, {"type": "null"}]})


def test_query_becomes_pattern() -> None:
    out, notes = repair_args("workspace_search", {"query": "Last"}, SEARCH)
    assert out == {"pattern": "Last"} and notes == ["query→pattern"]


def test_conversation_id_becomes_conv_id() -> None:
    out, _ = repair_args("ducky_read_chat", {"conversation_id": "abc"}, CHAT)
    assert out == {"conv_id": "abc"}


def test_content_becomes_new_text() -> None:
    out, _ = repair_args("workspace_edit_file", {"old_text": "a", "content": "b", "path": "x"}, EDIT)
    assert out == {"old_text": "a", "new_text": "b", "path": "x"}


def test_relative_path_kept_when_tool_has_it_and_file_path_maps() -> None:
    out, notes = repair_args("workspace_read_file", {"relative_path": "a.verse"}, READ)
    assert out == {"relative_path": "a.verse"} and notes == []
    out, _ = repair_args("workspace_search", {"pattern": "x", "relative_path": "Verse"}, SEARCH)
    assert out == {"pattern": "x", "path": "Verse"}


def test_definition_path_becomes_asset_path() -> None:
    out, _ = repair_args("validate_uefn_asset", {"definition_path": "/Game/X"}, ASSET)
    assert out == {"asset_path": "/Game/X"}


def test_arguments_json_string_is_unwrapped() -> None:
    payload = {"arguments": json.dumps({"pattern": "Await", "max_results": "20"})}
    out, notes = repair_args("workspace_search", payload, SEARCH)
    assert out == {"pattern": "Await", "max_results": 20}
    assert "unwrapped arguments" in notes


def test_ducky_call_tool_keeps_its_arguments_but_parses_the_string() -> None:
    out, _ = repair_args("ducky_call_tool", {"name": "t", "arguments": '{"a": 1}'}, CALL)
    assert out == {"name": "t", "arguments": {"a": 1}}


def test_string_ints_become_ints() -> None:
    out, _ = repair_args(
        "workspace_replace_lines", {"start_line": "175", "end_line": "180", "new_text": "x"}, REPLACE_LINES
    )
    assert out["start_line"] == 175 and out["end_line"] == 180 and out["new_text"] == "x"


def test_bool_and_array_strings() -> None:
    schema = _schema([], flag={"type": "boolean"}, items={"type": "array"})
    out, _ = repair_args("t", {"flag": "true", "items": '["a","b"]'}, schema)
    assert out == {"flag": True, "items": ["a", "b"]}


def test_one_missing_required_one_unknown_renames() -> None:
    out, notes = repair_args("ducky_plan_update_node", {"id": "B1"}, _schema(["node_id"], node_id=STR))
    assert out == {"node_id": "B1"} and notes == ["id→node_id"]


def test_good_args_untouched_and_no_schema_is_a_noop() -> None:
    args = {"pattern": "x", "max_results": 5}
    assert repair_args("workspace_search", args, SEARCH) == (args, [])
    assert repair_args("t", {"query": "x"}, "schema unavailable") == ({"query": "x"}, [])
    out, _ = repair_args("t", {"start_line": "abc"}, REPLACE_LINES)
    assert out["start_line"] == "abc"  # not a number: left for the tool to reject


def test_dispatch_gets_repaired_args_and_the_model_sees_the_note() -> None:
    seen: dict = {}

    async def fake_dispatch(name, arguments=None, **_kw):
        seen.update(arguments)
        return ToolCallResult(ok=True, tool=name, data="1 hit")

    with patch.object(tools, "_tool_input_schema", return_value=SEARCH), patch.object(
        tools, "_dispatch_tool", fake_dispatch
    ):
        result = asyncio.run(tools._execute_tool_inner("workspace_search", {"query": "Last"}))
    assert seen == {"pattern": "Last"}
    assert result.ok and "query→pattern" in result.data
