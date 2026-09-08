"""Turning a listener sidecar into a recorded editor change."""

from __future__ import annotations

import pytest

from backend.workspace import editor_record as rec
from backend.workspace import identity
from backend.workspace.editor_ops import REVERT_AUTO, REVERT_NONE
from backend.workspace.identity import RunContext

SIDECAR = {
    "v": 1,
    "command": "set_actor_transform",
    "kind": "actor",
    "facet": "transform",
    "targets": [{"kind": "actor", "id": "8F2A", "guid": "8F2A", "label": "Cube", "path": "/Game/x.Cube"}],
    "before": {"location": [0.0, 0.0, 0.0]},
    "inverse": [{"command": "set_actor_transform", "params": {"actor_path": "/Game/x.Cube", "location": [0, 0, 0]}}],
    "created": [],
    "revertable": "auto",
    "reason": "",
    "summary": "moved +250 on Z",
    "outcome": "ok",
}


def body(sidecar=None, **extra):
    out = {"success": True, "result": {}}
    if sidecar is not None:
        out["_ducky"] = sidecar
    out.update(extra)
    return out


@pytest.fixture(autouse=True)
def _clean():
    rec.reset_for_tests()
    yield
    rec.reset_for_tests()


class Collector:
    def __init__(self):
        self.changes = []

    def on_editor_change(self, change):
        self.changes.append(change)


# --- what counts as a change -----------------------------------------------------


def test_reads_are_not_changes() -> None:
    assert rec.build("get_all_actors", {}, None, ok=True) is None
    assert rec.build("list_assets", {}, None, ok=True) is None
    assert rec.build("save_current_level", {}, None, ok=True) is None


def test_a_mutation_without_a_sidecar_is_still_recorded() -> None:
    """An older listener, or a command with no capture spec, must not vanish."""
    change = rec.build("create_material", {"asset_name": "M_X"}, None, ok=True)
    assert change is not None
    assert change.applied and change.revertable != REVERT_AUTO
    assert change.command == "create_material"


def test_unknown_commands_are_recorded_as_opaque() -> None:
    change = rec.build("some_plugin_command", {"x": 1}, None, ok=True)
    assert change is not None
    assert change.spec.mutates == "opaque"
    assert change.reason


def test_sidecar_fields_are_carried_through() -> None:
    change = rec.build("set_actor_transform", {"actor_path": "/Game/x.Cube"}, SIDECAR, ok=True)
    assert change.kind == "actor"
    assert change.slot == "uefn://actor/8F2A/transform"
    assert change.revertable == REVERT_AUTO
    assert change.summary == "moved +250 on Z"
    assert change.before == {"location": [0.0, 0.0, 0.0]}
    assert len(change.inverse) == 1
    assert change.targets[0]["label"] == "Cube"


def test_slot_falls_back_to_a_path_from_the_params() -> None:
    change = rec.build("set_actor_label", {"actor_path": "/Game/x.Cube"}, None, ok=True)
    assert change.slot == "uefn://actor/Game/x.Cube/label"
    assert rec.build("create_data_table", {}, None, ok=True).slot.endswith("/unknown/exists")


# --- outcomes --------------------------------------------------------------------


def test_a_refusal_is_recorded_as_blocked_with_nothing_to_undo() -> None:
    change = rec.build(
        "delete_actors", {"actor_paths": ["/a"]}, None,
        ok=False,
        error="Refused: never delete island actors. Fix the asset/device instead.",
    )
    assert change.outcome == rec.OUTCOME_BLOCKED
    assert not change.applied
    assert change.revertable == REVERT_NONE
    assert "Refused" in change.reason


def test_an_error_is_recorded_as_failed() -> None:
    change = rec.build("set_actor_transform", {"actor_path": "/x"}, None,
                       ok=False, error="Actor not found: /x")
    assert change.outcome == rec.OUTCOME_FAILED
    assert change.revertable == REVERT_NONE


def test_a_failed_command_never_keeps_an_inverse() -> None:
    change = rec.build("set_actor_transform", {}, SIDECAR, ok=False, error="boom")
    assert change.revertable == REVERT_NONE


def test_a_failed_read_is_not_recorded() -> None:
    assert rec.build("get_all_actors", {}, None, ok=False, error="boom") is None


# --- attribution and observers ----------------------------------------------------


def test_the_change_carries_the_running_ducky() -> None:
    token = identity.bind(RunContext(run_id="r1", conv_id="c1", ducky_name="Hacker", model="m"))
    try:
        change = rec.build("set_actor_transform", {}, SIDECAR, ok=True)
    finally:
        identity.reset(token)
    assert change.writer["ducky_name"] == "Hacker"
    assert change.writer["run_id"] == "r1"
    assert change.writer["tool"] == "set_actor_transform"


def test_record_notifies_observers(monkeypatch) -> None:
    seen = Collector()
    rec.add_observer(seen)
    rec.record("set_actor_transform", {"actor_path": "/x"}, body(SIDECAR), ok=True)
    rec.record("get_all_actors", {}, body(), ok=True)
    assert [c.command for c in seen.changes] == ["set_actor_transform"]
    rec.remove_observer(seen)
    rec.record("set_actor_label", {"actor_path": "/x"}, body(), ok=True)
    assert len(seen.changes) == 1


def test_a_broken_observer_cannot_break_the_command() -> None:
    class Boom:
        def on_editor_change(self, change):
            raise RuntimeError("observer bug")

    good = Collector()
    rec.add_observer(Boom())
    rec.add_observer(good)
    change = rec.record("set_actor_transform", {}, body(SIDECAR), ok=True)
    assert change is not None and len(good.changes) == 1


def test_recording_never_raises(monkeypatch) -> None:
    monkeypatch.setattr(rec, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    assert rec.record("set_actor_transform", {}, body(SIDECAR), ok=True) is None


def test_a_malformed_sidecar_is_tolerated() -> None:
    for bad in ("nonsense", 42, {"targets": "not-a-list", "inverse": 7, "created": None}):
        change = rec.build("set_actor_transform", {"actor_path": "/x"}, bad if isinstance(bad, dict) else None, ok=True)
        assert change is not None
        assert change.targets == () or isinstance(change.targets, tuple)
        assert change.inverse == ()


def test_sidecar_from_ignores_a_listener_that_sends_none() -> None:
    assert rec.sidecar_from({"success": True, "result": {}}) is None
    assert rec.sidecar_from({"_ducky": {"v": 1}}) == {"v": 1}
    assert rec.sidecar_from({"_ducky": "junk"}) is None
    assert rec.sidecar_from(None) is None


# --- opaque commands -------------------------------------------------------------


OPAQUE_SIDECAR = {
    "v": 1,
    "command": "execute_python",
    "kind": "world",
    "facet": "opaque",
    "targets": [],
    "before": {"code": "spawn(5)", "diff": {"count": 5, "added": 5, "removed": 0, "moved": 0}},
    "inverse": None,
    "created": [
        {"kind": "actor", "id": f"G{i}", "guid": f"G{i}", "label": f"Cube{i}", "path": f"/Game/x.Cube{i}"}
        for i in range(5)
    ],
    "revertable": "auto",
    "reason": "",
    "summary": "+5 actors",
    "outcome": "ok",
}


def test_two_scripts_in_one_run_are_two_rows_not_one() -> None:
    """An opaque command is an event, not a target edited twice."""
    token = identity.bind(RunContext(run_id="r1", conv_id="c1", ducky_name="Artist"))
    try:
        first = rec.build("execute_python", {"code": "a"}, None, ok=True)
        second = rec.build("execute_python", {"code": "b"}, None, ok=True)
    finally:
        identity.reset(token)
    assert first.slot != second.slot
    assert first.slot == "uefn://opaque/execute_python/r1-1"
    assert second.slot == "uefn://opaque/execute_python/r1-2"


def test_an_opaque_slot_stays_inside_its_own_run() -> None:
    for run_id in ("r1", "r2"):
        token = identity.bind(RunContext(run_id=run_id, conv_id="c1"))
        try:
            change = rec.build("exec_console_command", {"command": "stat fps"}, None, ok=True)
        finally:
            identity.reset(token)
        assert change.slot == f"uefn://opaque/exec_console_command/{run_id}-1"


def test_a_script_that_spawned_actors_can_be_reverted_to_exactly_those_actors() -> None:
    change = rec.build("execute_python", {"code": "spawn(5)"}, OPAQUE_SIDECAR, ok=True)
    assert change.revertable == REVERT_AUTO
    assert [t["guid"] for t in change.created] == ["G0", "G1", "G2", "G3", "G4"]
    assert change.summary == "+5 actors"
    # The code is part of the record: an opaque change is only auditable if readable.
    assert change.before["code"] == "spawn(5)"


def test_a_script_with_no_sidecar_is_recorded_but_promises_no_undo() -> None:
    change = rec.build("execute_python", {"code": "print(1)"}, None, ok=True)
    assert change.applied and change.revertable != REVERT_AUTO
    assert change.reason
