"""ducky_plugin_test on tools and nodes that wait by design (a timeout parameter):
they get a short timeout and pass when they give up cleanly; a real hang still fails."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from backend.tools.panel import panel_plugin_check as check


def _waiter(timeout_sec: float) -> dict[str, Any]:
    deadline = time.monotonic() + float(timeout_sec)
    while time.monotonic() < deadline:
        time.sleep(0.02)
    return {"ok": True, "found": False, "timed_out": True}


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    monkeypatch.setattr(check, "_WAIT_TEST_S", 0.2)
    monkeypatch.setattr(check, "_TOOL_TIMEOUT_S", 1.5)
    release = threading.Event()
    yield release
    release.set()  # lets the hung worker end


def test_short_timeouts_only_touch_numeric_timeout_fields() -> None:
    fields = {
        "timeout_sec": {"type": "number", "default": 180},
        "timeout_ms": {"type": "integer"},
        "Timeout": {"type": ["number", "null"]},
        "timeout_label": {"type": "number"},
        "timeout_s": {"type": "string"},
        "pattern": {"type": "string"},
    }
    got = check.short_timeouts(fields)
    assert got == {"timeout_sec": check._WAIT_TEST_S, "timeout_ms": check._WAIT_TEST_S * 1000,
                   "Timeout": check._WAIT_TEST_S}
    assert check.short_timeouts(None) == {}


def test_a_waiting_tool_gets_a_short_timeout_and_a_hang_still_fails(quick, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.uefn_plugins import host

    def wait(pattern: str, timeout_sec: float = 180.0) -> dict[str, Any]:
        return _waiter(timeout_sec)

    def hang(pattern: str) -> dict[str, Any]:
        quick.wait(30)
        return {"ok": True}

    def schema(**props: Any) -> dict[str, Any]:
        return {"type": "object", "properties": {"pattern": {"type": "string"}, **props}, "required": ["pattern"]}

    tools = {
        "demo_wait": SimpleNamespace(fn=wait, parameters=schema(timeout_sec={"type": "number", "default": 180})),
        "demo_hang": SimpleNamespace(fn=hang, parameters=schema()),
    }
    monkeypatch.setattr(host, "get_contributions", lambda: {"agent_tools": {"demo": {"tools": list(tools)}}})
    monkeypatch.setattr(check.mcp._tool_manager, "get_tool", lambda name: tools.get(name))

    checks: list[dict[str, Any]] = []
    stragglers: list[threading.Thread] = []
    check._check_tools("demo", checks, stragglers)
    rows = {c["check"]: c for c in checks}

    waited = rows["tool demo_wait"]
    assert waited["status"] == "pass", waited
    assert waited["input"] == {"pattern": "", "timeout_sec": check._WAIT_TEST_S}
    assert "timed_out" in waited["result"] and "note" not in waited
    hung = rows["tool demo_hang"]
    assert hung["status"] == "fail" and "TimeoutError" in hung["detail"]
    assert len(stragglers) == 1


def test_a_waiting_node_gets_a_short_timeout_in_its_config(quick, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.automations import plugin

    seen: list[dict[str, Any]] = []

    def node_wait(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(ctx["config"]))
        return _waiter(ctx["config"].get("timeout_sec") or 180)

    monkeypatch.setattr(plugin, "node_types", lambda: [("demo.wait", "demo")])
    monkeypatch.setattr(plugin, "get_handler", lambda ntype: node_wait if ntype == "demo.wait" else None)
    manifest = {"contributes": {"automations": {"nodes": [{"id": "demo.wait", "config_fields": [
        {"id": "pattern", "type": "string", "default": "arrived"},
        {"id": "timeout_sec", "type": "number", "default": 180},
    ]}]}}}

    checks: list[dict[str, Any]] = []
    check._check_nodes("demo", manifest, checks, [])

    assert seen == [{"pattern": "arrived", "timeout_sec": check._WAIT_TEST_S}]
    assert checks[0]["status"] == "pass" and "waits by design" in checks[0]["detail"], checks


def test_the_test_syncs_the_draft_from_its_repo_first(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from backend.tools.panel import panel_ai_plugins
    from backend.uefn_plugins import store

    drafts = tmp_path / "drafts"
    monkeypatch.setattr(store, "appdata_ai_plugins_dir", lambda: drafts)
    monkeypatch.setattr(panel_ai_plugins, "install_ai_plugin", lambda pid: {"ok": False, "error": "stop here"})
    repo = tmp_path / "uefn-plugin-demo"
    (repo / "backend").mkdir(parents=True)
    (repo / "plugin.json").write_text(json.dumps({"id": "demo"}), encoding="utf-8")
    (repo / "backend" / "drive.py").write_text("V = 1\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1", encoding="utf-8")
    draft = drafts / "demo"
    (draft / "ui").mkdir(parents=True)
    (draft / "plugin.json").write_text(json.dumps({"id": "demo"}), encoding="utf-8")
    (draft / "ui" / "old.html").write_text("old shell", encoding="utf-8")

    first = check.test_plugin("demo", repo=str(repo))
    row = next(c for c in first["checks"] if c["check"] == "repo sync")
    assert row["files"] == ["backend/drive.py"] and row["draft_only"] == ["ui/old.html"], row
    assert "synced 1 file from uefn-plugin-demo" in row["detail"]
    assert not (draft / ".env").exists()

    # The link is remembered: a changed repo file reaches the draft on the next plain run.
    (repo / "backend" / "drive.py").write_text("V = 2\n", encoding="utf-8")
    second = check.test_plugin("demo")
    assert (draft / "backend" / "drive.py").read_text(encoding="utf-8") == "V = 2\n"
    assert next(c for c in second["checks"] if c["check"] == "repo sync")["files"] == ["backend/drive.py"]

    (repo / "plugin.json").write_text(json.dumps({"id": "other"}), encoding="utf-8")
    assert check.test_plugin("demo")["ok"] is False


def test_a_write_that_reaches_the_users_data_fails_the_test(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.store import db
    from backend.store.repos import plugin_kv
    from backend.uefn_plugins import scopes

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("DUCKY_STORE_BACKEND", raising=False)
    db.reset_for_tests()
    try:
        real = {"account": "_local", "scope": "personal"}
        plugin_kv.set("webgpu", "downloads", [{"name": "Web Engine 5.8.1"}], **real)
        test_copy = {"account": "_local", "scope": scopes.SANDBOX_PREFIX + "abc"}
        before = check._real_data("webgpu")

        # A write into the test copy (what api data does under test) leaves the user's copy alone.
        plugin_kv.set("webgpu", "downloads", [{"name": "Web Engine 5.8.2-web4"}], **test_copy)
        plugin_kv.set("other", "downloads", [{"name": "not this plugin"}], **real)
        assert check._check_untouched(before, check._real_data("webgpu"))["status"] == "pass"

        # One that lands in the user's copy (a worker started outside the test) fails, named.
        plugin_kv.set("webgpu", "job:web4", {"name": "Web Engine 5.8.2-web4"}, **real)
        row = check._check_untouched(before, check._real_data("webgpu"))
        assert row["status"] == "fail" and row["added"] == ["plugin_kv personal/job:web4"], row
        assert "changed" not in row
        assert plugin_kv.get("webgpu", "downloads", **real)[0] == [{"name": "Web Engine 5.8.1"}]
    finally:
        db.reset_for_tests()


def test_findable_data_names_the_doc_kinds_without_a_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # Oct 26 2026: ducky_find matches a doc by its name/title/label; a kind without one is
    # found only by its key (project.mvbtpazj), so the test says which kinds to name.
    from backend.uefn_plugins import scopes

    docs: dict[str, Any] = {}

    class _Data:
        def __init__(self, pid: str) -> None:
            pass

        def items(self, prefix: str = "") -> dict[str, Any]:
            return dict(docs)

    monkeypatch.setattr(scopes, "PluginData", _Data)
    assert check._check_data_names("p")["status"] == "skip"
    docs.update({"project.a": {"name": "UEFN Ducky Promo"}, "project.b": {"title": "Trailer"},
                 "job.1": {"state": "done"}, "job.2": {"state": "queued"}, "card.c": {"label": " "}})
    row = check._check_data_names("p")
    assert row["status"] == "pass" and row["detail"].startswith("1 of 3 doc kinds")
    assert [n.split(" ")[0] for n in row["name_these"]] == ["card.*", "job.*"]
