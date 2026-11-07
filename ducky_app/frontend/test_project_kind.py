"""Folder projects: any folder can be a project, and UEFN island machinery never touches one."""

from __future__ import annotations

from pathlib import Path

import pytest

from frontend import deploy
from frontend import project_kind as pk
from frontend.project_kind import FOLDER, UEFN


@pytest.fixture(autouse=True)
def _fresh_kind_cache(monkeypatch):
    # Project switches export the workspace to the environment; restore it after each test.
    for key in ("UEFN_VSCODE_WORKSPACE_FOLDERS", "UEFN_DUCKY_PROJECT_ROOT"):
        monkeypatch.delenv(key, raising=False)
    pk.forget_cached_kind()
    yield
    pk.forget_cached_kind()


def _island(tmp_path: Path, name: str = "Island") -> Path:
    root = tmp_path / name
    (root / "Content" / "Verse").mkdir(parents=True)
    (root / f"{name}.uefnproject").write_text("{}", encoding="utf-8")
    return root


def _repo(tmp_path: Path) -> Path:
    """A plain code repo: root-level Python, a src/ tree, and a lowercase content/ folder."""
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "content").mkdir()
    (root / ".git").mkdir()
    (root / "conftest.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "pkg" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "src" / "pkg" / "__pycache__").mkdir()
    (root / "content" / "post.md").write_text("# hi\n", encoding="utf-8")
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return root


def _snapshot(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


# --------------------------------------------------------------------------- detection


def test_island_detected_by_uefnproject(tmp_path: Path) -> None:
    island = _island(tmp_path)
    assert pk.has_uefnproject(island)
    assert pk.resolve_project_root(island) == (island.resolve(), UEFN)
    assert pk.resolve_project_root(island / "Island.uefnproject") == (island.resolve(), UEFN)


def test_picking_island_content_opens_the_island(tmp_path: Path) -> None:
    island = _island(tmp_path)
    assert pk.resolve_project_root(island / "Content") == (island.resolve(), UEFN)


def test_new_folder_with_content_dir_is_a_folder_project(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert pk.resolve_project_root(repo) == (repo.resolve(), FOLDER)
    # A folder named content inside a repo is just a folder, never "the island above".
    assert pk.resolve_project_root(repo / "content") == ((repo / "content").resolve(), FOLDER)


def test_unreal_engine_project_is_a_folder_project(tmp_path: Path, monkeypatch) -> None:
    # A .uproject folder has Content/ like an island; an old row may even say "uefn".
    root = tmp_path / "MyGame"
    (root / "Content").mkdir(parents=True)
    (root / "MyGame.uproject").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pk, "_saved_kind", lambda _root: UEFN)
    assert pk.has_uproject(root) and not pk.has_uefnproject(root)
    assert pk.project_kind(root) == FOLDER
    assert pk.resolve_project_root(root) == (root.resolve(), FOLDER)


def test_drive_roots_and_files_are_refused(tmp_path: Path) -> None:
    anchor = Path(tmp_path.resolve().anchor)
    with pytest.raises(ValueError):
        pk.resolve_project_root(anchor)
    stray = tmp_path / "notes.txt"
    stray.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        pk.resolve_project_root(stray)


def test_islands_inside_lists_child_islands(tmp_path: Path) -> None:
    _island(tmp_path, "Alpha")
    _island(tmp_path, "Beta")
    (tmp_path / "loose").mkdir()
    names = [row["name"] for row in pk.islands_inside(tmp_path)]
    assert names == ["Alpha", "Beta"]


def test_unrecorded_content_only_root_keeps_the_old_island_rule(tmp_path: Path) -> None:
    legacy = tmp_path / "Legacy"
    (legacy / "Content").mkdir(parents=True)
    assert pk.project_kind(legacy) == UEFN
    plain = tmp_path / "Plain"
    plain.mkdir()
    assert pk.project_kind(plain) == FOLDER


# --------------------------------------------------------------------------- stored kinds


def test_saved_kind_upgrades_to_island_but_never_downgrades(tmp_path: Path) -> None:
    from backend.store.repos import projects

    repo = _repo(tmp_path)
    projects.touch(str(repo), kind="folder")
    assert projects.kind_for(str(repo)) == "folder"
    projects.touch(str(repo), kind="uefn")
    assert projects.kind_for(str(repo)) == "uefn"
    projects.touch(str(repo), kind="folder")
    assert projects.kind_for(str(repo)) == "uefn"


def test_rows_saved_before_kinds_are_islands(tmp_path: Path) -> None:
    from backend.store.repos import projects

    legacy = tmp_path / "OldIsland"
    legacy.mkdir()
    projects.touch(str(legacy))  # the pre-kind call shape
    assert projects.kind_for(str(legacy)) == "uefn"
    assert pk.project_kind(legacy) == UEFN


def test_json_store_keeps_kinds_and_grandfathers_old_paths(tmp_path: Path, monkeypatch) -> None:
    import json

    from frontend.ui_web import recent_projects as rp

    monkeypatch.setenv("DUCKY_STORE_BACKEND_PROJECTS", "files")
    old = tmp_path / "OldIsland"
    old.mkdir()
    rp._store_path().parent.mkdir(parents=True, exist_ok=True)
    rp._store_path().write_text(json.dumps({"projects": [str(old)]}), encoding="utf-8")
    assert rp.load_project_kind(str(old)) == "uefn"
    repo = _repo(tmp_path)
    rp.add_recent_project(str(repo), kind="folder")
    assert rp.load_project_kind(str(repo)) == "folder"
    assert rp.load_project_kind(str(old)) == "uefn"
    assert rp.load_recent_projects()[0] == str(repo.resolve())


def test_switching_to_a_folder_saves_its_kind(tmp_path: Path, monkeypatch) -> None:
    from frontend.ui_web import project_switch

    repo = _repo(tmp_path)
    info = project_switch.set_panel_project_root(str(repo), push_ui=False)
    assert info["path"] == str(repo.resolve())
    assert info["kind"] == "folder"
    assert info["content_root"] == "."
    rows = {row["path"]: row for row in project_switch.list_panel_projects()}
    assert rows[str(repo.resolve())]["kind"] == "folder"
    island = _island(tmp_path)
    info = project_switch.set_panel_project_root(str(island), push_ui=False)
    assert info["kind"] == "uefn" and info["content_root"] == "Content"


# --------------------------------------------------------------------------- island machinery never runs on a folder


def test_folder_python_is_never_quarantined(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    before = _snapshot(repo)
    monkeypatch.setattr(deploy, "quarantine_python_root", lambda: tmp_path / "q")
    assert deploy.quarantine_project_python(repo, deep=True) == []
    assert deploy.quarantine_project_python(repo, deep=False) == []
    assert _snapshot(repo) == before
    assert not (tmp_path / "q").exists()


def test_folder_never_gets_listener_init(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    before = _snapshot(repo)
    assert deploy.ensure_project_init(repo) == []
    assert deploy.remove_project_init(repo) == []
    assert deploy.enable_uefn_project_python(repo) is None
    assert _snapshot(repo) == before


def test_deploy_listener_leaves_a_folder_untouched(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    before = _snapshot(repo)
    monkeypatch.setattr(deploy, "quarantine_python_root", lambda: tmp_path / "q")
    monkeypatch.setattr("backend.mcp_plugins.epic.ensure_editor_auto_start", lambda: False)
    deploy.deploy_listener(repo, 4200, shared=False)
    assert _snapshot(repo) == before


def test_refresh_inits_writes_islands_only(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    island = _island(tmp_path)
    monkeypatch.setattr(
        "frontend.ui_web.recent_projects.load_saved_projects", lambda: [(str(repo), 0.0), (str(island), 0.0)]
    )
    monkeypatch.setattr(deploy, "install_user_init_unreal", lambda: None)
    monkeypatch.setattr(deploy, "install_toolset_listener_boot", lambda: None)
    before = _snapshot(repo)
    deploy.refresh_inits()
    assert (island / "Content" / "Python" / "init_unreal.py").is_file()
    assert _snapshot(repo) == before


def test_startup_deploy_skips_folders(tmp_path: Path, monkeypatch) -> None:
    from frontend.ui_web import project_deploy

    repo = _repo(tmp_path)
    island = _island(tmp_path)
    deployed: list[str] = []
    monkeypatch.setattr(project_deploy, "known_project_roots", lambda rescan=False: [str(repo), str(island)])
    monkeypatch.setattr(
        project_deploy, "deploy_listener", lambda root, _port, **_kw: deployed.append(str(root)) or []
    )
    lines = project_deploy.deploy_all_recent_projects()
    assert deployed == [str(island.resolve())]
    assert not any("Deploy skipped" in line for line in lines)


def test_removing_a_folder_project_keeps_its_files(tmp_path: Path, monkeypatch) -> None:
    from frontend.ui_web import project_switch

    repo = _repo(tmp_path)
    monkeypatch.setattr(deploy, "quarantine_python_root", lambda: tmp_path / "q")
    project_switch.set_panel_project_root(str(repo), push_ui=False)
    before = _snapshot(repo)
    project_switch.delete_panel_project(str(repo), push_ui=False)
    assert _snapshot(repo) == before


# --------------------------------------------------------------------------- write rules


def test_folder_write_rule_allows_python_blocks_git(tmp_path: Path) -> None:
    from backend.workspace.paths import require_writable_folder_path

    root = str(tmp_path)
    require_writable_folder_path(str(tmp_path / "src" / "app.py"), root)
    require_writable_folder_path(str(tmp_path / ".gitignore"), root)
    for bad in (tmp_path / ".git" / "config", tmp_path / "sub" / ".git" / "HEAD", tmp_path.parent / "x.py", tmp_path):
        with pytest.raises(ValueError):
            require_writable_folder_path(str(bad), root)


def test_writer_uses_folder_rules_only_for_saved_folder_projects(tmp_path: Path) -> None:
    from backend.workspace.writer import ProjectWriter
    from frontend.ui_web.recent_projects import add_recent_project

    repo = _repo(tmp_path)
    island = _island(tmp_path)
    unknown = tmp_path / "unknown"
    unknown.mkdir()
    add_recent_project(str(repo), kind="folder")

    ProjectWriter.for_root(str(repo)).write_text("src/pkg/new.py", "x = 2\n", tool="test")
    assert (repo / "src" / "pkg" / "new.py").read_text(encoding="utf-8") == "x = 2\n"
    with pytest.raises(ValueError):
        ProjectWriter.for_root(str(repo)).write_text(".git/config", "x", tool="test")
    # Islands and roots Ducky never recorded keep the island rules.
    for root in (island, unknown):
        with pytest.raises(ValueError):
            ProjectWriter.for_root(str(root)).write_text("scratch.py", "x", tool="test")
    ProjectWriter.for_root(str(island)).write_text("Content/Verse/a.verse", "x", tool="test")


# --------------------------------------------------------------------------- agents and UEFN status


def test_agent_prompts_tell_folder_projects_apart(tmp_path: Path) -> None:
    from backend.agent.coding_agents.mcp_inject import _folder_project_note
    from backend.agent.prompt import folder_project_note
    from frontend.ui_web.recent_projects import add_recent_project

    repo = _repo(tmp_path)
    island = _island(tmp_path)
    add_recent_project(str(repo), kind="folder")
    assert "folder project" in folder_project_note(str(repo))
    assert "folder project" in _folder_project_note(str(repo))
    assert folder_project_note(str(island)) == ""
    assert _folder_project_note(str(island)) == ""
    assert folder_project_note("") == ""


def test_uefn_open_on_an_island_is_no_mismatch_for_a_folder_project(tmp_path: Path) -> None:
    from backend.bridge.status import _project_from_health
    from frontend.ui_web.recent_projects import add_recent_project

    repo = _repo(tmp_path)
    island = _island(tmp_path)
    add_recent_project(str(repo), kind="folder")
    health = {"project_name": "Tycoony", "project_dir": str(tmp_path / "Tycoony")}
    assert _project_from_health(health, selected_project_root=str(repo))[2] is True
    assert _project_from_health(health, selected_project_root=str(island))[2] is False


def test_verse_tools_are_off_for_folder_projects(tmp_path: Path, monkeypatch) -> None:
    from frontend.ui_web import project_switch
    from frontend.ui_web.verse_editor.api import VerseEditorApi

    for key in ("UEFN_VSCODE_WORKSPACE_FOLDERS", "UEFN_DUCKY_PROJECT_ROOT"):
        monkeypatch.delenv(key, raising=False)
    repo = _repo(tmp_path)
    project_switch.set_panel_project_root(str(repo), push_ui=False)
    api = VerseEditorApi()
    assert api.start_lsp(client_id="t").get("folder_project") is True
    assert api.scan_verse_diagnostics(push_ui=False).get("folder_project") is True
    assert api.load_verse_diagnostics_cache()["files"] == []


# --------------------------------------------------------------------------- island identity
#
# UEFN mounts an island under its ROOT PLUGIN name and reports that as the live project.
# It is usually the folder name, but UEFN names some root plugins by a GUID.

_GUID = "86ef2167-1111-2222-3333-444444444444"


def _guid_island(tmp_path: Path, name: str = "MyProjectBJJJ") -> Path:
    import json

    root = tmp_path / name
    (root / "Content" / "Verse").mkdir(parents=True)
    manifest = {"title": name, "plugins": [{"name": _GUID, "bIsRoot": True, "bIsPublic": False}]}
    (root / f"{name}.uefnproject").write_text(json.dumps(manifest), encoding="utf-8")
    (root / f"{_GUID}.uplugin").write_text("{}", encoding="utf-8")
    return root


def test_identity_names_root_plugin_first(tmp_path: Path) -> None:
    island = _guid_island(tmp_path)
    names = pk.island_identity_names(island)
    assert names[0] == _GUID
    assert "MyProjectBJJJ" in names
    assert pk.island_mount_name(island) == _GUID
    assert pk.is_island_identity(island, _GUID.upper())
    assert pk.is_island_identity(island, "myprojectbjjj")
    assert not pk.is_island_identity(island, "Tycoony")
    assert not pk.is_island_identity(island, "")


def test_identity_prefers_the_bIsRoot_plugin(tmp_path: Path) -> None:
    import json

    root = tmp_path / "Island"
    root.mkdir()
    manifest = {
        "plugins": [
            {"name": "Helper", "bIsRoot": False},
            {"name": _GUID, "bIsRoot": True},
        ]
    }
    (root / "Island.uefnproject").write_text(json.dumps(manifest), encoding="utf-8")
    assert pk.island_mount_name(root) == _GUID
    assert pk.island_identity_names(root)[:2] == [_GUID, "Helper"]


def test_identity_falls_back_to_uplugin_then_folder(tmp_path: Path) -> None:
    old = tmp_path / "Dark"
    (old / "Plugins" / "DarkPlugin" / "Content").mkdir(parents=True)
    (old / "Dark.uefnproject").write_text("{}", encoding="utf-8")
    (old / "Plugins" / "DarkPlugin" / "DarkPlugin.uplugin").write_text("{}", encoding="utf-8")
    assert pk.island_mount_name(old) == "DarkPlugin"
    assert pk.island_identity_names(old) == ["DarkPlugin", "Dark"]

    bare = tmp_path / "Bare"
    bare.mkdir()
    (bare / "Bare.uefnproject").write_text("{not json", encoding="utf-8")
    assert pk.island_mount_name(bare) == "Bare"
    assert pk.island_identity_names(bare) == ["Bare"]
    assert pk.island_identity_names("") == []
    assert pk.island_mount_name("") == ""


def test_identity_cache_follows_the_manifest(tmp_path: Path) -> None:
    import json
    import os

    island = _guid_island(tmp_path)
    assert pk.island_mount_name(island) == _GUID
    assert pk.island_identity_names(island)[0] == _GUID
    desc = island / "MyProjectBJJJ.uefnproject"
    desc.write_text(json.dumps({"plugins": [{"name": "Renamed", "bIsRoot": True}]}), encoding="utf-8")
    st = desc.stat()
    os.utime(desc, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
    assert pk.island_identity_names(island)[0] == "Renamed"


def test_reserved_mounts(tmp_path: Path) -> None:
    assert pk.is_reserved_mount("Temp") and pk.is_reserved_mount("game")
    assert not pk.is_reserved_mount(_GUID) and not pk.is_reserved_mount("")


def test_recent_project_rows_carry_the_mount_and_resolve_by_it(tmp_path: Path, monkeypatch) -> None:
    from frontend.ui_web import project_switch

    island = _guid_island(tmp_path)
    repo = _repo(tmp_path)
    # Switching saves each project's kind (the repo's lowercase content/ would otherwise
    # read as an island on a case-insensitive disk).
    project_switch.set_panel_project_root(str(repo), push_ui=False)
    project_switch.set_panel_project_root(str(island), push_ui=False)
    rows = {row["path"]: row for row in project_switch.list_panel_projects()}
    assert rows[str(island.resolve())]["mount"] == _GUID
    assert rows[str(repo.resolve())]["mount"] == ""
    # The live UEFN name (a GUID no folder is called) resolves to the island.
    assert project_switch.resolve_panel_project_ref(name=_GUID) == str(island.resolve())
    assert project_switch.resolve_panel_project_ref(name="MyProjectBJJJ") == str(island.resolve())
    with pytest.raises(ValueError):
        project_switch.resolve_panel_project_ref(name="aaaaaaaa-0000-0000-0000-000000000000")


# --------------------------------------------------------------------------- flavor


def test_flavor_names_what_a_project_is_for_its_icon(tmp_path: Path) -> None:
    assert pk.project_flavor("") == ""
    assert pk.project_flavor(_island(tmp_path)) == UEFN
    plain = tmp_path / "notes"
    plain.mkdir()
    (plain / "todo.md").write_text("# hi\n", encoding="utf-8")
    assert pk.project_flavor(plain) == FOLDER

    rojo = tmp_path / "obby"
    rojo.mkdir()
    (rojo / "default.project.json").write_text("{}", encoding="utf-8")
    assert pk.project_flavor(rojo) == pk.ROBLOX

    place = tmp_path / "place"
    place.mkdir()
    (place / "Game.rbxlx").write_text("<roblox/>", encoding="utf-8")
    assert pk.project_flavor(place) == pk.ROBLOX

    unreal = tmp_path / "MyGame"
    (unreal / "Content").mkdir(parents=True)
    (unreal / "MyGame.uproject").write_text("{}", encoding="utf-8")
    assert pk.project_flavor(unreal) == pk.UNREAL

    unity = tmp_path / "UnityGame"
    (unity / "Assets").mkdir(parents=True)
    (unity / "ProjectSettings").mkdir()
    assert pk.project_flavor(unity) == pk.UNITY

    godot = tmp_path / "GodotGame"
    godot.mkdir()
    (godot / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    assert pk.project_flavor(godot) == pk.GODOT
