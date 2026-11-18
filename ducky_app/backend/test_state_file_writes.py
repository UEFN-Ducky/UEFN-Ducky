"""Backend state files go through frontend.atomic_json: same bytes, no fixed temp name."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from backend.automations import versions
from backend.sandbox import manifest
from backend.uefn_plugins import model_gate
from backend.uefn_plugins.plugin_host import crash_guard
from backend.workspace import human_watch

_APP_DIR = Path(__file__).resolve().parents[1]


def _only(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_workflow_version_bytes_match_old_code(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "versions"
    monkeypatch.setattr(versions, "directory", lambda _wid: folder)
    doc = {"id": "w1", "name": "Café", "graph": {"nodes": [1]}}
    versions.archive_file(doc)
    (written,) = folder.iterdir()
    assert written.suffix == ".json"
    assert written.read_bytes() == json.dumps(versions.snapshot(doc), ensure_ascii=False).encode("utf-8")


def test_model_gate_bytes_match_old_code(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "d" / model_gate._FILE
    monkeypatch.setattr(model_gate, "_path", lambda: target)
    model_gate.set_allowed_models("Demo", ["a", "b", "a"])
    assert target.read_bytes() == json.dumps({"demo": ["a", "b"]}, indent=2).encode()
    assert _only(target.parent) == [model_gate._FILE]


def test_human_watch_index_bytes_match_old_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(human_watch, "_watch_use_db", lambda: False)
    human_watch._save_saved(tmp_path, {"Content/a.verse": {"hash": "h1"}, "b": {"hash": ""}})
    target = tmp_path / human_watch._INDEX_NAME
    assert target.read_bytes() == json.dumps({"Content/a.verse": "h1"}, indent=1).encode()


def test_sandbox_manifest_bytes_match_old_code(tmp_path: Path) -> None:
    box = tmp_path / "box"
    box.mkdir()
    manifest.record(box, "run", command="echo hi")
    raw = (box / "manifest.json").read_bytes()
    assert raw == json.dumps(json.loads(raw), indent=1).encode()
    assert _only(box) == ["manifest.json"]


def test_two_writers_never_collide(tmp_path: Path, monkeypatch) -> None:
    """The window and a plugin host write crashed-app.json at once: no write fails."""
    markers = tmp_path / "markers"
    markers.mkdir()
    target = markers / crash_guard._CRASHED_FILE
    monkeypatch.setattr(crash_guard, "_markers_dir", lambda: markers)
    failures: list[str] = []

    class _Log:
        def debug(self, msg, *a, **k):
            failures.append(msg)

    monkeypatch.setattr(crash_guard, "_log", _Log())
    other = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; from frontend.atomic_json import write_json_plain\n"
         "for i in range(150): write_json_plain(sys.argv[1], {'other@1': str(i)})",
         str(target)],
        cwd=str(_APP_DIR),
    )
    try:
        while other.poll() is None:
            crash_guard._write_crashed({"demo@1.0": "crashed"})
    finally:
        other.wait(timeout=60)
    crash_guard._write_crashed({"demo@1.0": "crashed"})
    assert other.returncode == 0
    assert failures == []
    assert json.loads(target.read_text(encoding="utf-8")) == {"demo@1.0": "crashed"}
    assert _only(markers) == [crash_guard._CRASHED_FILE]
