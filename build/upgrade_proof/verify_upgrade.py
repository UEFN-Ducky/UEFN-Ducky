"""Upgrade proof: NEW code (worktree) boots against the legacy AppData the OLD code wrote.

Usage: python verify_upgrade.py <legacy_localappdata_copy> <project_dir>
Simulates three panel boots (maintenance pass each) and asserts every store
came across, then that legacy/ is gone and the root holds nothing stray.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

scratch = Path(sys.argv[1]).resolve()
project = Path(sys.argv[2]).resolve()
expected = json.loads((scratch / "legacy_report.json").read_text(encoding="utf-8"))
os.environ["LOCALAPPDATA"] = str(scratch)
os.environ["APPDATA"] = str(scratch / "Roaming")
os.environ["UEFN_DUCKY_PROJECT_ROOT"] = str(project)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ducky_app"))  # this checkout (new code)

root = scratch / "UEFN-Ducky"
failures: list[str] = []


def check(cond: bool, what: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        failures.append(what)


# ---- boot 1: maintenance = open_checked + import everything ------------------
from frontend.appdata_maintenance import maintain_appdata  # noqa: E402

r1 = maintain_appdata(root)
print("boot 1 maintenance:", r1)
check(r1["db_checked"] == 1, "database opened and passed integrity on boot 1")
check(r1["db_imported"] >= 10, f"importers ran on boot 1 ({r1['db_imported']} stores)")

from backend.store import db  # noqa: E402
from backend.store.repos import chats, kv, memory, misc, usage  # noqa: E402
from frontend.settings import PanelSettings  # noqa: E402

conn = db.connect()
s = PanelSettings.load()
check(s.uefn_project_root == str(project), "settings: uefn_project_root imported")
check(s.editor_tracking_enabled is False, "settings: non-default bool field imported")

from backend.agent import secrets  # noqa: E402

check(secrets.get_key("anthropic") == "sk-ant-legacy-123", "secrets: anthropic key readable after import (DPAPI)")
check(secrets.get_key("openai") == "sk-openai-legacy-456", "secrets: openai key readable after import")

from frontend.ui_web.recent_projects import load_recent_projects  # noqa: E402

check(str(project) in load_recent_projects(), "recent projects imported")

from frontend.ui_web import window_bounds, workspace_dock  # noqa: E402

b = window_bounds.get_bounds("main")
check(isinstance(b, dict) and int(b.get("width") or 0) == 1200, f"window bounds imported ({b})")
dock = workspace_dock.load_window("main")
check(isinstance(dock, dict) and dock.get("widths", {}).get("left") == 320, f"dock layout imported ({dock})")

from frontend.ui_web import project_chats as pc  # noqa: E402

slug = pc.project_slug(str(project))
convs = pc.list_conversations(str(project)) if hasattr(pc, "list_conversations") else None
n_conv = conn.execute("SELECT count(*) FROM conversations WHERE project_id=?", (slug,)).fetchone()[0]
n_msg = conn.execute(
    "SELECT count(*) FROM messages WHERE conv_id IN (SELECT id FROM conversations WHERE project_id=?)", (slug,)
).fetchone()[0]
n_fold = conn.execute("SELECT count(*) FROM folders WHERE project_id=? AND id<>'archive'", (slug,)).fetchone()[0]
check(n_conv == expected["chats"]["conversations"], f"chats: {n_conv} conversations")
check(n_msg == expected["chats"]["messages"], f"chats: {n_msg} messages")
check(n_fold == expected["chats"]["folders"], f"chats: {n_fold} folders")
first = pc.load_conversation(expected["chats"]["conv_ids"][0], str(project))
check(first is not None and first.title == "Legacy chat one" and len(first.messages) == 4, "chats: conversation loads with title + 4 messages")
hits = chats.search_messages(slug, "legacy message 3", limit=5)
check(bool(hits), "chats: body search finds an imported message")

check(usage.count() == expected["usage"], f"usage ledger: {usage.count()} calls")
n_mem = conn.execute("SELECT count(*) FROM memory_entries WHERE project_id=?", (slug,)).fetchone()[0]
check(n_mem == expected["memory"], f"memory: {n_mem} entries")
check(bool(memory.search(slug, "legacy memory", limit=5)), "memory: FTS search hits imported note")

from backend.store.repos import plans as plans_repo  # noqa: E402

tpl_ids = {doc.get("id") for _k, doc in plans_repo.plan_docs(plans_repo.TEMPLATE_PROJECT, "template")}
check(expected["plans"]["template_id"] in tpl_ids, f"plans: template imported ({sorted(map(str, tpl_ids))})")
plan_ids = {doc.get("id") for _k, doc in plans_repo.plan_docs(slug, "project")}
check(expected["plans"]["plan_id"] in plan_ids, f"plans: per-project .ducky plan folded ({sorted(map(str, plan_ids))})")
check(not (project / ".ducky" / "plans").exists(), "plans: <project>/.ducky/plans removed after fold")

n_runs = conn.execute("SELECT count(*) FROM runs WHERE project_id=?", (slug,)).fetchone()[0]
n_entries = conn.execute("SELECT count(*) FROM run_entries").fetchone()[0]
n_blobs = conn.execute("SELECT count(*) FROM blobs").fetchone()[0]
check(n_runs >= 1 and n_entries == 2, f"ledger: {n_runs} runs / {n_entries} entries imported")
check(n_blobs >= 2, f"ledger: {n_blobs} blobs imported")
n_fv = conn.execute("SELECT count(*) FROM file_versions WHERE project_id=?", (slug,)).fetchone()[0]
check(n_fv >= 1, f"file history: {n_fv} versions imported")

from frontend.ui_web import plugin_host_api as pha  # noqa: E402

check(pha.cache_get("demo_plugin", "state") == {"count": 7, "name": "legacy"}, "plugin data: cache_get returns imported value")
prefs = pha.prefs_all_get()
check(prefs.get("demo_plugin", {}).get("theme") == "dark", f"plugin prefs imported ({prefs})")

check("legacy_custom" in misc.mcp_servers_get(), f"mcp servers imported ({sorted(misc.mcp_servers_get())})")

kinds = {k: n for k, n in conn.execute("SELECT kind, count(*) FROM events GROUP BY kind").fetchall()}
print("  events by kind:", kinds)
for kind in ("error", "activity", "ui_crash", "plugin_load_error"):
    check(kinds.get(kind, 0) >= 1, f"events: {kind} imported")
mc = kv.get_doc("cache_docs", "models_cache")
check(isinstance(mc, dict) and mc.get("anthropic", [{}])[0].get("id") == "claude-legacy", "models cache imported")
check(misc.capture_names() == set(expected["captures"]), "captures indexed")
check(all((root / "tool_captures" / n).exists() for n in expected["captures"]), "capture files kept on disk")

# ---- what is left on disk after boot 1 ----------------------------------------
top = sorted(p.name for p in root.iterdir())
print("  root after boot 1:", top)
stray = [n for n in top if n not in {"ducky.db", "ducky.db-wal", "ducky.db-shm", "legacy", "tool_captures", "mcp.json", "snapshots", "config.json"}]
check(not stray, f"no stray files/folders after boot 1 (stray: {stray})")
legacy_stores = sorted(p.name for p in (root / "legacy").iterdir())
print("  legacy/ stores:", legacy_stores)
check((root / "legacy").is_dir() and len(legacy_stores) >= 10, "legacy/ holds the moved stores")

# ---- boots 2 and 3: legacy retired -------------------------------------------
db.reset_for_tests()
from backend.store.importers import phase1  # noqa: E402

phase1.reset_for_tests()
r2 = maintain_appdata(root)
db.reset_for_tests()
phase1.reset_for_tests()
r3 = maintain_appdata(root)
print("boot 2:", r2, "boot 3:", r3)
check(not (root / "legacy").exists(), "legacy/ deleted after three clean boots")
top = sorted(p.name for p in root.iterdir())
print("  root after boot 3:", top)
check(kv.get_doc("cache_docs", "models_cache") is not None and secrets.get_key("anthropic") == "sk-ant-legacy-123", "data still intact after cleanup")

print()
print("FAILURES:" if failures else "ALL CHECKS PASSED", *failures, sep="\n  ")
sys.exit(1 if failures else 0)
