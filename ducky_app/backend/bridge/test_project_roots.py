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
