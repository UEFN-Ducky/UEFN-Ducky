"""First boot after an upgrade: every legacy store is imported, nothing stray is
left in App Data, and ``legacy/`` disappears after three clean boots.

The fixture files under ``fixtures/legacy/`` were written by the last
files-based release through its own writers (see build/upgrade_proof/README.md).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from backend.store import db
from backend.store.repos import kv

FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
SLUG = "LegacyIsland_8fe41f6c7d4aeac1"  # the slug the old release computed for its project


def _seed_legacy_appdata(root: Path, project: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    settings = json.loads((FIXTURES / "panel_settings.json").read_text(encoding="utf-8"))
    settings["uefn_project_root"] = str(project)
    (root / "panel_settings.json").write_text(json.dumps(settings), encoding="utf-8")
    (root / "recent_projects.json").write_text(json.dumps({"projects": [str(project)]}), encoding="utf-8")
    chats = root / "chats" / "projects" / SLUG
    (chats / "conversations" / "24b2ea2d-88d5-486a-887b-975fd878a92a").mkdir(parents=True)
    shutil.copy(FIXTURES / "folders.json", chats / "folders.json")
    shutil.copy(FIXTURES / "conversation.json", chats / "conversations" / "24b2ea2d-88d5-486a-887b-975fd878a92a" / "conversation.json")
    cs = root / "changesets" / SLUG
    (cs / "runs").mkdir(parents=True)
    (cs / "blobs").mkdir()
    shutil.copy(FIXTURES / "changeset_run.json", cs / "runs" / "human_2026-09-10.json")
    shutil.copy(FIXTURES / "changeset_index.json", cs / "index.json")
    shutil.copy(FIXTURES / "changeset_catalog.json", cs / "catalog.json")
    for blob in (FIXTURES / "blobs").glob("*.txt"):
        shutil.copy(blob, cs / "blobs" / blob.name)
    fh = root / "file_history" / SLUG / "Content" / "Hello.verse"
    fh.mkdir(parents=True)
    shutil.copy(FIXTURES / "file_history_version.json", fh / "1789087318718.json")
    mem = root / "memory" / "projects" / SLUG / "architecture"
    mem.mkdir(parents=True)
    shutil.copy(FIXTURES / "memory_MEMORY.md", mem / "MEMORY.md")
    (root / "plan_templates").mkdir()
    shutil.copy(FIXTURES / "plan_template.json", root / "plan_templates" / "c5dc917f66b3.json")
    shutil.copy(FIXTURES / "provider_usage.jsonl", root / "provider_usage.jsonl")
    (root / "models_cache.json").write_text(json.dumps({"anthropic": [{"id": "claude-legacy", "display_name": "L"}]}), encoding="utf-8")
    (root / "backups" / "chats").mkdir(parents=True)
    (root / "backups" / "chats" / "folders.json.bak.20260910").write_text("{}", encoding="utf-8")
    (root / "perf").mkdir()
    (root / "perf" / "session-1.jsonl").write_text(json.dumps({"ts": 1.0, "kind": "tool_push", "name": "x"}) + "\n", encoding="utf-8")
    (root / "perf" / "latest-report.json").write_text("{}", encoding="utf-8")
    (root / "errors.jsonl").write_text(json.dumps({"ts": 1.0, "source": "gen", "message": "legacy error"}) + "\n", encoding="utf-8")
    (root / "ui_crashes.jsonl").write_text(json.dumps({"ts": 1.0, "label": "x", "message": "crash", "surface": "chat"}) + "\n", encoding="utf-8")
    dot = project / ".ducky" / "plans"
    dot.mkdir(parents=True)
    shutil.copy(FIXTURES / "project_plan.json", dot / "24b2ea2d-88d5-486a-887b-975fd878a92a.json")


def _boot(root: Path) -> dict[str, int]:
    from backend.store.importers import phase1
    from frontend.appdata_maintenance import maintain_appdata

    db.reset_for_tests()
    phase1.reset_for_tests()
    return maintain_appdata(root)


def test_first_boot_imports_everything_and_leaves_no_stray_files(tmp_path: Path) -> None:
    root = db.app_root()
    project = tmp_path / "Island"
    (project / "Content").mkdir(parents=True)
    _seed_legacy_appdata(root, project)

    r1 = _boot(root)
    assert r1["db_checked"] == 1 and r1["db_imported"] >= 10

    conn = db.connect()
    count = lambda sql, *a: int(conn.execute(sql, a).fetchone()[0])  # noqa: E731
    from frontend.settings import PanelSettings

    assert PanelSettings.load().uefn_project_root == str(project)
    assert count("SELECT count(*) FROM conversations WHERE project_id=?", SLUG) == 1
    assert count("SELECT count(*) FROM messages") == 4
    assert count("SELECT count(*) FROM folders WHERE project_id=? AND id<>'archive'", SLUG) == 1
    assert count("SELECT count(*) FROM runs WHERE project_id=?", SLUG) == 1
    assert count("SELECT count(*) FROM run_entries") == 2
    assert count("SELECT count(*) FROM blobs") >= 2
    assert count("SELECT count(*) FROM file_versions WHERE project_id=?", SLUG) == 1
    assert count("SELECT count(*) FROM memory_entries WHERE project_id=?", SLUG) == 1
    assert count("SELECT count(*) FROM plans WHERE kind='template'") == 1
    assert count("SELECT count(*) FROM usage_calls") == 2
    assert kv.get_doc("cache_docs", "models_cache")["anthropic"][0]["id"] == "claude-legacy"
    kinds = {k: n for k, n in conn.execute("SELECT kind, count(*) FROM events GROUP BY kind").fetchall()}
    assert kinds.get("error") == 1 and kinds.get("ui_crash") == 1
    # the per-project .ducky/ folder of a recent project is folded at boot, not on first open
    from frontend.ui_web.project_chats import project_slug

    assert count("SELECT count(*) FROM plans WHERE project_id=? AND kind='project'", project_slug(str(project))) == 1
    assert not (project / ".ducky" / "plans").exists()

    top = sorted(p.name for p in root.iterdir())
    assert set(top) <= {"ducky.db", "ducky.db-wal", "ducky.db-shm", "legacy", "snapshots", "mcp.json", "config.json"}, top
    legacy_stores = sorted(p.name for p in (root / "legacy").iterdir())
    for store in ("settings", "projects", "chats", "changesets", "file_history", "memory", "plan_templates", "usage", "cache_docs", "logs", "perf", "backups"):
        assert store in legacy_stores, (store, legacy_stores)

    assert _boot(root)["legacy_removed"] == 0
    assert (root / "legacy").is_dir()
    assert _boot(root)["legacy_removed"] == 1
    assert not (root / "legacy").exists()
    conn = db.connect()  # boots dropped the earlier connection
    assert int(conn.execute("SELECT count(*) FROM messages").fetchone()[0]) == 4  # cleanup removed files, not rows
