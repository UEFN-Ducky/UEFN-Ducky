"""Bridge and panel must agree on the project root."""

from __future__ import annotations

from pathlib import Path

from backend.bridge import client


def test_uefnproject_file_selection_resolves_to_its_folder(tmp_path: Path) -> None:
    root = tmp_path / "Island"
    root.mkdir()
    marker = root / ".uefnproject"
    marker.write_text("{}", encoding="utf-8")
    assert client._normalize_project_root(str(marker)) == str(root.resolve())  # noqa: SLF001
    assert client._normalize_project_root(str(root)) == str(root.resolve())  # noqa: SLF001


def test_configured_roots_prefer_env_then_panel(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("UEFN_VSCODE_WORKSPACE_FOLDERS", raising=False)
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(tmp_path))
    roots = client._configured_project_roots()  # noqa: SLF001
    assert roots == [str(tmp_path.resolve())]


def test_absolute_path_under_a_recent_project_is_readable(monkeypatch, tmp_path: Path) -> None:
    active = tmp_path / "Active"
    other = tmp_path / "Roguelike"
    outside = tmp_path / "NotAProject"
    active.mkdir()
    other.mkdir()
    outside.mkdir()
    verse = other / "Content" / "Verse" / "ui.verse"
    verse.parent.mkdir(parents=True)
    verse.write_text("ok", encoding="utf-8")
    monkeypatch.delenv("UEFN_VSCODE_WORKSPACE_FOLDERS", raising=False)
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(active))
    monkeypatch.setattr(
        "frontend.ui_web.recent_projects.load_recent_projects",
        lambda: [str(active), str(other)],
    )
    assert client.resolve_workspace_path(str(verse)) == str(verse.resolve())
    try:
        client.resolve_workspace_path(str(outside / "secret.txt"))
    except ValueError:
        return
    raise AssertionError("path outside every project should be refused")
