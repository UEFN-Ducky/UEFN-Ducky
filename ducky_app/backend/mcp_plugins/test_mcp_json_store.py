"""Nested MCP store: migrate, disable filter, validate. Rows in ducky.db."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.mcp_plugins import store


@pytest.fixture(autouse=True)
def _no_nested_sync(monkeypatch):
    """Server edits start a real nested-MCP sync thread (it spawns the server's command).
    It outlived the test, opened the next test's ducky.db and held its WAL open, so
    test_db's corrupt-restore tests failed with WinError 32."""
    from backend.mcp_plugins import bridge_proxy

    monkeypatch.setattr(bridge_proxy, "schedule_sync_nested_proxies", lambda: None)


@pytest.fixture(autouse=True)
def _fresh_row_store():
    """Each test owns its own server map in ducky.db."""
    from backend.store.importers import phase1
    from backend.store.repos import misc

    misc.mcp_servers_reset_for_tests()
    phase1.reset_for_tests()
    path = store.mcp_config_path()
    if path.is_file():
        try:
            path.unlink()
        except PermissionError:
            # Windows: another pytest/panel handle can hold mcp.json for a beat.
            pass
    yield
    misc.mcp_servers_reset_for_tests()
    phase1.reset_for_tests()


def _no_catalog(monkeypatch) -> Path:
    """Same AppData the isolated ducky.db uses — do not point appdata_dir elsewhere."""
    appdata = store.appdata_dir()
    appdata.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "bundled_mcp_plugins_dir", lambda: None)
    monkeypatch.setattr(store, "_list_bundled_catalog_manifests", lambda: [])
    return appdata


def test_migrate_legacy_folders_and_disabled(monkeypatch) -> None:
    appdata = _no_catalog(monkeypatch)
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
        store.ensure_mcp_config()
        servers = store.load_mcp_config()["mcpServers"]
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


def test_create_and_delete_custom(monkeypatch) -> None:
    _no_catalog(monkeypatch)

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


def test_set_mcp_config_text_roundtrip(monkeypatch) -> None:
    _no_catalog(monkeypatch)

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
    store.appdata_dir().mkdir(parents=True, exist_ok=True)
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
        # Connection comes from the DB seed
        assert isinstance(m.get("server"), dict)
        assert m["server"].get("command")


def test_retire_blender_nested_mcp(monkeypatch) -> None:
    """Blender is a Store desktop plugin — purge leftover nested MCP entries."""
    appdata = _no_catalog(monkeypatch)
    legacy = appdata / "mcp_plugins" / "blender"
    legacy.mkdir(parents=True)
    (legacy / "plugin.json").write_text('{"id":"blender"}', encoding="utf-8")

    with patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool:
        pool.return_value.invalidate_tools_cache = lambda: None
        store._write_servers(
            {
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
        )
        from backend.store.repos import misc

        assert set(misc.mcp_servers_get()) == {"blender", "keep_me"}
        servers = dict(misc.mcp_servers_get())
        store._retire_moved_to_desktop_plugin_mcp(servers)
        store._write_servers(servers)
        assert "blender" not in misc.mcp_servers_get()
        assert "keep_me" in misc.mcp_servers_get()
        assert not legacy.is_dir()


def test_http_bind_key_normalizes_localhost() -> None:
    assert store.http_bind_key("http://127.0.0.1:8000/mcp") == "127.0.0.1:8000"
    assert store.http_bind_key("http://localhost:8000/other") == "127.0.0.1:8000"
    assert store.http_bind_key("https://example.com/mcp") == "example.com:443"
    assert store.http_bind_key("") is None


def test_refuse_enable_second_http_on_same_port(monkeypatch) -> None:
    _no_catalog(monkeypatch)
    store._write_servers(
        {
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
    )

    with patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool:
        pool.return_value.invalidate_tools_cache = lambda: None
        pool.return_value.close_plugin = lambda _pid: None
        result = store.set_mcp_server_enabled("dup", True)
        assert result["ok"] is False
        assert result.get("port_conflict") is True
        assert "8000" in str(result.get("error") or "")
        assert "dup" not in store.get_enabled_plugin_ids()
        assert "unreal-mcp" in store.get_enabled_plugin_ids()


def test_heal_disables_duplicate_enabled_http_ports(monkeypatch) -> None:
    _no_catalog(monkeypatch)
    store._write_servers(
        {
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
    )

    with patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool:
        pool.return_value.invalidate_tools_cache = lambda: None
        store.ensure_mcp_config()
        servers = store.load_mcp_config()["mcpServers"]
        assert servers["unreal-mcp"].get("disabled") is not True
        assert servers["custom-epic"].get("disabled") is True
        rows = {r["id"]: r for r in store.list_mcp_servers()}
        assert rows["custom-epic"]["enable_blocked_by_port"] is True
        assert "unreal-mcp" in rows["custom-epic"]["port_conflict_with"]


def test_save_mcp_config_rejects_enabled_port_collision(monkeypatch) -> None:
    _no_catalog(monkeypatch)

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


def test_mcp_json_is_never_read_after_rows_exist(monkeypatch) -> None:
    appdata = _no_catalog(monkeypatch)

    with patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool:
        pool.return_value.invalidate_tools_cache = lambda: None
        store._write_servers(
            {
                "keep": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "keep"],
                    "kind": "custom",
                    "label": "Keep",
                }
            }
        )
        (appdata / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "poison": {
                            "type": "stdio",
                            "command": "evil",
                            "args": [],
                            "kind": "custom",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
        servers = store.load_mcp_config()["mcpServers"]
        assert "keep" in servers
        assert "poison" not in servers
        from backend.store.repos import misc

        assert "keep" in misc.mcp_servers_get()
        assert "poison" not in misc.mcp_servers_get()


def test_ai_plugin_json_is_not_an_mcp_server(monkeypatch) -> None:
    """Chat-authored plugin.json stays a desktop plugin file — never a nested MCP row."""
    from backend.store.repos import misc
    from backend.tools.panel.panel_ai_plugins import scaffold_ai_plugin, write_ai_plugin_file

    _no_catalog(monkeypatch)
    sc = scaffold_ai_plugin("ai_hello", label="AI Hello")
    assert sc.get("ok"), sc
    wr = write_ai_plugin_file(
        "ai_hello",
        "ui/theme.css",
        ":root{--x:1}\n",
    )
    assert wr.get("ok"), wr
    assert "ai_hello" not in misc.mcp_servers_get()
    assert "ai_hello" not in store.load_mcp_config()["mcpServers"]


def test_connection_editor_masks_keys_and_saves_new_ones_as_secrets(monkeypatch) -> None:
    _no_catalog(monkeypatch)
    secrets: dict[str, str] = {}
    monkeypatch.setattr("backend.agent.secrets.set_key", lambda name, value: secrets.__setitem__(name, value))
    monkeypatch.setattr("backend.agent.secrets.get_key_fresh", lambda name: secrets.get(name))
    store.create_mcp_server("site", "Site", transport="http", url="https://old.example/mcp",
                            headers={"Authorization": "Bearer sk-live-1234567890abcd", "X-Team": "ducks"})
    view = store.connection_view("site")
    assert view["ok"] and view["editable"] and view["transport"] == "http" and view["url"] == "https://old.example/mcp"
    auth = next(r for r in view["values"] if r["name"] == "Authorization")
    assert auth["masked"] == "Bearer ••••abcd" and auth["has_value"] and "sk-live" not in json.dumps(view)

    saved = store.update_connection("site", transport="http", url="https://new.example/mcp", values=[
        {"name": "Authorization", "value": "Bearer new-token-9999"},
        {"name": "X-Team", "keep": True},
    ])
    assert saved["ok"] and saved["url"] == "https://new.example/mcp"
    raw = store.load_plugin_manifest("site")["server"]["headers"]
    assert raw == {"Authorization": "${SECRET:MCP_SITE_AUTHORIZATION}", "X-Team": "ducks"}  # no key in the config
    assert secrets == {"MCP_SITE_AUTHORIZATION": "Bearer new-token-9999"}
    resolved = store.resolve_server_block(store.load_plugin_manifest("site"))
    assert resolved["headers"]["Authorization"] == "Bearer new-token-9999"

    # A second change writes the same secret; a dropped row goes away.
    store.update_connection("site", transport="http", url="https://new.example/mcp", values=[{"name": "Authorization", "value": "Bearer third"}])
    assert secrets["MCP_SITE_AUTHORIZATION"] == "Bearer third"
    assert store.load_plugin_manifest("site")["server"]["headers"] == {"Authorization": "${SECRET:MCP_SITE_AUTHORIZATION}"}
    assert store.update_connection("site", transport="http", url=" ", values=[])["ok"] is False


def test_a_key_saved_by_another_process_is_used_on_the_next_connection(monkeypatch) -> None:
    # Oct 22 2026: Settings saved a new duckyos.org key in the app, but the shared tool daemon
    # kept sending the one it had cached at start, so the server kept answering 401.
    stored = {"MCP_SITE_AUTHORIZATION": "Bearer old"}
    monkeypatch.setattr("backend.agent.secrets.get_key", lambda name: "Bearer old")  # this process's cache
    monkeypatch.setattr("backend.agent.secrets.get_key_fresh", lambda name: stored.get(name))
    assert store._resolve_env_value("${SECRET:MCP_SITE_AUTHORIZATION}") == "Bearer old"
    stored["MCP_SITE_AUTHORIZATION"] = "Bearer new"  # the app's Settings wrote it
    assert store._resolve_env_value("${SECRET:MCP_SITE_AUTHORIZATION}") == "Bearer new"


def test_secret_refs_resolve_inside_a_header_and_stdio_edits(monkeypatch) -> None:
    _no_catalog(monkeypatch)
    monkeypatch.setattr("backend.agent.secrets.get_key_fresh", lambda name: {"TOKEN": "abc"}.get(name))
    assert store._resolve_env_value("Bearer ${SECRET:TOKEN}") == "Bearer abc"
    assert store._resolve_env_value("${SECRET:TOKEN}") == "abc"
    store.create_mcp_server("tool", "Tool", command="uvx", args=["tool-mcp"], env={"API_KEY": "${SECRET:TOKEN}"})
    view = store.connection_view("tool")
    assert view["values_label"] == "Environment" and view["values"][0]["secret"] == "TOKEN"
    saved = store.update_connection("tool", transport="stdio", command="npx", args=["-y", "tool"], values=[{"name": "API_KEY", "keep": True}])
    block = store.load_plugin_manifest("tool")["server"]
    assert saved["ok"] and block["command"] == "npx" and block["args"] == ["-y", "tool"] and block["env"] == {"API_KEY": "${SECRET:TOKEN}"}
