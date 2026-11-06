"""Older versions still read every data file a launcher-era Ducky leaves behind.

panel_settings.json gains one key (``native_launcher``), ducky.db keeps its schema,
window_bounds.json keeps its keys, the WebView2 profile stays in the same folder, and
boot.json with the launcher's ``launcher`` key still turns into a crash report.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from frontend import boot_report
from frontend.app_paths import resolve_app_data_dir
from frontend.settings import PanelSettings, default_app_data_dir

REPO = Path(__file__).resolve().parents[2]


# --- 1. panel_settings.json with the new key, read by an old loader ---------------------


def _old_settings_class() -> type:
    """PanelSettings as a version before native_launcher existed: same fields minus the new one."""
    fields = [
        (f.name, f.type, f)
        for f in dataclasses.fields(PanelSettings)
        if f.name != "native_launcher"
    ]
    return dataclasses.make_dataclass("OldPanelSettings", [(n, Any, dataclasses.field(default=f.default, default_factory=f.default_factory)) if f.default_factory is not dataclasses.MISSING else (n, Any, dataclasses.field(default=f.default)) for n, _t, f in fields])


def _old_load(data: dict[str, Any]) -> Any:
    """The old loader's rule (from_json_dict since 1.2.x): known keys only, nulls dropped."""
    old = _old_settings_class()
    known = {f.name for f in dataclasses.fields(old)}
    return old(**{k: v for k, v in data.items() if k in known and v is not None})


@pytest.fixture
def files_backend(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    path = resolve_app_data_dir() / "panel_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("mode", ["on", "auto"])
def test_old_loader_reads_settings_file_with_the_new_key(files_backend: Path, mode: str) -> None:
    s = PanelSettings.load()
    s.web_access = "on"
    s.terminals_enabled = False
    s.native_launcher = mode
    s.save()
    data = json.loads(files_backend.read_text(encoding="utf-8"))
    assert data["native_launcher"] == mode
    old = _old_load(data)
    assert not hasattr(old, "native_launcher")
    assert old.web_access == "on" and old.terminals_enabled is False
    # Every key the old version knows comes back exactly as written.
    for f in dataclasses.fields(old):
        assert getattr(old, f.name) == data.get(f.name, getattr(old, f.name)), f.name


def test_the_shipped_loader_ignores_keys_it_does_not_know(files_backend: Path) -> None:
    """The same rule the old loader relies on, in today's code: a key from a newer version
    (or the launcher) never breaks loading."""
    files_backend.write_text(
        json.dumps({"web_access": "on", "native_launcher": "auto", "launcher_future_key": {"a": 1}}),
        encoding="utf-8",
    )
    s = PanelSettings.load()
    assert s.web_access == "on" and s.native_launcher == "auto"


def test_old_loader_reads_db_rows_with_the_new_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DUCKY_STORE_BACKEND", raising=False)
    monkeypatch.delenv("DUCKY_STORE_BACKEND_SETTINGS", raising=False)
    s = PanelSettings.load()
    s.web_access = "on"
    s.native_launcher = "auto"
    s.save()
    from backend.store.repos import settings as repo

    rows, _version = repo.load_fields_versioned()
    assert rows.get("native_launcher") == "auto"
    old = _old_load(dict(rows))
    assert old.web_access == "on"


# --- 2. ducky.db schema is unchanged ----------------------------------------------------


def _schema() -> tuple[int, list[tuple[str, str, str]]]:
    from backend.store import db

    conn = db.connect()
    rows = conn.execute(
        "SELECT type, name, COALESCE(sql, '') FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return db.user_version(conn), [tuple(r) for r in rows]


def test_launcher_activity_leaves_the_db_schema_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DUCKY_STORE_BACKEND", raising=False)
    monkeypatch.delenv("DUCKY_STORE_BACKEND_SETTINGS", raising=False)
    from backend.store.importers import phase1

    phase1.ensure("settings")
    before = _schema()
    s = PanelSettings.load()
    s.native_launcher = "auto"
    s.save()
    ns = pytest.importorskip("frontend.native_switch")
    ns.record_start("python", None, None, "no_hello")
    ns.note_failure()
    assert _schema() == before


def test_no_migration_is_about_the_launcher() -> None:
    folder = REPO / "ducky_app" / "backend" / "store" / "migrations"
    files = sorted(folder.glob("*.sql"))
    assert files, folder
    for f in files:
        assert not re.search(r"launcher", f.read_text(encoding="utf-8"), re.I), f.name
        assert "launcher" not in f.name.lower()


# --- 3. window_bounds keys --------------------------------------------------------------


def test_window_bounds_file_keys_are_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    from frontend.ui_web import window_bounds

    window_bounds.save_bounds("main", 10, 20, 1200, 800)
    path = Path(os.environ["LOCALAPPDATA"]) / "UEFN-Ducky" / "window_bounds.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"main": {"x": 10, "y": 20, "width": 1200, "height": 800}}
    # A file the launcher wrote (same shape, extra keys from a newer writer) reads back.
    data["main"]["maximized"] = True
    data["focus:chat-1"] = {"x": 1, "y": 2, "width": 640, "height": 480}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert window_bounds.get_bounds("main") == {"x": 10, "y": 20, "width": 1200, "height": 800}


def test_rust_bounds_crate_uses_the_same_file_and_key() -> None:
    src = REPO / "native_launcher" / "crates" / "bounds" / "src" / "lib.rs"
    if not src.is_file():
        pytest.skip("bounds crate not in this checkout")
    text = src.read_text(encoding="utf-8")
    assert 'MAIN_KEY: &str = "main"' in text
    assert '"window_bounds.json"' in text and '"UEFN-Ducky"' in text
    for key in ("x", "y", "width", "height"):
        assert re.search(rf"\b{key}\b", text), key


# --- 4. the WebView2 profile path -------------------------------------------------------


def test_webview2_profile_path_is_the_same() -> None:
    from frontend.ui_web.webview_app import webview_profile_dir

    assert webview_profile_dir() == str(default_app_data_dir() / "webview")
    assert Path(webview_profile_dir()) == resolve_app_data_dir() / "webview"


def test_rust_window_uses_the_same_profile_folder() -> None:
    src = REPO / "native_launcher" / "crates" / "window" / "src" / "config.rs"
    if not src.is_file():
        pytest.skip("window crate not in this checkout")
    text = src.read_text(encoding="utf-8")
    assert 'WEBVIEW_DIR_NAME: &str = "webview"' in text
    assert '"UEFN-Ducky"' in text


# --- 5. boot.json with launcher stages parses in boot_report.py ------------------------


def _record(**extra: Any) -> dict[str, Any]:
    rec = {
        "version": "1.2.471",
        "started": 1793900000.5,
        "pid": 987654321,
        "os": "Windows-11-10.0.26300-SP0",
        "python": "3.12.7",
        "state": "starting",
        "stages": [["launch", 0.0], ["window", 1.2]],
    }
    rec.update(extra)
    return rec


LAUNCHER = {
    "pid": 123,
    "version": "1.2.471",
    "started": 1793900000.4,
    "stages": [["launch", 0.0], ["window", 0.1], ["painted", 0.9]],
    "backend_pid": 987654321,
    "exit_code": 23,
    "exit_reason": "no_hello",
    "error": "no hello within 15 s",
}


def test_report_reads_a_record_with_the_launcher_key() -> None:
    body = boot_report.build_report(_record(launcher=LAUNCHER), "")
    assert body is not None
    log = body["error_log"]
    assert log.startswith("Ducky closed while starting, last stage reached: window")
    assert "Stages: launch 0.0s, window 1.2s" in log
    assert "Launcher: exit 23 no_hello; stages launch 0.0s, window 0.1s, painted 0.9s" in log


def test_clean_end_with_launcher_key_is_still_quiet() -> None:
    assert boot_report.build_report(_record(state="closed", launcher=LAUNCHER), "") is None


@pytest.mark.parametrize(
    "launcher",
    [None, "garbage", [], {"stages": "x"}, {"stages": [["a"], ["b", "nan?"], 5, ["ok", 1]]}, {"exit_code": None}],
)
def test_odd_launcher_values_never_break_the_report(launcher: Any) -> None:
    body = boot_report.build_report(_record(launcher=launcher), "")
    assert body is not None and body["error_log"]


def test_odd_stage_rows_never_break_the_report() -> None:
    rec = _record(stages=[["launch", 0.0], ["window", 1.2, "native"], ["x"], "y", ["z", "bad"], ["last", "2.5"]])
    body = boot_report.build_report(rec, "")
    assert body is not None
    assert "last stage reached: last" in body["error_log"]
    assert "window 1.2s" in body["error_log"]


def test_begin_reads_boot_json_with_launcher_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """The next launch reads a launcher-era boot.json, keeps its report, and starts a new record."""
    monkeypatch.setenv("DUCKY_SANDBOX", "1")  # test copy: no pop-up, nothing sent
    monkeypatch.setattr(boot_report, "_still_running", lambda pid: False)
    monkeypatch.setattr(boot_report, "_pc_restarted_since", lambda when: False)
    folder = boot_report._dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "boot.json").write_text(json.dumps(_record(launcher=LAUNCHER)), encoding="utf-8")
    try:
        boot_report.begin("1.2.472")
        now = json.loads((folder / "boot.json").read_text(encoding="utf-8"))
        assert now["version"] == "1.2.472" and now["state"] == "starting"
        reports = boot_report.list_reports()
        assert reports, "the launcher-era record became a kept report"
    finally:
        boot_report.mark("closed")
        boot_report._state.clear()
