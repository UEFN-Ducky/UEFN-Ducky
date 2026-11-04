"""List commands page inside UEFN: a 12,000-actor world builds one page of rows, not all of them.

No Unreal required: the handlers load with stub ``unreal`` / sibling modules and a fake world.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
WORLD = 12_000
MB = 1024 * 1024


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


paging = _load("paging_under_test", _HERE / "paging.py")


class _Cls:
    def __init__(self, name: str) -> None:
        self._name = name

    def get_name(self) -> str:
        return self._name


class _Loc:
    def __init__(self, x: float) -> None:
        self.x, self.y, self.z = x, 0.0, 0.0


class _Actor:
    def __init__(self, i: int) -> None:
        self.i = i
        self._cls = _Cls("FortPlayerStart" if i % 10 == 0 else "StaticMeshActor")

    def get_class(self):
        return self._cls

    def get_actor_label(self) -> str:
        return f"Actor_{self.i}"

    def get_name(self) -> str:
        return f"StaticMeshActor_{self.i}"

    def get_path_name(self) -> str:
        return f"/Game/Maps/Island.Island:PersistentLevel.StaticMeshActor_{self.i}"

    def get_actor_location(self):
        return _Loc(float(self.i))


@pytest.fixture
def world(monkeypatch):
    """12,000 fake actors behind stub listener modules; ``built`` counts serialized rows."""
    actors = [_Actor(i) for i in range(WORLD)]
    built: list[int] = []

    def serialize_actor(a, fields=None):
        built.append(a.i)
        return {"label": a.get_actor_label(), "class": a.get_class().get_name(),
                "path": a.get_path_name(), "fields": list(fields or [])}

    unreal = types.ModuleType("unreal")
    unreal.Actor = _Actor
    unreal.Class = type("Class", (), {})
    unreal.EditorActorSubsystem = object
    unreal.get_editor_subsystem = lambda cls: types.SimpleNamespace(get_selected_level_actors=lambda: actors[:2000])
    listener = types.ModuleType("listener")
    listener.__path__ = []
    lookup = types.ModuleType("listener.lookup")
    lookup.actor_list = lambda: actors
    lookup.invalidate = lambda: None
    listener.lookup = lookup
    listener.paging = paging
    device_editor = types.ModuleType("listener.device_editor")
    device_editor._device_kind = lambda a: ""
    device_scale = types.ModuleType("listener.device_scale")
    device_scale.refuse_if_creative_device_scale = lambda *a: None
    dispatch = types.ModuleType("listener.dispatch")
    dispatch.register = lambda name: (lambda fn: fn)
    serialize = types.ModuleType("listener.serialize")
    serialize.ALL_ACTOR_FIELDS = ("label", "class", "path", "location")
    serialize.is_live = lambda a: a is not None
    serialize.rotator_pyr = lambda *a: a
    serialize.serialize = lambda v: v
    serialize.serialize_actor = serialize_actor
    for name, mod in {
        "unreal": unreal, "listener": listener, "listener.lookup": lookup, "listener.paging": paging,
        "listener.device_editor": device_editor, "listener.device_scale": device_scale,
        "listener.dispatch": dispatch, "listener.serialize": serialize,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    handlers = _load("actors_paging_under_test", _HERE / "handlers" / "actors.py")
    fort = _load("fort_paging_under_test", _HERE / "registry" / "fort.py")
    level = _load("level_paging_under_test", _HERE / "registry" / "level.py")
    return types.SimpleNamespace(actors=handlers, fort=fort, level=level, built=built)


# --------------------------------------------------------------------------- the helper


def test_window_defaults_and_clamps() -> None:
    assert paging.window() == (0, paging.DEFAULT_LIMIT)
    assert paging.window(-5, -1) == (0, paging.DEFAULT_LIMIT)
    assert paging.window(10, 10**9) == (10, paging.MAX_LIMIT)
    assert paging.window("x", "y", default=7) == (0, 7)


def test_page_builds_only_the_window() -> None:
    built: list[int] = []
    out = paging.page(range(100), lambda i: built.append(i) or i, offset=40, limit=10, keep=lambda i: i % 2 == 0)
    assert out["rows"] == list(range(80, 100, 2))[:10] and built == out["rows"]
    assert out["total"] == 50 and out["returned"] == 10 and out["truncated"] is False


def test_count_all_false_stops_after_the_page() -> None:
    seen: list[int] = []
    out = paging.page(range(10_000), lambda i: i, limit=5, keep=lambda i: seen.append(i) or True, count_all=False)
    assert out["rows"] == [0, 1, 2, 3, 4] and len(seen) == 6
    assert out["truncated"] is True and out["total_exact"] is False and out["next_offset"] == 5


# --------------------------------------------------------------------------- 12,000 actors


def test_default_census_builds_one_page_not_the_world(world) -> None:
    out = world.actors.cmd_get_all_actors()
    assert len(world.built) == paging.DEFAULT_LIMIT  # was 12,000 serialized rows
    assert out["total"] == WORLD and out["returned"] == len(out["actors"]) == paging.DEFAULT_LIMIT
    assert out["truncated"] is True and out["next_offset"] == paging.DEFAULT_LIMIT
    assert len(json.dumps({"success": True, "result": out})) < 2 * MB


def test_offset_pages_through_every_actor_once(world) -> None:
    seen: list[str] = []
    offset, pages = 0, 0
    while True:
        out = world.actors.cmd_get_all_actors(offset=offset, limit=1000)
        seen += [row["label"] for row in out["actors"]]
        pages += 1
        assert out["total"] == WORLD
        if not out["truncated"]:
            break
        offset = out["next_offset"]
    assert pages == 12 and len(seen) == WORLD == len(set(seen))
    assert len(world.built) == WORLD  # each actor built exactly once over all the pages


def test_filtered_census_counts_cheaply_and_builds_the_page(world) -> None:
    out = world.actors.cmd_get_all_actors(class_prefix="Fort", limit=50, fields=["label", "bogus"])
    assert out["total"] == WORLD // 10 and out["returned"] == 50 and len(world.built) == 50
    assert out["actors"][0]["fields"] == ["label"]
    out = world.actors.cmd_get_all_actors(label_filter="actor_1199")
    assert out["total"] == 11 and out["truncated"] is False  # Actor_1199, Actor_11990..11999


def test_selection_and_fort_list_and_radius_page_too(world) -> None:
    sel = world.actors.cmd_get_selected_actors(limit=100)
    assert sel["total"] == 2000 and sel["returned"] == 100
    world.built.clear()
    fort = world.fort.list_fort_actors()
    assert fort["total"] == WORLD // 10 and fort["returned"] == paging.DEFAULT_LIMIT
    assert len(world.built) == paging.DEFAULT_LIMIT and fort["class_prefix"] == "Fort"
    world.built.clear()
    near = world.level.get_actors_in_radius([0, 0, 0], 5000.0, offset=4000, limit=10)
    assert near["total"] == 5001 and [r["label"] for r in near["actors"]][0] == "Actor_4000"
    assert len(world.built) == 10 and near["radius"] == 5000.0


def test_a_huge_explicit_limit_is_clamped(world) -> None:
    out = world.actors.cmd_get_all_actors(limit=50_000)
    assert out["returned"] == paging.MAX_LIMIT == len(world.built)
