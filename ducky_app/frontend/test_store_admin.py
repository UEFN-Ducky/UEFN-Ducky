"""Settings → App Data → Database backend (frontend/store_admin.py)."""

from __future__ import annotations

import json
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
    plugin_kv.set("demo", "token", None, encrypted_b64="ZW5j")
    plugin_kv.set("demo", "plain", {"v": 1})
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


def test_restore_is_staged_and_applied_on_next_open() -> None:
    root = _appdata()
    kv.set_doc("cache_docs", "marker", "before")
    snap = store_admin.action("snapshot")["snapshot"]
    kv.set_doc("cache_docs", "marker", "after")
    staged = store_admin.action("restore", snap)
    assert staged["ok"] and staged["restart_required"]
    assert store_admin.overview()["restore_pending"] is True
    # "next boot": drop this process's connections and open again
    db.reset_for_tests()
    assert kv.get_doc("cache_docs", "marker") == "before"
    assert not (root / store_admin.RESTORE_PENDING_NAME).exists()
    assert any(p.name.startswith("ducky.db.replaced-") for p in root.iterdir())
    assert store_admin.action("restore", "ducky-nope.db")["ok"] is False
    store_admin.action("restore", snap)
    assert store_admin.action("cancel_restore")["ok"] and store_admin.overview()["restore_pending"] is False


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
