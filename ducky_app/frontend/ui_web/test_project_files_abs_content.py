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
