"""Content pane for a folder project: the folder root instead of an island's Content/."""

from __future__ import annotations

from pathlib import Path

import pytest

import frontend.ui_web.project_files as pf
from frontend import project_kind as pk


@pytest.fixture(autouse=True)
def _restore_workspace_env(monkeypatch):
    # Project switches export the workspace to the environment; restore it after each test.
    for key in ("UEFN_VSCODE_WORKSPACE_FOLDERS", "UEFN_DUCKY_PROJECT_ROOT"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "ducky-repo"
    (root / "src").mkdir(parents=True)
    (root / "Content").mkdir()  # a real folder that happens to be named Content
    (root / "node_modules" / "left-pad").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (root / "Content" / "notes.md").write_text("# notes\n", encoding="utf-8")
    (root / "level.umap").write_bytes(b"\0")
    (root / ".gitignore").write_text("node_modules\n", encoding="utf-8")
    from frontend.ui_web import project_switch

    pk.forget_cached_kind()
    project_switch.set_panel_project_root(str(root), push_ui=False)
    monkeypatch.setattr(pf, "_show_hidden_project_files", lambda: False)
    yield root.resolve()
    pk.forget_cached_kind()


def _names(listing: dict) -> list[str]:
    return [str(e["name"]) for e in listing["entries"]]


def test_roots_are_just_the_folder(repo: Path) -> None:
    roots = pf.list_workspace_roots()
    assert roots == [{"id": "ws:0", "name": repo.name, "path": str(repo), "read_only": False}]
    top = pf.list_project_files("")
    assert top["entries"][0]["kind"] == "content"
    assert top["entries"][0]["read_only"] is False
    assert pf.content_root_rel() == "."


def test_root_listing_shows_the_repo_not_uefn_rules(repo: Path) -> None:
    listing = pf.list_project_files(".")
    names = _names(listing)
    assert names == ["Content", "src", ".gitignore", "level.umap"]
    paths = {e["name"]: e["path"] for e in listing["entries"]}
    assert paths["src"] == "src"
    assert paths["Content"] == "Content"
    assert _names(pf.list_project_files("src")) == ["app.py"]
    assert _names(pf.list_project_files("ws:0")) == names


def test_show_hidden_reveals_tool_folders_but_never_git(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(pf, "_show_hidden_project_files", lambda: True)
    names = _names(pf.list_project_files("."))
    assert "node_modules" in names
    assert ".git" not in names


def test_edit_create_delete_in_a_folder_project(repo: Path) -> None:
    assert pf.read_project_file("src/app.py")["content"] == "print(1)\n"
    assert pf.create_project_file(".", "tool.py", "x = 1\n") == {"path": "tool.py"}
    assert pf.create_project_folder(".", "docs") == {"path": "docs"}
    assert pf.create_project_file("docs", "a.md") == {"path": "docs/a.md"}
    assert (repo / "tool.py").read_text(encoding="utf-8") == "x = 1\n"
    # A top-level folder named Content is ordinary here: it can be renamed and deleted.
    assert pf.rename_project_entry("Content", "Notes") == {"path": "Notes"}
    pf.delete_project_entry("Notes")
    assert not (repo / "Notes").exists()
    assert not (repo / ".undo-trash").exists()  # undo slots live in AppData, not the repo
    for bad in (".", ""):
        with pytest.raises(ValueError):
            pf.delete_project_entry(bad)
    with pytest.raises(ValueError):
        pf.rename_project_entry(".", "x")


def test_quick_open_index_is_root_relative(repo: Path) -> None:
    reply = pf.list_project_file_paths()
    assert reply["truncated"] is False and reply["total"] == len(reply["paths"])
    paths = set(reply["paths"])
    assert {"src/app.py", "Content/notes.md", ".gitignore", "level.umap"} <= paths
    assert not any(p.startswith(("node_modules/", ".git/")) for p in paths)


def test_island_listing_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    from frontend.ui_web import project_switch

    island = tmp_path / "Island"
    (island / "Content" / "Verse").mkdir(parents=True)
    (island / "Island.uefnproject").write_text("{}", encoding="utf-8")
    (island / "Content" / "Verse" / "a.verse").write_text("x", encoding="utf-8")
    (island / "Content" / "Island.umap").write_bytes(b"\0")
    (island / "src").mkdir()
    pk.forget_cached_kind()
    project_switch.set_panel_project_root(str(island), push_ui=False)
    monkeypatch.setattr(pf, "_show_hidden_project_files", lambda: False)
    assert pf.content_root_rel() == "Content"
    assert _names(pf.list_project_files("Content")) == ["Verse"]  # .umap hidden on islands
    assert pf.list_project_files("Content")["entries"][0]["path"] == "Content/Verse"
    # No island rules (owner, Nov 11 2026): .py and folders outside Content are files too.
    pf.create_project_file("Content", "scratch.py")
    assert (island / "Content" / "scratch.py").is_file()
    pf.list_project_files("src")
