"""The Call tool node's list must come from host tools only and never connect nested MCPs."""

from __future__ import annotations

import time

import pytest

from backend.uefn_plugins.plugin_host.testing import patch_host


def test_workflow_tool_catalog_is_host_only_and_cached(monkeypatch):
    from frontend.ui_web import mcp_catalog

    def _no_pool(*_a, **_k):
        pytest.fail("workflow tool catalog connected the nested MCP plugin pool")

    monkeypatch.setattr("backend.mcp_plugins.client_pool.get_plugin_pool", _no_pool)
    # Never block on desktop plugin register() from the tool picker.
    patch_host(
        monkeypatch,
        "ensure_plugins_loaded",
        lambda *a, **k: pytest.fail("sync-waited on plugins"),
    )
    patch_host(monkeypatch, "plugins_ready", lambda: False)
    mcp_catalog._WORKFLOW_CACHE.clear()

    first = mcp_catalog.build_workflow_tool_catalog()
    assert first["host_only_catalog"] is True
    names = [row["name"] for row in first["tools"]]
    assert first["total"] == len(names) > 100
    assert "ducky_get_status" in names and "workspace_compile_verse" in names
    assert not any("__" in name for name in names), "nested plugin tools are not callable from a node"
    for row in first["tools"]:
        assert all(p["name"] != "pretty" for p in row["parameters"]), row["name"]
    labels = {c["id"]: c["label"] for c in first["categories"]}
    assert labels.get("panel") == "Panel & chats"

    started = time.perf_counter()
    second = mcp_catalog.build_workflow_tool_catalog()
    assert time.perf_counter() - started < 0.5
    assert [r["name"] for r in second["tools"]] == names


def test_workflow_tool_catalog_skips_async_tools(monkeypatch):
    from frontend.ui_web import mcp_catalog

    patch_host(monkeypatch, "plugins_ready", lambda: False)
    mcp_catalog._WORKFLOW_CACHE.clear()
    from backend.agent.tools import _ensure_mcp

    async_names = {t.name for t in _ensure_mcp()._tool_manager.list_tools() if getattr(t, "is_async", False)}
    listed = {row["name"] for row in mcp_catalog.build_workflow_tool_catalog()["tools"]}
    assert not (async_names & listed)
