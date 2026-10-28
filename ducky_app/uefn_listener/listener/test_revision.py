"""Revision control handlers: save before check-in, never sync or reload over unsaved work."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parent / "handlers" / "revision.py"


class FakePackage:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


class FakeFileState:
    def __init__(self, conflicted):
        self.conflicted = conflicted

    def get_editor_property(self, name):
        return {"is_conflicted": self.conflicted}[name]


class FakeWorld:
    def get_path_name(self):
        return "/MyIsland/MyMap.MyMap"


@pytest.fixture
def mod(monkeypatch, tmp_path):
    (tmp_path / "Content").mkdir()
    (tmp_path / "MyIsland.uefnproject").write_text("{}", encoding="utf-8")
    state = {
        "dirty": [],
        "saved": [],
        "save_ok": True,
        "checked_in": [],
        "check_in_ok": True,
        "synced": [],
        "sync_ok": True,
        "loaded": [],
        "blank_ok": True,
        "island": str(tmp_path),
        "conflicted": set(),
        "queried": [],
    }

    unreal = types.ModuleType("unreal")

    class EditorLoadingAndSavingUtils:
        @staticmethod
        def get_dirty_content_packages():
            return [p for p in state["dirty"] if not p.name.endswith("/MyMap")]

        @staticmethod
        def get_dirty_map_packages():
            return [p for p in state["dirty"] if p.name.endswith("/MyMap")]

        @staticmethod
        def save_packages(packages, only_dirty):
            state["saved"].append([p.name for p in packages])
            if state["save_ok"]:
                state["dirty"] = []
            return state["save_ok"]

        @staticmethod
        def new_blank_map(save_existing_map):
            assert save_existing_map is False
            state["loaded"].append("<blank>")
            return state["blank_ok"]

    class SourceControl:
        @staticmethod
        def check_in_files(files, description, silent):
            assert silent is True
            state["checked_in"].append((list(files), description))
            return state["check_in_ok"]

        @staticmethod
        def sync_files(files, silent):
            # The map must already be closed: a loaded actor keeps its file open.
            state["synced"].append((list(files), list(state["loaded"])))
            return state["sync_ok"]

        @staticmethod
        def query_file_states(files, silent):
            assert silent is True
            state["queried"].append(list(files))
            return [FakeFileState(f in state["conflicted"]) for f in files]

        @staticmethod
        def last_error_msg():
            return "remote said no"

        @staticmethod
        def current_provider():
            return "Lore"

        @staticmethod
        def is_available():
            return True

    class LevelEditorSubsystem:
        def load_level(self, path):
            state["loaded"].append(path)
            return True

    class EditorLevelLibrary:
        @staticmethod
        def get_editor_world():
            return FakeWorld()

    unreal.EditorLoadingAndSavingUtils = EditorLoadingAndSavingUtils
    unreal.SourceControl = SourceControl
    unreal.LevelEditorSubsystem = LevelEditorSubsystem
    unreal.EditorLevelLibrary = EditorLevelLibrary
    unreal.get_editor_subsystem = lambda cls: cls()

    pkg = types.ModuleType("listener")
    pkg.__path__ = []
    dispatch = types.ModuleType("listener.dispatch")
    dispatch.register = lambda name: (lambda fn: fn)
    project_paths = types.ModuleType("listener.project_paths")
    project_paths.content_root = lambda: "/MyIsland/"
    project_paths.island_directory = lambda: state["island"]
    for name, module in {
        "unreal": unreal,
        "listener": pkg,
        "listener.dispatch": dispatch,
        "listener.project_paths": project_paths,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location("revision_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._state = state
    return module


def test_status_lists_dirty_packages_and_provider(mod) -> None:
    mod._state["dirty"] = [FakePackage("/MyIsland/__ExternalActors__/MyMap/1/8X/ABC")]
    assert mod.cmd_revision_status() == {
        "island": mod._state["island"],
        "dirty": ["/MyIsland/__ExternalActors__/MyMap/1/8X/ABC"],
        "provider": "Lore",
        "available": True,
    }


def test_check_in_refuses_an_empty_description(mod) -> None:
    with pytest.raises(ValueError, match="description"):
        mod.cmd_revision_check_in(files=["C:/x.uasset"], description="  ")
    assert mod._state["checked_in"] == []


def test_check_in_saves_first_and_adds_the_islands_saved_packages(mod) -> None:
    mod._state["dirty"] = [FakePackage("/MyIsland/__ExternalActors__/MyMap/1/8X/ABC"), FakePackage("/Temp/Untitled")]
    out = mod.cmd_revision_check_in(files=["C:/Island/Content/A.uasset"], description="moved the cube")
    assert mod._state["saved"] == [["/MyIsland/__ExternalActors__/MyMap/1/8X/ABC", "/Temp/Untitled"]]
    files, description = mod._state["checked_in"][0]
    assert files == ["C:/Island/Content/A.uasset", "/MyIsland/__ExternalActors__/MyMap/1/8X/ABC"]
    assert description == "moved the cube"
    assert out["ok"] is True and out["checked_in"] == files and out["error"] == ""


def test_check_in_stops_when_the_save_fails(mod) -> None:
    mod._state["dirty"] = [FakePackage("/MyIsland/X")]
    mod._state["save_ok"] = False
    out = mod.cmd_revision_check_in(files=["C:/a"], description="d")
    assert out["ok"] is False and "saving" in out["error"]
    assert mod._state["checked_in"] == []


def test_check_in_reports_the_provider_error(mod) -> None:
    mod._state["check_in_ok"] = False
    out = mod.cmd_revision_check_in(files=["C:/a"], description="d")
    assert out == {"ok": False, "targets": ["C:/a"], "checked_in": [], "saved": [], "error": "remote said no"}


def test_nothing_to_check_in_is_ok_without_a_call(mod) -> None:
    out = mod.cmd_revision_check_in(files=[], description="d")
    assert out["ok"] is True and out["checked_in"] == []
    assert mod._state["checked_in"] == []


def test_sync_refuses_while_unsaved(mod) -> None:
    mod._state["dirty"] = [FakePackage("/MyIsland/X")]
    out = mod.cmd_revision_sync()
    assert out["ok"] is False and out["needs_save"] == ["/MyIsland/X"]
    assert mod._state["synced"] == [] and mod._state["loaded"] == []


def test_sync_pulls_content_and_descriptors_then_reloads_the_map(mod) -> None:
    out = mod.cmd_revision_sync()
    island = mod._state["island"].replace("\\", "/")
    assert mod._state["synced"] == [([f"{island}/Content/", f"{island}/MyIsland.uefnproject"], ["<blank>"])]
    assert mod._state["loaded"] == ["<blank>", "/MyIsland/MyMap"]
    assert out["ok"] is True and out["synced"] is True and out["reloaded"] is True


def test_forced_sync_never_reloads_over_unsaved_work(mod) -> None:
    mod._state["dirty"] = [FakePackage("/MyIsland/X")]
    out = mod.cmd_revision_sync(force=True)
    assert out["synced"] is True and out["reloaded"] is False and out["dirty"] == ["/MyIsland/X"]
    assert mod._state["loaded"] == []


def test_sync_without_reload(mod) -> None:
    out = mod.cmd_revision_sync(reload=False)
    assert out["synced"] is True and out["reloaded"] is False
    assert mod._state["loaded"] == []


def test_sync_failure_reports_the_provider_error(mod) -> None:
    mod._state["sync_ok"] = False
    out = mod.cmd_revision_sync()
    assert out["ok"] is False and out["error"] == "remote said no"
    # Never left on the blank map.
    assert mod._state["loaded"] == ["<blank>", "/MyIsland/MyMap"] and out["reloaded"] is True


def test_sync_stops_when_the_map_cannot_be_closed(mod) -> None:
    mod._state["blank_ok"] = False
    out = mod.cmd_revision_sync()
    assert out["ok"] is False and "close the map" in out["error"]
    assert mod._state["synced"] == []


class FakeActor:
    def __init__(self, package, label):
        self.package, self.label = package, label

    def get_package(self):
        return FakePackage(self.package)

    def get_actor_label(self):
        return self.label


def test_targets_name_each_actor_asset_and_folder_file(mod, monkeypatch, tmp_path) -> None:
    content = tmp_path / "Content"
    actor_file = content / "__ExternalActors__" / "MyMap" / "1" / "8X" / "ABC.uasset"
    actor_file.parent.mkdir(parents=True)
    actor_file.write_bytes(b"")
    (content / "MyMap.umap").write_bytes(b"")
    (content / "Materials").mkdir()
    (content / "Materials" / "M_Red.uasset").write_bytes(b"")
    actors = {
        "Cube": FakeActor("/MyIsland/__ExternalActors__/MyMap/1/8X/ABC", "VerifyCube"),
        "OldStyle": FakeActor("/MyIsland/MyMap", "OldStyle"),  # not World Partition: lives in the map
        "Unsaved": FakeActor("/MyIsland/__ExternalActors__/MyMap/2/AA/NEW", "Unsaved"),  # no file yet
    }
    lookup = type(sys)("listener.lookup")
    lookup.find_actor = actors.get
    monkeypatch.setitem(sys.modules, "listener.lookup", lookup)
    monkeypatch.setattr(sys.modules["listener"], "lookup", lookup, raising=False)

    out = mod.cmd_revision_targets(paths=[
        "Cube", "OldStyle", "Unsaved", "/MyIsland/Materials/M_Red.M_Red", "/MyIsland/Materials",
        "/Fortnite/Some/Asset", "/MyIsland/MyMap.MyMap:PersistentLevel.Gone", "",
    ])["targets"]

    def norm(p):
        return os.path.abspath(p).replace("\\", "/")

    assert out == {
        "Cube": {"file": norm(actor_file), "label": "VerifyCube"},
        "OldStyle": {"file": norm(content / "MyMap.umap"), "label": "OldStyle"},
        "/MyIsland/Materials/M_Red.M_Red": {"file": norm(content / "Materials" / "M_Red.uasset"), "label": "M_Red"},
        "/MyIsland/Materials": {"file": norm(content / "Materials"), "label": "Materials"},
    }


def test_targets_without_an_island_name_nothing(mod) -> None:
    mod._state["island"] = ""
    assert mod.cmd_revision_targets(paths=["Cube"]) == {"targets": {}}


def test_conflicts_names_the_files_revision_control_marks_conflicted(mod) -> None:
    mod._state["conflicted"] = {"C:/I/Content/a.verse"}
    out = mod.cmd_revision_conflicts(paths=["C:/I/Content/a.verse", "C:/I/Content/B.uasset", "", "C:/I/Content/a.verse"])
    assert out == {"conflicted": ["C:/I/Content/a.verse"], "error": ""}
    # one provider query for every file (fresh states, not the editor's cache)
    assert mod._state["queried"] == [["C:/I/Content/a.verse", "C:/I/Content/B.uasset"]]


def test_conflicts_without_paths_or_with_a_failing_provider(mod, monkeypatch) -> None:
    assert mod.cmd_revision_conflicts(paths=[]) == {"conflicted": [], "error": ""}
    assert mod._state["queried"] == []

    def boom(files, silent):
        raise RuntimeError("provider offline")

    monkeypatch.setattr(mod.unreal.SourceControl, "query_file_states", staticmethod(boom))
    out = mod.cmd_revision_conflicts(paths=["C:/I/Content/a.verse"])
    assert out == {"conflicted": [], "error": "remote said no"}  # the provider's own message first


def test_conflicts_fall_back_to_one_query_per_file(mod, monkeypatch) -> None:
    monkeypatch.delattr(mod.unreal.SourceControl, "query_file_states")
    mod.unreal.SourceControl.query_file_state = staticmethod(lambda f, silent: FakeFileState(f.endswith(".verse")))
    out = mod.cmd_revision_conflicts(paths=["C:/I/Content/a.verse", "C:/I/Content/B.uasset"])
    assert out["conflicted"] == ["C:/I/Content/a.verse"]
