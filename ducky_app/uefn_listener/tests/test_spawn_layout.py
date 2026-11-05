"""spawn_layout / remove_layout place and take back a JSON layout — no Unreal required."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_ACTORS = Path(__file__).resolve().parents[1] / "listener" / "handlers" / "actors.py"


class _Actor:
    def __init__(self, obj, loc, rot):
        self.cls, self.loc, self.rot = obj, loc, rot
        self.label = self.folder = ""
        self.scale = (1, 1, 1)
        self.props = {"tags": []}
        self.alive = True

    def set_actor_label(self, label):
        self.label = label

    def set_folder_path(self, folder):
        self.folder = folder

    def set_editor_property(self, name, value):
        self.props[name] = value

    def get_editor_property(self, name):
        return self.props[name]

    def set_actor_scale3d(self, v):
        self.scale = v


@pytest.fixture
def actors(monkeypatch):
    world: list[_Actor] = []
    transactions: list[str] = []

    unreal = types.ModuleType("unreal")
    unreal.Vector = lambda *xyz: tuple(xyz)
    unreal.Name = str
    unreal.Class = type("Class", (), {})
    unreal.EditorActorSubsystem = object

    class _Txn:
        def __init__(self, name):
            transactions.append(name)

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    unreal.ScopedEditorTransaction = _Txn

    def _spawn(obj, loc, rot):
        actor = _Actor(obj, loc, rot)
        world.append(actor)
        return actor

    unreal.EditorLevelLibrary = types.SimpleNamespace(spawn_actor_from_object=_spawn)

    def _destroy(actor):
        actor.alive = False
        world.remove(actor)

    unreal.get_editor_subsystem = lambda _cls: types.SimpleNamespace(destroy_actor=_destroy)

    listener = types.ModuleType("listener")
    lookup = types.ModuleType("listener.lookup")
    lookup.invalidate = lambda: None
    lookup.actor_list = lambda: list(world)
    listener.lookup = lookup
    listener.paging = types.ModuleType("listener.paging")
    dispatch = types.ModuleType("listener.dispatch")
    dispatch.register = lambda _name: (lambda fn: fn)
    device_editor = types.ModuleType("listener.device_editor")
    device_editor._device_kind = lambda _a: ""
    device_scale = types.ModuleType("listener.device_scale")
    device_scale.refuse_if_creative_device_scale = lambda *_a: None
    serialize = types.ModuleType("listener.serialize")
    serialize.ALL_ACTOR_FIELDS = ()
    serialize.is_live = lambda a: a is not None and a.alive
    serialize.rotator_pyr = lambda p, y, r: (p, y, r)
    serialize.serialize = serialize.serialize_actor = lambda *a, **_k: a[0]
    placeable = types.ModuleType("listener.island_placeable")
    placeable.resolve_content_drawer_placeable = lambda path: None if "Missing" in path else f"BP:{path}"

    for name, mod in {
        "unreal": unreal,
        "listener": listener,
        "listener.lookup": lookup,
        "listener.paging": listener.paging,
        "listener.dispatch": dispatch,
        "listener.device_editor": device_editor,
        "listener.device_scale": device_scale,
        "listener.serialize": serialize,
        "listener.island_placeable": placeable,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("_actors_under_test", _ACTORS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._test_world = world
    module._test_transactions = transactions
    return module


def _write(tmp_path: Path, rows, name="lamps.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(rows), encoding="utf-8")
    return str(path)


LAMP = "/Game/Creative/Props/CP_Streetlight_BlackMonday_01.CP_Streetlight_BlackMonday_01_C"


def test_places_three_entries_and_removes_them(actors, tmp_path) -> None:
    rows = [
        {"cls": LAMP, "loc": [0, 0, 0], "rot": [0, 90, 0], "label": "Lamp_1", "folder": "City/Lamps", "tags": ["street"]},
        {"cls": LAMP, "loc": [512, 0, 0], "scale": [1, 1, 2], "label": "Lamp_2"},
        {"cls": LAMP, "loc": [1024, 0, 0]},
    ]
    out = actors.cmd_spawn_layout(_write(tmp_path, rows), undo_tag="city:test")

    assert out["placed"] == 3 and out["failed"] == 0 and out["done"] is True
    world = actors._test_world
    assert len(world) == 3
    assert world[0].label == "Lamp_1" and world[0].folder == "City/Lamps" and world[0].rot == (0, 90, 0)
    assert world[0].props["tags"] == ["street", "city:test"]
    assert world[1].scale == (1, 1, 2) and world[1].loc == (512, 0, 0)
    assert all("city:test" in a.props["tags"] for a in world)

    gone = actors.cmd_remove_layout("city:test")
    assert gone == {"removed": 3, "remaining": 0, "done": True, "undo_tag": "city:test"}
    assert actors._test_world == []


def test_remove_layout_leaves_other_layouts_alone(actors, tmp_path) -> None:
    actors.cmd_spawn_layout(_write(tmp_path, [{"cls": LAMP}], "a.json"))
    actors.cmd_spawn_layout(_write(tmp_path, [{"cls": LAMP}, {"cls": LAMP}], "b.json"))

    assert actors.cmd_remove_layout("layout:a")["removed"] == 1
    assert len(actors._test_world) == 2


def test_chunks_resume_and_failures_are_counted(actors, tmp_path) -> None:
    rows = [{"cls": LAMP, "loc": [i * 100, 0, 0]} for i in range(5)]
    rows[2] = {"cls": "/Game/Creative/Missing.Missing_C"}
    path = _write(tmp_path, rows, "big.json")

    first = actors.cmd_spawn_layout(path, chunk=2, budget_sec=0)
    assert first["done"] is False and first["next_offset"] == 2 and first["placed"] == 2

    rest = actors.cmd_spawn_layout(path, offset=first["next_offset"], chunk=2)
    assert rest["done"] is True and rest["placed"] == 2 and rest["failed"] == 1
    assert rest["errors"][0]["index"] == 2
    assert len(actors._test_world) == 4
    # one editor transaction per chunk: [0,1] | [2,3] [4]
    assert len(actors._test_transactions) == 3
