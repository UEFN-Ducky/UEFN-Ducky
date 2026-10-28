"""An agent's spawn_actor leaves the person's selection alone — no Unreal required.

The person and the agent work in the same editor: selecting each new actor replaced
whatever the person had selected, mid-task.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


class _Actor:
    def set_actor_label(self, label):
        self.label = label


class _ActorSubsystem:
    def __init__(self) -> None:
        self.selected = ["PersonsPick"]

    def set_selected_level_actors(self, actors):
        self.selected = list(actors)


@pytest.fixture()
def actors(monkeypatch):
    sub = _ActorSubsystem()
    spawned: list = []

    def spawn(obj, loc, rot):
        spawned.append(_Actor())
        return spawned[-1]

    unreal = types.ModuleType("unreal")
    unreal.Vector = lambda *a: a
    unreal.Class = type("Class", (), {})
    unreal.Name = str
    unreal.EditorActorSubsystem = object
    unreal.get_editor_subsystem = lambda cls: sub
    unreal.EditorLevelLibrary = types.SimpleNamespace(spawn_actor_from_object=spawn, spawn_actor_from_class=spawn)
    listener = types.ModuleType("listener")
    listener.__path__ = []
    lookup = types.ModuleType("listener.lookup")
    lookup.invalidate = lambda: None
    listener.lookup = lookup
    device_editor = types.ModuleType("listener.device_editor")
    device_editor._device_kind = lambda a: ""
    device_scale = types.ModuleType("listener.device_scale")
    device_scale.refuse_if_creative_device_scale = lambda *a: None
    dispatch = types.ModuleType("listener.dispatch")
    dispatch.register = lambda name: (lambda fn: fn)
    serialize = types.ModuleType("listener.serialize")
    serialize.ALL_ACTOR_FIELDS = ()
    serialize.is_live = lambda a: a is not None
    serialize.rotator_pyr = lambda *a: a
    serialize.serialize = lambda v: v
    serialize.serialize_actor = lambda a, fields=None: {"label": getattr(a, "label", "")}
    placeable = types.ModuleType("listener.island_placeable")
    placeable.resolve_content_drawer_placeable = lambda path: object()
    for name, mod in {
        "unreal": unreal, "listener": listener, "listener.lookup": lookup,
        "listener.device_editor": device_editor, "listener.device_scale": device_scale,
        "listener.dispatch": dispatch, "listener.serialize": serialize,
        "listener.island_placeable": placeable,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("actors_under_test", _HERE / "handlers" / "actors.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, sub, spawned


def test_a_spawn_keeps_the_persons_selection(actors):
    mod, sub, spawned = actors
    out = mod.cmd_spawn_actor(asset_path="/Game/Props/Rock", label="Rock")
    assert out["actor"]["label"] == "Rock" and len(spawned) == 1
    assert sub.selected == ["PersonsPick"]


def test_select_true_still_selects_the_new_actor(actors):
    mod, sub, spawned = actors
    mod.cmd_spawn_actor(asset_path="/Game/Props/Rock", select=True)
    assert sub.selected == spawned
