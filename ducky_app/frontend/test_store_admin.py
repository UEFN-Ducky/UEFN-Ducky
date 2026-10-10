"""Settings → App Data → Database backend (frontend/store_admin.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from backend.store import db
from backend.store.repos import kv
from frontend import store_admin


def _appdata() -> Path:
    root = db.app_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_overview_lists_every_table_with_counts_and_backends() -> None:
    _appdata()
    kv.set_doc("cache_docs", "x", {"a": 1})
    ov = store_admin.overview()
    assert ov["error"] == ""
    assert ov["schema_version"] == ov["head_version"] == db.head_version()
    names = {t["name"] for t in ov["tables"]}
    assert names == set(store_admin.TABLES)
    assert next(t for t in ov["tables"] if t["name"] == "cache_docs")["rows"] == 1
    assert ov["backends"]["settings"] == "rows"
    assert ov["journal_mode"] == "wal"
    assert ov["legacy"] is None and ov["snapshots"] == []


def test_preview_masks_secrets_settings_and_encrypted_plugin_rows(monkeypatch) -> None:
    _appdata()
    from backend.agent import secrets as sec
    from backend.store.repos import plugin_kv

    monkeypatch.setattr(sec, "protect_text", lambda s: b"blob-" + s.encode())
    monkeypatch.setattr(sec, "unprotect_text", lambda b: b[5:].decode())
    sec.set_key("anthropic", "sk-ant-secret-value")
    kv.set_doc("settings", "openai_api_key", "sk-plain")
    kv.set_doc("settings", "theme", "dark")
    plugin_kv.set("demo", "token", None, encrypted_b64="ZW5j", account="_local", scope="personal")
    plugin_kv.set("demo", "plain", {"v": 1}, account="_local", scope="personal")
    secrets = store_admin.table_preview("secrets")
    assert secrets["total"] == 1
    assert all("secret-value" not in json.dumps(r) for r in secrets["rows"])
    settings = {r["key"]: r["value"] for r in store_admin.table_preview("settings")["rows"]}
    assert settings["openai_api_key"] == "••••••••" and settings["theme"] == '"dark"'
    pk = store_admin.table_preview("plugin_kv")["rows"]
    enc = next(r for r in pk if r["key"] == "token")
    assert "ZW5j" not in json.dumps(enc)
    assert store_admin.table_preview("nope")["ok"] is False


def test_actions_check_snapshot_vacuum_export_and_clear(tmp_path: Path) -> None:
    _appdata()
    from backend.store.repos import events

    events.insert("perf", ts=time.time(), source="t", message="m")
    assert store_admin.action("check")["ok"] is True
    assert store_admin.overview()["integrity"]["result"] == "ok"
    snap = store_admin.action("snapshot")
    assert snap["ok"] and store_admin.overview()["snapshots"][0]["name"] == snap["snapshot"]
    assert store_admin.action("vacuum")["ok"] is True
    exp = store_admin.action("export_table", "events")
    assert exp["ok"] and exp["rows"] == 1 and Path(exp["path"]).read_text(encoding="utf-8").count("\n") == 1
    assert store_admin.action("clear_table", "events") == {"ok": True, "removed": 1, "removed_files": 0}
    assert store_admin.action("clear_table", "messages")["ok"] is False  # not clearable
    assert store_admin.action("delete_snapshot", snap["snapshot"])["ok"] is True
    assert store_admin.overview()["snapshots"] == []
    assert store_admin.action("optimize")["ok"] is True
    assert store_admin.action("bogus")["ok"] is False


def _stage_restore_in_first_boot() -> str:
    _appdata()
    kv.set_doc("cache_docs", "marker", "before")
    snap = store_admin.action("snapshot")["snapshot"]
    kv.set_doc("cache_docs", "marker", "after")
    staged = store_admin.action("restore", snap)
    assert staged["ok"] and staged["restart_required"]
    assert store_admin.overview()["restore_pending"] is True
    assert kv.get_doc("cache_docs", "marker") == "after"  # staging is not a live restore
    return snap


def _verify_restore_in_second_boot(snap: str) -> None:
    root = _appdata()
    assert kv.get_doc("cache_docs", "marker") == "before"
    assert not (root / store_admin.RESTORE_PENDING_NAME).exists()
    assert any(p.name.startswith("ducky.db.replaced-") for p in root.iterdir())
    assert store_admin.action("restore", "ducky-nope.db")["ok"] is False
    store_admin.action("restore", snap)
    assert store_admin.action("cancel_restore")["ok"] and store_admin.overview()["restore_pending"] is False


def test_restore_is_staged_and_applied_on_next_open(tmp_path: Path) -> None:
    # A process-global AppData redirect is visible to surviving test workers.
    # reset_for_tests closes only the calling thread's handles, so it cannot
    # simulate a restart while another worker holds the redirected Windows DB.
    # Use actual process exit/open, with the restore root ONLY in child envs.
    env = os.environ.copy()
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME",
                "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "TEMP", "TMP"):
        directory = tmp_path / "restore-process" / key
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    for key in ("DUCKY_CACHE_SMOKE", "DUCKY_TESTS_REAL_APPDATA"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    app = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(app)
    stage = subprocess.run(
        [sys.executable, "-B", "-c", "import json; from frontend.test_store_admin import _stage_restore_in_first_boot; print(json.dumps(_stage_restore_in_first_boot()))"],
        cwd=app, env=env, capture_output=True, text=True, timeout=30,
    )
    assert stage.returncode == 0, stage.stdout + stage.stderr
    snapshot = json.loads(stage.stdout)
    restored = subprocess.run(
        [sys.executable, "-B", "-c", "import sys; from frontend.test_store_admin import _verify_restore_in_second_boot; _verify_restore_in_second_boot(sys.argv[1])", snapshot],
        cwd=app, env=env, capture_output=True, text=True, timeout=30,
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr


def test_restore_boots_leave_parent_worker_and_appdata_untouched(tmp_path: Path) -> None:
    parent_env = {key: os.environ.get(key) for key in ("LOCALAPPDATA", "APPDATA", "HOME", "TEMP")}
    parent_path = db.db_path()
    opened, release = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    paths: list[Path] = []

    def worker() -> None:
        try:
            conn = db.connect()
            paths.append(db.db_path())
            opened.set()
            assert release.wait(60)
            # Our own handle remains usable; the child test must not close it.
            assert conn.execute("SELECT 1").fetchone()[0] == 1
            paths.append(db.db_path())
        except BaseException as exc:
            errors.append(exc)
            opened.set()
        finally:
            db.close_thread_connections()

    thread = threading.Thread(target=worker, name="store-restore-parent-reader")
    thread.start()
    try:
        assert opened.wait(10) and not errors
        test_restore_is_staged_and_applied_on_next_open(tmp_path)
        assert {key: os.environ.get(key) for key in parent_env} == parent_env
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    assert paths == [parent_path, parent_path]


def test_retire_legacy_and_import_now() -> None:
    root = _appdata()
    (root / "legacy" / "chats").mkdir(parents=True)
    (root / "legacy" / "chats" / "old.json").write_text("{}")
    ov = store_admin.overview()
    assert ov["legacy"]["files"] == 1 and ov["legacy"]["stores"][0]["name"] == "chats"
    assert store_admin.action("retire_legacy")["removed"] == 1
    assert store_admin.overview()["legacy"] is None
    reports = store_admin.action("import_now")["reports"]
    assert "settings" in reports and "chats" in reports and "mcp_servers" in reports


def test_delete_project_rows_and_project_listing(tmp_path: Path) -> None:
    _appdata()
    from frontend.appdata_maintenance import appdata_projects, delete_project_appdata
    from frontend.settings import PanelSettings
    from frontend.ui_web import project_chats as pc

    project = str(tmp_path / "Island")
    Path(project).mkdir()
    s = PanelSettings.load()
    s.uefn_project_root = project
    s.save()
    conv = pc.create_conversation(title="t", project_root=project, skill_snapshot="x")
    pc.append_message(conv, {"role": "user", "content": "hi", "text": "hi", "ts": time.time()}, project)
    slug = pc.project_slug(project)
    listing = appdata_projects()
    mine = next(p for p in listing["projects"] if p["slug"] == slug)
    assert mine["rows"]["conversations"] == 1 and mine["rows"]["messages"] == 1
    removed = store_admin.delete_project_rows(slug)
    assert removed["conversations"] == 1 and removed["messages"] == 1
    assert pc.load_conversation(conv.id, project) is None
    assert delete_project_appdata("never-existed") == 0
