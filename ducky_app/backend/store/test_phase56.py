"""Phases 5/6: mcp servers, captures, diagnostics cache, digest index, paged loads,
catalog cache, perf rows, skill manifest cache, CLI, legacy retirement."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from backend.store import db
from backend.store.repos import kv, misc


@pytest.fixture
def appdata(tmp_path: Path) -> Path:
    root = tmp_path / ".ducky-appdata" / "UEFN-Ducky"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_mcp_servers_rows_with_export(appdata: Path, monkeypatch) -> None:
    from backend.mcp_plugins import store as mcp

    monkeypatch.setattr(mcp, "bundled_mcp_plugins_dir", lambda: None)
    (appdata / "mcp.json").write_text(json.dumps({"mcpServers": {"custom_one": {"type": "http", "url": "http://127.0.0.1:9000/mcp", "kind": "custom"}}}))
    cfg = mcp.load_mcp_config()
    assert "custom_one" in cfg["mcpServers"]
    assert misc.mcp_servers_get()["custom_one"]["url"] == "http://127.0.0.1:9000/mcp"
    mcp.save_mcp_config({"mcpServers": {"custom_two": {"type": "http", "url": "http://127.0.0.1:9100/mcp", "kind": "custom"}}})
    assert set(misc.mcp_servers_get()) == {"custom_two"}
    exported = json.loads((appdata / "mcp.json").read_text())
    assert set(exported["mcpServers"]) == {"custom_two"}  # export stays in sync
    assert "custom_two" in mcp.get_mcp_config_text()


def test_captures_are_indexed_and_pruned_by_row(appdata: Path) -> None:
    from frontend.ui_web import tool_captures as tc

    tc._MAX_KEEP = 3
    stray = tc.tool_captures_dir(for_write=True) / "GI_kit_buildings.png"
    stray.write_bytes(b"stray")
    names = [tc.save_tool_capture_png(b"png" + bytes([i]), prefix="shot")["filename"] for i in range(5)]
    kept = misc.capture_names()
    assert len(kept) == 3 and set(names[-3:]) == kept
    assert not stray.exists()
    assert not (tc.tool_captures_dir() / names[0]).exists()


def test_verse_diagnostics_rows_and_stamp(tmp_path: Path) -> None:
    from frontend.ui_web.verse_editor.lsp import diagnostics_cache as dc

    root = tmp_path / "Island"
    (root / "Content").mkdir(parents=True)
    (root / "Content" / "a.verse").write_text("x")
    store = dc.load(str(root))
    assert store["files"] == {}
    store["files"]["content/a.verse"] = {"mtime_ns": 1, "size": 1, "errors": 2, "warnings": 0, "items": [{"msg": "boom"}]}
    dc.save(str(root), store)
    dc.clear(str(root)) if hasattr(dc, "clear") else None
    dc._MEM.clear()
    again = dc.load(str(root))
    assert again["files"]["content/a.verse"]["errors"] == 2
    assert dc._disk_fingerprint(str(root)) is not None


def test_digest_trigram_search_matches_substring_scan(tmp_path: Path) -> None:
    from backend.tools.verse import verse_digests as vd

    vd.clear_cache()
    digest = tmp_path / "Fortnite.digest.verse"
    lines = ["using { /Verse.org/Simulation }", "npc_spawner_device<public> := class<final>(creative_device):", "    # Spawns NPCs when enabled", "    Enable<public>():void", "creative_prop<public> := class(creative_object):"]
    digest.write_text("\n".join(lines), encoding="utf-8")
    out = vd.search_verse_digest("spawner", digest_path=str(digest))
    assert [m["line"] for m in out["matches"]] == [2]
    assert out["matches"][0]["rank"] == 1
    assert vd.search_verse_digest("Spawns", digest_path=str(digest))["matches"][0]["rank"] == 2
    assert misc.digest_indexed_mtime(str(digest)) is not None
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM digest_lines").fetchone()[0] == 5
    # a rewrite re-indexes once
    digest.write_text("\n".join(lines + ["extra_thing := class():"]), encoding="utf-8")
    import os

    os.utime(digest, (time.time() + 5, time.time() + 5))
    vd.clear_cache()
    assert vd.search_verse_digest("extra_thing", digest_path=str(digest))["count"] == 1
    assert conn.execute("SELECT count(*) FROM digest_lines").fetchone()[0] == 6


def test_load_messages_paging_keeps_row_ids(tmp_path: Path) -> None:
    import frontend.ui_web.panel_api  # noqa: F401 — mixins import the package root first
    from frontend.settings import PanelSettings
    from frontend.ui_web import project_chats as pc
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

    project = str(tmp_path / "Island")
    Path(project).mkdir()
    s = PanelSettings.load()
    s.uefn_project_root = project
    s.save()
    conv = pc.create_conversation(title="Paged", project_root=project, skill_snapshot="x")
    for i in range(6):
        pc.append_message(conv, {"role": "user", "content": f"m{i}", "text": f"m{i}", "ts": time.time()}, project)
    api = PanelApiChatsMixin()
    full = api.load_messages(conv.id)
    assert [r["id"] for r in full] == list(range(6))
    tail = api.load_messages(conv.id, limit=2)
    assert [r["id"] for r in tail] == [4, 5]
    older = api.load_messages(conv.id, before_id=4, limit=2)
    assert [r["id"] for r in older] == [2, 3]


def test_store_catalog_cache_serves_last_good_copy(monkeypatch) -> None:
    from frontend.ui_web import panel_api_store as pas

    good = {"ok": True, "items": [{"slug": "anthropic"}]}
    assert pas._cache_store_catalog(good) == good
    stale = pas._cache_store_catalog({"ok": False, "error": "offline", "items": []})
    assert stale["ok"] is True and stale["stale"] is True and stale["items"] == good["items"]


def test_perf_rows_and_latest_report(monkeypatch) -> None:
    from frontend import perf_trace as pt

    pt.trace("tool_push", "probe", 1.0, result_bytes=10)  # always persisted
    report = pt.write_report()
    assert pt.read_latest_report()["session_id"] == report["session_id"]
    from backend.store.repos import events

    assert events.count("perf") >= 1


def test_skill_manifest_cache_hits_until_a_reference_changes(appdata: Path, monkeypatch) -> None:
    from backend.skills import store as skills

    pack = appdata / "skill_packs" / "demo"
    (pack / "references").mkdir(parents=True)
    (pack / "SKILL.md").write_text("---\nname: demo\ndescription: Demo pack\n---\nbody\n", encoding="utf-8")
    (pack / "references" / "one.md").write_text("---\ndescription: One\n---\nx\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_plugin_owned_skill_map", lambda: {})
    m1 = skills.load_pack_manifest("demo")
    assert [s["id"] for s in m1["subskills"]] == ["core", "one"]
    assert kv.get_doc("cache_docs", "skill_manifest:demo") is not None
    calls = {"n": 0}
    real = skills._manifest_from_skill_dir

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(skills, "_manifest_from_skill_dir", counting)
    assert skills.load_pack_manifest("demo") == m1 and calls["n"] == 0
    time.sleep(0.01)
    (pack / "references" / "two.md").write_text("---\ndescription: Two\n---\ny\n", encoding="utf-8")
    m2 = skills.load_pack_manifest("demo")
    assert calls["n"] == 1 and [s["id"] for s in m2["subskills"]] == ["core", "one", "two"]


def test_cli_check_stats_and_snapshot(capsys) -> None:
    from frontend import store_cli

    assert store_cli.main(["db", "check"]) == 0
    assert store_cli.main(["db", "stats"]) == 0
    out = json.loads(capsys.readouterr().out.split("\n", 2)[2]) if False else None
    del out
    assert store_cli.main(["db", "snapshot"]) == 0
    assert db.newest_snapshot() is not None
    assert store_cli.main(["db", "export", "settings"]) == 0
    assert store_cli.main(["db", "export", "nope"]) == 2


def test_legacy_dir_is_retired_after_three_clean_boots(appdata: Path) -> None:
    from frontend import appdata_maintenance as am

    legacy = appdata / "legacy" / "chats"
    legacy.mkdir(parents=True)
    (legacy / "old.json").write_text("{}")
    for boot in range(3):
        result = am.maintain_appdata(appdata)
        assert result["db_checked"] == 1
        assert legacy.exists() == (boot < 2)
    assert result["legacy_removed"] == 1


def test_more_logs_import(appdata: Path) -> None:
    from backend.store.importers import phase6
    from backend.store.repos import events

    (appdata / "ui_crashes.jsonl").write_text(json.dumps({"ts": 1.0, "label": "x", "message": "crash", "surface": "chat"}) + "\n")
    (appdata / "uefn_plugin_load_errors.jsonl").write_text(json.dumps({"ts": 2.0, "plugin_id": "demo", "error": "ImportError: nope"}) + "\n")
    phase6.ensure("more_logs")
    assert events.newest("ui_crash", limit=5)[0]["message"] == "crash"
    assert events.newest("plugin_load_error", limit=5)[0]["source"] == "demo"
    assert not (appdata / "ui_crashes.jsonl").exists()
