"""A full path anywhere inside the open island is as writable as its project-relative form;
only paths outside the project stay read-only (owner, Nov 11 2026: no Content-only rule)."""

from __future__ import annotations

from frontend.ui_web import project_files as pf


def _island(tmp_path, monkeypatch):
    root = tmp_path / "MyIsland"
    (root / "Content" / "Probes").mkdir(parents=True)
    monkeypatch.setattr(pf, "_project_root", lambda: root)
    monkeypatch.setattr(pf, "_active_is_folder", lambda: False)
    monkeypatch.setattr(pf, "_is_folder_root", lambda _root: False)
    return root


def test_full_path_inside_content_is_writable(tmp_path, monkeypatch):
    root = _island(tmp_path, monkeypatch)
    probe = (root / "Content" / "Probes").as_posix()
    assert pf._is_workspace_locked_path(probe) is False
    assert pf._is_workspace_locked_path(f"{pf.ABS_PATH_PREFIX}{probe}") is False
    assert pf._is_workspace_locked_path("Content/Probes") is False


def test_the_whole_island_is_writable_not_only_content(tmp_path, monkeypatch):
    root = _island(tmp_path, monkeypatch)
    for path in (root / ".ducky", root / "Saved" / "x.txt", root / "Content" / "Python" / "tool.py"):
        assert pf._is_workspace_locked_path(path.as_posix()) is False
        assert pf._is_workspace_locked_path(f"{pf.ABS_PATH_PREFIX}{path.as_posix()}") is False
    assert pf._is_workspace_locked_path(".ducky/notes.md") is False
    escape = f"{root.as_posix()}/Content/Probes/../../.ducky/x.md"  # still inside the island
    assert pf._is_workspace_locked_path(escape) is False


def test_paths_outside_the_project_stay_locked(tmp_path, monkeypatch):
    _island(tmp_path, monkeypatch)
    (tmp_path / "Other" / "Content").mkdir(parents=True)
    assert pf._is_workspace_locked_path((tmp_path / "Elsewhere").as_posix()) is True
    assert pf._is_workspace_locked_path((tmp_path / "Other" / "Content" / "a.verse").as_posix()) is True


def test_only_the_project_folder_itself_cannot_be_moved_or_deleted(tmp_path, monkeypatch):
    root = _island(tmp_path, monkeypatch)
    assert pf._is_project_root_rel(root.as_posix()) is True
    assert pf._is_project_root_rel(f"{pf.ABS_PATH_PREFIX}{root.as_posix()}") is True
    assert pf._is_project_root_rel("") is True
    assert pf._is_project_root_rel((root / "Content").as_posix()) is False
    assert pf._is_project_root_rel("Content") is False
    assert not hasattr(pf, "is_locked_project_file")  # init_unreal.py too: each start rewrites it
