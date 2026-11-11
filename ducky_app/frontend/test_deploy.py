from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontend import deploy


@pytest.fixture(autouse=True)
def _isolate_init_refresh(monkeypatch, tmp_path_factory) -> None:
    deploy._shared_deploy_done = False
    monkeypatch.setattr("frontend.ui_web.recent_projects.load_saved_projects", lambda: [])
    monkeypatch.setattr(
        deploy, "documents_unreal_python_dir", lambda: tmp_path_factory.mktemp("docs_python")
    )
    monkeypatch.setattr(deploy, "install_toolset_listener_boot", lambda: None)


def _load_boot_file(monkeypatch):
    """Run the managed init_unreal.py against a fake ``unreal`` module."""
    import importlib.util
    import sys
    import types

    calls: dict[str, list] = {"log": [], "ticks": []}
    fake = types.SimpleNamespace(
        log=calls["log"].append,
        log_warning=lambda _msg: None,
        log_error=lambda _msg: None,
        register_slate_post_tick_callback=lambda fn: calls["ticks"].append(fn) or len(calls["ticks"]),
        unregister_slate_post_tick_callback=lambda _handle: None,
    )
    monkeypatch.setitem(sys.modules, "unreal", fake)
    spec = importlib.util.spec_from_file_location("ducky_boot_under_test", Path(deploy.__file__).with_name("init_unreal.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


def test_boot_file_starts_nothing_outside_uefn(monkeypatch) -> None:
    # Full Unreal Editor runs the Documents copy too (Unreal MCP needs Python); this test
    # process is python.exe, not UEFN.
    module, calls = _load_boot_file(monkeypatch)
    assert module._in_uefn() is False
    assert calls["ticks"] == []
    assert any("not UEFN" in line for line in calls["log"])
    monkeypatch.setattr(module, "_in_uefn", lambda: True)
    module._run()
    assert len(calls["ticks"]) == 1


def _listener_source(root: Path) -> Path:
    source = root / "source"
    package = source / "listener"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "config.py").write_text("PROTOCOL_VERSION = 'test'\n", encoding="utf-8")
    (package / "verse_editable_editor.py").write_text(
        "def verse_mangled_name(field: str) -> str:\n    return field\n",
        encoding="utf-8",
    )
    return source


def test_listener_sync_skips_identical_complete_deployment(tmp_path, monkeypatch) -> None:
    source = _listener_source(tmp_path)
    destination = tmp_path / "appdata" / "listener"
    monkeypatch.setattr(deploy, "_source_listener_dir", lambda: source)
    monkeypatch.setattr(deploy, "appdata_listener_dir", lambda: destination)

    assert deploy.sync_listener_to_appdata() == destination
    sentinel = destination / "keep-if-not-recopied"
    sentinel.write_text("present", encoding="utf-8")

    assert deploy.sync_listener_to_appdata() == destination
    assert sentinel.is_file()
    assert (destination / "listener" / "config.py").is_file()


def test_listener_sync_uses_process_unique_staging_tree(tmp_path, monkeypatch) -> None:
    source = _listener_source(tmp_path)
    destination = tmp_path / "appdata" / "listener"
    staged: list[Path] = []
    real_replace = deploy.os.replace

    monkeypatch.setattr(deploy, "_source_listener_dir", lambda: source)
    monkeypatch.setattr(deploy, "appdata_listener_dir", lambda: destination)

    def recording_replace(src: Path, dst: Path) -> None:
        staged.append(Path(src))
        real_replace(src, dst)

    monkeypatch.setattr(deploy.os, "replace", recording_replace)

    assert deploy.sync_listener_to_appdata() == destination
    assert len(staged) == 1
    assert staged[0].name.startswith("listener.tmp.")
    assert staged[0].name != "listener.tmp"


def test_enable_uefn_project_python_flips_flag(tmp_path: Path) -> None:
    path = tmp_path / "Island.uefnproject"
    path.write_text('{"dataSets": {"experimental": {}}}\n', encoding="utf-8")
    msg = deploy.enable_uefn_project_python(tmp_path)
    assert msg and "Island.uefnproject" in msg
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["dataSets"]["experimental"]["pythonExperimental"]["bEnablePythonForProject"] is True
    assert deploy.enable_uefn_project_python(tmp_path) is None


def test_deploy_listener_writes_init_and_keeps_it(tmp_path, monkeypatch) -> None:
    project = tmp_path / "Island"
    (project / "Content" / "Verse").mkdir(parents=True)
    (project / "Island.uefnproject").write_text("{}", encoding="utf-8")
    (project / "_junk.py").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "enable_uefn_project_python", lambda _p: None)
    monkeypatch.setattr(deploy, "install_toolset_listener_boot", lambda: None)
    monkeypatch.setattr(
        "backend.mcp_plugins.epic.ensure_editor_auto_start", lambda: False
    )
    import frontend.skill_deploy as skill_deploy

    monkeypatch.setattr(skill_deploy, "sync_skill_on_mcp_update", lambda: [])

    logs = deploy.deploy_listener(project, 4200)
    init = project / "Content" / "Python" / "init_unreal.py"
    assert init.is_file()
    assert "from listener.bootstrap import run" in init.read_text(encoding="utf-8")
    assert (project / "_junk.py").read_text(encoding="utf-8") == "x\n"  # the island's own .py stays
    assert any("init_unreal.py" in line for line in logs)


def test_a_listener_deploy_windows_refused_says_why(tmp_path, monkeypatch) -> None:
    """Content/Python refused by Controlled folder access: the island never got the listener."""
    project = tmp_path / "Island"
    (project / "Content").mkdir(parents=True)
    (project / "Island.uefnproject").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(deploy, "enable_uefn_project_python", lambda _p: None)
    monkeypatch.setattr("backend.mcp_plugins.epic.ensure_editor_auto_start", lambda: False)

    def refused(_root):
        raise PermissionError(13, "Access is denied", str(project / "Content" / "Python"))

    monkeypatch.setattr(deploy, "ensure_project_init", refused)
    asked: list = []
    monkeypatch.setattr("frontend.folder_access.refusal_hint", lambda path: asked.append(Path(path)) or "CFA HINT")
    logs = deploy.deploy_listener(project, 4200, shared=False)
    assert logs[-1] == "CFA HINT" and asked == [project / "Content" / "Python"]
    monkeypatch.setattr(deploy, "ensure_project_init", lambda _root: ["Wrote init_unreal.py"])
    assert "CFA HINT" not in deploy.deploy_listener(project, 4200, shared=False)


def test_enable_uefn_project_python_skips_missing_file(tmp_path: Path) -> None:
    assert deploy.enable_uefn_project_python(tmp_path) is None


def _island_with_python(tmp_path: Path) -> Path:
    project = tmp_path / "Island"
    (project / "Content" / "Python").mkdir(parents=True)
    (project / "Island.uefnproject").write_text("{}", encoding="utf-8")
    (project / "Content" / "Verse").mkdir(parents=True)
    (project / "Saved").mkdir()
    (project / "_probe_errs.py").write_text("print(1)\n", encoding="utf-8")
    (project / "Content" / "Python" / "init_unreal.py").write_text("# stub\n", encoding="utf-8")
    (project / "Content" / "Verse" / "foo.verse").write_text("using { /Verse.org/Simulation }\n", encoding="utf-8")
    (project / "Saved" / "x.py").write_text("# cook junk\n", encoding="utf-8")
    (project / "Content" / "Verse" / "nested.py").write_text("# deep scratch\n", encoding="utf-8")
    return project


def test_deploy_leaves_every_island_python_file_where_it_is(tmp_path, monkeypatch) -> None:
    """No quarantine any more (owner, Nov 11 2026): an agent's .py in the island stays put."""
    project = _island_with_python(tmp_path)
    monkeypatch.setattr(deploy, "enable_uefn_project_python", lambda _p: None)
    monkeypatch.setattr("backend.mcp_plugins.epic.ensure_editor_auto_start", lambda: False)
    logs = deploy.deploy_listener(project, 4200, shared=False)
    for rel in ("_probe_errs.py", "Content/Verse/nested.py", "Saved/x.py", "Content/Verse/foo.verse"):
        assert (project / rel).is_file(), rel
    assert not any("uarantine" in line for line in logs)
    assert not hasattr(deploy, "quarantine_project_python")


def test_ensure_project_init_replaces_stale(tmp_path: Path) -> None:
    project = tmp_path / "Island"
    dest = project / "Content" / "Python"
    dest.mkdir(parents=True)
    (project / "Island.uefnproject").write_text("{}", encoding="utf-8")
    (dest / "init_unreal.py").write_text("# old stub\n", encoding="utf-8")
    cache = dest / "__pycache__"
    cache.mkdir()
    (cache / "init_unreal.cpython-311.pyc").write_bytes(b"stale")
    logs = deploy.ensure_project_init(project)
    text = (dest / "init_unreal.py").read_text(encoding="utf-8")
    assert deploy._DUCKY_INIT_MARKER in text
    assert "from listener.bootstrap import run" in text
    assert any("Installed" in line for line in logs)
    assert not cache.exists()


def test_ensure_project_init_replaces_old_bootstrap_stub(tmp_path: Path) -> None:
    project = tmp_path / "Island"
    dest = project / "Content" / "Python"
    dest.mkdir(parents=True)
    (project / "Island.uefnproject").write_text("{}", encoding="utf-8")
    (dest / "init_unreal.py").write_text(
        "from listener.bootstrap import run\nrun()\n", encoding="utf-8"
    )
    deploy.ensure_project_init(project)
    text = (dest / "init_unreal.py").read_text(encoding="utf-8")
    assert deploy._DUCKY_INIT_MARKER in text
    assert "UEFN-Ducky managed init" in text


def test_install_user_init_replaces_old_ducky_file(tmp_path: Path, monkeypatch) -> None:
    py_dir = tmp_path / "UnrealEngine" / "Python"
    py_dir.mkdir(parents=True)
    (py_dir / "init_unreal.py").write_text(
        "# UEFN-Ducky user-level Python startup\nfrom listener.bootstrap import run\n",
        encoding="utf-8",
    )
    (py_dir / "ducky_init_unreal.py").write_text("# leftover helper\n", encoding="utf-8")
    cache = py_dir / "__pycache__"
    cache.mkdir()
    (cache / "init_unreal.cpython-311.pyc").write_bytes(b"stale")
    monkeypatch.setattr(deploy, "documents_unreal_python_dir", lambda: py_dir)
    msg = deploy.install_user_init_unreal()
    text = (py_dir / "init_unreal.py").read_text(encoding="utf-8")
    assert deploy._DUCKY_INIT_MARKER in text
    leftover = (py_dir / "ducky_init_unreal.py").read_text(encoding="utf-8")
    assert leftover.strip() == text.strip()
    assert not cache.exists()
    assert msg is not None


def test_install_user_init_shims_foreign_file(tmp_path: Path, monkeypatch) -> None:
    py_dir = tmp_path / "UnrealEngine" / "Python"
    py_dir.mkdir(parents=True)
    init = py_dir / "init_unreal.py"
    init.write_text("import unreal\nprint('mine')\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "documents_unreal_python_dir", lambda: py_dir)
    msg = deploy.install_user_init_unreal()
    existing = init.read_text(encoding="utf-8")
    assert "print('mine')" in existing
    assert "import ducky_init_unreal" in existing
    helper = (py_dir / "ducky_init_unreal.py").read_text(encoding="utf-8")
    assert deploy._DUCKY_INIT_MARKER in helper
    assert msg is not None and "Hooked" in msg

def test_remove_project_init_deletes_ducky_file(tmp_path: Path) -> None:
    project = tmp_path / "Island"
    dest = project / "Content" / "Python"
    dest.mkdir(parents=True)
    (project / "Island.uefnproject").write_text("{}", encoding="utf-8")
    (dest / "init_unreal.py").write_text(
        f"# {deploy._DUCKY_INIT_MARKER}\nfrom listener.bootstrap import run\n",
        encoding="utf-8",
    )
    logs = deploy.remove_project_init(project)
    assert not (dest / "init_unreal.py").exists()
    assert not dest.exists()
    assert any("Removed" in line for line in logs)


def test_appdata_uefn_ducky_dir_aliases_canonical() -> None:
    from frontend.app_paths import resolve_app_data_dir

    assert deploy.appdata_uefn_ducky_dir() == resolve_app_data_dir()


def test_remove_project_init_leaves_foreign_file(tmp_path: Path) -> None:
    project = tmp_path / "Island"
    dest = project / "Content" / "Python"
    dest.mkdir(parents=True)
    (project / "Island.uefnproject").write_text("{}", encoding="utf-8")
    init = dest / "init_unreal.py"
    init.write_text("# my own python\nprint('hi')\n", encoding="utf-8")
    deploy.remove_project_init(project)
    assert init.is_file()


def test_resolve_uefn_project_root_walks_up_from_content(tmp_path: Path) -> None:
    island = tmp_path / "Island"
    content = island / "Content"
    content.mkdir(parents=True)
    (island / ".uefnproject").write_text("{}", encoding="utf-8")
    assert deploy.resolve_uefn_project_root(content) == island.resolve()
    assert deploy.resolve_uefn_project_root(island) == island.resolve()


def test_resolve_uefn_project_root_rejects_unrelated_folder(tmp_path: Path) -> None:
    other = tmp_path / "NotAnIsland"
    other.mkdir()
    with pytest.raises(ValueError, match="UEFN project folder"):
        deploy.resolve_uefn_project_root(other)


def test_newer_app_version_recopies_even_when_dest_stamp_is_newer(tmp_path, monkeypatch) -> None:
    source = _listener_source(tmp_path)
    destination = tmp_path / "appdata" / "listener"
    monkeypatch.setattr(deploy, "_source_listener_dir", lambda: source)
    monkeypatch.setattr(deploy, "appdata_listener_dir", lambda: destination)
    monkeypatch.setattr(deploy, "_running_app_version", lambda: "1.2.131")

    assert deploy.sync_listener_to_appdata() == destination
    sentinel = destination / "keep-if-not-recopied"
    sentinel.write_text("present", encoding="utf-8")
    (destination / deploy._DEPLOY_STAMP_NAME).write_text("1.2.130\n9999999999.000\n", encoding="utf-8")

    assert deploy.sync_listener_to_appdata() == destination
    assert not sentinel.is_file()
    version, recency = deploy._read_deploy_stamp(destination)
    assert version == "1.2.131"
    assert recency == pytest.approx(deploy._source_recency(source), abs=0.001)


def test_overlay_does_not_bump_core_stamp(tmp_path, monkeypatch) -> None:
    source = _listener_source(tmp_path)
    destination = tmp_path / "appdata" / "listener"
    monkeypatch.setattr(deploy, "_source_listener_dir", lambda: source)
    monkeypatch.setattr(deploy, "appdata_listener_dir", lambda: destination)
    monkeypatch.setattr(deploy, "_overlay_plugin_listeners", lambda _dest: True)
    monkeypatch.setattr("backend.bridge.send_command", lambda *a, **k: {"ok": True})

    assert deploy.sync_listener_to_appdata() == destination
    before = (destination / deploy._DEPLOY_STAMP_NAME).read_text(encoding="utf-8")
    assert deploy.overlay_plugin_listeners_to_appdata(reload=False) is True
    assert (destination / deploy._DEPLOY_STAMP_NAME).read_text(encoding="utf-8") == before


def test_missing_verse_mangled_name_forces_copy(tmp_path, monkeypatch) -> None:
    source = _listener_source(tmp_path)
    destination = tmp_path / "appdata" / "listener"
    monkeypatch.setattr(deploy, "_source_listener_dir", lambda: source)
    monkeypatch.setattr(deploy, "appdata_listener_dir", lambda: destination)
    monkeypatch.setattr(deploy, "_running_app_version", lambda: "1.2.130")

    assert deploy.sync_listener_to_appdata() == destination
    sentinel = destination / "keep-if-not-recopied"
    sentinel.write_text("present", encoding="utf-8")
    (destination / "listener" / "verse_editable_editor.py").write_text(
        "def _mangled_name(field):\n    raise ValueError('Unknown Verse field')\n",
        encoding="utf-8",
    )
    src_recency = deploy._source_recency(source)
    (destination / deploy._DEPLOY_STAMP_NAME).write_text(
        f"1.2.130\n{src_recency:.3f}\n", encoding="utf-8"
    )

    assert deploy.sync_listener_to_appdata() == destination
    assert not sentinel.is_file()
    assert "def verse_mangled_name" in (
        destination / "listener" / "verse_editable_editor.py"
    ).read_text(encoding="utf-8")


def test_listener_sync_sweeps_staging_trees_left_by_killed_processes(tmp_path, monkeypatch) -> None:
    """A panel or bridge killed mid-copy left its listener.tmp.<pid>.<tid> tree
    behind; the name is unique per process, so nothing ever removed it."""
    import os
    import time

    from frontend import open_files

    source = _listener_source(tmp_path)
    destination = tmp_path / "appdata" / "listener"
    monkeypatch.setattr(deploy, "_source_listener_dir", lambda: source)
    monkeypatch.setattr(deploy, "appdata_listener_dir", lambda: destination)
    alive = {os.getpid(), 4242}
    monkeypatch.setattr(open_files, "panel_process_alive", lambda pid: pid in alive)

    def staging(name: str, *, age_s: float = 0.0) -> Path:
        tree = destination.parent / name
        (tree / "listener").mkdir(parents=True)
        (tree / "listener" / "config.py").write_text("partial\n", encoding="utf-8")
        if age_s:
            old = time.time() - age_s
            os.utime(tree, (old, old))
        return tree

    dead = staging("listener.tmp.999999.1")
    reused_pid_old = staging("listener.tmp.4242.7", age_s=2 * 3600)
    in_progress = staging(f"listener.tmp.{os.getpid()}.12345")
    assert deploy.sync_listener_to_appdata() == destination
    assert not dead.exists() and not reused_pid_old.exists()
    assert in_progress.is_dir()
    late = staging("listener.tmp.888888.2")
    assert deploy.sync_listener_to_appdata() == destination  # the core is current: skip path
    assert not late.exists()


def test_plugin_listener_overlays_are_not_rewritten_when_unchanged(tmp_path, monkeypatch) -> None:
    """Every ship deleted and re-copied each enabled plugin's listener overlay,
    even when not one byte had changed."""
    from backend.uefn_plugins import store as plugin_store

    source = _listener_source(tmp_path)
    destination = tmp_path / "appdata" / "listener"
    plugins = tmp_path / "uefn_plugins"
    handlers = plugins / "anim-tools" / "listener"
    handlers.mkdir(parents=True)
    (handlers / "__init__.py").write_text("from . import cmds\n", encoding="utf-8")
    (handlers / "cmds.py").write_text("X = 1\n", encoding="utf-8")
    (handlers / "gone.py").write_text("Y = 1\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "_source_listener_dir", lambda: source)
    monkeypatch.setattr(deploy, "appdata_listener_dir", lambda: destination)
    monkeypatch.setattr(plugin_store, "appdata_uefn_plugins_dir", lambda: plugins)
    monkeypatch.setattr(plugin_store, "get_enabled_plugin_ids", lambda: ["anim-tools"])
    monkeypatch.setattr(plugin_store, "load_plugin_manifest", lambda pid: {"id": pid})

    assert deploy.sync_listener_to_appdata() == destination
    overlay = destination / "listener" / "plugins" / "anim_tools"
    before = {p.name: p.stat().st_ino for p in (overlay, overlay / "cmds.py", overlay / "__init__.py")}
    assert deploy.sync_listener_to_appdata() == destination
    assert deploy._overlay_plugin_listeners(destination) is False
    after = {p.name: p.stat().st_ino for p in (overlay, overlay / "cmds.py", overlay / "__init__.py")}
    assert after == before

    (handlers / "cmds.py").write_text("X = 2\n", encoding="utf-8")
    (handlers / "gone.py").unlink()
    assert deploy._overlay_plugin_listeners(destination) is True
    assert (overlay / "cmds.py").read_text(encoding="utf-8") == "X = 2\n"
    assert not (overlay / "gone.py").exists()

