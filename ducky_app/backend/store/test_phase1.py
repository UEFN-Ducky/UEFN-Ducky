"""Phase 1 (settings and singletons): repo parity, import, rollback, lost-update."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from backend.store import db
from backend.store.importers import phase1
from backend.store.repos import kv
from backend.store.repos import plugin_kv as plugin_repo
from backend.store.repos import projects as projects_repo
from backend.store.repos import settings as settings_repo


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("DUCKY_STORE_BACKEND", raising=False)
    db.reset_for_tests()
    settings_repo.reset_for_tests()
    phase1.reset_for_tests()
    yield tmp_path
    db.reset_for_tests()
    settings_repo.reset_for_tests()
    phase1.reset_for_tests()


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "UEFN-Ducky"
    root.mkdir(parents=True, exist_ok=True)
    return root


# --------------------------------------------------------------------------- settings


def test_settings_round_trip_through_rows(tmp_path: Path) -> None:
    from frontend.settings import PanelSettings

    s = PanelSettings.load()
    assert s.uefn_project_root == ""
    s.uefn_project_root = r"C:\Islands\One"
    s.enabled_uefn_plugins = []  # explicitly none, not "inherit"
    s.save()
    again = PanelSettings.load()
    assert again.uefn_project_root == r"C:\Islands\One"
    assert again.enabled_uefn_plugins == []
    rows = settings_repo.load_fields()
    assert rows["uefn_project_root"] == r"C:\Islands\One"
    assert rows["enabled_uefn_plugins"] == []
    assert "agent_model" not in rows  # defaults are absent rows


def test_settings_save_writes_only_changed_keys(tmp_path: Path) -> None:
    from frontend.settings import PanelSettings

    a = PanelSettings.load()
    a.chat_title_model = "gpt-5-nano"
    a.save()
    b = PanelSettings.load()
    b.memory_keep_last_messages = 33
    written = settings_repo.save_fields(b.to_json_dict(), PanelSettings().to_json_dict())
    assert written == ["memory_keep_last_messages"]


def test_settings_no_lost_update_between_two_writers(tmp_path: Path) -> None:
    """Process A holds a stale object while B saves a different field; A's save
    must not wipe B's change (this is the bug the file store had)."""
    from frontend.settings import PanelSettings

    a = PanelSettings.load()  # A's view, taken before B writes
    other = sqlite3.connect(str(db.db_path()), isolation_level=None)
    other.execute(
        "INSERT INTO settings(key, value, updated) VALUES ('duckyos_base_url', ?, 0)",
        (json.dumps("https://b.example"),),
    )
    other.close()
    a.chat_title_model = "from-a"
    a.save()
    final = PanelSettings.load()
    assert final.chat_title_model == "from-a"
    assert final.duckyos_base_url == "https://b.example"


def test_settings_import_from_legacy_file(tmp_path: Path) -> None:
    from frontend.settings import PanelSettings

    root = _root(tmp_path)
    (root / "panel_settings.json").write_text(
        json.dumps({"uefn_project_root": "X:/Proj", "enabled_uefn_plugins": None, "openai_api_key": "nope",
                    "memory_keep_last_messages": 20, "unknown_key": 1}),
        encoding="utf-8",
    )
    s = PanelSettings.load()
    assert s.uefn_project_root == "X:/Proj"
    assert s.enabled_uefn_plugins is None
    rows = settings_repo.load_fields()
    assert "openai_api_key" not in rows and "unknown_key" not in rows
    assert "memory_keep_last_messages" not in rows  # equal to default → no row
    report = json.loads(kv.meta_get("imported:settings") or "{}")
    assert report["found"] is True
    # idempotent: a second boot does not re-import
    (root / "panel_settings.json").write_text(json.dumps({"uefn_project_root": "Y:/Other"}), encoding="utf-8")
    phase1.reset_for_tests()
    assert PanelSettings.load().uefn_project_root == "X:/Proj"


def test_settings_rollback_to_files(tmp_path: Path, monkeypatch) -> None:
    from frontend.settings import PanelSettings

    root = _root(tmp_path)
    (root / "panel_settings.json").write_text(json.dumps({"uefn_project_root": "F:/Files"}), encoding="utf-8")
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    assert PanelSettings.load().uefn_project_root == "F:/Files"
    assert not db.db_path().exists()


def test_settings_cache_revalidates_on_foreign_commit(tmp_path: Path) -> None:
    from frontend.settings import PanelSettings

    assert PanelSettings.load().chat_title_model == ""
    other = sqlite3.connect(str(db.db_path()), isolation_level=None)
    other.execute("INSERT INTO settings(key, value, updated) VALUES ('chat_title_model', ?, 0)", (json.dumps("x"),))
    other.close()
    assert PanelSettings.load().chat_title_model == "x"


# --------------------------------------------------------------------------- secrets


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")
def test_secrets_rows_are_dpapi_blobs_and_import_legacy_file(tmp_path: Path) -> None:
    from backend.agent import secrets
    from backend.store.repos import secrets as repo

    root = _root(tmp_path)
    (root / "credentials.dat").write_bytes(secrets._serialize_keys({"anthropic": "sk-legacy"}))
    secrets.clear_memory_cache()
    assert secrets.get_key("anthropic") == "sk-legacy"
    assert not (root / "credentials.dat").exists()
    assert (root / "legacy" / "secrets" / "credentials.dat").exists()
    secrets.set_key("openai", "sk-new")
    blobs = repo.all_blobs()
    assert set(blobs) == {"anthropic", "openai"}
    assert b"sk-new" not in blobs["openai"]
    raw = db.db_path().read_bytes() + (db.db_path().parent / "ducky.db-wal").read_bytes()
    assert b"sk-new" not in raw and b"sk-legacy" not in raw
    secrets.clear_key("anthropic")
    secrets.clear_memory_cache()
    assert secrets.load_keys() == {"openai": "sk-new"}


# --------------------------------------------------------------------------- projects


def test_recent_projects_repo_and_import(tmp_path: Path) -> None:
    from frontend.ui_web import recent_projects as rp

    root = _root(tmp_path)
    p1, p2 = tmp_path / "IslandA", tmp_path / "IslandB"
    p1.mkdir()
    p2.mkdir()
    (root / "recent_projects.json").write_text(json.dumps({"projects": [str(p1), str(p2)]}), encoding="utf-8")
    assert rp.load_recent_projects() == [str(p1), str(p2)]
    assert not (root / "recent_projects.json").exists()
    rp.add_recent_project(str(p2))
    assert rp.load_recent_projects()[0] == str(p2)
    rp.remove_recent_project(str(p1))
    assert rp.load_recent_projects() == [str(p2)]
    assert projects_repo.recent_paths() == [str(p2)]


# --------------------------------------------------------------------------- workspace state


def test_window_bounds_dock_and_editor_workspace_rows(tmp_path: Path) -> None:
    from frontend.ui_web import window_bounds, workspace_dock

    root = _root(tmp_path)
    (root / "window_bounds.json").write_text(json.dumps({"main": {"x": 1, "y": 2, "width": 800, "height": 600}}))
    (root / "workspace_dock.json").write_text(json.dumps({"main": {"leftWidth": 280}}))
    ws = root / "workspace" / "projects" / "Island_abc"
    ws.mkdir(parents=True)
    (ws / "editor.json").write_text(json.dumps({"version": 1, "openTabs": [], "layout": {}, "focusWindows": []}))

    assert window_bounds.get_bounds("main") == {"x": 1, "y": 2, "width": 800, "height": 600}
    window_bounds.save_bounds("focus:1", 5, 6, 700, 500)
    assert window_bounds.get_bounds("focus:1") == {"x": 5, "y": 6, "width": 700, "height": 500}
    assert workspace_dock.load_window("main") == {"leftWidth": 280}
    workspace_dock.save_window("main", {"leftWidth": 300})
    assert workspace_dock.load_window("main") == {"leftWidth": 300}
    assert workspace_dock.load_window("missing") is None
    assert kv.get_doc("workspace_state", "editor:Island_abc")["version"] == 1
    for name in ("window_bounds.json", "workspace_dock.json"):
        assert not (root / name).exists()
    assert not (root / "workspace").exists()
    assert (root / "legacy" / "workspace_state" / "workspace" / "projects" / "Island_abc" / "editor.json").exists()


def test_editor_workspace_round_trip(tmp_path: Path, monkeypatch) -> None:
    from frontend.ui_web import editor_workspace as ew

    monkeypatch.setattr(ew, "_merge_live_focus_windows", lambda snap: snap)
    payload = {"version": 1, "openTabs": [{"id": "chat:abc", "kind": "chat", "name": "Chat", "chatId": "abc"}],
               "layout": None, "focusWindows": []}
    ew.save_editor_workspace(payload, project_root=str(tmp_path / "Proj"))
    loaded = ew.load_editor_workspace(ew.project_slug(str(tmp_path / "Proj")))
    assert [t["id"] for t in loaded["openTabs"]] == ["chat:abc"]


# --------------------------------------------------------------------------- cache docs


def test_models_cache_doc_round_trip(tmp_path: Path) -> None:
    from frontend.ui_web import panel_api as pa

    root = _root(tmp_path)
    (root / pa._MODELS_CACHE_FILE).write_text(json.dumps({"openai": [{"id": "gpt-5", "display_name": "GPT-5"}]}))
    doc = pa._read_models_cache_doc()
    assert doc["openai"][0]["id"] == "gpt-5"
    pa._write_models_cache_doc({"openai": [{"id": "gpt-6"}]})
    assert kv.get_doc("cache_docs", "models_cache")["openai"][0]["id"] == "gpt-6"
    # Imported into cache_docs and parked under legacy/; nothing rewrites the old file.
    assert not (root / pa._MODELS_CACHE_FILE).exists()
    assert (root / "legacy" / "cache_docs" / pa._MODELS_CACHE_FILE).is_file()


# --------------------------------------------------------------------------- plugin kv


def test_plugin_cache_and_prefs_rows_and_import(tmp_path: Path) -> None:
    from frontend.ui_web import plugin_host_api as pha

    root = _root(tmp_path)
    legacy = root / "uefn_plugin_cache" / "demo"
    legacy.mkdir(parents=True)
    (legacy / "es.json").write_text(json.dumps({"Hello": "Hola"}), encoding="utf-8")
    prefs_dir = root / "uefn_plugin_prefs"
    prefs_dir.mkdir()
    (prefs_dir / "all.json").write_text(json.dumps({"demo": {"language": "es"}}), encoding="utf-8")

    assert pha.cache_get("demo", "es") == {"Hello": "Hola"}
    assert not legacy.exists()
    pha.cache_set("demo", "vf_zh_a", {"t": 1})
    pha.cache_set("demo", "vf_zh_b", {"t": 2})
    pha.cache_set("demo", "vf_bg_c", {"t": 3})
    cleared = pha.cache_clear("demo", "vf_zh_*")
    assert sorted(cleared["cleared"]) == ["vf_zh_a", "vf_zh_b"]
    assert pha.cache_get("demo", "vf_zh_a") == {}
    assert pha.cache_get("demo", "vf_bg_c") == {"t": 3}
    assert pha.cache_clear("demo", "es")["cleared"] == ["es"]
    assert pha.prefs_all_get() == {"demo": {"language": "es"}}
    pha.prefs_plugin_set("other", {"showInHeader": True})
    pha.prefs_plugin_set("demo", {"language": "bg"})
    assert pha.prefs_all_get() == {"demo": {"language": "bg"}, "other": {"showInHeader": True}}
    assert pha.cache_clear("demo")["ok"] and pha.cache_get("demo", "vf_bg_c") == {}
    assert plugin_repo.all_prefs()["demo"] == {"language": "bg"}  # prefs survive a cache wipe


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")
def test_sensitive_plugin_data_is_encrypted_at_rest(tmp_path: Path) -> None:
    from frontend.ui_web import plugin_host_api as pha

    pha.cache_set("discord", "token", {"bot_token": "very-secret-token"}, sensitive=True)
    assert pha.cache_get("discord", "token") == {"bot_token": "very-secret-token"}
    value, encrypted = plugin_repo.get("discord", "token")
    assert encrypted and "very-secret-token" not in value
    raw = db.db_path().read_bytes() + (db.db_path().parent / "ducky.db-wal").read_bytes()
    assert b"very-secret-token" not in raw
