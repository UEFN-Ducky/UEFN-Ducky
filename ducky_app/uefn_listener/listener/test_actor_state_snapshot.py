"""The level snapshot that brackets an opaque command, and the diff over it.

Both are loaded against fakes: the point under test is the selection, ordering,
capping and tolerance logic, none of which needs a running editor.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parent / "registry" / "device_graph.py"


class FakeVec:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def as_dict(self):
        return {"x": self.x, "y": self.y, "z": self.z}


class FakeClass:
    def __init__(self, name):
        self._name = name

    def get_name(self):
        return self._name


class FakeActor:
    def __init__(self, label, path, *, guid="", device=False, cls="StaticMeshActor",
                 loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1), folder="", tags=(), parent=None):
        self.label, self.path, self.guid, self.device = label, path, guid, device
        self._cls = FakeClass(cls)
        self._loc, self._rot, self._scale = FakeVec(*loc), FakeVec(*rot), FakeVec(*scale)
        self._folder, self._tags, self._parent = folder, list(tags), parent

    def get_actor_label(self):
        return self.label

    def get_path_name(self):
        return self.path

    def get_class(self):
        return self._cls

    def get_actor_location(self):
        return self._loc

    def get_actor_rotation(self):
        return self._rot

    def get_actor_scale3d(self):
        return self._scale

    def get_folder_path(self):
        return self._folder

    def get_attach_parent_actor(self):
        return self._parent

    def get_editor_property(self, name):
        if name == "tags":
            return self._tags
        raise RuntimeError(name)


@pytest.fixture
def graph(monkeypatch):
    actors: list = []

    unreal = types.ModuleType("unreal")
    pkg = types.ModuleType("listener")
    pkg.__path__ = []

    lookup = types.ModuleType("listener.lookup")
    lookup.actor_list = lambda: list(actors)
    device_editor = types.ModuleType("listener.device_editor")
    device_editor.get_device_settings = lambda *a, **k: {}
    device_editor.is_creative_device = lambda a: bool(getattr(a, "device", False))
    device_editor.list_creative_devices = lambda *a, **k: []
    dispatch = types.ModuleType("listener.dispatch")
    dispatch.register = lambda name: (lambda fn: fn)
    serialize = types.ModuleType("listener.serialize")
    serialize.serialize = lambda v: v.as_dict() if hasattr(v, "as_dict") else v
    serialize.actor_guid = lambda a: getattr(a, "guid", "")
    verse_editable = types.ModuleType("listener.verse_editable_editor")
    verse_editable.get_verse_editables = lambda *a, **k: {}

    modules = {
        "unreal": unreal,
        "listener": pkg,
        "listener.lookup": lookup,
        "listener.device_editor": device_editor,
        "listener.dispatch": dispatch,
        "listener.serialize": serialize,
        "listener.verse_editable_editor": verse_editable,
    }
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("device_graph_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._test_actors = actors
    return module


def cube(name, **kw):
    return FakeActor(name, f"/Game/Map.Map:PersistentLevel.{name}", guid=f"G-{name}", **kw)


# --- scope -------------------------------------------------------------------------


def test_devices_scope_is_unchanged_and_stays_the_default(graph) -> None:
    graph._test_actors[:] = [cube("Timer", device=True), cube("Prop")]
    snap = graph.actor_state_snapshot()
    assert [a["label"] for a in snap["actors"]] == ["Timer"]
    assert snap["scope"] == "devices"


def test_a_named_actor_is_included_even_when_it_is_not_a_device(graph) -> None:
    graph._test_actors[:] = [cube("Timer", device=True), cube("Prop")]
    snap = graph.actor_state_snapshot(labels=["Prop"])
    assert [a["label"] for a in snap["actors"]] == ["Prop"]


def test_all_scope_walks_every_actor(graph) -> None:
    graph._test_actors[:] = [cube("Timer", device=True), cube("Prop"), cube("Rock")]
    snap = graph.actor_state_snapshot(scope="all", limit=10)
    # A script that spawns props touches nothing a device filter would see.
    assert [a["label"] for a in snap["actors"]] == ["Prop", "Rock", "Timer"]
    assert snap["scope"] == "all"


def test_a_level_too_large_is_refused_rather_than_walked(graph) -> None:
    graph._test_actors[:] = [cube(f"A{i}") for i in range(graph._SNAPSHOT_ACTOR_CEILING + 1)]
    snap = graph.actor_state_snapshot(scope="all", limit=10)
    assert snap["skipped"] == "level_too_large"
    assert snap["actors"] == [] and snap["count"] == 0
    assert snap["actor_count"] == graph._SNAPSHOT_ACTOR_CEILING + 1


def test_a_filtered_scan_is_allowed_however_big_the_level(graph) -> None:
    graph._test_actors[:] = [cube(f"A{i}") for i in range(graph._SNAPSHOT_ACTOR_CEILING + 1)]
    snap = graph.actor_state_snapshot(scope="all", label_filter="a1", limit=5)
    # The ceiling exists to stop an unbounded walk, not to break a targeted one.
    assert "skipped" not in snap and snap["count"] == 5


# --- ordering and capping -----------------------------------------------------------


def test_rows_are_sorted_before_the_cap_so_two_snapshots_are_comparable(graph) -> None:
    graph._test_actors[:] = [cube("C"), cube("A"), cube("B")]
    first = graph.actor_state_snapshot(scope="all", limit=2)
    # lookup.actor_list() order is not stable across ticks; the sort makes it so.
    graph._test_actors[:] = [cube("B"), cube("C"), cube("A")]
    second = graph.actor_state_snapshot(scope="all", limit=2)
    assert [a["label"] for a in first["actors"]] == ["A", "B"]
    assert [a["label"] for a in second["actors"]] == ["A", "B"]


def test_a_capped_snapshot_says_how_much_it_left_out(graph) -> None:
    graph._test_actors[:] = [cube(f"A{i}") for i in range(5)]
    snap = graph.actor_state_snapshot(scope="all", limit=2)
    assert snap["count"] == 2 and snap["truncated"] is True and snap["total"] == 5


# --- extra fields --------------------------------------------------------------------


def test_extra_fields_are_opt_in(graph) -> None:
    graph._test_actors[:] = [cube("Prop", folder="Props", tags=("hot",))]
    plain = graph.actor_state_snapshot(scope="all")["actors"][0]
    assert "guid" not in plain and "folder" not in plain

    rich = graph.actor_state_snapshot(scope="all", fields=["guid", "folder", "tags"])["actors"][0]
    assert rich["guid"] == "G-Prop" and rich["folder"] == "Props" and rich["tags"] == ["hot"]


def test_an_unknown_field_is_ignored_rather_than_failing(graph) -> None:
    graph._test_actors[:] = [cube("Prop")]
    row = graph.actor_state_snapshot(scope="all", fields=["guid", "wingspan"])["actors"][0]
    assert row["guid"] == "G-Prop" and "wingspan" not in row


# --- diff ----------------------------------------------------------------------------


def snap(*actors) -> dict:
    return {"actors": list(actors)}


def row(guid, path, label, loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    keys = ("x", "y", "z")
    return {
        "guid": guid,
        "path": path,
        "label": label,
        "location": dict(zip(keys, loc)),
        "rotation": dict(zip(("pitch", "yaw", "roll"), rot)),
        "scale": dict(zip(keys, scale)),
    }


def test_a_renamed_actor_is_one_actor_not_a_delete_and_an_add(graph) -> None:
    before = snap(row("G1", "/p/Old", "Old"))
    after = snap(row("G1", "/p/New", "New"))
    # Keyed on guid: the path changed, the actor did not.
    assert graph.actor_state_diff(before, after)["changes"] == []


def test_rows_without_a_guid_still_match_on_path(graph) -> None:
    before = snap(row("", "/p/A", "A"))
    after = snap(row("", "/p/A", "A", loc=(0, 0, 500)))
    [change] = graph.actor_state_diff(before, after)["changes"]
    assert change["change"] == "moved"


def test_a_rescale_is_visible(graph) -> None:
    before = snap(row("G1", "/p/A", "A"))
    after = snap(row("G1", "/p/A", "A", scale=(0.9, 1, 1)))
    # One shared epsilon of 1.0 treated a 0.1 scale delta as noise.
    [change] = graph.actor_state_diff(before, after)["changes"]
    assert change["change"] == "moved" and "scale" in change


def test_a_small_rotation_is_visible_but_a_millimetre_of_drift_is_not(graph) -> None:
    before = snap(row("G1", "/p/A", "A"))
    turned = snap(row("G1", "/p/A", "A", rot=(0, 2.0, 0)))
    nudged = snap(row("G1", "/p/A", "A", loc=(0.1, 0, 0)))
    assert graph.actor_state_diff(before, turned)["count"] == 1
    assert graph.actor_state_diff(before, nudged)["count"] == 0


def test_added_and_removed_carry_enough_to_act_on(graph) -> None:
    before = snap(row("G1", "/p/A", "A"))
    after = snap(row("G2", "/p/B", "B"))
    changes = {c["change"]: c for c in graph.actor_state_diff(before, after)["changes"]}
    # A revert needs the guid and path, not just a display label.
    assert changes["added"]["guid"] == "G2" and changes["added"]["path"] == "/p/B"
    assert changes["removed"]["guid"] == "G1"


def test_the_tolerances_are_reported_back(graph) -> None:
    result = graph.actor_state_diff(snap(), snap(), epsilon=2.0, rotation_epsilon=1.0, scale_epsilon=0.5)
    assert result["epsilon"] == 2.0
    assert result["rotation_epsilon"] == 1.0
    assert result["scale_epsilon"] == 0.5
