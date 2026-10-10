"""Read-only diagnostics: observed inventory is not runtime exposure."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from mcp.types import Tool
from frontend.ui_web import mcp_catalog as catalog


@pytest.fixture
def observations(monkeypatch):
    rows = [{"id": "docs", "enabled": True, "kind": "custom", "url": "https://SECRET", "env": {"TOKEN": "SECRET"}}]
    tools = [Tool(name="docs__read_page", inputSchema={}, annotations={"readOnlyHint": True, "destructiveHint": False}),
             Tool(name="docs__write_page", inputSchema={}, description="SECRET")]
    snapshot = {"servers": rows, "inventories": {"docs": tools}, "failures": set(), "complete": True}
    monkeypatch.setattr(catalog, "_diagnostic_observations", lambda: snapshot, raising=False)
    return snapshot


def test_inventory_does_not_prove_runtime_stages(observations):
    report = catalog.build_mcp_diagnostics("docs")
    row = report["servers"][0]
    assert row["status"] == "unknown"
    assert row["counts"] == {"catalog": 2, "policy_blocked": 0, "unexposed_by_filter": 0}
    assert row["stages"] == {"started": None, "connected": None, "model_exposed": None}
    assert row["recent_error"] == "unobserved"
    assert report["stage_counts"] == {"catalog_tools": 2, "started_servers": None, "connected_servers": None, "model_exposed_tools": None}


def test_offline_keeps_cached_inventory_and_unknown_exposure(observations):
    observations["failures"].add("docs")
    row = catalog.build_mcp_diagnostics("docs")["servers"][0]
    assert row["status"] == "offline"
    assert row["counts"]["catalog"] == 2
    assert row["recent_error"] == "inventory_refresh_failed"
    assert row["stages"]["model_exposed"] is None
    assert "retry" in row["guidance"].lower()


def test_disabled_missing_and_unobserved_are_distinct(observations):
    observations["servers"][0]["enabled"] = False
    assert catalog.build_mcp_diagnostics("docs")["servers"][0]["status"] == "disabled"
    assert catalog.build_mcp_diagnostics("absent")["servers"][0]["status"] == "missing"
    observations["complete"] = False
    assert catalog.build_mcp_diagnostics("absent")["servers"][0]["status"] == "unknown"
    observations["inventories"].clear()
    assert catalog.build_mcp_diagnostics("docs")["servers"][0]["counts"]["catalog"] is None


def test_policy_and_filter_exclusions_do_not_claim_model_exposure(observations, monkeypatch):
    monkeypatch.setattr(catalog, "EXCLUDED_TOOLS", {"docs__read_page"})
    row = catalog.build_mcp_diagnostics("docs", "ask")["servers"][0]
    assert row["counts"]["policy_blocked"] == 1
    assert row["counts"]["unexposed_by_filter"] == 1
    assert row["tools"][0]["name"] == "docs__read_page"
    assert row["tools"][0]["status"] == "unexposed"
    assert row["tools"][1]["status"] == "policy-blocked"
    assert "Ask mode" in row["tools"][1]["mode_reason"]
    assert row["stages"]["model_exposed"] is None


def test_privacy_invalid_identity_and_invalid_mode(observations):
    observations["servers"].append({"id": "C:/SECRET/private", "enabled": True})
    observations["inventories"]["docs"].append(Tool(name="SECRET/path", inputSchema={}))
    report = catalog.build_mcp_diagnostics()
    encoded = json.dumps(report)
    assert "SECRET" not in encoded and "TOKEN" not in encoded
    assert len(report["correlation_id"]) == 32
    assert all(c in "0123456789abcdef" for c in report["correlation_id"])
    assert "SECRET" not in json.dumps(catalog.build_mcp_diagnostics("C:/SECRET/private"))
    with pytest.raises(ValueError, match="Invalid agent mode"):
        catalog.build_mcp_diagnostics(mode="secret-mode")


def test_empty_inventory_is_known_zero_not_unobserved(observations):
    observations["inventories"]["docs"] = []
    assert catalog.build_mcp_diagnostics("docs")["servers"][0]["counts"]["catalog"] == 0


def test_observation_error_never_echoes_exception(monkeypatch):
    def fail():
        raise RuntimeError("https://secret:password@host C:/private/chat TOKEN=SECRET")
    monkeypatch.setattr(catalog, "_diagnostic_observations", fail)
    report = catalog.build_mcp_diagnostics("docs")
    assert report["observation_error"] == "observation_unavailable"
    assert report["servers"][0]["status"] == "unknown"
    assert "SECRET" not in json.dumps(report) and "password" not in json.dumps(report)


def test_bounded_details_preserve_full_counts(observations):
    observations["inventories"]["docs"] = [Tool(name=f"docs__write_{n}", inputSchema={}) for n in range(70)]
    row = catalog.build_mcp_diagnostics("docs", "ask")["servers"][0]
    assert row["counts"]["catalog"] == row["counts"]["policy_blocked"] == 70
    assert len(row["tools"]) == 50 and row["tools_truncated"]


def test_agent_and_settings_report_parity_without_discovery_connect(observations, monkeypatch):
    from backend.agent import run_context, tools
    from backend.tools.panel import ducky_panel
    from frontend.ui_web import panel_api  # Bootstrap the existing mixin import cycle.
    from frontend.ui_web.panel_api_store import PanelApiStoreMixin
    monkeypatch.setattr(tools, "list_mcp_tools", lambda **kw: pytest.fail("diagnostics must not discover/connect"))
    observations["failures"].add("docs")
    token = run_context.set_mode("ask")
    try:
        agent = json.loads(asyncio.run(ducky_panel.ducky_get_tools(diagnostics=True, server_id="docs")))
    finally:
        run_context.reset_mode(token)
    ui = PanelApiStoreMixin.get_mcp_diagnostics(NS(), "docs", "ask")
    assert agent.pop("correlation_id") != ui.pop("correlation_id")
    assert agent == ui and agent["mode"] == "ask"
    # The rendered UI tests consume this same public contract, not a second DTO.
    fixture = json.loads((Path(__file__).parent / "web/src/views/settings/McpDiagnostics.fixture.json").read_text(encoding="utf-8"))
    fixture.pop("correlation_id")
    assert agent == fixture


def test_actual_collector_does_not_create_pool_or_load_plugins(monkeypatch):
    from backend.mcp_plugins import client_pool, store
    from backend.uefn_plugins import host
    from backend.agent import builtin_toolsets, tools
    monkeypatch.setattr(client_pool, "_pool", None)
    monkeypatch.setattr(client_pool, "get_plugin_pool", lambda: pytest.fail("must not create pool"))
    monkeypatch.setattr(tools, "_ensure_mcp", lambda: pytest.fail("must not register tools"))
    monkeypatch.setattr(host, "plugins_ready", lambda: False)
    monkeypatch.setattr(host, "uefn_plugin_tool_group_rows", lambda: pytest.fail("must not load plugins"))
    monkeypatch.setattr(store, "list_mcp_servers", lambda: [{"id": "docs", "enabled": True}])
    monkeypatch.setattr(builtin_toolsets, "builtin_group_rows", lambda: [])
    row = catalog.build_mcp_diagnostics("docs")["servers"][0]
    assert row["counts"]["catalog"] is None and row["status"] == "unknown"


def test_actual_collector_uses_existing_failed_inventory_without_mutation(monkeypatch):
    from backend.mcp_plugins import client_pool, store
    from backend.uefn_plugins import host
    from backend.agent import builtin_toolsets
    cached = [Tool(name="docs__read", inputSchema={})]
    pool = NS(_inventories={"docs": cached}, _inventory_failures={"docs": 2})
    monkeypatch.setattr(client_pool, "_pool", pool)
    monkeypatch.setattr(host, "plugins_ready", lambda: False)
    monkeypatch.setattr(store, "list_mcp_servers", lambda: [{"id": "docs", "enabled": True}])
    monkeypatch.setattr(builtin_toolsets, "builtin_group_rows", lambda: [])
    row = catalog.build_mcp_diagnostics("docs")["servers"][0]
    assert row["status"] == "offline" and row["counts"]["catalog"] == 1
    assert pool._inventories["docs"] is cached and cached[0].meta is None


def test_actual_session_health_and_http_failure_expiry(monkeypatch):
    from backend.mcp_plugins import client_pool, store
    from backend.uefn_plugins import host
    from backend.agent import builtin_toolsets
    conn = NS(retired=False, session=object(), owner=NS(alive=True))
    pool = NS(_connections={"docs": conn}, _failed_until={"docs": 105})
    monkeypatch.setattr(client_pool, "_pool", pool)
    monkeypatch.setattr(catalog.time, "time", lambda: 100)
    monkeypatch.setattr(host, "plugins_ready", lambda: False)
    monkeypatch.setattr(store, "list_mcp_servers", lambda: [{"id": "docs", "enabled": True}])
    monkeypatch.setattr(builtin_toolsets, "builtin_group_rows", lambda: [])
    row = catalog.build_mcp_diagnostics("docs")["servers"][0]
    assert row["status"] == "offline" and row["recent_error"] == "http_connection_failed"
    pool._failed_until["docs"] = 99
    row = catalog.build_mcp_diagnostics("docs")["servers"][0]
    assert row["status"] == "connected"
    assert row["stages"] == {"started": None, "connected": True, "model_exposed": None}
    for field in ("retired", "dead", "closing"):
        conn.retired, conn.owner.alive = field == "retired", field != "dead"
        pool._closing = field == "closing"
        row = catalog.build_mcp_diagnostics("docs")["servers"][0]
        assert row["stages"]["connected"] is None
