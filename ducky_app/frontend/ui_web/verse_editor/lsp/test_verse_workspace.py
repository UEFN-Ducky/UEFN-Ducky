"""Tests for Verse workspace folder discovery (old + Digests/BuiltIn layouts)."""

from __future__ import annotations

from pathlib import Path

from frontend.ui_web.verse_editor.lsp.verse_workspace import (
    _expand_builtin_digest_folders,
    _saved_layout_folders,
    discover_verse_workspace,
    workspace_folder_fingerprint,
)


def _touch_digest(folder: Path, name: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.digest.verse").write_text(f"# {name}\n", encoding="utf-8")


def test_expand_builtin_digests_splits_into_package_roots(tmp_path: Path):
    builtin = tmp_path / "Digests" / "BuiltIn"
    for name in ("Fortnite", "UnrealEngine", "Verse"):
        _touch_digest(builtin / name, name)

    folders = _expand_builtin_digest_folders(
        [
            {
                "name": "Built-in Digests",
                "path": str(builtin),
            }
        ]
    )
    by_name = {f["name"]: Path(f["path"]).name for f in folders}
    assert by_name == {
        "/Fortnite.com": "Fortnite",
        "/UnrealEngine.com": "UnrealEngine",
        "/Verse.org": "Verse",
    }
    # Each root has the digest as a direct child (UEFN Core flat list).
    for f in folders:
        digests = list(Path(f["path"]).glob("*.digest.verse"))
        assert len(digests) == 1


def test_saved_layout_prefers_digests_builtin(tmp_path: Path):
    project = tmp_path / "MyIsland"
    content = project / "Content"
    content.mkdir(parents=True)
    saved = tmp_path / "VerseProject" / "MyIsland"
    for name in ("Fortnite", "UnrealEngine", "Verse"):
        _touch_digest(saved / "Digests" / "BuiltIn" / name, name)
    assets = saved / "Digests" / "MyIsland-Assets"
    _touch_digest(assets, "MyIsland-Assets")
    (saved / "vproject").mkdir(parents=True)

    folders = _saved_layout_folders(project, saved)
    paths = {Path(f["path"]).as_posix() for f in folders}
    assert any(p.endswith("/Content") for p in paths)
    assert any(p.endswith("/Digests/MyIsland-Assets") for p in paths)
    assert any(p.endswith("/Digests/BuiltIn/Fortnite") for p in paths)
    assert any(p.endswith("/Digests/BuiltIn/Verse") for p in paths)
    assert any(p.endswith("/vproject") for p in paths)
    # Must not keep the BuiltIn parent as a single root.
    assert not any(p.endswith("/Digests/BuiltIn") for p in paths)


def test_saved_layout_legacy_top_level_still_works(tmp_path: Path):
    project = tmp_path / "OldIsland"
    (project / "Content").mkdir(parents=True)
    saved = tmp_path / "VerseProject" / "OldIsland"
    for name in ("Fortnite", "UnrealEngine", "Verse"):
        _touch_digest(saved / name, name)
    _touch_digest(saved / "OldIsland-Assets", "Assets")
    (saved / "vproject").mkdir(parents=True)

    folders = _saved_layout_folders(project, saved)
    names = {f["name"] for f in folders}
    assert "/Fortnite.com" in names
    assert "/Verse.org" in names
    assert "OldIsland (Assets)" in names


def test_expand_missing_builtin_falls_back_to_fortnite_game(tmp_path: Path, monkeypatch):
    missing = tmp_path / "island" / "Digests" / "BuiltIn"
    shared = (
        tmp_path
        / "Local"
        / "UnrealEditorFortnite"
        / "Saved"
        / "VerseProject"
        / "FortniteGame"
        / "Digests"
        / "BuiltIn"
    )
    for name in ("Fortnite", "UnrealEngine", "Verse"):
        _touch_digest(shared / name, name)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

    folders = _expand_builtin_digest_folders(
        [{"name": "Built-in Digests", "path": str(missing)}]
    )
    by_name = {f["name"]: Path(f["path"]) for f in folders}
    assert set(by_name) == {"/Fortnite.com", "/UnrealEngine.com", "/Verse.org"}
    for path in by_name.values():
        assert path.is_dir()
        assert "FortniteGame" in path.parts
    # Dead BuiltIn root must not remain (causes abs: open failures).
    assert not any(f["name"] == "Built-in Digests" for f in folders)


def test_discover_expands_code_workspace_builtin(tmp_path: Path, monkeypatch):
    project = tmp_path / "uefnmcp"
    content = project / "Content"
    content.mkdir(parents=True)
    saved = tmp_path / "Local" / "UnrealEditorFortnite" / "Saved" / "VerseProject" / "uefnmcp"
    builtin = saved / "Digests" / "BuiltIn"
    for name in ("Fortnite", "UnrealEngine", "Verse"):
        _touch_digest(builtin / name, name)
    assets = saved / "Digests" / "uefnmcp-Assets"
    _touch_digest(assets, "uefnmcp-Assets")
    (saved / "vproject").mkdir(parents=True)
    (saved / "vproject" / "uefnmcp.vproject").write_text("{}", encoding="utf-8")

    ws = {
        "folders": [
            {"name": "Content", "path": str(content)},
            {"name": "Assets", "path": str(assets)},
            {"name": "vproject (read-only)", "path": str(saved / "vproject")},
            {"name": "Built-in Digests", "path": str(builtin)},
        ]
    }
    import json

    (project / "uefnmcp.code-workspace").write_text(json.dumps(ws), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

    result = discover_verse_workspace(str(project))
    names = [f["name"] for f in result["workspace_folders"]]
    assert "Built-in Digests" not in names
    assert "/Fortnite.com" in names
    assert "/UnrealEngine.com" in names
    assert "/Verse.org" in names
    assert "vproject (read-only)" in names


def test_workspace_fingerprint_changes_when_builtin_appears(tmp_path: Path, monkeypatch):
    import json

    project = tmp_path / "CardGame"
    content = project / "Content"
    content.mkdir(parents=True)
    builtin = (
        tmp_path
        / "Local"
        / "UnrealEditorFortnite"
        / "Saved"
        / "VerseProject"
        / "CardGame"
        / "Digests"
        / "BuiltIn"
    )
    (project / "CardGame.code-workspace").write_text(
        json.dumps(
            {
                "folders": [
                    {"name": "uefn.ducky@fortnite.com", "path": str(content)},
                    {"name": "Built-in Digests", "path": str(builtin)},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

    before = workspace_folder_fingerprint(str(project))
    assert "/Fortnite.com" not in before

    for name in ("Fortnite", "UnrealEngine", "Verse"):
        _touch_digest(builtin / name, name)

    after = workspace_folder_fingerprint(str(project))
    assert before != after
    assert "/Fortnite.com" in after
    assert "/Verse.org" in after


def test_refresh_editor_lsp_after_build_stops_sessions(monkeypatch):
    from frontend.ui_web.verse_editor import api as verse_api

    class Fake:
        stopped = False

        def stop_lsp(self, client_id=None):
            self.stopped = True

    fake = Fake()
    monkeypatch.setattr(verse_api, "_VERSE_EDITOR", fake)
    cleared = {"n": 0}

    def _clear() -> None:
        cleared["n"] = 1

    monkeypatch.setattr(
        "frontend.ui_web.project_files.invalidate_workspace_folders_cache",
        _clear,
    )
    verse_api.refresh_editor_lsp_after_build()
    assert fake.stopped
    assert cleared["n"] == 1
