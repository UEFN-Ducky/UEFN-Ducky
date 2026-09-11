"""Populate a scratch %LOCALAPPDATA% with the OLD (files) code from the main checkout.

Usage: python gen_legacy_appdata.py <scratch_localappdata> <project_dir> <old_checkout_dir>
Everything below goes through the same writers the shipped 1.1.x app uses, so
the layout is exactly what an upgrading user has on disk.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

scratch = Path(sys.argv[1]).resolve()
project = Path(sys.argv[2]).resolve()
scratch.mkdir(parents=True, exist_ok=True)
(project / "Content").mkdir(parents=True, exist_ok=True)
(project / "Island.uplugin").write_text("{}", encoding="utf-8")
os.environ["LOCALAPPDATA"] = str(scratch)
os.environ["APPDATA"] = str(scratch / "Roaming")
os.environ["UEFN_DUCKY_PROJECT_ROOT"] = str(project)
(scratch / "Roaming").mkdir(exist_ok=True)
sys.path.insert(0, str(Path(sys.argv[3]).resolve() / "ducky_app"))  # the OLD (pre-database) checkout

report: dict[str, object] = {}

# settings + secrets + recent projects --------------------------------------
from frontend.settings import PanelSettings, replace  # noqa: E402

s = PanelSettings.load()
s = replace(s, uefn_project_root=str(project), editor_tracking_enabled=False)
s.save()
report["settings"] = {"uefn_project_root": str(project)}

from backend.agent import secrets  # noqa: E402

secrets.set_key("anthropic", "sk-ant-legacy-123")
secrets.set_key("openai", "sk-openai-legacy-456")
report["secrets"] = ["anthropic", "openai"]

from frontend.ui_web.recent_projects import add_recent_project  # noqa: E402

add_recent_project(str(project))

from frontend.ui_web import window_bounds, workspace_dock  # noqa: E402

window_bounds.save_bounds("main", 40, 60, 1200, 800)
workspace_dock.save_window("main", {"left": ["chat"], "right": ["editor"], "widths": {"left": 320}})

# chats ----------------------------------------------------------------------
from frontend.ui_web import project_chats as pc  # noqa: E402

folder = pc.create_folder("Ideas", project_root=str(project))
conv = pc.create_conversation(title="Legacy chat one", project_root=str(project), skill_snapshot="snapshot-text-1")
for i in range(4):
    role = "user" if i % 2 == 0 else "assistant"
    pc.append_message(
        conv,
        {"role": role, "content": f"legacy message {i}", "text": f"legacy message {i}", "ts": time.time() + i},
        str(project),
    )
conv2 = pc.create_conversation(title="Legacy chat two", project_root=str(project), skill_snapshot="snapshot-text-1")
pc.append_message(conv2, {"role": "user", "content": "hello two", "text": "hello two", "ts": time.time()}, str(project))
report["chats"] = {"folders": 1, "conversations": 2, "messages": 5, "conv_ids": [conv.id, conv2.id], "folder_id": folder.id}

# usage ledger ---------------------------------------------------------------
from frontend.ui_web import provider_usage_log  # noqa: E402

provider_usage_log.log_call(provider="anthropic", model="claude-x", input_tokens=120, output_tokens=40, cost_usd=0.012, conv_id=conv.id)
provider_usage_log.log_call(provider="openai", model="gpt-x", input_tokens=10, output_tokens=5, cost_usd=0.001, conv_id=conv2.id)
report["usage"] = 2

# memory + plan template + plan ----------------------------------------------
from backend.memory import project as memory  # noqa: E402

memory.save_entry("architecture", "# Arch\nlegacy memory body", description="How it fits", project_root=str(project))
memory.save_entry("architecture/devices", "device notes", description="Devices", project_root=str(project))
report["memory"] = 2

from backend.agent.coding_agents import plans  # noqa: E402

tpl = plans.create_template(title="Legacy template", overview="A template", body_markdown="- step")
plan = plans.create_plan(conv.id, title="Legacy plan", overview="plan body", project_root=str(project))
report["plans"] = {"template_id": tpl["id"], "plan_id": plan["id"]}

# change ledger (real writer + journal) + file history ------------------------
from frontend.ui_web import workspace_bootstrap  # noqa: E402

workspace_bootstrap.install()
from backend.workspace.runtime import get_writer  # noqa: E402

w = get_writer()
w.write_text("Content/Hello.verse", "using { /Fortnite.com/Devices }\nhello := class(creative_device):\n", op="create", tool="gen")
w.write_text("Content/Hello.verse", "using { /Fortnite.com/Devices }\nhello := class(creative_device):\n    # v2\n", tool="gen")
report["changes"] = {"files": ["Content/Hello.verse"], "writes": 2}

from frontend.ui_web.verse_editor import file_history  # noqa: E402

file_history.snapshot_before_write("Content/Hello.verse", "old content v0", str(project), source="editor")
report["file_history"] = 1

# plugin data + prefs ----------------------------------------------------------
from frontend.ui_web import plugin_host_api as pha  # noqa: E402

pha.cache_set("demo_plugin", "state", {"count": 7, "name": "legacy"})
pha.prefs_all_set({"demo_plugin": {"theme": "dark"}})
report["plugin_kv"] = {"demo_plugin": ["state", "prefs"]}

# mcp.json --------------------------------------------------------------------
from backend.mcp_plugins import store as mcp  # noqa: E402

mcp.ensure_mcp_config()
cfg = mcp.load_mcp_config()
cfg["mcpServers"]["legacy_custom"] = {"type": "http", "url": "http://127.0.0.1:9333/mcp", "kind": "custom", "label": "Legacy"}
mcp.save_mcp_config(cfg)
report["mcp"] = sorted(mcp.load_mcp_config()["mcpServers"])

# logs ------------------------------------------------------------------------
from frontend import error_log  # noqa: E402

error_log.record_error("gen", "legacy error line")
error_log.record_activity("gen", "legacy activity line")
from backend.tools.verse import verse_stats  # noqa: E402

verse_stats.record_compile({"3506": 2}, ["Content/Hello.verse"])
(scratch / "UEFN-Ducky" / "ui_crashes.jsonl").write_text(
    json.dumps({"ts": time.time(), "label": "x", "message": "legacy ui crash", "surface": "chat"}) + "\n", encoding="utf-8"
)
(scratch / "UEFN-Ducky" / "uefn_plugin_load_errors.jsonl").write_text(
    json.dumps({"ts": time.time(), "plugin_id": "broken_plugin", "error": "ImportError: nope"}) + "\n", encoding="utf-8"
)
report["logs"] = ["errors", "activity", "verse_stats", "ui_crashes", "plugin_load_errors"]

# models cache (old writer format) + capture + diagnostics --------------------
(scratch / "UEFN-Ducky" / "models_cache.json").write_text(
    json.dumps({"anthropic": [{"id": "claude-legacy", "display_name": "Claude Legacy"}]}), encoding="utf-8"
)
from frontend.ui_web import tool_captures  # noqa: E402

cap = tool_captures.save_tool_capture_png(b"\x89PNG legacy", prefix="shot")
report["captures"] = [cap["filename"]]
from frontend.ui_web.verse_editor.lsp import diagnostics_cache as dc  # noqa: E402

store = dc.load(str(project))
store["files"]["content/hello.verse"] = {"mtime_ns": 1, "size": 1, "errors": 1, "warnings": 0, "items": [{"message": "legacy diag"}]}
dc.save(str(project), store)

# perf ------------------------------------------------------------------------
from frontend import perf_trace  # noqa: E402

perf_trace.ensure_started()
perf_trace.trace("tool_push", "gen", 3.0, result_bytes=1)
perf_trace.write_report()

root = scratch / "UEFN-Ducky"
tree = sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())
report["tree"] = tree
(scratch / "legacy_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if k != "tree"}, indent=1))
print("files:", len(tree))
for t in tree:
    print("  ", t)
