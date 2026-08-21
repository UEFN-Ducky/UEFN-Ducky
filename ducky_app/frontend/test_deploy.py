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


def test_enable_uefn_project_python_skips_missing_file(tmp_path: Path) -> None:
    assert deploy.enable_uefn_project_python(tmp_path) is None
