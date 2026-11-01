"""PerInstanceSMData must be cleared before a size-changing SetProperties."""

from __future__ import annotations

import asyncio
import json

from backend.mcp_plugins.epic_array_set import expand_epic_call, guard_instance_array_script

_SEAT = {
    "toolset_name": "EditorToolset.ObjectToolset",
    "tool_name": "SetProperties",
    "arguments": {
        "instance": {"refPath": "/ExampleProject1/PersistentLevel.Stadium_Seat_Instances"},
        "values": json.dumps(
            {
                "perInstanceSMData": [
                    {"transform": {"wPlane": {"x": 1, "y": 2, "z": 7050, "w": 1}}}
                ]
            }
        ),
    },
}

_SCRIPT = """
import json, math
async def apply(data):
    return (await execute_tool(
        "EditorToolset.ObjectToolset.SetProperties",
        json.dumps({
            "instance": {"refPath": "/ExampleProject1/PersistentLevel.Stadium_Seat_Instances"},
            "values": json.dumps({"perInstanceSMData": data}),
        }),
    ))["returnValue"]
async def run():
    data = [{"transform": {"wPlane": {"x": 4933.5, "y": 3354, "z": 7050, "w": 1}}}]
    return {"ok": await apply(data), "instances": len(data)}
"""


def test_set_properties_clears_instance_array_then_writes():
    calls = expand_epic_call("call_tool", _SEAT)
    assert len(calls) == 2
    first_values = json.loads(calls[0]["arguments"]["values"])
    assert first_values == {"perInstanceSMData": []}
    assert calls[1] is _SEAT
    assert "4933" not in json.dumps(first_values)


def test_empty_instance_array_is_one_call():
    args = {
        "tool_name": "SetProperties",
        "arguments": {"values": {"perInstanceSMData": []}},
    }
    assert expand_epic_call("call_tool", args) == [args]


def test_unrelated_array_is_not_cleared():
    args = {
        "tool_name": "set_properties",
        "arguments": {"values": {"tags": ["a", "b"]}},
    }
    assert expand_epic_call("call_tool", args) == [args]


def test_script_wrapper_clears_then_sets_the_original_payload():
    calls: list[tuple[str, str]] = []

    async def execute_tool(name, args):
        calls.append((name, args))
        return {"returnValue": True}

    guarded = guard_instance_array_script(_SCRIPT)
    assert guarded.splitlines()[1] == "import json as _json"  # 43.00: imports at top level
    ns: dict = {"execute_tool": execute_tool}
    exec(guarded, ns)
    out = asyncio.run(ns["run"]())
    assert out["instances"] == 1
    assert len(calls) == 2
    first = json.loads(json.loads(calls[0][1])["values"])
    second = json.loads(json.loads(calls[1][1])["values"])
    assert first["perInstanceSMData"] == []
    assert second["perInstanceSMData"][0]["transform"]["wPlane"]["x"] == 4933.5


def test_script_wrapper_is_idempotent_and_skips_unrelated_scripts():
    guarded = guard_instance_array_script(_SCRIPT)
    assert guard_instance_array_script(guarded) == guarded
    plain = "def run():\n    return execute_tool('EditorToolset.ActorToolset.SetLabel', '{}')\n"
    assert guard_instance_array_script(plain) == plain


def test_execute_tool_script_call_is_wrapped_once():
    args = {
        "toolset_name": "editor_toolset.toolsets.programmatic.ProgrammaticToolset",
        "tool_name": "execute_tool_script",
        "arguments": {"script": _SCRIPT},
    }
    calls = expand_epic_call("call_tool", args)
    assert len(calls) == 1
    script = calls[0]["arguments"]["script"]
    assert script.startswith("# ducky:instance-array-clear\n")
    assert "Stadium_Seat_Instances" in script
    assert args["arguments"]["script"] == _SCRIPT


def test_code_block_script_is_wrapped_in_place():
    args = {
        "tool_name": "execute_tool_script",
        "arguments": {"script": {"content": _SCRIPT, "language": "Python"}},
    }
    [call] = expand_epic_call("call_tool", args)
    block = call["arguments"]["script"]
    assert block["language"] == "Python" and block["content"].startswith("# ducky:instance-array-clear\n")


def test_legacy_snake_case_name_still_splits():
    legacy = {**_SEAT, "tool_name": "set_properties"}
    assert len(expand_epic_call("call_tool", legacy)) == 2
