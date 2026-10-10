"""Phases 5/6: mcp servers, captures, diagnostics cache, digest index, paged loads,
catalog cache, perf rows, skill manifest cache, CLI, legacy retirement."""

from __future__ import annotations

import json
import os
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


def test_digest_trigram_search_matches_substring_scan(tmp_path: Path, monkeypatch) -> None:
    from backend.tools.verse import verse_digests as vd

    # Own AppData: this asserts the live digest_lines row count, which is
    # session-global. Earlier tests leave other digest rows in the shared db.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    db.reset_for_tests()
    try:
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
    finally:
        vd.clear_cache()
        db.reset_for_tests()


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


def test_store_catalog_cache_never_serves_another_account() -> None:
    from frontend.ui_web import panel_api_store as pas

    offline = lambda: {"ok": False, "error": "offline", "items": []}  # noqa: E731
    pas._cache_store_catalog({"ok": True, "items": [{"slug": "alpha-private", "my_team": True}]}, "site|ana")
    assert pas._cache_store_catalog(offline(), "site|ana")["items"][0]["slug"] == "alpha-private"
    # Account switch: B gets nothing, and A's copy is gone too (cleared, not just hidden).
    assert pas._cache_store_catalog(offline(), "site|bo")["items"] == []
    assert pas._cache_store_catalog(offline(), "site|ana")["items"] == []


def test_unchanged_store_catalog_is_not_rewritten(monkeypatch) -> None:
    """The Store tab polls every 10 minutes per window and got the same ~250 KB
    catalog almost every time; each poll rewrote it into the database."""
    import frontend.ui_web.panel_api  # noqa: F401 - panel_api_store is imported through it
    from frontend.ui_web import panel_api_store as pas

    writes: list[str] = []
    real_set_doc = kv.set_doc
    monkeypatch.setattr(kv, "set_doc", lambda t, key, v: (writes.append(key), real_set_doc(t, key, v)))
    for _ in range(3):
        pas._cache_store_catalog({"ok": True, "items": [{"slug": "anthropic", "version": "1.0.0"}]}, "site|ana")
    assert writes == ["store_catalog"]
    pas._cache_store_catalog({"ok": True, "items": [{"slug": "anthropic", "version": "1.0.1"}]}, "site|ana")
    assert writes == ["store_catalog", "store_catalog"]
    offline = pas._cache_store_catalog({"ok": False, "error": "offline", "items": []}, "site|ana")
    assert offline["items"][0]["version"] == "1.0.1"


def test_perf_rows_and_latest_report(monkeypatch) -> None:
    from frontend import perf_trace as pt

    pt.trace("tool_push", "probe", 1.0, result_bytes=10)  # always persisted
    report = pt.write_report()
    assert pt.read_latest_report()["session_id"] == report["session_id"]
    from backend.store.repos import events

    assert events.count("perf") >= 1


@pytest.fixture
def quiet_perf(monkeypatch):
    """perf_trace with its background report thread stopped and a fresh ring."""
    from frontend import perf_trace as pt

    stop = pt._stop_writer
    stop.set()
    if pt._writer_thread is not None:
        pt._writer_thread.join(timeout=5)
    monkeypatch.setattr(pt, "ensure_started", lambda: None)
    monkeypatch.setattr(pt, "_session_id", "session-20261010-000000-1")
    monkeypatch.setattr(pt, "_ring", [])
    for name, value in (("_pending_rows", []), ("_ring_seq", 0), ("_reported_seq", 0), ("_last_trim", None)):
        monkeypatch.setattr(pt, name, value, raising=False)
    yield pt
    stop.clear()


class _Waits:
    """Stands in for the report thread's stop event: *n* ticks, then stop."""

    def __init__(self, n: int) -> None:
        self.left = n

    def wait(self, _timeout: float) -> bool:
        self.left -= 1
        return self.left < 0


def test_perf_events_are_written_in_one_transaction_per_report(quiet_perf, monkeypatch) -> None:
    """Every notable perf event was its own BEGIN IMMEDIATE/COMMIT; the UI
    reports up to 200 samples per flush, and every ~100 KB evaluate_js push was
    persisted for its size alone (36,565 rows at a 4 ms median)."""
    from backend.store.repos import events

    pt = quiet_perf
    single: list[int] = []
    batches: list[int] = []
    monkeypatch.setattr(events, "insert", lambda *a, **k: single.append(1))
    monkeypatch.setattr(events, "insert_many", lambda kind, rows: batches.append(len(rows)) or len(rows))
    for _ in range(500):
        pt.trace("ui_frame", "raf_gap", 300.0)
    pt.trace("ui_js", "evaluate_js", 4.0, js_bytes=105_656)
    assert single == [] and batches == []
    pt.write_report()
    assert single == []
    assert batches == [500]


def test_idle_report_loop_does_not_rewrite_reports_or_trim(quiet_perf, monkeypatch) -> None:
    """The report thread rewrote two report docs and ran a 50,000-row trim
    every 15 s even when nothing had been traced since the last report."""
    from backend.store.repos import events

    pt = quiet_perf
    writes: list[str] = []
    trims: list[str] = []
    real_set_doc = kv.set_doc
    monkeypatch.setattr(kv, "set_doc", lambda table, key, value: (writes.append(key), real_set_doc(table, key, value)))
    monkeypatch.setattr(events, "trim", lambda kind, **k: trims.append(kind) or 0)
    pt.trace("tool_push", "probe", 1.0, result_bytes=10)
    monkeypatch.setattr(pt, "_stop_writer", _Waits(3))
    pt._report_loop()
    assert sorted(writes) == ["perf_report:session-20261010-000000-1", "perf_report_latest"]
    assert trims == ["perf"]


def test_old_perf_session_reports_are_pruned(quiet_perf, monkeypatch) -> None:
    """One perf_report:session-* doc per process start (bridges included)
    piled up forever in database mode: 1,490 docs, none ever removed."""
    pt = quiet_perf
    for i in range(15):
        kv.set_doc("cache_docs", f"perf_report:session-20261001-{i:06d}-1", {"i": i})
    kv.set_doc("cache_docs", "perf_report_latest", {"keep": True})
    monkeypatch.setattr(pt, "_stop_writer", _Waits(0))
    pt._report_loop()
    left = sorted(kv.list_docs("cache_docs", "perf_report:session-"))
    assert len(left) == pt.MAX_SESSIONS
    assert left[0] == "perf_report:session-20261001-000005-1"  # the newest ones stay
    assert kv.get_doc("cache_docs", "perf_report_latest") == {"keep": True}


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


def test_store_installed_skill_pack_hits_the_manifest_cache(appdata: Path, monkeypatch) -> None:
    """A pack installed from the Store was cached under kind "store" but looked
    up as "custom", so every load re-parsed the whole pack and rewrote its row."""
    from backend.skills import store as skills

    pack = appdata / "skill_packs" / "shop-pack"
    (pack / "references").mkdir(parents=True)
    (pack / "SKILL.md").write_text(
        "---\nname: shop-pack\ndescription: From the Store\nmetadata:\n  source: store\n---\nbody\n", encoding="utf-8"
    )
    (pack / "references" / "one.md").write_text("---\ndescription: One\n---\nx\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_plugin_owned_skill_map", lambda: {})
    parses: list[str] = []
    writes: list[str] = []
    real_parse = skills._manifest_from_skill_dir
    real_set_doc = kv.set_doc
    monkeypatch.setattr(skills, "_manifest_from_skill_dir", lambda *a, **k: (parses.append(a[2]), real_parse(*a, **k))[1])
    monkeypatch.setattr(kv, "set_doc", lambda t, key, v: (writes.append(key), real_set_doc(t, key, v)))
    first = skills.load_pack_manifest("shop-pack")
    assert parses == ["store"]
    for _ in range(2):
        assert skills.load_pack_manifest("shop-pack") == first
    assert parses == ["store"]
    assert writes == ["skill_manifest:shop-pack"]


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


def _last_integrity_row() -> tuple:
    return tuple(db.connect().execute("SELECT value, updated FROM meta WHERE key='last_integrity'").fetchone())


def test_bridge_skips_the_store_check_the_panel_already_did_today(appdata: Path, monkeypatch) -> None:
    """Every coding-agent turn starts a bridge, and each bridge ran PRAGMA
    integrity_check over the whole database (1.4 s of CPU on a 247 MB store),
    wrote meta and walked AppData, while the panel had checked it at boot."""
    from frontend import appdata_maintenance as am

    am.maintain_appdata(appdata)  # the panel boot
    before = _last_integrity_row()
    calls: list[str] = []
    monkeypatch.setattr(db, "integrity_check", lambda conn: calls.append("check") or "ok")
    monkeypatch.setattr(am, "prune_empty_project_dirs", lambda *a, **k: calls.append("walk") or 0)
    for _ in range(5):
        am.maintain_appdata(appdata, count_boot=False)
    assert calls == []
    assert _last_integrity_row() == before


def test_bridge_still_checks_the_store_when_nothing_did_today(appdata: Path, monkeypatch) -> None:
    from frontend import appdata_maintenance as am

    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value, updated) VALUES ('last_integrity', ?, ?)",
            (json.dumps({"result": "ok", "ts": time.time() - 2 * 86400}), time.time() - 2 * 86400),
        )
    calls: list[str] = []
    real = db.integrity_check
    monkeypatch.setattr(db, "integrity_check", lambda c: calls.append("check") or real(c))
    assert am.maintain_appdata(appdata, count_boot=False)["db_checked"] == 1
    assert calls == ["check"]


def test_backups_age_out_in_database_mode(appdata: Path) -> None:
    """In database mode nothing pruned backups/, so one-time migration copies
    (a 172 MB ducky.db.bak and a 128 MB copied data tree) stayed forever."""
    from frontend import appdata_maintenance as am

    am.maintain_appdata(appdata)  # first boot: the one-time importers have run
    backups = appdata / "backups"
    old = time.time() - 31 * 86400
    old_db = backups / "ducky.db.bak.20200101-000000"
    old_tree = backups / "p3-host-data-x" / "chats"
    old_tree.mkdir(parents=True)
    old_db.write_bytes(b"x")
    (old_tree / "a.json").write_text("{}")
    for path in (old_db, old_tree / "a.json"):
        os.utime(path, (old, old))
    recent = backups / "workflow_editor.json.bak.20261010000000"
    recent.write_text("{}")
    recent_foreign = backups / "manual-copy" / "notes.txt"
    recent_foreign.parent.mkdir()
    recent_foreign.write_text("keep")
    am.maintain_appdata(appdata)
    assert not old_db.exists()
    assert not (backups / "p3-host-data-x").exists()
    assert recent.is_file() and recent_foreign.is_file()


def test_more_logs_import(appdata: Path) -> None:
    from backend.store.importers import phase6
    from backend.store.repos import events

    (appdata / "ui_crashes.jsonl").write_text(json.dumps({"ts": 1.0, "label": "x", "message": "crash", "surface": "chat"}) + "\n")
    (appdata / "uefn_plugin_load_errors.jsonl").write_text(json.dumps({"ts": 2.0, "plugin_id": "demo", "error": "ImportError: nope"}) + "\n")
    phase6.ensure("more_logs")
    assert events.newest("ui_crash", limit=5)[0]["message"] == "crash"
    assert events.newest("plugin_load_error", limit=5)[0]["source"] == "demo"
    assert not (appdata / "ui_crashes.jsonl").exists()
