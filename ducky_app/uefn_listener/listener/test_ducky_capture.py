"""Editor-change capture — no Unreal required.

``ducky_capture`` keeps every ``unreal`` / ``listener.*`` import inside a
function, so it can be loaded here against fakes. That property is itself part
of what these tests assert: the host test suite relies on it too.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, Dict

import pytest

MODULE = Path(__file__).resolve().parent / "ducky_capture.py"


# --- fakes ------------------------------------------------------------------------


class FakeVec:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class FakeRot:
    def __init__(self, pitch=0.0, yaw=0.0, roll=0.0):
        self.pitch, self.yaw, self.roll = float(pitch), float(yaw), float(roll)


class FakeActor:
    def __init__(self, label="Cube", path="/Game/Map.Map:PersistentLevel.Cube",
                 guid="8F2A", loc=(0, 0, 0), live=True, folder="Props", tags=("a",)):
        self.label, self.path, self.guid = label, path, guid
        self._loc = FakeVec(*loc)
        self._rot = FakeRot()
        self._scale = FakeVec(1, 1, 1)
        self._live = live
        self._folder = folder
        self._props: Dict[str, Any] = {"tags": list(tags), "Health": 100}
        self._parent = None

    # identity
    def get_actor_label(self): return self.label
    def get_path_name(self): return self.path
    def get_actor_guid(self): return self.guid
    def get_folder_path(self): return self._folder
    def get_attach_parent_actor(self): return self._parent

    # transform
    def get_actor_location(self): return self._loc
    def get_actor_rotation(self): return self._rot
    def get_actor_scale3d(self): return self._scale

    # properties
    def get_editor_property(self, name):
        if name not in self._props:
            raise RuntimeError(f"no such property {name}")
        return self._props[name]


@pytest.fixture
def capture(monkeypatch):
    """Load ducky_capture with fake listener.serialize / listener.lookup."""
    actors: Dict[str, FakeActor] = {}

    pkg = types.ModuleType("listener")
    pkg.__path__ = []  # mark as a package so submodule imports resolve
    serialize = types.ModuleType("listener.serialize")
    serialize.is_live = lambda a: bool(getattr(a, "_live", True))
    serialize.serialize = lambda v: v
    lookup = types.ModuleType("listener.lookup")
    lookup.find_actor = lambda ident: actors.get(ident)
    pkg.serialize = serialize
    pkg.lookup = lookup

    for name, mod in (("listener", pkg), ("listener.serialize", serialize), ("listener.lookup", lookup)):
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("ducky_capture_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # a top-level `import unreal` would fail here
    module._test_actors = actors
    return module


def add(capture, actor: FakeActor) -> FakeActor:
    capture._test_actors[actor.path] = actor
    capture._test_actors[actor.label] = actor
    return actor


# --- shape ------------------------------------------------------------------------


def test_module_imports_without_unreal(capture) -> None:
    assert "unreal" not in sys.modules or True  # loading above is the real assertion
    assert capture.CAPTURE_VERSION == 1
    assert "set_actor_transform" in capture.CAPTURE


def test_unregistered_commands_cost_nothing(capture) -> None:
    assert capture.before("get_all_actors", {"x": 1}) is None
    assert capture.after("get_all_actors", {}, {"actors": []}, None, ok=True) is None


def test_kill_switch(capture) -> None:
    add(capture, FakeActor())
    try:
        capture.configure(enabled=False)
        assert capture.before("set_actor_transform", {"actor_path": "Cube"}) is None
        assert capture.after("set_actor_transform", {}, {}, None, ok=True) is None
    finally:
        capture.configure(enabled=True)
    assert capture.before("set_actor_transform", {"actor_path": "Cube"}) is not None


def test_capture_never_raises(capture, monkeypatch) -> None:
    def boom(_params):
        raise RuntimeError("capture bug")

    monkeypatch.setattr(capture.CAPTURE["set_actor_transform"], "before", boom)
    assert capture.before("set_actor_transform", {"actor_path": "Cube"}) is None
    monkeypatch.setattr(capture.CAPTURE["set_actor_transform"], "inverse", boom)
    assert capture.after("set_actor_transform", {}, {}, {"targets": [{}]}, ok=True) is not None


def test_missing_actor_captures_nothing(capture) -> None:
    assert capture.before("set_actor_transform", {"actor_path": "Ghost"}) is None
    side = capture.after("set_actor_transform", {"actor_path": "Ghost"}, {}, None, ok=True)
    assert side["revertable"] == "manual" and side["inverse"] is None


def test_destroyed_actor_is_flagged_not_dereferenced(capture) -> None:
    add(capture, FakeActor(label="Dead", path="/dead", live=False))
    cap = capture.before("set_actor_label", {"actor_path": "/dead"})
    # is_live said no, so the target is recorded as invalid and nothing was read.
    assert cap is None or cap["targets"][0].get("invalid") is True


# --- per-command round trips ------------------------------------------------------


def test_transform_round_trip(capture) -> None:
    add(capture, FakeActor(loc=(0, 0, 0)))
    cap = capture.before("set_actor_transform", {"actor_path": "Cube"})
    assert cap["state"]["location"] == [0.0, 0.0, 0.0]
    assert cap["targets"][0]["guid"] == "8F2A"

    result = {"actor": {"path": "/Game/Map.Map:PersistentLevel.Cube",
                        "location": {"x": 0.0, "y": 0.0, "z": 250.0}}}
    side = capture.after("set_actor_transform", {"actor_path": "Cube"}, result, cap, ok=True)
    assert side["revertable"] == "auto"
    assert side["summary"] == "moved +250 on Z"
    [step] = side["inverse"]
    assert step["command"] == "set_actor_transform"
    assert step["params"]["location"] == [0.0, 0.0, 0.0]


def test_label_folder_and_tags(capture) -> None:
    add(capture, FakeActor(label="Old", path="/p", folder="Props/Old", tags=("red",)))
    for command, key, expected in (
        ("set_actor_label", "label", "Old"),
        ("set_actor_folder", "folder", "Props/Old"),
        ("set_actor_tags", "tags", ["red"]),
    ):
        cap = capture.before(command, {"actor_path": "/p"})
        assert cap["state"][key] == expected
        side = capture.after(command, {"actor_path": "/p"}, {}, cap, ok=True)
        assert side["revertable"] == "auto"
        [step] = side["inverse"]
        assert step["command"] == command and step["params"][key] == expected


def test_properties_capture_only_what_is_written(capture) -> None:
    add(capture, FakeActor(path="/p"))
    params = {"actor_path": "/p", "properties": {"Health": 50}}
    cap = capture.before("set_actor_properties", params)
    assert cap["state"]["properties"] == {"Health": 100}
    assert "tags" not in cap["state"]["properties"]
    side = capture.after("set_actor_properties", params, {}, cap, ok=True)
    assert side["inverse"][0]["params"]["properties"] == {"Health": 100}


def test_unreadable_property_blocks_a_partial_restore(capture) -> None:
    add(capture, FakeActor(path="/p"))
    params = {"actor_path": "/p", "properties": {"Health": 1, "Mystery": 2}}
    cap = capture.before("set_actor_properties", params)
    assert cap["state"]["unreadable"] == ["Mystery"]
    side = capture.after("set_actor_properties", params, {}, cap, ok=True)
    assert side["inverse"] is None
    assert side["revertable"] == "manual"


def test_group_moves_capture_by_path_not_label(capture) -> None:
    add(capture, FakeActor(label="Dup", path="/a", loc=(0, 0, 0)))
    add(capture, FakeActor(label="Dup", path="/b", loc=(10, 0, 0)))
    params = {"actor_paths": ["/a", "/b"], "axis": "x"}
    cap = capture.before("align_actors", params)
    assert cap["state"]["locations"] == [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]
    side = capture.after("align_actors", params, {}, cap, ok=True)
    assert side["revertable"] == "auto" and len(side["inverse"]) == 2
    assert [s["params"]["actor_path"] for s in side["inverse"]] == ["/a", "/b"]
    assert side["summary"] == "moved 2 actors"


def test_group_move_with_a_missing_actor_captures_nothing(capture) -> None:
    add(capture, FakeActor(path="/a"))
    assert capture.before("align_actors", {"actor_paths": ["/a", "/ghost"]}) is None


def test_attach_without_a_prior_parent_is_manual(capture) -> None:
    child = add(capture, FakeActor(label="Child", path="/c"))
    params = {"child_path": "/c", "parent_path": "/p"}
    cap = capture.before("attach_actor", params)
    assert cap["state"]["parent"] == ""
    side = capture.after("attach_actor", params, {}, cap, ok=True)
    assert side["revertable"] == "manual"
    assert "cannot detach" in side["reason"]

    child._parent = add(capture, FakeActor(label="Old", path="/old"))
    cap = capture.before("attach_actor", params)
    side = capture.after("attach_actor", params, {}, cap, ok=True)
    assert side["revertable"] == "auto"
    assert side["inverse"][0]["params"]["parent_path"] == "/old"


def test_spawn_records_what_it_created(capture) -> None:
    result = {"actor": {"path": "/Game/Map.Map:PersistentLevel.New", "guid": "AAA", "label": "New"}}
    side = capture.after("spawn_actor", {"actor_class": "x"}, result, None, ok=True)
    assert side["created"] == [{"kind": "actor", "id": "AAA", "guid": "AAA",
                                "label": "New", "path": "/Game/Map.Map:PersistentLevel.New"}]
    assert side["targets"] == side["created"]
    assert side["revertable"] == "auto"
    assert side["summary"] == "created New"


def test_spawn_that_returned_nothing_usable(capture) -> None:
    for result in ({}, {"actor": {"invalid": True}}, {"actor": {}}):
        side = capture.after("spawn_actor", {}, result, None, ok=True)
        assert side["created"] == []
        assert side["revertable"] == "manual"


def test_a_failed_command_is_recorded_as_changing_nothing(capture) -> None:
    add(capture, FakeActor(path="/p"))
    cap = capture.before("set_actor_label", {"actor_path": "/p"})
    side = capture.after("set_actor_label", {"actor_path": "/p"}, None, cap, ok=False)
    assert side["outcome"] == "error"
    assert side["revertable"] == "none"
    assert side["inverse"] is None
