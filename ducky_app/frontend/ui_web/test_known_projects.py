"""Every island on disk is a known project: listed, reachable by file tools, set up at boot."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from frontend.ui_web import known_projects


def _island(parent: Path, name: str, key_art: bool = True) -> Path:
    root = parent / name
    (root / "Content").mkdir(parents=True)
    data = {"title": name, "plugins": [{"name": name, "bIsRoot": True}]}
    if key_art:
        (root / "Resources").mkdir()
        (root / "Resources" / "Icon128.png").write_bytes(b"\x89PNG")
        data["keyArt"] = "Resources/Icon128.png"
    (root / f"{name}.uefnproject").write_text(json.dumps(data), encoding="utf-8")
    return root.resolve()


@pytest.fixture
def docs(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "Docs"
    (d / "Fortnite Projects").mkdir(parents=True)
    monkeypatch.setenv("UEFN_DUCKY_DOCUMENTS_DIR", str(d))
    known_projects.forget_cache()
    return d


def test_islands_in_fortnite_projects_and_beside_saved_ones_are_known(docs: Path, tmp_path: Path) -> None:
    from frontend.ui_web.recent_projects import add_recent_project

    fortnite = docs / "Fortnite Projects"
    a, b = _island(fortnite, "CardGame"), _island(fortnite, "Tycoony", key_art=False)
    (fortnite / "NotAnIsland").mkdir()
    elsewhere = tmp_path / "Elsewhere"
    saved = _island(elsewhere, "Saved")
    sibling = _island(elsewhere, "Sibling")
    repo = tmp_path / "repo"
    repo.mkdir()
    add_recent_project(str(saved), kind="uefn")
    add_recent_project(str(repo), kind="folder")

    roots = known_projects.known_project_roots(rescan=True)
    assert set(roots) == {str(a), str(b), str(saved), str(sibling), str(repo.resolve())}

    listing = known_projects.list_known_projects()
    by_path = {r["path"]: r for r in listing["projects"]}
    assert by_path[str(a)]["source"] == "uefn" and by_path[str(a)]["has_thumbnail"]
    assert not by_path[str(b)]["has_thumbnail"]
    assert by_path[str(repo.resolve())]["source"] == "other"
    assert by_path[str(saved)]["last_opened"] > 0 and by_path[str(a)]["last_opened"] == 0
    # The details bar: no duckies yet, and the listener isn't there until boot sets it up.
    assert "duckies" not in by_path[str(a)] and by_path[str(a)]["listener_ready"] is False
    (a / "Content" / "Python").mkdir()
    (a / "Content" / "Python" / "init_unreal.py").write_text("", encoding="utf-8")
    assert next(r for r in known_projects.list_known_projects()["projects"] if r["path"] == str(a))["listener_ready"]
    assert [s["id"] for s in listing["sources"]][:2] == ["uefn", "other"]
    assert known_projects.thumbnail_path(str(a)).endswith("Icon128.png")


def test_a_removed_island_stays_out_of_the_list(docs: Path) -> None:
    from frontend.ui_web.recent_projects import remove_recent_project

    gone = _island(docs / "Fortnite Projects", "Gone")
    kept = _island(docs / "Fortnite Projects", "Kept")
    remove_recent_project(str(gone))
    assert known_projects.known_project_roots(rescan=True) == [str(kept)]


def test_file_tools_reach_an_island_never_opened_in_ducky(docs: Path, tmp_path: Path, monkeypatch) -> None:
    from backend.bridge import client

    active = tmp_path / "Active"
    active.mkdir()
    island = _island(docs / "Fortnite Projects", "Unopened")
    monkeypatch.delenv("UEFN_VSCODE_WORKSPACE_FOLDERS", raising=False)
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(active))
    target = island / "Content" / "Verse" / "game.verse"
    assert client.resolve_workspace_path(str(target)) == os.path.realpath(target)


def test_the_boot_deploy_sets_up_every_discovered_island(docs: Path, monkeypatch) -> None:
    from frontend.ui_web import project_deploy

    islands = [_island(docs / "Fortnite Projects", n) for n in ("One", "Two", "Three")]
    deployed: list[str] = []
    monkeypatch.setattr(
        project_deploy, "deploy_listener", lambda root, _port, **_kw: deployed.append(str(root)) or []
    )
    project_deploy.deploy_all_recent_projects()
    assert sorted(deployed) == sorted(str(i) for i in islands)


def test_the_boot_deploy_refreshes_every_island_selected_first(docs: Path, monkeypatch) -> None:
    """Each start redeploys every island's listener (what a refused write left out lands now);
    the selected island does the once-per-process shared work."""
    from types import SimpleNamespace

    from frontend.ui_web import project_deploy

    islands = [_island(docs / "Fortnite Projects", n) for n in ("One", "Two", "Three")]
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        project_deploy, "deploy_listener",
        lambda root, _port, shared=True: calls.append((str(root), shared)) or [],
    )
    monkeypatch.setattr(project_deploy.PanelSettings, "load",
                        classmethod(lambda cls: SimpleNamespace(uefn_project_root=str(islands[1]))))
    project_deploy.deploy_all_recent_projects()
    assert sorted(root for root, _ in calls) == sorted(str(i) for i in islands)
    assert [shared for _, shared in calls] == [True, False, False]


def test_a_plugin_source_adds_a_tab_and_claims_its_folders(docs: Path, tmp_path: Path, monkeypatch) -> None:
    from frontend.ui_web.recent_projects import add_recent_project

    game = tmp_path / "RobloxGame"
    game.mkdir()
    add_recent_project(str(game), kind="folder")
    monkeypatch.setattr(
        known_projects,
        "_plugin_rows",
        lambda: ([{"id": "roblox:rojo", "label": "Roblox"}], [{"path": str(game.resolve()), "source": "roblox:rojo"}]),
    )
    listing = known_projects.list_known_projects(rescan=True)
    assert [s["label"] for s in listing["sources"]] == ["UEFN", "Other", "Roblox"]
    row = next(r for r in listing["projects"] if r["path"] == str(game.resolve()))
    assert row["source"] == "roblox:rojo"
