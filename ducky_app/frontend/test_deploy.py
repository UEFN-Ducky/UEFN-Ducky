from __future__ import annotations

import json
from pathlib import Path

from frontend import deploy


def _listener_source(root: Path) -> Path:
    source = root / "source"
    package = source / "listener"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "config.py").write_text("PROTOCOL_VERSION = 'test'\n", encoding="utf-8")
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
    (project / "_junk.py").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "quarantine_python_root", lambda: tmp_path / "q")
    monkeypatch.setattr(deploy, "enable_uefn_project_python", lambda _p: None)
    monkeypatch.setattr(deploy, "install_user_init_unreal", lambda: None)
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
    assert not (project / "_junk.py").exists()
    assert any("init_unreal.py" in line for line in logs)


def test_enable_uefn_project_python_skips_missing_file(tmp_path: Path) -> None:
    assert deploy.enable_uefn_project_python(tmp_path) is None


def _island_with_python(tmp_path: Path) -> Path:
    project = tmp_path / "Island"
    (project / "Content" / "Python").mkdir(parents=True)
    (project / "Content" / "Verse").mkdir(parents=True)
    (project / "Saved").mkdir()
    (project / "_probe_errs.py").write_text("print(1)\n", encoding="utf-8")
    (project / "Content" / "Python" / "init_unreal.py").write_text("# stub\n", encoding="utf-8")
    (project / "Content" / "Verse" / "foo.verse").write_text("using { /Verse.org/Simulation }\n", encoding="utf-8")
    (project / "Saved" / "x.py").write_text("# cook junk\n", encoding="utf-8")
    (project / "Content" / "Verse" / "nested.py").write_text("# deep scratch\n", encoding="utf-8")
    return project


def test_quarantine_project_python_deep_moves_island_py_skips_saved(tmp_path, monkeypatch) -> None:
    project = _island_with_python(tmp_path)
    quarantine = tmp_path / "quarantine"
    monkeypatch.setattr(deploy, "quarantine_python_root", lambda: quarantine)

    logs = deploy.quarantine_project_python(project, deep=True)
    assert any("_probe_errs.py" in line for line in logs)
    assert any("nested.py" in line for line in logs)
    assert not any("init_unreal.py" in line for line in logs)
    assert not (project / "_probe_errs.py").exists()
    assert (project / "Content" / "Python" / "init_unreal.py").is_file()
    assert not (project / "Content" / "Verse" / "nested.py").exists()
    assert (project / "Content" / "Verse" / "foo.verse").is_file()
    assert (project / "Saved" / "x.py").is_file()
    dests = list(quarantine.rglob("*.py"))
    names = {p.name for p in dests}
    assert names == {"_probe_errs.py", "nested.py"}


def test_quarantine_project_python_shallow_catches_root_and_content_python(
    tmp_path, monkeypatch
) -> None:
    project = _island_with_python(tmp_path)
    quarantine = tmp_path / "quarantine"
    monkeypatch.setattr(deploy, "quarantine_python_root", lambda: quarantine)

    logs = deploy.quarantine_project_python(project, deep=False)
    assert any("_probe_errs.py" in line for line in logs)
    assert not any("init_unreal.py" in line for line in logs)
    assert not (project / "_probe_errs.py").exists()
    assert (project / "Content" / "Python" / "init_unreal.py").is_file()
    # ponytail ceiling: deep scratch outside Content/Python waits for deploy
    assert (project / "Content" / "Verse" / "nested.py").is_file()
    assert (project / "Content" / "Verse" / "foo.verse").is_file()
    assert (project / "Saved" / "x.py").is_file()
