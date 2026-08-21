"""mcp.json store: migrate, disable filter, validate."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from backend.mcp_plugins import store


def test_migrate_legacy_folders_and_disabled(tmp_path: Path, monkeypatch) -> None:
    appdata = tmp_path / "UEFN-Ducky"
    plugins = appdata / "mcp_plugins"
    (plugins / "my_tool").mkdir(parents=True)
    (plugins / "my_tool" / "plugin.json").write_text(
        json.dumps(
            {
                "id": "my_tool",
                "label": "My Tool",
                "kind": "custom",
                "server": {"type": "stdio", "command": "echo", "args": ["hi"], "env": {}},
                "tool_prefix": "my_tool",
            }
        ),
        encoding="utf-8",
    )
    (plugins / "catalog_demo").mkdir(parents=True)
    (plugins / "catalog_demo" / "plugin.json").write_text(
        json.dumps(
            {
                "id": "catalog_demo",
                "kind": "catalog",
                "server": {"type": "stdio", "command": "uvx", "args": ["demo-mcp"], "env": {}},
                "tool_prefix": "catalog_demo",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(store, "appdata_dir", lambda: appdata)
    # Avoid pulling real bundled catalog into this tmp appdata.
    monkeypatch.setattr(store, "bundled_mcp_plugins_dir", lambda: None)
    monkeypatch.setattr(store, "_list_bundled_catalog_manifests", lambda: [])

    class _Settings:
        enabled_mcp_plugins = ["my_tool"]

        def save(self) -> None:
            pass

        @classmethod
        def load(cls) -> "_Settings":
            return cls()

    pool = patch("backend.mcp_plugins.client_pool.get_plugin_pool")
    with (
        patch("frontend.settings.PanelSettings", _Settings),
        patch("frontend.settings.replace", lambda s, **kw: type(s)()),
        pool as pool_mock,
    ):
        pool_mock.return_value.invalidate_tools_cache = lambda: None
        pool_mock.return_value.close_plugin = lambda _pid: None
        path = store.ensure_mcp_config()
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        servers = data["mcpServers"]
        assert "my_tool" in servers
        assert servers["my_tool"].get("disabled") is not True
        assert "catalog_demo" in servers
        assert servers["catalog_demo"].get("disabled") is True

        enabled = store.get_enabled_plugin_ids()
        assert enabled == ["my_tool"]

        store.set_mcp_server_enabled("my_tool", False)
        assert "my_tool" not in store.get_enabled_plugin_ids()
        assert store.load_mcp_config()["mcpServers"]["my_tool"]["disabled"] is True

        store.set_mcp_server_enabled("my_tool", True)
        assert "my_tool" in store.get_enabled_plugin_ids()


def test_validate_rejects_bad_shape() -> None:
    try:
        store.validate_mcp_config({"mcpServers": {"bad id": {}}})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        store.validate_mcp_config({"mcpServers": {"ok": {"type": "stdio"}}})
        raise AssertionError("expected ValueError for missing command")
    except ValueError:
        pass


def test_create_and_delete_custom(tmp_path: Path, monkeypatch) -> None:
    appdata = tmp_path / "UEFN-Ducky"
    appdata.mkdir()
    monkeypatch.setattr(store, "appdata_dir", lambda: appdata)
    monkeypatch.setattr(store, "bundled_mcp_plugins_dir", lambda: None)
    monkeypatch.setattr(store, "_list_bundled_catalog_manifests", lambda: [])

    with patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool:
        pool.return_value.invalidate_tools_cache = lambda: None
        pool.return_value.close_plugin = lambda _pid: None
        store.ensure_mcp_config()
        store.create_mcp_server("demo", "Demo", command="uvx", args=["demo-mcp"])
        cfg = store.load_mcp_config()
        assert "demo" in cfg["mcpServers"]
        assert cfg["mcpServers"]["demo"]["disabled"] is True
        assert store.delete_mcp_server("demo") is True
        assert "demo" not in store.load_mcp_config()["mcpServers"]


def test_set_mcp_config_text_roundtrip(tmp_path: Path, monkeypatch) -> None:
    appdata = tmp_path / "UEFN-Ducky"
    appdata.mkdir()
    monkeypatch.setattr(store, "appdata_dir", lambda: appdata)
    monkeypatch.setattr(store, "bundled_mcp_plugins_dir", lambda: None)
    monkeypatch.setattr(store, "_list_bundled_catalog_manifests", lambda: [])

    with patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool:
        pool.return_value.invalidate_tools_cache = lambda: None
        store.ensure_mcp_config()
        text = json.dumps(
            {
                "mcpServers": {
                    "x": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "x"],
                        "disabled": True,
                        "kind": "custom",
                        "label": "X",
                    }
                }
            },
            indent=2,
        )
        store.set_mcp_config_text(text)
        rows = store.list_mcp_servers()
        assert len(rows) == 1
        assert rows[0]["id"] == "x"
        assert rows[0]["enabled"] is False
        assert rows[0]["label"] == "X"


def test_manifest_from_block_uses_catalog_meta(tmp_path: Path, monkeypatch) -> None:
    appdata = tmp_path / "UEFN-Ducky"
    appdata.mkdir()
    bundled = tmp_path / "bundled" / "catalog_demo"
    bundled.mkdir(parents=True)
    (bundled / "plugin.json").write_text(
        json.dumps(
            {
                "id": "catalog_demo",
                "label": "Catalog Demo",
                "kind": "catalog",
                "description": "From bundle",
                "tool_prefix": "catalog_demo",
                "health_probe_tool": "ping",
                "server_windows": {
                    "type": "stdio",
                    "command": "cmd",
                    "args": ["/c", "uvx", "demo-mcp"],
                    "env": {},
                },
                "server_unix": {
                    "type": "stdio",
                    "command": "uvx",
                    "args": ["demo-mcp"],
                    "env": {},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(store, "appdata_dir", lambda: appdata)
    monkeypatch.setattr(store, "bundled_mcp_plugins_dir", lambda: bundled.parent)

    with patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool:
        pool.return_value.invalidate_tools_cache = lambda: None
        store.ensure_mcp_config()
        m = store.load_plugin_manifest("catalog_demo")
        assert m is not None
        assert m["label"] == "Catalog Demo"
        assert m["description"] == "From bundle"
        assert m["kind"] == "catalog"
        assert m["health_probe_tool"] == "ping"
        # Connection comes from mcp.json seed
        assert isinstance(m.get("server"), dict)
        assert m["server"].get("command")


def test_retire_blender_nested_mcp(tmp_path: Path, monkeypatch) -> None:
    """Blender is a Store desktop plugin — purge leftover nested MCP entries."""
    appdata = tmp_path / "UEFN-Ducky"
    appdata.mkdir()
    cfg = appdata / "mcp.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "blender": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["blender-mcp"],
                        "kind": "custom",
                        "label": "Blender",
                    },
                    "keep_me": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "demo"],
                        "kind": "custom",
                        "label": "Keep",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    legacy = appdata / "mcp_plugins" / "blender"
    legacy.mkdir(parents=True)
    (legacy / "plugin.json").write_text('{"id":"blender"}', encoding="utf-8")

    monkeypatch.setattr(store, "appdata_dir", lambda: appdata)
    monkeypatch.setattr(store, "bundled_mcp_plugins_dir", lambda: None)
    monkeypatch.setattr(store, "_list_bundled_catalog_manifests", lambda: [])

    with patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool:
        pool.return_value.invalidate_tools_cache = lambda: None
        store.ensure_mcp_config()
        servers = store.load_mcp_config()["mcpServers"]
        assert "blender" not in servers
        assert "keep_me" in servers
        assert not legacy.is_dir()


def test_http_bind_key_normalizes_localhost() -> None:
    assert store.http_bind_key("http://127.0.0.1:8000/mcp") == "127.0.0.1:8000"
    assert store.http_bind_key("http://localhost:8000/other") == "127.0.0.1:8000"
    assert store.http_bind_key("https://example.com/mcp") == "example.com:443"
    assert store.http_bind_key("") is None


def test_refuse_enable_second_http_on_same_port(tmp_path: Path, monkeypatch) -> None:
    appdata = tmp_path / "UEFN-Ducky"
    appdata.mkdir(parents=True)
    cfg = {
        "mcpServers": {
            "unreal-mcp": {
                "type": "http",
                "url": "http://127.0.0.1:8000/mcp",
                "label": "UEFN MCP (Epic)",
                "kind": "custom",
            },
            "dup": {
                "type": "http",
                "url": "http://localhost:8000/also",
                "label": "Dup",
                "kind": "custom",
                "disabled": True,
            },
        }
    }
    (appdata / "mcp.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(store, "appdata_dir", lambda: appdata)
    monkeypatch.setattr(store, "bundled_mcp_plugins_dir", lambda: None)
    monkeypatch.setattr(store, "_list_bundled_catalog_manifests", lambda: [])

    with patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool:
        pool.return_value.invalidate_tools_cache = lambda: None
        pool.return_value.close_plugin = lambda _pid: None
        result = store.set_mcp_server_enabled("dup", True)
        assert result["ok"] is False
        assert result.get("port_conflict") is True
        assert "8000" in str(result.get("error") or "")
        assert "dup" not in store.get_enabled_plugin_ids()
        assert "unreal-mcp" in store.get_enabled_plugin_ids()


def test_heal_disables_duplicate_enabled_http_ports(tmp_path: Path, monkeypatch) -> None:
    appdata = tmp_path / "UEFN-Ducky"
    appdata.mkdir(parents=True)
    cfg = {
        "mcpServers": {
            "unreal-mcp": {
                "type": "http",
                "url": "http://127.0.0.1:8000/mcp",
                "kind": "custom",
                "label": "UEFN MCP (Epic)",
            },
            "custom-epic": {
                "type": "http",
                "url": "http://127.0.0.1:8000/mcp",
                "kind": "custom",
                "label": "Custom Epic",
            },
        }
    }
    (appdata / "mcp.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(store, "appdata_dir", lambda: appdata)
    monkeypatch.setattr(store, "bundled_mcp_plugins_dir", lambda: None)
    monkeypatch.setattr(store, "_list_bundled_catalog_manifests", lambda: [])

    with patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool:
        pool.return_value.invalidate_tools_cache = lambda: None
        store.ensure_mcp_config()
        servers = store.load_mcp_config()["mcpServers"]
        assert servers["unreal-mcp"].get("disabled") is not True
        assert servers["custom-epic"].get("disabled") is True
        rows = {r["id"]: r for r in store.list_mcp_servers()}
        assert rows["custom-epic"]["enable_blocked_by_port"] is True
        assert "unreal-mcp" in rows["custom-epic"]["port_conflict_with"]


def test_save_mcp_config_rejects_enabled_port_collision(tmp_path: Path, monkeypatch) -> None:
    appdata = tmp_path / "UEFN-Ducky"
    appdata.mkdir(parents=True)
    (appdata / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    monkeypatch.setattr(store, "appdata_dir", lambda: appdata)
    monkeypatch.setattr(store, "bundled_mcp_plugins_dir", lambda: None)
    monkeypatch.setattr(store, "_list_bundled_catalog_manifests", lambda: [])

    with patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool:
        pool.return_value.invalidate_tools_cache = lambda: None
        try:
            store.save_mcp_config(
                {
                    "mcpServers": {
                        "a": {"type": "http", "url": "http://127.0.0.1:9000/mcp"},
                        "b": {"type": "http", "url": "http://127.0.0.1:9000/x"},
                    }
                }
            )
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "9000" in str(exc)
