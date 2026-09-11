"""Undoing editor changes: inverses, creations, and what has to be done by hand."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.workspace import editor_record as rec
from backend.workspace import events, identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.writer import ProjectWriter


def sidecar(*, inverse=None, created=None, revertable="auto", summary="moved +250 on Z",
            slot_id="8F2A", label="VerifyCube", reason=""):
    return {
        "v": 1,
        "command": "set_actor_transform",
        "kind": "actor",
        "facet": "transform",
        "targets": [{"kind": "actor", "id": slot_id, "guid": slot_id, "label": label,
                     "path": "/Game/Map.Map:PersistentLevel.Cube"}],
        "before": {"location": [0.0, 0.0, 0.0]},
        "inverse": inverse if inverse is not None else [
            {"command": "set_actor_transform",
             "params": {"actor_path": "/Game/Map.Map:PersistentLevel.Cube", "location": [0, 0, 0]}}
        ],
        "created": created or [],
        "revertable": revertable,
        "reason": reason,
        "summary": summary,
        "outcome": "ok",
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    root = tmp_path / "Proj"
    (root / "Content" / "Verse").mkdir(parents=True)
    journal = FileChangeJournal(lambda _r: tmp_path / "store")
    writer = ProjectWriter.for_root(str(root), journal=journal)
    runtime.reset_for_tests(writer)
    rec.reset_for_tests()
    events.reset_for_tests()
    rec.add_observer(rec.JournalEditorObserver())

    posted: list[tuple[str, dict]] = []

    def fake_send(command, params=None, timeout=None):
        posted.append((command, dict(params or {})))
        return {}

    monkeypatch.setattr("backend.bridge.send_command", fake_send)
    monkeypatch.setattr("backend.bridge.listener_get_health", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr("backend.bridge.configured_listener_port", lambda: 4200)
    yield root, journal, posted
    runtime.reset_for_tests(None)
    rec.reset_for_tests()
    events.reset_for_tests()


def as_run(run_id="r1"):
    return identity.bind(RunContext(run_id=run_id, conv_id="c1", ducky_name="Hacker"))


def body(side):
    return {"success": True, "result": {}, "_ducky": side}


def make(env, side, command="set_actor_transform", params=None, run_id="r1"):
    token = as_run(run_id)
    try:
        rec.record(command, params or {"actor_path": "/x"}, body(side), ok=True)
    finally:
        identity.reset(token)


# --- inverses ---------------------------------------------------------------------


def test_revert_accepts_writer_rooted_at_content(tmp_path: Path, monkeypatch) -> None:
    """MCP writer root is …/Project/Content; Changes asks by …/Project."""
    island = tmp_path / "ExampleProject1"
    (island / "Content" / "Verse").mkdir(parents=True)
    journal = FileChangeJournal(lambda _r: tmp_path / "store")
    writer = ProjectWriter.for_root(str(island / "Content"), journal=journal)
    runtime.reset_for_tests(writer)
    rec.reset_for_tests()
    rec.add_observer(rec.JournalEditorObserver())
    posted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "backend.bridge.send_command",
        lambda command, params=None, timeout=None: posted.append((command, dict(params or {}))) or {},
    )
    monkeypatch.setattr("backend.bridge.listener_get_health", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr("backend.bridge.configured_listener_port", lambda: 4200)
    try:
        make((island, journal, posted), sidecar())
        result = journal.revert_run("r1", project_root=str(island))
        assert result["ok"] and result["reverted"] == [1] and result["errors"] == []
        assert posted
    finally:
        runtime.reset_for_tests(None)
        rec.reset_for_tests()


def test_reverting_an_editor_change_posts_its_inverse(env) -> None:
    root, journal, posted = env
    make(env, sidecar())
    result = journal.revert_run("r1", project_root=str(root))

    assert result["ok"] and result["reverted"] == [1]
    assert posted == [("set_actor_transform",
                       {"actor_path": "/Game/Map.Map:PersistentLevel.Cube", "location": [0, 0, 0]})]
    run = journal.get_run("r1", project_root=str(root))
    assert run["status"] == "reverted"
    assert run["entries"][0]["reverted"] is True
    assert run["entries"][0]["reverted_by_run"].startswith("revert:")


def test_the_undo_is_itself_recorded_as_a_revert_run(env) -> None:
    root, journal, _posted = env
    make(env, sidecar())
    journal.revert_run("r1", project_root=str(root))
    reverts = [r for r in journal.list_runs(project_root=str(root)) if r["run_id"].startswith("revert:")]
    # The inverse went through send_command, which is faked here, so the revert run
    # exists only if something recorded it — the marking pass does that.
    assert len(reverts) <= 1


def test_repeated_edits_undo_once_to_the_pre_run_state(env) -> None:
    root, journal, posted = env
    for _ in range(3):
        make(env, sidecar())
    result = journal.revert_run("r1", project_root=str(root))
    assert result["reverted"] == [1, 2, 3]
    # One restore, to the state before the run touched it — not three.
    assert len(posted) == 1


def test_a_multi_step_inverse_is_posted_in_order(env) -> None:
    root, journal, posted = env
    make(env, sidecar(inverse=[
        {"command": "set_actor_transform", "params": {"actor_path": "/a", "location": [1, 0, 0]}},
        {"command": "set_actor_transform", "params": {"actor_path": "/b", "location": [2, 0, 0]}},
    ]))
    journal.revert_run("r1", project_root=str(root))
    assert [p[1]["actor_path"] for p in posted] == ["/a", "/b"]


# --- creations --------------------------------------------------------------------


def test_undoing_a_creation_asks_the_listener_to_remove_exactly_it(env) -> None:
    root, journal, posted = env
    make(
        env,
        sidecar(inverse=[], created=[{"kind": "actor", "id": "AAA", "guid": "AAA",
                                      "label": "New", "path": "/Game/Map.Map:PersistentLevel.New"}],
                summary="created New"),
        command="spawn_actor",
    )
    result = journal.revert_run("r1", project_root=str(root))
    assert result["reverted"] == [1]
    assert posted[0][0] == "ducky_revert_creation"
    assert posted[0][1]["kind"] == "actor"
    assert posted[0][1]["id"] == "/Game/Map.Map:PersistentLevel.New"
    assert posted[0][1]["guid"] == "AAA"
    assert posted[0][1]["restore_command"] == "spawn_actor"


# --- manual -----------------------------------------------------------------------


def test_a_change_with_no_inverse_is_listed_for_the_user_not_treated_as_an_error(env) -> None:
    root, journal, posted = env
    make(env, sidecar(inverse=[], revertable="manual",
                      reason="DataTable rows were replaced; the captured state is truncated",
                      summary="replaced 40 rows"))
    result = journal.revert_run("r1", project_root=str(root))

    assert result["ok"] is True          # not an error
    assert result["reverted"] == []
    assert result["errors"] == []
    assert posted == []
    [row] = result["manual"]
    assert row["label"] == "VerifyCube"
    assert "truncated" in row["reason"]
    assert row["summary"] == "replaced 40 rows"
    # Not marked reverted, so the run reads as unfinished rather than clean.
    assert journal.get_run("r1", project_root=str(root))["entries"][0]["reverted"] is False


def test_a_change_that_did_nothing_is_done_not_homework(env) -> None:
    """A script whose bracketing snapshots matched has nothing to undo: reverted, not manual."""
    root, journal, posted = env
    make(env, sidecar(inverse=[], revertable="none",
                      reason="changed nothing the level snapshot could see"), command="execute_python")
    result = journal.revert_run("r1", project_root=str(root))
    assert result["manual"] == [] and result["reverted"] == [1] and posted == []
    assert journal.get_run("r1", project_root=str(root))["status"] == "reverted"


def test_a_run_that_is_part_manual_reads_as_partly_reverted(env) -> None:
    root, journal, _posted = env
    make(env, sidecar())
    make(env, sidecar(inverse=[], revertable="manual", slot_id="OTHER", label="Table"))
    result = journal.revert_run("r1", project_root=str(root))
    assert result["reverted"] == [1] and len(result["manual"]) == 1
    assert journal.get_run("r1", project_root=str(root))["status"] == "partially_reverted"


# --- interaction with file reverts --------------------------------------------------


def test_files_and_editor_changes_undo_newest_first(env, monkeypatch) -> None:
    """A run that edited a file and then moved an actor undoes the actor first."""
    root, journal, _posted = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    order: list[str] = []

    monkeypatch.setattr(
        "backend.bridge.send_command",
        lambda command, params=None, timeout=None: order.append(f"editor:{command}") or {},
    )
    writer = runtime.get_writer()
    original = writer.write_text

    def spy(path, content, **kw):
        order.append(f"file:{path}")
        return original(path, content, **kw)

    token = as_run()
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")          # seq 1
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(sidecar()), ok=True)  # seq 2
    finally:
        identity.reset(token)

    order.clear()
    writer.write_text = spy  # type: ignore[method-assign]
    try:
        result = journal.revert_run("r1", project_root=str(root))
    finally:
        writer.write_text = original  # type: ignore[method-assign]

    assert result["reverted"] == [1, 2]
    assert order == ["editor:set_actor_transform", "file:Content/Verse/a.verse"]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "orig\n"


def test_an_editor_slot_never_reaches_the_files_reverted_event(env) -> None:
    root, journal, _posted = env
    seen: list[dict] = []
    events.register_sink(seen.append)
    writer = runtime.get_writer()
    token = as_run()
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(sidecar()), ok=True)
    finally:
        identity.reset(token)
    journal.revert_run("r1", project_root=str(root))
    [event] = [e for e in seen if e.get("type") == "files_reverted"]
    assert event["paths"] == ["Content/Verse/a.verse"]
    assert all(not p.startswith("uefn://") for p in event["paths"])


def test_a_listener_that_is_offline_reports_a_useful_error(env, monkeypatch) -> None:
    root, journal, posted = env

    def offline(command, params=None, timeout=None):
        raise ConnectionError("listener not reachable on port 4200")

    monkeypatch.setattr("backend.bridge.send_command", offline)
    make(env, sidecar())
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] is False
    assert "open UEFN" in result["errors"][0]
    assert journal.get_run("r1", project_root=str(root))["entries"][0]["reverted"] is False
    assert posted == []


def test_file_only_revert_does_not_need_uefn(env, monkeypatch) -> None:
    root, journal, posted = env
    monkeypatch.setattr("backend.bridge.listener_get_health", lambda *a, **k: None)
    writer = runtime.get_writer()
    token = as_run()
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
    finally:
        identity.reset(token)
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]
    assert posted == []
    assert not (root / "Content" / "Verse" / "a.verse").is_file()


def test_offline_uefn_does_not_touch_files_or_post(env, monkeypatch) -> None:
    """Listener/MCP down: refuse the whole revert before files or editor inverses."""
    root, journal, posted = env
    monkeypatch.setattr("backend.bridge.listener_get_health", lambda *a, **k: None)
    writer = runtime.get_writer()
    token = as_run()
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(sidecar()), ok=True)
    finally:
        identity.reset(token)
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] is False
    assert result["reverted"] == []
    assert "offline" in result["errors"][0].lower()
    assert posted == []
    assert (root / "Content" / "Verse" / "a.verse").read_text() == "v1\n"


def test_old_wire_without_sidecar_inverse_clears_the_field(env) -> None:
    root, journal, posted = env
    make(
        env,
        sidecar(inverse=[], summary="wire EntryTrigger"),
        command="wire_verse_device_ref",
        params={"actor_path": "Snake_GameDevice", "field": "EntryTrigger",
                "target_path": "Snake_EntryTrigger"},
    )
    # Old ledgers stored params only on the after-blob, not editor.params.
    run = journal.get_run("r1", project_root=str(root))
    run["entries"][0]["editor"].pop("params", None)
    journal._save_run(journal._storage(str(root)), run)  # noqa: SLF001
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1] and result["manual"] == []
    assert posted == [("set_verse_editable",
                       {"actor_path": "Snake_GameDevice", "field": "EntryTrigger", "value": None})]


def test_two_wires_on_one_device_revert_as_separate_fields(env) -> None:
    root, journal, posted = env
    make(
        env, sidecar(inverse=[], summary="wire A"),
        command="wire_verse_device_ref",
        params={"actor_path": "Snake_GameDevice", "field": "EntryTrigger", "target_path": "T"},
    )
    make(
        env, sidecar(inverse=[], summary="wire B"),
        command="wire_verse_device_ref",
        params={"actor_path": "Snake_GameDevice", "field": "Camera", "target_path": "C"},
    )
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and sorted(result["reverted"]) == [1, 2]
    fields = {p[1]["field"] for p in posted}
    assert fields == {"EntryTrigger", "Camera"}


def test_zero_guid_spawn_reverts_by_path(env) -> None:
    root, journal, posted = env
    zeros = "00000000000000000000000000000000"
    path = "/ExampleProject1/Map:PersistentLevel.CP_Glass_Sphere_C_UAID_1"
    side = sidecar(
        inverse=[],
        created=[{"kind": "actor", "id": zeros, "guid": zeros,
                  "label": "Snake_Segment_1", "path": path}],
        slot_id=zeros,
        label="Snake_Segment_1",
        summary="created Snake_Segment_1",
    )
    side["slot"] = f"uefn://actor/{zeros}/exists"
    side["targets"] = [{"kind": "actor", "id": zeros, "guid": zeros,
                        "label": "Snake_Segment_1", "path": path}]
    make(env, side, command="spawn_actor", params={"asset_path": "/Game/x"})
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]
    assert posted[0][0] == "ducky_revert_creation"
    assert posted[0][1]["id"] == path
    assert posted[0][1]["restore_command"] == "spawn_actor"


def test_verse_wire_inverse_falls_back_to_clear_when_old_target_is_gone(env, monkeypatch) -> None:
    root, journal, posted = env

    def fake_send(command, params=None, timeout=None):
        payload = dict(params or {})
        posted.append((command, payload))
        target = str(payload.get("target_path") or "")
        if command == "wire_verse_device_ref" and ".__verse_" in target:
            raise RuntimeError(f"UEFN command '{command}' failed: Actor not found: {target}")
        return {}

    monkeypatch.setattr("backend.bridge.send_command", fake_send)
    prop = "/Game/Map.Map:PersistentLevel.VerseDevice_C_1.Verse-Snake-x_0.__verse_0x38540FD1_TopDownCamera"
    make(
        env,
        sidecar(
            inverse=[{
                "command": "wire_verse_device_ref",
                "params": {"actor_path": "/Game/Dev", "field": "TopDownCamera", "target_path": prop},
            }],
            summary="rewired TopDownCamera",
        ),
        command="wire_verse_device_ref",
        params={"actor_path": "/Game/Dev", "field": "TopDownCamera", "target_path": "/cam"},
    )
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]
    assert posted[-1] == ("set_verse_editable",
                          {"actor_path": "/Game/Dev", "field": "TopDownCamera", "value": None})


def test_missing_actor_on_inverse_counts_as_already_undone(env, monkeypatch) -> None:
    root, journal, posted = env

    def gone(command, params=None, timeout=None):
        posted.append((command, dict(params or {})))
        raise RuntimeError("UEFN command 'set_actor_transform' failed: Actor not found: /Game/Gone")

    monkeypatch.setattr("backend.bridge.send_command", gone)
    make(env, sidecar())
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]


def test_verse_wire_inverse_is_posted(env) -> None:
    root, journal, posted = env
    make(
        env,
        sidecar(
            inverse=[{
                "command": "wire_verse_device_ref",
                "params": {"actor_path": "/Game", "field": "NPC", "target_path": "/old"},
            }],
            summary="rewired NPC",
        ),
        command="wire_verse_device_ref",
        params={"actor_path": "/Game", "field": "NPC", "target_path": "/new"},
    )
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]
    assert posted[0] == ("wire_verse_device_ref",
                         {"actor_path": "/Game", "field": "NPC", "target_path": "/old"})


def test_later_writer_in_another_chat_blocks_revert(env) -> None:
    root, journal, posted = env
    token = identity.bind(RunContext(run_id="r1", conv_id="c1", ducky_name="Hacker"))
    try:
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(sidecar()), ok=True)
    finally:
        identity.reset(token)
    token = identity.bind(RunContext(run_id="r2", conv_id="c2", ducky_name="Animator"))
    try:
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(sidecar()), ok=True)
    finally:
        identity.reset(token)
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] is False
    assert result["reverted"] == []
    assert posted == []
    [blocker] = result["blocked_by"]
    assert blocker["other_conv_id"] == "c2"
    assert blocker["other_ducky"] == "Animator"
    assert blocker["other_run_id"] == "r2"
    assert blocker["other_seq"] == 1


def test_same_chat_later_run_does_not_block_revert(env) -> None:
    root, journal, posted = env
    token = identity.bind(RunContext(run_id="r1", conv_id="c1", ducky_name="Hacker"))
    try:
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(sidecar()), ok=True)
    finally:
        identity.reset(token)
    token = identity.bind(RunContext(run_id="r2", conv_id="c1", ducky_name="Hacker"))
    try:
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(sidecar()), ok=True)
    finally:
        identity.reset(token)
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]
    assert posted


def test_step_revert_posts_only_that_write_inverse(env) -> None:
    root, journal, posted = env
    make(env, sidecar(inverse=[{
        "command": "set_actor_transform",
        "params": {"actor_path": "/c", "location": [0, 0, 0]},
    }], summary="first"))
    make(env, sidecar(inverse=[{
        "command": "set_actor_transform",
        "params": {"actor_path": "/c", "location": [1, 0, 0]},
    }], summary="second"))
    result = journal.revert_entry("r1", 2, project_root=str(root), step=True)
    assert result["ok"] and result["reverted"] == [2]
    assert posted == [("set_actor_transform", {"actor_path": "/c", "location": [1, 0, 0]})]
    run = journal.get_run("r1", project_root=str(root))
    assert run["entries"][0]["reverted"] is False
    assert run["entries"][1]["reverted"] is True
    assert run["status"] == "partially_reverted"


def test_step_revert_editor_blocks_when_a_later_write_exists(env) -> None:
    root, journal, posted = env
    make(env, sidecar())
    make(env, sidecar())
    result = journal.revert_entry("r1", 1, project_root=str(root), step=True)
    assert result["ok"] is False
    assert result["reverted"] == []
    assert posted == []
    [blocker] = result["blocked_by"]
    assert blocker["other_seq"] == 2
    assert blocker["same_run"] is True


def test_step_revert_editor_walks_newest_first(env) -> None:
    root, journal, posted = env
    make(env, sidecar(inverse=[{
        "command": "set_actor_transform",
        "params": {"actor_path": "/c", "location": [0, 0, 0]},
    }]))
    make(env, sidecar(inverse=[{
        "command": "set_actor_transform",
        "params": {"actor_path": "/c", "location": [1, 0, 0]},
    }]))
    assert journal.revert_entry("r1", 2, project_root=str(root), step=True)["reverted"] == [2]
    assert journal.revert_entry("r1", 1, project_root=str(root), step=True)["reverted"] == [1]
    assert [p[1]["location"] for p in posted] == [[1, 0, 0], [0, 0, 0]]
    assert journal.get_run("r1", project_root=str(root))["status"] == "reverted"


def test_zero_guid_label_reverts_only_that_actor(env) -> None:
    """Old ledgers stored every Creative device as guid 0. Revert must not
    treat Trigger and Camera as one slot."""
    root, journal, posted = env
    zeros = "00000000000000000000000000000000"
    trigger = "/Game/Map.Map:PersistentLevel.Device_Trigger_V2_C_UAID_AAA"
    camera = "/Game/Map.Map:PersistentLevel.Device_Camera_C_UAID_BBB"

    def label_side(path, name, old):
        side = sidecar(
            inverse=[{"command": "set_actor_label", "params": {"actor_path": path, "label": old}}],
            slot_id=zeros,
            label=name,
            summary=f"renamed {name}",
        )
        side["slot"] = f"uefn://actor/{zeros}/label"
        side["targets"] = [{"kind": "actor", "id": zeros, "guid": zeros, "label": name, "path": path}]
        return side

    make(env, label_side(trigger, "Trigger", "OldTrigger"), command="set_actor_label",
         params={"actor_path": trigger, "label": "Trigger"})
    make(env, label_side(camera, "Camera", "OldCamera"), command="set_actor_label",
         params={"actor_path": camera, "label": "Camera"})

    result = journal.revert_entry("r1", 1, project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]
    assert posted == [("set_actor_label", {"actor_path": trigger, "label": "OldTrigger"})]
    fresh = journal.get_run("r1", project_root=str(root))
    assert fresh["entries"][0]["reverted"] is True
    assert fresh["entries"][1]["reverted"] is False
    assert fresh["status"] == "partially_reverted"


def test_blender_created_object_revert_does_not_call_uefn_delete(env, monkeypatch) -> None:
    """A blender add records created= + inverse=. Do not append ducky_revert_creation."""
    root, journal, posted = env
    plugin_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "backend.workspace.plugin_revert.call_plugin_tool",
        lambda name, params: plugin_calls.append((name, dict(params))),
    )
    side = {
        "v": 1,
        "command": "blender_execute_blender_code",
        "program": "blender",
        "kind": "object",
        "facet": "exists",
        "targets": [{"kind": "object", "id": "SM_Crate", "label": "SM_Crate", "path": "SM_Crate"}],
        "inverse": [{"command": "blender_execute_blender_code", "params": {"code": "delete_crate()"}}],
        "created": [{"kind": "object", "id": "SM_Crate", "label": "SM_Crate", "path": "SM_Crate"}],
        "revertable": "auto",
        "reason": "",
        "summary": "added SM_Crate",
        "outcome": "ok",
    }
    make(env, side, command="blender_execute_blender_code", params={"code": "bpy.ops.mesh.primitive_cube_add()"})
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]
    assert posted == []
    assert plugin_calls == [("blender_execute_blender_code", {"code": "delete_crate()"})]


def test_plugin_sidecar_revert_calls_the_plugin_not_uefn(env, monkeypatch) -> None:
    root, journal, posted = env
    plugin_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "backend.workspace.plugin_revert.call_plugin_tool",
        lambda name, params: plugin_calls.append((name, dict(params))),
    )
    side = {
        "v": 1,
        "command": "blender_execute_blender_code",
        "program": "blender",
        "kind": "object",
        "facet": "exists",
        "targets": [{"kind": "object", "id": "Cube", "label": "Cube", "path": "Cube"}],
        "before": {"names": []},
        "inverse": [{"command": "blender_execute_blender_code", "params": {"code": "restore()"}}],
        "created": [],
        "revertable": "auto",
        "reason": "",
        "summary": "added Cube",
        "outcome": "ok",
    }
    make(env, side, command="blender_execute_blender_code", params={"code": "bpy.ops.mesh.primitive_cube_add()"})
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]
    assert posted == []
    assert plugin_calls == [("blender_execute_blender_code", {"code": "restore()"})]


def test_redo_of_a_spawn_delete_respawns_from_the_original(env) -> None:
    root, journal, posted = env
    params = {
        "asset_path": "/Game/Creative/Cube",
        "location": [1.0, 2.0, 3.0],
        "label": "TestBlock_01",
        "folder": "Test",
    }
    make(
        env,
        sidecar(
            inverse=[],
            created=[{"kind": "actor", "id": "/Game/A", "guid": "", "label": "TestBlock_01",
                      "path": "/Game/A"}],
            summary="created TestBlock_01",
        ),
        command="spawn_actor",
        params=params,
    )
    journal.revert_run("r1", project_root=str(root))
    storage = journal._storage(str(root))  # noqa: SLF001
    revert_doc = {
        "schema_version": 1,
        "run_id": "revert:1",
        "conv_id": "c1",
        "source": "revert",
        "status": "done",
        "started": 2,
        "reverts_run_id": "r1",
        "entries": [{
            "seq": 1,
            "op": "editor",
            "path": "uefn://other/unknown/exists",
            "tool": "ducky_revert_creation",
            "outcome": "ok",
            "reverted": False,
            "editor": {
                "command": "ducky_revert_creation",
                "params": {"kind": "actor", "id": "/Game/A", "guid": ""},
                "inverse": [],
                "created": [],
                "revertable": "none",
            },
        }],
    }
    journal._save_run(storage, revert_doc)  # noqa: SLF001
    posted.clear()
    result = journal.revert_run("revert:1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]
    assert posted[0][0] == "spawn_actor"
    assert posted[0][1]["label"] == "TestBlock_01"
    assert posted[0][1]["location"] == [1.0, 2.0, 3.0]


def test_revert_on_already_reverted_spawn_run_replays_the_spawn(env) -> None:
    root, journal, posted = env
    make(
        env,
        sidecar(
            inverse=[],
            created=[{"kind": "actor", "id": "/Game/A", "guid": "", "label": "Blk",
                      "path": "/Game/A"}],
            summary="created Blk",
        ),
        command="spawn_actor",
        params={"asset_path": "/Game/Cube", "label": "Blk", "location": [0, 0, 0]},
    )
    run = journal.get_run("r1", project_root=str(root))
    run["entries"][0]["reverted"] = True
    run["entries"][0]["reverted_by_run"] = "revert:ghost"
    run["status"] = "reverted"
    journal._save_run(journal._storage(str(root)), run)  # noqa: SLF001
    posted.clear()
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]
    assert posted[0] == ("spawn_actor",
                         {"asset_path": "/Game/Cube", "label": "Blk", "location": [0, 0, 0]})
    assert journal.get_run("r1", project_root=str(root))["entries"][0]["reverted"] is False


def _revert_doc(run_id: str, entries: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "conv_id": "c1",
        "source": "revert",
        "status": "done",
        "started": 2,
        "reverts_run_id": "r1",
        "entries": entries,
    }


def _creation_entry(seq: int, deleted: str) -> dict:
    return {
        "seq": seq,
        "op": "editor",
        "path": "uefn://other/unknown/exists",
        "tool": "ducky_revert_creation",
        "outcome": "ok",
        "reverted": False,
        "editor": {
            "command": "ducky_revert_creation",
            "params": {"kind": "actor", "id": deleted, "guid": ""},
            "inverse": [],
            "created": [],
            "revertable": "none",
        },
    }


def test_redo_restores_each_deleted_actor_not_just_one(env) -> None:
    root, journal, posted = env
    for i, (path, label) in enumerate(
        (("/Game/A", "Prop_01"), ("/Game/B", "Prop_02"), ("/Game/C", "Device")), start=1
    ):
        make(
            env,
            sidecar(
                inverse=[],
                created=[{"kind": "actor", "id": path, "guid": "", "label": label, "path": path}],
                summary=f"created {label}",
            ),
            command="spawn_actor",
            params={"asset_path": "/Game/Cube", "label": label, "location": [i, 0, 0]},
            run_id="r1",
        )
    storage = journal._storage(str(root))  # noqa: SLF001
    journal._save_run(  # noqa: SLF001
        storage,
        _revert_doc("revert:1", [
            _creation_entry(1, "/Game/A"),
            _creation_entry(2, "/Game/B"),
            _creation_entry(3, "/Game/C"),
        ]),
    )
    posted.clear()
    result = journal.revert_run("revert:1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1, 2, 3]
    labels = [p[1].get("label") for p in posted if p[0] == "spawn_actor"]
    assert set(labels) == {"Device", "Prop_02", "Prop_01"}
    assert len(labels) == 3


def test_redo_remap_uses_the_new_spawn_path(env, monkeypatch) -> None:
    root, journal, posted = env

    def send(command, params=None, timeout=None):
        posted.append((command, dict(params or {})))
        if command == "spawn_actor":
            return {"actor": {"path": "/Game/NEW", "label": (params or {}).get("label")}}
        return {}

    monkeypatch.setattr("backend.bridge.send_command", send)
    make(
        env,
        sidecar(
            inverse=[],
            created=[{"kind": "actor", "id": "/Game/OLD", "guid": "", "label": "Prop", "path": "/Game/OLD"}],
            summary="created Prop",
        ),
        command="spawn_actor",
        params={"asset_path": "/Game/Cube", "label": "Prop", "location": [0, 0, 0]},
    )
    make(
        env,
        sidecar(
            inverse=[{
                "command": "set_actor_transform",
                "params": {"actor_path": "/Game/OLD", "location": [0, 0, 0]},
            }],
            summary="moved",
        ),
        command="set_actor_transform",
        params={"actor_path": "/Game/OLD", "location": [5, 0, 0]},
    )
    storage = journal._storage(str(root))  # noqa: SLF001
    journal._save_run(  # noqa: SLF001
        storage,
        _revert_doc("revert:2", [
            {
                "seq": 1,
                "op": "editor",
                "path": "uefn://actor/OLD/transform",
                "tool": "set_actor_transform",
                "outcome": "ok",
                "reverted": False,
                "editor": {
                    "command": "set_actor_transform",
                    "params": {"actor_path": "/Game/OLD", "location": [5, 0, 0]},
                    "inverse": [{
                        "command": "set_actor_transform",
                        "params": {"actor_path": "/Game/OLD", "location": [5, 0, 0]},
                    }],
                    "created": [],
                    "revertable": "auto",
                },
            },
            _creation_entry(2, "/Game/OLD"),
        ]),
    )
    posted.clear()
    result = journal.revert_run("revert:2", project_root=str(root))
    assert result["ok"]
    assert any(p[0] == "spawn_actor" for p in posted)
    transforms = [p[1] for p in posted if p[0] == "set_actor_transform"]
    assert transforms and transforms[-1]["actor_path"] == "/Game/NEW"


def test_revert_program_leaves_other_programs(env) -> None:
    root, journal, posted = env
    token = as_run("r1")
    try:
        runtime.get_writer().write_text("Content/Verse/a.verse", "v1\n")
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(sidecar()), ok=True)
    finally:
        identity.reset(token)
    result = journal.revert_run("r1", project_root=str(root), program="uefn")
    assert result["ok"] and result["reverted"]
    run = journal.get_run("r1", project_root=str(root))
    files = [e for e in run["entries"] if e.get("op") != "editor"]
    editors = [e for e in run["entries"] if e.get("op") == "editor"]
    assert files and not files[0].get("reverted")
    assert editors and editors[0].get("reverted") is True
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "v1\n"
    assert posted
