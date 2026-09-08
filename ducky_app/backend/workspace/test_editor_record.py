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
