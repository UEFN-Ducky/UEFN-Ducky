"""Nested Epic unreal-mcp catalog, fast-fail, prefix routing, pruned Ducky tools."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

from backend.mcp_plugins import epic, store
from backend.mcp_plugins.client_pool import PluginClientPool
from backend.mcp_plugins.epic import EPIC_MCP_DEFAULT_URL, EPIC_MCP_SERVER_ID, EPIC_PRUNED_DUCKY_TOOLS
from backend.mcp_plugins.registry import namespace_tool_name, parse_plugin_tool, rebuild_plugin_prefix_cache


def test_catalog_plugin_json_is_http_unreal_mcp() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "mcp_plugins"
        / "unreal-mcp"
        / "plugin.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["id"] == EPIC_MCP_SERVER_ID
    assert data["kind"] == "catalog"
    assert data["default_enabled"] is True
    assert data["tool_prefix"] == "unreal"
    assert data["server"]["type"] == "http"
    assert data["server"]["url"] == EPIC_MCP_DEFAULT_URL
    assert data["setup_steps"]


def test_catalog_seeds_mcp_json(tmp_path: Path, monkeypatch) -> None:
    appdata = tmp_path / "UEFN-Ducky"
    appdata.mkdir()
    bundled = Path(__file__).resolve().parents[2] / "frontend" / "mcp_plugins"
    monkeypatch.setattr(store, "appdata_dir", lambda: appdata)
    monkeypatch.setattr(store, "bundled_mcp_plugins_dir", lambda: bundled)

    class _Settings:
        enabled_mcp_plugins: list[str] = []

        def save(self) -> None:
            pass

        @classmethod
        def load(cls) -> "_Settings":
            return cls()

    with (
        patch("frontend.settings.PanelSettings", _Settings),
        patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool,
    ):
        pool.return_value.invalidate_tools_cache = lambda: None
        store.ensure_mcp_config()
        cfg = store.load_mcp_config()
        block = cfg["mcpServers"][EPIC_MCP_SERVER_ID]
        assert block.get("type") == "http"
        assert block.get("url") == EPIC_MCP_DEFAULT_URL
        assert block.get("disabled") is not True
        assert EPIC_MCP_SERVER_ID in store.get_enabled_plugin_ids()
        manifest = store.load_plugin_manifest(EPIC_MCP_SERVER_ID)
        assert manifest is not None
        assert manifest["tool_prefix"] == "unreal"
        assert manifest["kind"] == "catalog"


def test_http_down_editor_list_tools_fails_fast(monkeypatch) -> None:
    monkeypatch.setattr("backend.mcp_plugins.epic.tcp_probe_url", lambda *_a, **_k: False)
    pool = PluginClientPool()
    manifest = {
        "id": EPIC_MCP_SERVER_ID,
        "tool_prefix": "unreal",
        "server": {"type": "http", "url": EPIC_MCP_DEFAULT_URL},
    }
    monkeypatch.setattr(
        "backend.mcp_plugins.client_pool.load_plugin_manifest",
        lambda _pid: manifest,
    )
    monkeypatch.setattr(
        "backend.mcp_plugins.client_pool.resolve_server_block",
        lambda _m: {"type": "http", "url": EPIC_MCP_DEFAULT_URL, "headers": {}},
    )
    t0 = time.perf_counter()
    try:
        asyncio.run(pool.list_tools_for_plugin(EPIC_MCP_SERVER_ID))
        raise AssertionError("expected ConnectionError")
    except ConnectionError as exc:
        assert "unreachable" in str(exc).lower()
    assert time.perf_counter() - t0 < 3.0


def test_unreal_prefix_routes_to_plugin_id() -> None:
    manifest = {"id": EPIC_MCP_SERVER_ID, "tool_prefix": "unreal"}
    rebuild_plugin_prefix_cache([manifest])
    assert namespace_tool_name(manifest, "compile_verse") == "unreal__compile_verse"
    assert parse_plugin_tool("unreal__compile_verse") == (EPIC_MCP_SERVER_ID, "compile_verse")


def test_pruned_ducky_tools_are_not_registered() -> None:
    from backend.tools.scene import scene_graph
    from backend.tools.uefn import device_focused, editor

    leaked = EPIC_PRUNED_DUCKY_TOOLS & (
        set(dir(scene_graph)) | set(dir(editor)) | set(dir(device_focused))
    )
    assert not leaked, leaked
    assert hasattr(scene_graph, "instantiate_prefab")
    assert hasattr(scene_graph, "create_empty_prefab")


def test_tcp_probe_closed_port_is_false() -> None:
    assert epic.tcp_probe_url("http://127.0.0.1:1/mcp", timeout=0.2) is False


def test_ensure_editor_auto_start_appends_and_flips(tmp_path: Path) -> None:
    ini = tmp_path / "Editor.ini"
    ini.write_text("[/Script/Foo]\nBar=1\n", encoding="utf-8")
    assert epic.ensure_editor_auto_start(ini) is True
    text = ini.read_text(encoding="utf-8")
    assert "bAutoStartServer=True" in text
    assert epic.ensure_editor_auto_start(ini) is False
    ini.write_text("[/Script/Foo]\nbAutoStartServer=False\n", encoding="utf-8")
    assert epic.ensure_editor_auto_start(ini) is True
    assert "bAutoStartServer=True" in ini.read_text(encoding="utf-8")
