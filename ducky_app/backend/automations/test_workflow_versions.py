"""Saved copies survive edits, reloads, restores, and deletion in both storage modes."""
from copy import deepcopy

import pytest
from backend.automations import store, versions
from backend.store import db
from frontend.ui_web.panel_api_automations import PanelApiAutomationsMixin


@pytest.fixture(params=[False, True], ids=["files", "database"])
def storage(request, monkeypatch, tmp_path):
    monkeypatch.setattr(store, "use_db", lambda *_: request.param)
    monkeypatch.setattr(store, "_files_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)
    monkeypatch.setattr(db, "app_root", lambda: tmp_path)
    yield request.param
    db.close_thread_connections()


def sample():
    return {"name": "Island", "graph": {
        "nodes": [{"id": "a", "type": "flow.wait", "x": 20, "y": 30, "config": {"seconds": 9}}],
        "edges": [], "groups": [{"id": "g", "name": "Setup", "node_ids": ["a"]}],
    }}


def put_legacy(storage, doc):
    """A workflow saved by an older app: a JSON file, or a row of the shared table."""
    if storage:
        from backend.store.repos import automations as legacy

        legacy.put(doc)
    else:
        store._files_put(doc)


def test_every_save_is_immutable_and_restore_creates_another_version(storage):
    original = store.save_workflow(sample())
    first = versions.list_versions(original["id"])[0]
    changed = deepcopy(original)
    changed["graph"]["nodes"][0]["config"]["seconds"] = 100
    changed["graph"]["groups"] = []
    changed["name"] = "Changed"
    store.save_workflow(changed)
    store.save_workflow(changed)  # Identical explicit saves are still kept.
    assert len(versions.list_versions(original["id"])) == 3
    old = versions.get_version(original["id"], first["id"])
    assert old["graph"] == original["graph"]
    store.append_run(original["id"], {"ok": True, "ended": 42})
    restored = store.save_workflow(old)
    assert restored["graph"] == original["graph"]
    assert restored["runs"] == [{"ok": True, "ended": 42}]
    assert len(versions.list_versions(original["id"])) == 4
    assert store.get_workflow(original["id"])["graph"] == original["graph"]
    store.delete_workflow(original["id"])
    assert versions.get_version(original["id"], first["id"])["name"] == "Island"


def test_archives_preexisting_workflow_before_first_edit(storage):
    baseline = store.empty_workflow(name="Legacy")
    put_legacy(storage, baseline)
    store.save_workflow({"id": baseline["id"], "name": "New"})
    assert [row["name"] for row in versions.list_versions(baseline["id"])] == ["New", "Legacy"]


def test_versions_are_scoped_and_panel_api_returns_missing_errors(storage):
    first = store.save_workflow(sample())
    second = store.save_workflow({"name": "Other"})
    api = PanelApiAutomationsMixin()
    version_id = api.list_workflow_versions(first["id"])["versions"][0]["id"]
    assert api.get_workflow_version(first["id"], version_id)["workflow"]["id"] == first["id"]
    assert api.get_workflow_version(second["id"], version_id)["ok"] is False
    assert api.get_workflow_version(first["id"], "../../outside")["ok"] is False


def test_no_rolling_retention_limit(storage):
    saved = store.save_workflow(sample())
    for i in range(25):
        store.save_workflow({"id": saved["id"], "name": f"Edit {i}"})
    assert len(versions.list_versions(saved["id"])) == 26


def test_database_save_and_snapshot_roll_back_together(storage, monkeypatch):
    if not storage:
        return
    from backend.store.repos import plugin_data

    saved = store.save_workflow(sample())

    def fail(*_a, **_k):
        raise RuntimeError("write failed")

    monkeypatch.setattr(plugin_data, "upsert", fail)
    with pytest.raises(RuntimeError, match="write failed"):
        store.save_workflow({"id": saved["id"], "name": "Must not commit"})
    assert store.get_workflow(saved["id"])["name"] == "Island"
    assert len(versions.list_versions(saved["id"])) == 1


def test_database_versions_are_sealed(storage):
    if not storage:
        return
    saved = store.save_workflow(sample())
    raw = db.connect().execute("SELECT snapshot FROM workflow_versions WHERE workflow_id=?", (saved["id"],)).fetchone()[0]
    assert raw.startswith("enc1:") and "Island" not in raw


def test_upgrade_from_schema_nine_preserves_existing_workflows(storage):
    if not storage:
        return
    legacy = store.empty_workflow(name="Before versions")
    put_legacy(storage, legacy)
    conn = db.connect()
    conn.execute("DROP TABLE workflow_versions")
    conn.execute("ALTER TABLE projects DROP COLUMN kind")  # 0011 re-adds it
    conn.execute("ALTER TABLE messages DROP COLUMN fmt")  # 0015 re-adds it
    # 0016 recreates the search triggers and FTS table, 0017 re-adds the summary column.
    for trigger in ("search_docs_ai", "search_docs_ad", "search_docs_au"):
        conn.execute(f"DROP TRIGGER {trigger}")
    conn.execute("DROP TABLE search_fts")
    conn.execute("ALTER TABLE search_docs DROP COLUMN summary")
    # 0019 renames the space tables and columns 0009 and 0012 made: undo it.
    conn.execute("DROP TABLE space_meta")
    conn.execute("ALTER TABLE scope_sync RENAME COLUMN space_id TO team_id")
    # 0020 adds plugin_data's sync columns and scope_sync's rewind: undo it.
    for column in ("sync_state", "sync_error", "attempts", "next_try"):
        conn.execute(f"ALTER TABLE plugin_data DROP COLUMN {column}")
    for column in ("rewind_rev", "rewind_at"):
        conn.execute(f"ALTER TABLE scope_sync DROP COLUMN {column}")
    conn.execute("ALTER TABLE plugin_data DROP COLUMN group_id")  # 0021 re-adds it
    # 0024 adds the owner account to chats and duckies and rebuilds folders: undo it.
    for index in ("ix_conv_account", "ux_duckies_account", "duckies_history_account"):
        conn.execute(f"DROP INDEX {index}")
    for table, column in (("conversations", "account_id"), ("messages", "account_id"), ("duckies", "account_id"),
                          ("duckies", "ducky_id"), ("duckies_history", "account_id")):
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.execute(
        "CREATE TABLE folders_v3 (project_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', "
        "parent_id TEXT NOT NULL DEFAULT '', sort_order REAL NOT NULL DEFAULT 0, "
        "group_hub_id TEXT NOT NULL DEFAULT '', PRIMARY KEY (project_id, id))"
    )
    conn.execute("DROP TABLE folders")
    conn.execute("ALTER TABLE folders_v3 RENAME TO folders")
    for column in ("author", "author_pc"):  # 0025 adds who a live group's leader sent a change for
        conn.execute(f"ALTER TABLE space_changesets DROP COLUMN {column}")
    for trigger in ("messages_rev_ai", "messages_rev_ad", "messages_rev_au"):  # 0026 adds the message revision
        conn.execute(f"DROP TRIGGER {trigger}")
    conn.execute("ALTER TABLE conversations DROP COLUMN msg_rev")
    conn.execute("PRAGMA user_version=9")
    db.reset_for_tests()
    assert store.get_workflow(legacy["id"])["name"] == "Before versions"
    store.save_workflow({"id": legacy["id"], "name": "After upgrade"})
    assert [row["name"] for row in versions.list_versions(legacy["id"])] == ["After upgrade", "Before versions"]
