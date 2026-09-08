"""The revert-only delete path refuses anything it cannot prove is safe.

Ducky refuses deletion everywhere else. This command is the single exception,
so its guards are the tests that matter most here: the journal asking for it is
never enough on its own.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parent / "handlers" / "ducky_revert.py"


class FakeActor:
    def __init__(self, label="New", path="/Game/Map.Map:PersistentLevel.New", guid="AAA", live=True):
        self.label, self.path, self.guid, self._live = label, path, guid, live
        self.destroyed = False

    def get_actor_label(self):
        return self.label

    def get_path_name(self):
        return self.path


class FakeActorSubsystem:
    def __init__(self, sink):
        self._sink = sink

    def destroy_actor(self, actor):
        actor.destroyed = True
        self._sink.append(actor)


class FakeTransaction:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def mod(monkeypatch):
    state = {
        "actors": [],
        "assets": set(),
        "referencers": {},
        "deleted": [],
        "destroyed": [],
        "content_root": "/MyProject/",
        "transactions": [],
    }

    unreal = types.ModuleType("unreal")

    def scoped(name):
        state["transactions"].append(name)
        return FakeTransaction(name)

    unreal.ScopedEditorTransaction = scoped
    unreal.EditorActorSubsystem = object
    unreal.get_editor_subsystem = lambda _cls: FakeActorSubsystem(state["destroyed"])

    class EditorAssetLibrary:
        @staticmethod
        def does_asset_exist(path):
            return path in state["assets"]

        @staticmethod
        def delete_asset(path):
            state["deleted"].append(path)
            state["assets"].discard(path)

    unreal.EditorAssetLibrary = EditorAssetLibrary

    pkg = types.ModuleType("listener")
    pkg.__path__ = []
    lookup = types.ModuleType("listener.lookup")
    lookup.actor_list = lambda: list(state["actors"])
    lookup.find_actor = lambda ident: next(
        (a for a in state["actors"] if a.path == ident or a.label == ident), None
    )
    lookup.invalidate = lambda: None
    dispatch = types.ModuleType("listener.dispatch")
    dispatch.register = lambda name: (lambda fn: fn)
    logutil = types.ModuleType("listener.logutil")
    logutil.log_msg = lambda *a, **k: None
    project_paths = types.ModuleType("listener.project_paths")
    project_paths.content_root = lambda: state["content_root"]
    serialize = types.ModuleType("listener.serialize")
    serialize.is_live = lambda a: bool(getattr(a, "_live", True))
    serialize.actor_guid = lambda a: a.guid
    registry = types.ModuleType("listener.registry")
    registry.__path__ = []
    assets_pipeline = types.ModuleType("listener.registry.assets_pipeline")
    assets_pipeline.get_referencers = lambda path: {
        "referencers": list(state["referencers"].get(path, [])),
    }

    modules = {
        "unreal": unreal,
        "listener": pkg,
        "listener.lookup": lookup,
        "listener.dispatch": dispatch,
        "listener.logutil": logutil,
        "listener.project_paths": project_paths,
        "listener.serialize": serialize,
        "listener.registry": registry,
        "listener.registry.assets_pipeline": assets_pipeline,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location("ducky_revert_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._state = state
    return module


def call(mod, **kw):
    return mod.cmd_ducky_revert_creation(**kw)


# --- the five guards ---------------------------------------------------------------


def test_only_actor_and_asset_kinds(mod) -> None:
    for kind in ("", "level", "folder", "everything"):
        with pytest.raises(ValueError, match="Refused"):
            call(mod, kind=kind, id="/x")


def test_nothing_identified(mod) -> None:
    with pytest.raises(ValueError, match="nothing identified"):
        call(mod, kind="actor")


def test_a_recorded_guid_that_is_gone_refuses_rather_than_guessing(mod) -> None:
    mod._state["actors"] = [FakeActor(label="Different", path="/Game/Map.Map:PersistentLevel.New", guid="ZZZ")]
    with pytest.raises(ValueError) as exc:
        call(mod, kind="actor", id="/Game/Map.Map:PersistentLevel.New", guid="AAA")
    assert "no actor with guid" in str(exc.value)
    # The actor sitting at that path is a different one, and is left alone.
    assert mod._state["destroyed"] == []


def test_an_actor_is_removed_inside_a_transaction(mod) -> None:
    actor = FakeActor()
    mod._state["actors"] = [actor]
    result = call(mod, kind="actor", id=actor.path, guid="AAA")
    assert result == {"ok": True, "kind": "actor", "removed": "New", "guid": "AAA"}
    assert actor.destroyed and mod._state["destroyed"] == [actor]
    assert mod._state["transactions"] == ["Ducky revert creation"]


def test_a_missing_actor_refuses(mod) -> None:
    with pytest.raises(ValueError, match="actor not found"):
        call(mod, kind="actor", id="/nope")


def test_assets_outside_the_project_are_refused(mod) -> None:
    mod._state["assets"] = {"/Engine/Foo/Bar", "/Fortnite/Baz"}
    for path in ("/Engine/Foo/Bar", "/Fortnite/Baz"):
        with pytest.raises(ValueError) as exc:
            call(mod, kind="asset", id=path)
        assert "outside this project" in str(exc.value)
    assert mod._state["deleted"] == []


def test_a_referenced_asset_is_refused_with_the_reason(mod) -> None:
    path = "/MyProject/Materials/M_New"
    mod._state["assets"] = {path}
    mod._state["referencers"] = {path: ["/MyProject/Maps/Main", "/MyProject/Props/Cube"]}
    with pytest.raises(ValueError) as exc:
        call(mod, kind="asset", id=path)
    message = str(exc.value)
    assert "still used by 2" in message
    assert "/MyProject/Maps/Main" in message   # the panel shows why
    assert mod._state["deleted"] == []


def test_an_unreferenced_asset_in_the_project_is_removed(mod) -> None:
    path = "/MyProject/Materials/M_New"
    mod._state["assets"] = {path}
    result = call(mod, kind="asset", id=path)
    assert result == {"ok": True, "kind": "asset", "removed": path}
    assert mod._state["deleted"] == [path]


def test_an_asset_that_is_already_gone_is_not_an_error(mod) -> None:
    result = call(mod, kind="asset", id="/MyProject/Materials/M_Gone")
    assert result["ok"] is True and result["removed"] == ""
    assert mod._state["deleted"] == []


def test_a_referencer_check_that_fails_refuses_rather_than_deleting(mod) -> None:
    path = "/MyProject/Materials/M_New"
    mod._state["assets"] = {path}
    sys.modules["listener.registry.assets_pipeline"].get_referencers = (
        lambda _p: (_ for _ in ()).throw(RuntimeError("registry unavailable"))
    )
    with pytest.raises(ValueError) as exc:
        call(mod, kind="asset", id=path)
    assert "could not check what references" in str(exc.value)
    assert mod._state["deleted"] == []


def test_a_path_that_is_not_an_asset_path_is_refused(mod) -> None:
    with pytest.raises(ValueError, match="not an asset path"):
        call(mod, kind="asset", id="Materials/M_New")


def test_it_takes_one_id_and_has_no_bulk_form(mod) -> None:
    import inspect

    params = inspect.signature(mod.cmd_ducky_revert_creation).parameters
    assert set(params) == {"kind", "id", "guid"}
    # Every parameter is a single string: no list, so there is no way to ask this
    # command to remove more than one thing per call.
    assert [str(p.annotation) for p in params.values()] == ["str", "str", "str"]
