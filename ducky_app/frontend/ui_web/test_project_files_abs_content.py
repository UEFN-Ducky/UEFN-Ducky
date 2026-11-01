"""A full path inside the island's Content is as writable as its Content/ form."""

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


def test_full_path_outside_content_stays_locked(tmp_path, monkeypatch):
    root = _island(tmp_path, monkeypatch)
    assert pf._is_workspace_locked_path((root / ".ducky").as_posix()) is True
    assert pf._is_workspace_locked_path(root.as_posix()) is True
    assert pf._is_workspace_locked_path((tmp_path / "Elsewhere").as_posix()) is True
    assert pf._is_workspace_locked_path(f"{pf.ABS_PATH_PREFIX}{(root / '.ducky').as_posix()}") is True


def test_dotdot_and_other_projects_stay_locked(tmp_path, monkeypatch):
    root = _island(tmp_path, monkeypatch)
    (tmp_path / "Other" / "Content").mkdir(parents=True)
    escape = f"{root.as_posix()}/Content/Probes/../../.ducky/x.md"
    assert pf._is_workspace_locked_path(escape) is True
    assert pf._is_workspace_locked_path(f"{pf.ABS_PATH_PREFIX}{escape}") is True
    assert pf._is_workspace_locked_path((tmp_path / "Other" / "Content" / "a.verse").as_posix()) is True


def test_full_path_keeps_locked_file_and_content_root_guards(tmp_path, monkeypatch):
    root = _island(tmp_path, monkeypatch)
    init = (root / "Content" / "Python" / "init_unreal.py").as_posix()
    assert pf.is_locked_project_file(init) is True
    assert pf.is_locked_project_file(f"{pf.ABS_PATH_PREFIX}{init}") is True
    assert pf._is_content_root_rel((root / "Content").as_posix()) is True
    assert pf._is_content_root_rel(f"{pf.ABS_PATH_PREFIX}{(root / 'Content').as_posix()}") is True
    assert pf._is_content_root_rel((root / "Content" / "Probes").as_posix()) is False
