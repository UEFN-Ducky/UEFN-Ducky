"""An agent never changes what the other PC holds; reads, creations and unknown lock info pass."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import backend.bridge.client as bridge
from backend.bridge import island_locks

ISLAND = os.path.abspath("Islands/MyIsland")
CUBE = os.path.join(ISLAND, "Content", "__ExternalActors__", "Map", "1", "8X", "ABC.uasset")
MAT = os.path.join(ISLAND, "Content", "Materials", "M_Red.uasset")


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p)).replace("\\", "/")


class _Held(dict):
    calls: list[str]


@pytest.fixture
def locks(monkeypatch):
    held = _Held()
    calls: list[str] = []

    def others_locks(island):
        calls.append(island)
        return dict(held)

    monkeypatch.setattr(island_locks, "_watch", lambda: SimpleNamespace(others_locks=others_locks, norm_path=_norm))
    held.calls = calls
    return held


def _resolver(table):
    asked: list[list[str]] = []

    def resolve(idents):
        asked.append(list(idents))
        return {i: table[i] for i in idents if i in table}

    resolve.asked = asked  # type: ignore[attr-defined]
    return resolve


def _island():
    return ISLAND


def test_targets_reads_the_changed_params_only() -> None:
    assert island_locks.targets("align_actors", {"actor_paths": ["A", "B", "A"], "axis": "x"}) == ["A", "B"]
    assert island_locks.targets("wire_verse_device_ref", {"actor_path": "Dev", "target_path": "Other"}) == ["Dev"]
    assert island_locks.targets("assign_material_to_mesh", {"actor_path": "Cube", "material_path": "/I/M"}) == ["Cube"]
    assert island_locks.targets("set_material_flags", {"material_path": "/I/M"}) == ["/I/M"]
    assert island_locks.targets("export_asset", {"asset_path": "/I/M"}) == []


def test_the_actor_another_pc_holds_is_refused(locks) -> None:
    locks[_norm(CUBE)] = "Mich ou"
    resolve = _resolver({"Cube": {"file": CUBE, "label": "VerifyCube"}})
    out = island_locks.refusal("set_actor_transform", {"actor_path": "Cube"}, _island, resolve)
    assert out == "VerifyCube is being edited on the other PC by Mich ou: pick something else or wait"
    assert resolve.asked == [["Cube"]]


def test_several_locked_targets_name_the_first_and_count_the_rest(locks) -> None:
    other = CUBE.replace("ABC", "DEF")
    locks[_norm(CUBE)] = "Mich ou"
    locks[_norm(other)] = "Mich ou"
    resolve = _resolver({"A": {"file": CUBE, "label": "A"}, "B": {"file": other, "label": "B"}})
    out = island_locks.refusal("delete_actors", {"actor_paths": ["A", "B"]}, _island, resolve)
    assert out.startswith("A is being edited on the other PC by Mich ou") and "(1 more of its targets is locked too)" in out


def test_a_folder_holding_a_locked_file_is_refused(locks) -> None:
    locks[_norm(MAT)] = "Mich ou"
    folder = os.path.dirname(MAT)
    resolve = _resolver({"/MyIsland/Materials": {"file": folder, "label": "Materials"}})
    out = island_locks.refusal("delete_project_folder", {"directory": "/MyIsland/Materials"}, _island, resolve)
    assert out.startswith("Materials is being edited on the other PC by Mich ou")


def test_targets_nobody_else_holds_pass(locks) -> None:
    locks[_norm(MAT)] = "Mich ou"
    resolve = _resolver({"Cube": {"file": CUBE, "label": "VerifyCube"}})
    assert island_locks.refusal("set_actor_label", {"actor_path": "Cube", "label": "x"}, _island, resolve) == ""


def test_no_locks_costs_no_listener_round_trip(locks) -> None:
    resolve = _resolver({})
    assert island_locks.refusal("set_actor_transform", {"actor_path": "Cube"}, _island, resolve) == ""
    assert resolve.asked == []


@pytest.mark.parametrize(
    "command, params",
    [
        ("get_actor_properties", {"actor_path": "Cube"}),  # a read
        ("spawn_actor", {"asset_path": "/MyIsland/Materials/M_Red"}),  # a new actor has no lock
        ("duplicate_actor", {"actor_path": "Cube"}),  # the source only is read
        ("execute_python", {"code": "x"}),  # nothing to name
    ],
)
def test_reads_creations_and_unnamed_targets_never_look(locks, command, params) -> None:
    locks[_norm(CUBE)] = "Mich ou"
    locks[_norm(MAT)] = "Mich ou"
    resolve = _resolver({"Cube": {"file": CUBE, "label": "Cube"}})
    assert island_locks.refusal(command, params, _island, resolve) == ""
    assert resolve.asked == [] and locks.calls == []


def test_unknown_lock_info_fails_open(locks) -> None:
    locks[_norm(CUBE)] = "Mich ou"
    resolve = _resolver({"Cube": {"file": CUBE, "label": "Cube"}})
    assert island_locks.refusal("set_actor_tags", {"actor_path": "Cube"}, lambda: "", resolve) == ""

    def broken(_idents):
        raise RuntimeError("Unknown command: revision_targets")  # an older listener

    assert island_locks.refusal("set_actor_tags", {"actor_path": "Cube"}, _island, broken) == ""
    assert island_locks.refusal("set_actor_tags", {"actor_path": "Gone"}, _island, resolve) == ""


def test_locked_files_lists_pending_files_the_other_pc_holds(locks) -> None:
    pending = [CUBE.replace("\\", "/"), MAT.replace("\\", "/")]
    assert island_locks.locked_files(ISLAND, pending) == []
    locks[_norm(MAT)] = "Mich ou"
    assert island_locks.locked_files(ISLAND, pending) == [{"file": pending[1], "owner": "Mich ou"}]


# --------------------------------------------------------------------------- through send_command


@pytest.fixture
def listener(monkeypatch):
    posted: list[str] = []
    recorded: list[tuple] = []
    monkeypatch.setattr(bridge, "_pinned_port", None)
    monkeypatch.setattr(bridge, "configured_listener_port", lambda: 4200)
    monkeypatch.setattr(bridge, "listener_get_health", lambda _p, timeout=1.0: {"status": "ok", "island_dir": ISLAND})

    def post(_port, command, params, timeout=30.0):
        posted.append(command)
        if command == "revision_targets":
            return {"success": True, "result": {"targets": {"Cube": {"file": CUBE, "label": "VerifyCube"}}}}
        return {"success": True, "result": {"ok": True}}

    monkeypatch.setattr(bridge, "_post_json_locked", post)
    monkeypatch.setattr(bridge, "_record_editor_change",
                        lambda c, p, b, ok, error="": recorded.append((c, ok, error)))
    return SimpleNamespace(posted=posted, recorded=recorded)


def test_send_command_refuses_before_posting_and_records_it_blocked(listener, locks) -> None:
    locks[_norm(CUBE)] = "Mich ou"
    with pytest.raises(RuntimeError, match="refused: VerifyCube is being edited on the other PC by Mich ou"):
        bridge.send_command("set_actor_transform", {"actor_path": "Cube", "location": [0, 0, 0]})
    assert listener.posted == ["revision_targets"]
    assert listener.recorded == [("set_actor_transform", False,
                                  "refused: VerifyCube is being edited on the other PC by Mich ou: "
                                  "pick something else or wait")]
    assert locks.calls == [ISLAND]


def test_send_command_without_locks_posts_only_the_command(listener, locks) -> None:
    assert bridge.send_command("set_actor_transform", {"actor_path": "Cube"}) == {"ok": True}
    assert listener.posted == ["set_actor_transform"]
