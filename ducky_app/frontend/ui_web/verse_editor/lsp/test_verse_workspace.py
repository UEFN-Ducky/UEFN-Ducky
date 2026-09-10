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


# --- shadow .vproject: island digests never written, FortniteGame has them -------------

_BUILTIN_NAMES = ("Fortnite", "UnrealEngine", "Verse")


def _write_vproject(path: Path, builtin: Path, content: Path, assets: Path) -> None:
    import json

    packages: list[dict] = [
        {
            "desc": {
                "name": "Island",
                "dirPath": content.as_posix(),
                "settings": {"versePath": "/me@fortnite.com/Island", "allowExperimental": False},
            },
            "readOnly": False,
        },
        {
            "desc": {"name": "Island/Assets", "dirPath": assets.as_posix(), "settings": {}},
            "readOnly": True,
        },
    ]
    for name in _BUILTIN_NAMES:
        packages.append(
            {"desc": {"name": name, "dirPath": (builtin / name).as_posix(), "settings": {}}, "readOnly": True}
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"packages": packages}, indent=4), encoding="utf-8")


def _island_layout(tmp_path: Path, monkeypatch, *, island_builtin: bool, shared_builtin: bool):
    """UEFN-generated island: code-workspace + .vproject that both point at Digests/BuiltIn."""
    import json

    project = tmp_path / "Island"
    content = project / "Content"
    content.mkdir(parents=True)
    local = tmp_path / "Local"
    verse_project = local / "UnrealEditorFortnite" / "Saved" / "VerseProject"
    saved = verse_project / "Island"
    builtin = saved / "Digests" / "BuiltIn"
    assets = saved / "Digests" / "Island-Assets"
    _touch_digest(assets, "Island-Assets")
    if island_builtin:
        for name in _BUILTIN_NAMES:
            _touch_digest(builtin / name, name)
    if shared_builtin:
        for name in _BUILTIN_NAMES:
            _touch_digest(verse_project / "FortniteGame" / "Digests" / "BuiltIn" / name, name)
    vproject = saved / "vproject" / "Island.vproject"
    _write_vproject(vproject, builtin, content, assets)
    ws = {
        "folders": [
            {"name": "/me@fortnite.com/Island (Island)", "path": str(content)},
            {"name": "/me@fortnite.com/Island (Island/Assets)", "path": str(assets)},
            {"name": "vproject (read-only)", "path": str(saved / "vproject")},
            {"name": "Built-in Digests", "path": str(builtin)},
        ]
    }
    (project / "Island.code-workspace").write_text(json.dumps(ws), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return project, vproject, saved


def test_missing_island_digests_hand_verse_lsp_a_shadow_vproject(tmp_path: Path, monkeypatch):
    import json

    project, vproject, _saved = _island_layout(
        tmp_path, monkeypatch, island_builtin=False, shared_builtin=True
    )

    result = discover_verse_workspace(str(project))

    shadow = Path(result["vproject_shadow"])
    assert shadow.is_file()
    assert shadow.name == "Island.vproject"
    # Ducky-owned: lives in Ducky's AppData, never inside UEFN's Saved tree.
    assert "UEFN-Ducky" in shadow.parts
    assert "UnrealEditorFortnite" not in shadow.parts
    # verse-lsp resolves packages from whatever .vproject it can see: only the shadow may be
    # visible, both as the vproject workspace root and as the watched file.
    vproject_roots = [f for f in result["workspace_folders"] if f["name"].startswith("vproject")]
    assert [Path(f["path"]) for f in vproject_roots] == [shadow.parent]
    assert result["watch_files"] == [str(shadow.resolve())]
    assert str(vproject.resolve()) not in result["watch_files"]
    # Built-in packages now point at FortniteGame's digests; everything else is untouched.
    data = json.loads(shadow.read_text(encoding="utf-8"))
    by_name = {p["desc"]["name"]: p["desc"] for p in data["packages"]}
    for name in _BUILTIN_NAMES:
        target = Path(by_name[name]["dirPath"])
        assert "FortniteGame" in target.parts
        assert list(target.glob("*.digest.verse"))
    assert by_name["Island"]["dirPath"] == (project / "Content").as_posix()
    assert by_name["Island"]["settings"]["versePath"] == "/me@fortnite.com/Island"
    assert by_name["Island/Assets"]["dirPath"].endswith("Island-Assets")
    # The real file is left exactly as UEFN wrote it.
    assert "FortniteGame" not in vproject.read_text(encoding="utf-8")


def test_island_digests_arriving_restores_real_vproject_and_prunes_shadow(
    tmp_path: Path, monkeypatch
):
    project, vproject, saved = _island_layout(
        tmp_path, monkeypatch, island_builtin=False, shared_builtin=True
    )
    first = discover_verse_workspace(str(project))
    shadow = Path(first["vproject_shadow"])
    assert shadow.is_file()
    fp_before = workspace_folder_fingerprint(str(project))

    # UEFN's first Verse build writes the island's own digests.
    for name in _BUILTIN_NAMES:
        _touch_digest(saved / "Digests" / "BuiltIn" / name, name)

    second = discover_verse_workspace(str(project))
    assert second["vproject_shadow"] == ""
    assert second["watch_files"][0] == str(vproject.resolve())
    vproject_roots = [f for f in second["workspace_folders"] if f["name"].startswith("vproject")]
    assert [Path(f["path"]) for f in vproject_roots] == [vproject.parent]
    assert not shadow.exists()
    assert not shadow.parent.parent.exists()  # whole per-island shadow dir gone
    # Long-lived verse-lsp must be restarted on the real layout.
    assert workspace_folder_fingerprint(str(project)) != fp_before


def test_shadow_vproject_tracks_real_vproject_edits(tmp_path: Path, monkeypatch):
    import json

    project, vproject, _saved = _island_layout(
        tmp_path, monkeypatch, island_builtin=False, shared_builtin=True
    )
    shadow = Path(discover_verse_workspace(str(project))["vproject_shadow"])
    data = json.loads(vproject.read_text(encoding="utf-8"))
    data["packages"][0]["desc"]["settings"]["allowExperimental"] = True
    vproject.write_text(json.dumps(data, indent=4), encoding="utf-8")

    again = Path(discover_verse_workspace(str(project))["vproject_shadow"])

    assert again == shadow
    mirrored = json.loads(shadow.read_text(encoding="utf-8"))
    assert mirrored["packages"][0]["desc"]["settings"]["allowExperimental"] is True


def test_no_shared_digests_means_no_shadow(tmp_path: Path, monkeypatch):
    project, vproject, _saved = _island_layout(
        tmp_path, monkeypatch, island_builtin=False, shared_builtin=False
    )
    result = discover_verse_workspace(str(project))
    assert result["vproject_shadow"] == ""
    assert result["watch_files"][0] == str(vproject.resolve())


def test_island_with_own_digests_never_shadows(tmp_path: Path, monkeypatch):
    project, vproject, _saved = _island_layout(
        tmp_path, monkeypatch, island_builtin=True, shared_builtin=True
    )
    result = discover_verse_workspace(str(project))
    assert result["vproject_shadow"] == ""
    assert result["watch_files"][0] == str(vproject.resolve())
    assert not (tmp_path / "Local" / "UEFN-Ducky").exists()
