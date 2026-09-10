"""Bridge CallTool results land in the journal; listener / ducky_call_tool do not double-count."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.bridge import plugin_gate
from backend.workspace import editor_record as rec
from backend.workspace import identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.tool_record import record_tool_result
from backend.workspace.writer import ProjectWriter


@pytest.fixture
def env(tmp_path: Path):
    root = tmp_path / "Proj"
    (root / "Content" / "Verse").mkdir(parents=True)
    journal = FileChangeJournal(lambda _r: tmp_path / "store")
    writer = ProjectWriter.for_root(str(root), journal=journal)
    runtime.reset_for_tests(writer)
    rec.reset_for_tests()
    rec.add_observer(rec.JournalEditorObserver())
    token = identity.bind(RunContext(run_id="r1", conv_id="c1", ducky_name="Hacker"))
    yield root, journal
    identity.reset(token)
    runtime.reset_for_tests(None)
    rec.reset_for_tests()


def _req(name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(params=SimpleNamespace(name=name, arguments=arguments))


def _result(text: str, *, error: bool = False) -> SimpleNamespace:
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(root=SimpleNamespace(content=[block], isError=error), isError=error)


def test_place_device_from_bridge_is_journaled(env) -> None:
    root, journal = env
    plugin_gate._record_bridge_tool(
        "unreal__call_tool",
        _req("unreal__call_tool", {
            "toolset_name": "ValkyrieToolset.DeviceToolset",
            "tool_name": "PlaceDevice",
            "arguments": {"label": "Trigger_V2"},
        }),
        _result('{"actor_path": "/Game/Map.Map:PersistentLevel.Trigger", "label": "Trigger_V2"}'),
    )
    run = journal.get_run("r1", project_root=str(root))
    [entry] = run["entries"]
    assert entry["op"] == "editor"
    assert entry["editor"]["program"] == "uefn"
    assert entry["editor"]["created"][0]["label"] == "Trigger_V2"


def test_blender_sidecar_from_bridge_keeps_program(env) -> None:
    root, journal = env
    sidecar = {
        "program": "blender",
        "kind": "object",
        "facet": "exists",
        "slot": "blender://object/Cube/exists",
        "targets": [{"kind": "object", "id": "Cube", "label": "Cube", "path": "Cube"}],
        "created": [{"kind": "object", "id": "Cube", "label": "Cube", "path": "Cube"}],
        "revertable": "auto",
        "summary": "added Cube",
    }
    plugin_gate._record_bridge_tool(
        "blender_execute_blender_code",
        _req("blender_execute_blender_code", {"code": "bpy.ops.mesh.primitive_cube_add()"}),
        _result(json_dumps({"ok": True, "_ducky": sidecar})),
    )
    run = journal.get_run("r1", project_root=str(root))
    [entry] = run["entries"]
    assert entry["path"] == "blender://object/Cube/exists"
    assert entry["editor"]["program"] == "blender"


def test_ducky_call_tool_and_listener_names_are_not_recorded_again(env) -> None:
    root, journal = env
    plugin_gate._record_bridge_tool(
        "ducky_call_tool",
        _req("ducky_call_tool", {"name": "unreal__call_tool", "arguments": {}}),
        _result('{"actor_path": "/Game/Map.Map:PersistentLevel.Trigger"}'),
    )
    plugin_gate._record_bridge_tool(
        "spawn_actor",
        _req("spawn_actor", {"asset_path": "/Game/Cube"}),
        _result('{"actor": {"path": "/Game/Cube"}}'),
    )
    with pytest.raises(ValueError, match="not found"):
        journal.get_run("r1", project_root=str(root))


def test_record_tool_result_skips_reads(env) -> None:
    root, journal = env
    record_tool_result(
        "unreal__call_tool",
        {"tool_name": "GetAllActors", "arguments": {}},
        '{"actors": []}',
        ok=True,
    )
    with pytest.raises(ValueError, match="not found"):
        journal.get_run("r1", project_root=str(root))


def test_plugin_tool_without_sidecar_uses_owner(env) -> None:
    root, journal = env
    from backend.uefn_plugins import host

    host._PLUGIN_TOOL_OWNER["discord_send_message"] = "discord"
    try:
        plugin_gate._record_bridge_tool(
            "discord_send_message",
            _req("discord_send_message", {"channel_id": "1", "content": "hi"}),
            _result('{"ok": true}'),
        )
        run = journal.get_run("r1", project_root=str(root))
        [entry] = run["entries"]
        assert entry["op"] == "editor"
        assert entry["editor"]["program"] == "discord"
        assert entry["editor"]["revertable"] == "none"
        assert entry["editor"]["kind"] == "tool"
    finally:
        host._PLUGIN_TOOL_OWNER.pop("discord_send_message", None)


def test_plan_update_is_journaled_as_ducky(env) -> None:
    root, journal = env
    plugin_gate._record_bridge_tool(
        "ducky_plan_update_node",
        _req("ducky_plan_update_node", {"node_id": "n1", "status": "completed"}),
        _result('{"ok": true}'),
    )
    run = journal.get_run("r1", project_root=str(root))
    [entry] = run["entries"]
    assert entry["editor"]["program"] == "ducky"
    assert entry["tool"] == "ducky_plan_update_node"


def test_blender_read_is_not_journaled(env) -> None:
    root, journal = env
    plugin_gate._record_bridge_tool(
        "blender_get_scene_info",
        _req("blender_get_scene_info", {}),
        _result('{"objects": []}'),
    )
    with pytest.raises(ValueError, match="not found"):
        journal.get_run("r1", project_root=str(root))


def test_changeset_tools_are_not_journaled(env) -> None:
    root, journal = env
    plugin_gate._record_bridge_tool(
        "changeset_archive",
        _req("changeset_archive", {"run_ids": ["r1"]}),
        _result('{"ok": true}'),
    )
    with pytest.raises(ValueError, match="not found"):
        journal.get_run("r1", project_root=str(root))


def json_dumps(obj: dict) -> str:
    import json

    return json.dumps(obj)
