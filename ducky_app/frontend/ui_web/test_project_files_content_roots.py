"""Content sidebar roots: never cache an empty workspace; All projects abs: listing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import frontend.ui_web.project_files as pf


def test_large_directory_loads_project_settings_once(tmp_path: Path, monkeypatch):
    project = tmp_path / "Island"
    content = project / "Content"
    content.mkdir(parents=True)
    for index in range(100):
        (content / f"asset{index}.uasset").touch()
    root = Mock(return_value=project)
    monkeypatch.setattr(pf, "_project_root", root)
    monkeypatch.setattr(pf, "_show_hidden_project_files", lambda: False)
    entries = pf._list_directory_entries(content, content_tree=True)
    assert len(entries) == 100
    assert all(str(entry["path"]).startswith("Content/") for entry in entries)
    assert root.call_count == 1


def test_empty_workspace_is_not_cached(tmp_path: Path, monkeypatch):
    project = tmp_path / "Isle"
    project.mkdir()
    monkeypatch.setattr(pf, "_project_root", lambda: project)
    monkeypatch.setattr(pf, "discover_verse_workspace", lambda _root, **_kwargs: {"workspace_folders": []})
    pf._workspace_folders_cache.clear()
    assert pf._workspace_folders() == []
    assert str(project.resolve()) not in pf._workspace_folders_cache


def test_content_folder_is_injected_when_discover_omits_it(tmp_path: Path, monkeypatch):
    project = tmp_path / "Isle"
    content = project / "Content"
    content.mkdir(parents=True)
    monkeypatch.setattr(pf, "_project_root", lambda: project)
    monkeypatch.setattr(pf, "discover_verse_workspace", lambda _root, **_kwargs: {"workspace_folders": []})
    pf._workspace_folders_cache.clear()
    folders = pf._workspace_folders()
    assert len(folders) == 1
    assert Path(folders[0]["path"]).resolve() == content.resolve()


def test_other_project_content_is_listable(tmp_path: Path, monkeypatch):
    current = tmp_path / "A"
    other = tmp_path / "B"
    (current / "Content" / "Verse").mkdir(parents=True)
    verse = other / "Content" / "Verse"
    verse.mkdir(parents=True)
    (verse / "x.verse").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(pf, "_project_root", lambda: current)
    monkeypatch.setattr(
        pf,
        "_workspace_folders",
        lambda: [{"name": "A", "path": str((current / "Content").resolve())}],
    )
    other_content = (other / "Content").resolve()
    monkeypatch.setattr(
        pf,
        "_other_project_content_folders",
        lambda: [{"name": "B", "path": str(other_content)}],
    )
    encoded = pf._encode_abs_path(other_content)
    listing = pf.list_project_files(encoded)
    names = [entry["name"] for entry in listing["entries"]]
    assert "Verse" in names


def test_roots_tag_other_projects(tmp_path: Path, monkeypatch):
    current = tmp_path / "A"
    other = tmp_path / "B"
    (current / "Content").mkdir(parents=True)
    (other / "Content").mkdir(parents=True)
    monkeypatch.setattr(pf, "_project_root", lambda: current)
    monkeypatch.setattr(
        pf,
        "_workspace_folders",
        lambda: [{"name": "A", "path": str((current / "Content").resolve())}],
    )
    monkeypatch.setattr(
        pf,
        "_other_project_content_folders",
        lambda: [{"name": "B", "path": str((other / "Content").resolve())}],
    )
    roots = pf.list_project_files("__roots__")
    by_kind = {entry["kind"]: entry for entry in roots["entries"]}
    assert by_kind["content"]["name"] == "A"
    assert by_kind["project"]["name"] == "B"
    assert str(by_kind["project"]["path"]).startswith("abs:")
