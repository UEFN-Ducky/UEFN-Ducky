"""Phase 4 (plans, tasks, memory, usage, logs): parity on both backends, imports, no island side files."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture(params=["db", "files"])
def backend(request, monkeypatch) -> str:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", request.param)
    return request.param


@pytest.fixture
def island(tmp_path: Path) -> str:
    root = tmp_path / "Island"
    (root / "Content").mkdir(parents=True)
    return str(root)


@pytest.fixture
def appdata(tmp_path: Path) -> Path:
    return tmp_path / ".ducky-appdata" / "UEFN-Ducky"


# --------------------------------------------------------------------------- plans + tasks


def test_plans_round_trip_and_no_dotducky_on_rows(backend: str, island: str) -> None:
    from backend.agent.coding_agents import plans as p

    doc = p.create_plan("chat-1", title="Build shop", nodes=[{"id": "n1", "content": "Place devices", "status": "pending", "children": []}],
                        project_root=island)
    assert p.load_plan("chat-1", island)["title"] == "Build shop"
    rows = p.list_plans(island)
    assert [r["chat_id"] for r in rows] == ["chat-1"]
    assert p.delete_plan("chat-1", island) is True and p.load_plan("chat-1", island) is None
    assert (Path(island) / ".ducky").exists() == (backend == "files")
    del doc


def test_templates_round_trip(backend: str) -> None:
    from backend.agent.coding_agents import plans as p

    made = p.create_template(title="T", nodes=[{"id": "a", "content": "one", "status": "completed", "children": []}])
    loaded = p.load_template(made["id"])
    assert loaded["nodes"][0]["status"] == "pending"  # blueprints keep pending statuses
    ids = {t["template_id"] for t in p.list_templates()}
    assert made["id"] in ids and p.DEMO_TEMPLATE_ID in ids
    assert p.delete_template(made["id"]) is True and p.load_template(made["id"]) is None


def test_tasks_round_trip_and_artifacts_under_appdata(backend: str, island: str, appdata: Path) -> None:
    from backend.agent.coding_agents import epic

    task = epic.create_task("Ship it", goal="all of it", project_root=island)
    epic.add_phase(task["id"], "Phase 1", plan="do things", project_root=island)
    art = epic.write_artifact(task["id"], "spec", "# spec", project_root=island)
    loaded = epic.load_task(task["id"], island)
    assert [ph["title"] for ph in loaded["phases"]] == ["Phase 1"]
    assert loaded["artifacts"][0]["name"] == "spec.md" and Path(art["path"]).read_text() == "# spec"
    assert [t["id"] for t in epic.list_tasks(island)] == [task["id"]]
    inside_island = str(Path(island)) in art["path"]
    assert inside_island == (backend == "files")
    if backend == "db":
        assert str(appdata / "tasks") in art["path"]


def test_project_dotducky_is_folded_into_rows_once(island: str, appdata: Path) -> None:
    from backend.agent.coding_agents import epic
    from backend.agent.coding_agents import plans as p
    from backend.store.importers import phase4

    dot = Path(island) / ".ducky"
    (dot / "plans").mkdir(parents=True)
    (dot / "plans" / "chat-old.json").write_text(json.dumps({"id": "p1", "kind": "project", "chat_id": "chat-old", "title": "Old plan",
                                                           "todos": [{"id": "t", "content": "x", "status": "pending"}]}))
    (dot / "plans" / "chat-old.json.bak.20260816224521").write_text("junk")
    (dot / "tasks" / "abc123" / "artifacts").mkdir(parents=True)
    (dot / "tasks" / "abc123" / "artifacts" / "notes.md").write_text("notes")
    (dot / "tasks" / "abc123.json").write_text(json.dumps({"id": "abc123", "title": "T", "phases": [], "conv_ids": [],
                                                           "artifacts": [{"id": "a", "name": "notes.md", "kind": "spec", "path": "old"}]}))
    plan = p.load_plan("chat-old", island)
    assert plan["title"] == "Old plan" and plan["nodes"]  # legacy todos migrated
    task = epic.load_task("abc123", island)
    assert Path(task["artifacts"][0]["path"]).read_text() == "notes"
    assert str(appdata / "tasks") in task["artifacts"][0]["path"]
    assert not dot.exists()
    assert (appdata / "legacy" / "projects").exists()
    rep = phase4.ensure_project(island)
    assert rep is None  # already folded


# --------------------------------------------------------------------------- memory


def test_memory_parity(backend: str, island: str) -> None:
    from backend.memory import project as mem

    mem.save_entry("verse-naming", "Use snake_case for devices.", description="naming", author="Coder", project_root=island)
    mem.save_entry("verse-naming/props", "Props get a Prop_ prefix.", project_root=island)
    mem.append_entry("verse-naming", "Also: no spaces.", author="Coder", project_root=island)
    entries = mem.list_entries(island)
    assert [e["name"] for e in entries] == ["verse-naming"]
    assert entries[0]["subs"] == [{"name": "verse-naming/props", "description": "Props get a Prop_ prefix."}]
    top = mem.read_entry("verse-naming", island)
    assert "no spaces" in top["content"] and top["author"] == "Coder"
    assert mem.read_entry("verse-naming/props", island)["content"].strip() == "Props get a Prop_ prefix."
    index = mem.index_markdown(island, author_filter="coder")
    assert "verse-naming — naming (Coder)" in index and "  - verse-naming/props" in index
    assert mem.delete_entry("verse-naming/props", island) is True
    assert mem.read_entry("verse-naming", island)["subs"] == []
    assert mem.delete_entry("verse-naming", island) is True
    assert mem.list_entries(island) == []


def test_memory_import_from_markdown_tree(island: str, appdata: Path) -> None:
    from backend.memory import project as mem
    from frontend.ui_web.project_chats import project_slug

    slug = project_slug(island)
    d = appdata / "memory" / "projects" / slug
    (d / "areas").mkdir(parents=True)
    (d / "flat.md").write_text("---\nname: flat\ndescription: a flat note\nauthor: Verse Coder\nupdated: 2026-08-12 13:36 UTC\n---\n\nBody here\n", encoding="utf-8")
    (d / "areas" / "MEMORY.md").write_text("---\nname: areas\ndescription: index topic\n---\n\nIndex\n", encoding="utf-8")
    (d / "areas" / "palace.md").write_text("---\nname: areas/palace\ndescription: the palace\n---\n\nGold roof\n", encoding="utf-8")
    entries = {e["name"]: e for e in mem.list_entries(island)}
    assert entries["flat"]["author"] == "Verse Coder" and entries["areas"]["subs"][0]["name"] == "areas/palace"
    assert mem.read_entry("areas/palace", island)["content"] == "Gold roof"
    assert not d.exists() and (appdata / "legacy" / "memory" / "memory" / "projects" / slug / "flat.md").exists()


# --------------------------------------------------------------------------- usage


def test_usage_ledger_parity(backend: str) -> None:
    from frontend.ui_web import provider_usage_log as pul

    pul.log_call(provider="openai", model="gpt-5", input_tokens=10, output_tokens=5, conv_id="c1", agent="chat", ducky_label="Coder")
    pul.log_call(provider="openai", model="gpt-5", input_tokens=10, output_tokens=5, conv_id="c1", agent="chat", ducky_label="Coder",
                 ts=time.time() - 30 * 86400)  # out of the 7-day window
    report = pul.usage_report("openai", days=7)
    assert report["total_input"] == 10 and report["total_output"] == 5
    models = {m.get("model"): m for m in report["by_model"]} if isinstance(report["by_model"], list) else report["by_model"]
    assert models["gpt-5"]["input_tokens"] == 10


def test_usage_import_from_jsonl(appdata: Path) -> None:
    from backend.store.repos import usage
    from frontend.ui_web import provider_usage_log as pul

    appdata.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.time(), "provider": "anthropic", "model": "claude", "input_tokens": 3, "output_tokens": 4,
           "cache_read_tokens": 0, "cache_write_tokens": 0, "conv_id": "", "agent": "chat_title", "ducky_label": ""}
    (appdata / "provider_usage.jsonl").write_text(json.dumps(row) + "\n" + "not json\n", encoding="utf-8")
    report = pul.usage_report("anthropic", days=7)
    assert report["total_input"] == 3 and usage.count() == 1
    assert not (appdata / "provider_usage.jsonl").exists()


# --------------------------------------------------------------------------- logs


def test_error_and_activity_logs_parity(backend: str) -> None:
    from frontend import error_log

    error_log.record_error("bridge", "boom")
    error_log.record_error("bridge", "boom")  # deduped against the newest row
    error_log.record_error("bridge", "bang")
    error_log.record_activity("panel", "started")
    errs = error_log.read_errors()
    assert [e["message"] for e in errs] == ["bang", "boom"]
    assert [a["message"] for a in error_log.read_activity()] == ["started"]
    error_log.clear_errors()
    assert error_log.read_errors() == []


def test_listener_error_file_is_ingested_on_rows(appdata: Path) -> None:
    from frontend import error_log

    appdata.mkdir(parents=True, exist_ok=True)
    (appdata / "errors.jsonl").write_text(json.dumps({"ts": time.time(), "source": "listener", "message": "from UEFN"}) + "\n")
    assert [e["message"] for e in error_log.read_errors()] == ["from UEFN"]
    legacy = appdata / "errors.jsonl"
    assert not legacy.exists() or legacy.read_text() == ""  # folded into rows (first boot moves it; later reads truncate)
    legacy.write_text(json.dumps({"ts": time.time(), "source": "listener", "message": "second"}) + chr(10))
    assert [e["message"] for e in error_log.read_errors()] == ["second", "from UEFN"]
    assert legacy.read_text() == ""


def test_agent_crash_and_verse_stats_rows(backend: str) -> None:
    from backend.tools.verse import verse_stats
    from frontend import agent_crash_log

    agent_crash_log.record_crash(conv_id="c", provider="openai", model="m", error="timeout", thinking="t", answer="a",
                                 elapsed_s=1.5, first_token_s=None)
    crashes = agent_crash_log.read_crashes()
    assert crashes[0]["error"] == "timeout" and crashes[0]["thinking"] == "t"
    verse_stats.record_compile({"3512": 2}, ["Content/Verse/a.verse"])
    verse_stats.record_tool_failure("wire_verse_device_ref", "stale")
    summary = verse_stats.summarize(days=30)
    assert summary["by_code"] == {"3512": 2} and summary["tool_failures"] == 1
