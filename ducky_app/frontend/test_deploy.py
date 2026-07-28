from __future__ import annotations

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
