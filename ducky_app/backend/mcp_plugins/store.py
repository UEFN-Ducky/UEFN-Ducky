"""Nested MCP servers — Cursor-shaped ``mcp.json`` under AppData.

Source of truth: ``%LOCALAPPDATA%/UEFN-Ducky/mcp.json`` with a ``mcpServers`` map.
Catalog UI metadata still lives in bundled ``frontend/mcp_plugins/*/plugin.json``.
Legacy per-folder ``mcp_plugins/<id>/plugin.json`` is migrated once, then unused.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from backend.skills.store import appdata_dir
from urllib.parse import urlparse

PLUGIN_MANIFEST = "plugin.json"
MCP_PLUGINS_DIR = "mcp_plugins"
MCP_CONFIG_NAME = "mcp.json"
_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SECRET_REF_RE = re.compile(r"^\$\{SECRET:([A-Za-z0-9_]+)\}$")

# Cursor connection keys + disabled; everything else is Ducky/UI metadata on the block.
_SERVER_CONN_KEYS = frozenset(
    {"type", "command", "args", "env", "url", "headers", "disabled"}
)
_META_KEYS = frozenset(
    {
        "label",
        "description",
        "tool_prefix",
        "intents",
        "kind",
        "tags",
        "version",
        "health_probe_tool",
        "setup_steps",
        "requirements_note",
        "destructive_tools",
        "default_enabled",
    }
)

# Nested MCP ids that now ship only as Store desktop plugins (uefn-plugin-*).
# Drop leftover mcp.json entries + AppData mcp_plugins/<id> folders.
_MOVED_TO_DESKTOP_PLUGIN_MCP = frozenset({"blender"})

# Prefer keeping this catalog id when healing same-port HTTP conflicts.
_PORT_CONFLICT_KEEP_IDS = frozenset({"unreal-mcp"})


def appdata_mcp_plugins_dir() -> Path:
    """Legacy folder (migration / Open folder). Prefer :func:`mcp_config_path`."""
    return appdata_dir() / MCP_PLUGINS_DIR


def http_bind_key(url: str) -> str | None:
    """Normalize an HTTP(S) URL to ``host:port`` for conflict checks.

    Path/query are ignored — two MCPs on ``http://127.0.0.1:8000/mcp`` and
    ``http://localhost:8000/other`` still collide on the TCP bind.
    """
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "http").lower()
    if scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if host in ("localhost", "::1"):
        host = "127.0.0.1"
    if parsed.port:
        port = int(parsed.port)
    else:
        port = 443 if scheme == "https" else 80
    return f"{host}:{port}"


def _block_is_enabled(block: dict[str, Any]) -> bool:
    return block.get("disabled") is not True


def _block_http_bind(block: dict[str, Any]) -> str | None:
    transport = str(block.get("type") or "").strip().lower()
    url = str(block.get("url") or "").strip()
    if transport == "stdio":
        return None
    if transport in ("http", "sse") or (url and not transport):
        return http_bind_key(url)
    return None


def _prefer_keep_http_sid(a: str, b: str, servers: dict[str, Any]) -> str:
    if a in _PORT_CONFLICT_KEEP_IDS and b not in _PORT_CONFLICT_KEEP_IDS:
        return a
    if b in _PORT_CONFLICT_KEEP_IDS and a not in _PORT_CONFLICT_KEEP_IDS:
        return b
    ka = str((servers.get(a) or {}).get("kind") or "")
    kb = str((servers.get(b) or {}).get("kind") or "")
    if ka == "catalog" and kb != "catalog":
        return a
    if kb == "catalog" and ka != "catalog":
        return b
    return a if a <= b else b


def find_enabled_http_port_conflicts(servers: dict[str, Any]) -> dict[str, list[str]]:
    """Map ``host:port`` → enabled server ids when more than one share the bind."""
    by_bind: dict[str, list[str]] = {}
    for sid, block in servers.items():
        if not isinstance(block, dict) or not _block_is_enabled(block):
            continue
        bind = _block_http_bind(block)
        if not bind:
            continue
        by_bind.setdefault(bind, []).append(str(sid))
    return {bind: sorted(ids) for bind, ids in by_bind.items() if len(ids) > 1}


def heal_http_port_conflicts(servers: dict[str, Any]) -> list[str]:
    """Disable extras when several enabled HTTP/SSE servers share a port. Mutates ``servers``."""
    messages: list[str] = []
    for bind, ids in find_enabled_http_port_conflicts(servers).items():
        keep = ids[0]
        for sid in ids[1:]:
            keep = _prefer_keep_http_sid(keep, sid, servers)
        for sid in ids:
            if sid == keep:
                continue
            block = dict(servers[sid])
            block["disabled"] = True
            servers[sid] = block
            messages.append(
                f"Disabled '{sid}' — same TCP port as '{keep}' ({bind}). "
                "Only one nested HTTP/SSE MCP may use a host:port."
            )
    return messages


def http_port_conflict_message(
    enabling_id: str,
    servers: dict[str, Any],
    *,
    treat_as_enabled: bool = True,
) -> str | None:
    """If enabling ``enabling_id`` would share a port with another enabled server, explain why."""
    pid = str(enabling_id)
    block = servers.get(pid)
    if not isinstance(block, dict):
        return None
    probe = dict(block)
    if treat_as_enabled:
        probe.pop("disabled", None)
    bind = _block_http_bind(probe)
    if not bind:
        return None
    others: list[str] = []
    for sid, other in servers.items():
        if str(sid) == pid or not isinstance(other, dict):
            continue
        if not _block_is_enabled(other):
            continue
        if _block_http_bind(other) != bind:
            continue
        label = str(other.get("label") or sid)
        others.append(f"{label} ({sid})")
    if not others:
        return None
    return (
        f"Cannot enable '{pid}' — port {bind} is already used by "
        + ", ".join(others)
        + ". Disable the other server first, or change one URL "
        "(Epic: Editor Preferences → Model Context Protocol port)."
    )


def _format_port_conflict_save_error(conflicts: dict[str, list[str]]) -> str:
    parts = [
        f"{bind}: " + ", ".join(ids) for bind, ids in sorted(conflicts.items())
    ]
    return (
        "Multiple enabled HTTP/SSE MCP servers share the same TCP port. "
        "Only one may be enabled per host:port. Conflicts: "
        + "; ".join(parts)
        + ". Disable extras or change a URL before saving."
    )


def mcp_config_path() -> Path:
    return appdata_dir() / MCP_CONFIG_NAME


def bundled_mcp_plugins_dir() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "frontend" / MCP_PLUGINS_DIR
        if p.is_dir():
            return p
    repo = Path(__file__).resolve().parent.parent.parent / "frontend" / MCP_PLUGINS_DIR
    if repo.is_dir():
        return repo
    return None


def normalize_plugin_id(plugin_id: str) -> str:
    """Normalize a nested MCP server id (legacy name kept for callers)."""
    return normalize_server_id(plugin_id)


def normalize_server_id(server_id: str) -> str:
    pid = (server_id or "").strip().lower().replace(" ", "_")
    if not _PLUGIN_ID_RE.match(pid):
        raise ValueError(f"Invalid server id: {server_id!r}")
    return pid


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def validate_mcp_config(data: Any, *, resolve: bool = True) -> dict[str, Any]:
    """Validate Cursor-shaped config; returns normalized ``{"mcpServers": {...}}``."""
    if not isinstance(data, dict):
        raise ValueError("mcp.json root must be an object")
    raw = data.get("mcpServers")
    if raw is None:
        servers: dict[str, Any] = {}
    elif not isinstance(raw, dict):
        raise ValueError("mcpServers must be an object")
    else:
        servers = {}
        for key, block in raw.items():
            sid = normalize_server_id(str(key))
            if not isinstance(block, dict):
                raise ValueError(f"mcpServers.{sid} must be an object")
            servers[sid] = dict(block)
            if resolve:
                try:
                    resolve_server_block({"id": sid, "server": {k: v for k, v in block.items() if k != "disabled"}})
                except ValueError as exc:
                    raise ValueError(f"mcpServers.{sid}: {exc}") from exc
    return {"mcpServers": servers}


def load_mcp_config() -> dict[str, Any]:
    """Load ``mcp.json``, migrating / seeding as needed."""
    ensure_mcp_config()
    path = mcp_config_path()
    data = _read_json(path)
    if not data:
        return {"mcpServers": {}}
    try:
        return validate_mcp_config(data, resolve=False)
    except ValueError:
        return {"mcpServers": {}}


def save_mcp_config(data: dict[str, Any]) -> Path:
    normalized = validate_mcp_config(data)
    servers = normalized.get("mcpServers") if isinstance(normalized.get("mcpServers"), dict) else {}
    conflicts = find_enabled_http_port_conflicts(servers)
    if conflicts:
        raise ValueError(_format_port_conflict_save_error(conflicts))
    path = mcp_config_path()
    _write_json(path, normalized)
    from backend.mcp_plugins.client_pool import get_plugin_pool

    get_plugin_pool().invalidate_tools_cache()
    try:
        from backend.mcp_plugins.epic import invalidate_epic_mcp_probe

        invalidate_epic_mcp_probe()
    except Exception:
        pass
    try:
        from backend.mcp_plugins.bridge_proxy import schedule_sync_nested_proxies

        schedule_sync_nested_proxies()
    except Exception:
        pass
    return path


def get_mcp_config_text() -> str:
    ensure_mcp_config()
    path = mcp_config_path()
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return json.dumps({"mcpServers": {}}, indent=2) + "\n"


def set_mcp_config_text(text: str) -> Path:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
    return save_mcp_config(data)


def _list_bundled_catalog_manifests() -> list[dict[str, Any]]:
    bundled = bundled_mcp_plugins_dir()
    if not bundled or not bundled.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(bundled.iterdir()):
        if not child.is_dir():
            continue
        manifest = _read_json(child / PLUGIN_MANIFEST)
        if manifest and str(manifest.get("kind") or "catalog") == "catalog":
            out.append(manifest)
    return out


def _load_bundled_catalog(server_id: str) -> dict[str, Any] | None:
    pid = normalize_server_id(server_id)
    bundled = bundled_mcp_plugins_dir()
    if not bundled:
        return None
    return _read_json(bundled / pid / PLUGIN_MANIFEST)


def _server_block_from_legacy_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Extract a Cursor-shaped server block (no secrets resolved)."""
    if sys.platform == "win32" and isinstance(manifest.get("server_windows"), dict):
        block = dict(manifest["server_windows"])
    elif sys.platform != "win32" and isinstance(manifest.get("server_unix"), dict):
        block = dict(manifest["server_unix"])
    elif isinstance(manifest.get("server"), dict):
        block = dict(manifest["server"])
    else:
        raise ValueError("manifest missing server block")
    # Keep connection keys only for catalog seed; custom migration keeps meta below.
    return block


def _catalog_ui_meta(manifest: dict[str, Any]) -> dict[str, Any]:
    """UI metadata from a bundled catalog plugin.json (persisted into mcp.json)."""
    out: dict[str, Any] = {
        "kind": "catalog",
        "label": str(manifest.get("label") or manifest.get("id") or "").strip(),
        "description": str(manifest.get("description") or "").strip(),
        "tool_prefix": str(manifest.get("tool_prefix") or "").strip(),
    }
    if manifest.get("tags"):
        out["tags"] = list(manifest.get("tags") or [])
    if manifest.get("version") is not None:
        try:
            out["version"] = int(manifest.get("version") or 0)
        except (TypeError, ValueError):
            pass
    return {k: v for k, v in out.items() if v not in ("", None, [], 0)}


def _migrate_legacy_folders_into_config() -> dict[str, Any]:
    """Build mcpServers from AppData mcp_plugins/*/plugin.json + enabled list."""
    servers: dict[str, Any] = {}
    root = appdata_mcp_plugins_dir()
    enabled: set[str] = set()
    try:
        from frontend.settings import PanelSettings

        for item in getattr(PanelSettings.load(), "enabled_mcp_plugins", None) or []:
            try:
                enabled.add(normalize_server_id(str(item)))
            except ValueError:
                continue
    except Exception:
        pass

    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            manifest = _read_json(child / PLUGIN_MANIFEST)
            if not manifest:
                continue
            pid = str(manifest.get("id") or child.name)
            try:
                pid = normalize_server_id(pid)
            except ValueError:
                continue
            try:
                block = _server_block_from_legacy_manifest(manifest)
            except ValueError:
                continue
            kind = str(manifest.get("kind") or "custom")
            if kind != "catalog":
                for key in _META_KEYS:
                    if key in manifest and key not in block:
                        block[key] = manifest[key]
                block["kind"] = "custom"
                if "label" not in block:
                    block["label"] = str(manifest.get("label") or pid)
            if pid not in enabled:
                block["disabled"] = True
            else:
                block.pop("disabled", None)
            servers[pid] = block
    return {"mcpServers": servers}


def _seed_catalog_into_servers(servers: dict[str, Any]) -> None:
    """Ensure bundled catalog ids exist; refresh UI meta + missing connection defaults."""
    for manifest in _list_bundled_catalog_manifests():
        pid = str(manifest.get("id") or "").strip()
        if not pid:
            continue
        try:
            pid = normalize_server_id(pid)
        except ValueError:
            continue
        try:
            default_block = _server_block_from_legacy_manifest(manifest)
        except ValueError:
            continue
        ui_meta = _catalog_ui_meta(manifest)
        existing = servers.get(pid)
        if not isinstance(existing, dict):
            block = dict(default_block)
            block.update(ui_meta)
            if not bool(manifest.get("default_enabled")):
                block["disabled"] = True
            servers[pid] = block
            continue
        # Keep user connection edits; fill missing connection keys + always refresh UI meta.
        for key, value in default_block.items():
            if key not in existing:
                existing[key] = value
        existing.update(ui_meta)


def _retire_moved_to_desktop_plugin_mcp(servers: dict[str, Any]) -> None:
    """Remove nested MCP servers that are now Store desktop plugins only."""
    for sid in list(servers.keys()):
        try:
            nid = normalize_server_id(sid)
        except ValueError:
            continue
        if nid not in _MOVED_TO_DESKTOP_PLUGIN_MCP:
            continue
        del servers[sid]
        legacy = appdata_mcp_plugins_dir() / nid
        if legacy.is_dir():
            shutil.rmtree(legacy, ignore_errors=True)


def ensure_mcp_config() -> Path:
    """Create/migrate ``mcp.json`` if needed. Idempotent."""
    path = mcp_config_path()
    if path.is_file():
        data = _read_json(path)
        if data and isinstance(data.get("mcpServers"), dict):
            servers = {str(k): dict(v) for k, v in data["mcpServers"].items() if isinstance(v, dict)}
            before = json.dumps(servers, sort_keys=True)
            _seed_catalog_into_servers(servers)
            _retire_moved_to_desktop_plugin_mcp(servers)
            bundled_ids = {
                normalize_server_id(str(m.get("id")))
                for m in _list_bundled_catalog_manifests()
                if m.get("id")
            }
            for sid in list(servers.keys()):
                if str(servers[sid].get("kind") or "") == "custom":
                    continue
                if sid not in bundled_ids and str(servers[sid].get("kind") or "") == "catalog":
                    del servers[sid]
            heal_http_port_conflicts(servers)
            if json.dumps(servers, sort_keys=True) != before:
                _write_json(path, {"mcpServers": servers})
            return path

    # First-time: migrate legacy folders, then seed catalog.
    migrated = _migrate_legacy_folders_into_config()
    servers = dict(migrated.get("mcpServers") or {})
    _seed_catalog_into_servers(servers)
    _retire_moved_to_desktop_plugin_mcp(servers)
    heal_http_port_conflicts(servers)
    _write_json(path, {"mcpServers": servers})
    return path


def seed_mcp_plugins(*, force: bool = False) -> list[str]:
    """Ensure mcp.json exists and catalog servers are present (legacy name)."""
    del force  # connection defaults refresh happens in ensure_mcp_config
    path = ensure_mcp_config()
    return [f"mcp.json ready -> {path}"]


def _manifest_from_server_block(server_id: str, block: dict[str, Any]) -> dict[str, Any]:
    """Synthetic plugin.json-shaped manifest for pool/registry/UI."""
    pid = normalize_server_id(server_id)
    catalog = _load_bundled_catalog(pid)
    conn = {k: v for k, v in block.items() if k in _SERVER_CONN_KEYS and k != "disabled"}
    if "type" not in conn:
        conn["type"] = "stdio" if conn.get("command") else ("http" if conn.get("url") else "stdio")

    if catalog:
        manifest = dict(catalog)
        manifest["id"] = pid
        manifest["kind"] = "catalog"
        # Prefer live connection from mcp.json over bundled OS blocks.
        manifest["server"] = conn
        manifest.pop("server_windows", None)
        manifest.pop("server_unix", None)
    else:
        label = str(block.get("label") or pid.replace("_", " ").title())
        manifest = {
            "id": pid,
            "label": label,
            "description": str(block.get("description") or ""),
            "version": int(block.get("version") or 1),
            "kind": str(block.get("kind") or "custom"),
            "tags": list(block.get("tags") or []),
            "server": conn,
            "tool_prefix": str(block.get("tool_prefix") or pid),
            "intents": list(block.get("intents") or []),
            "default_enabled": bool(block.get("default_enabled")),
            "requirements_note": str(block.get("requirements_note") or ""),
            "setup_steps": list(block.get("setup_steps") or []),
            "health_probe_tool": block.get("health_probe_tool"),
            "destructive_tools": list(block.get("destructive_tools") or []),
        }
    return manifest


def load_plugin_manifest(plugin_id: str) -> dict[str, Any] | None:
    pid = normalize_server_id(plugin_id)
    ensure_mcp_config()
    data = _read_json(mcp_config_path()) or {}
    servers = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else {}
    block = servers.get(pid)
    if isinstance(block, dict):
        return _manifest_from_server_block(pid, block)
    # Fallback: bundled catalog not yet seeded.
    catalog = _load_bundled_catalog(pid)
    if catalog:
        return catalog
    return None


def list_mcp_plugin_manifests() -> list[dict[str, Any]]:
    cfg = load_mcp_config()
    out: list[dict[str, Any]] = []
    for sid, block in sorted(cfg.get("mcpServers", {}).items()):
        if isinstance(block, dict):
            out.append(_manifest_from_server_block(sid, block))
    return out


def get_enabled_plugin_ids() -> list[str]:
    cfg = load_mcp_config()
    enabled: list[str] = []
    for sid, block in cfg.get("mcpServers", {}).items():
        if not isinstance(block, dict):
            continue
        if block.get("disabled") is True:
            continue
        enabled.append(sid)
    return enabled


_ACTIVE_PLUGIN_IDS: ContextVar[tuple[str, ...] | None] = ContextVar(
    "uefn_ducky_active_mcp_plugin_ids", default=None
)


def set_active_plugin_ids(plugin_ids: list[str] | None) -> None:
    """Scope nested-server availability to the current run thread/task tree."""
    if plugin_ids is None:
        _ACTIVE_PLUGIN_IDS.set(None)
        return
    ids: list[str] = []
    for item in plugin_ids:
        try:
            ids.append(normalize_server_id(str(item)))
        except ValueError:
            continue
    _ACTIVE_PLUGIN_IDS.set(tuple(ids))


def effective_plugin_ids() -> list[str]:
    active = _ACTIVE_PLUGIN_IDS.get()
    if active is not None:
        return list(active)
    return get_enabled_plugin_ids()


def ensure_plugin_prefix_cache() -> None:
    from backend.mcp_plugins.registry import rebuild_plugin_prefix_cache

    ensure_mcp_config()
    rebuild_plugin_prefix_cache(list_mcp_plugin_manifests())


def set_mcp_plugin_enabled(plugin_id: str, enabled: bool) -> dict[str, Any]:
    return set_mcp_server_enabled(plugin_id, enabled)


def set_mcp_server_enabled(server_id: str, enabled: bool) -> dict[str, Any]:
    pid = normalize_server_id(server_id)
    cfg = load_mcp_config()
    servers = cfg.setdefault("mcpServers", {})
    block = servers.get(pid)
    if not isinstance(block, dict):
        raise FileNotFoundError(f"MCP server not found: {pid}")
    block = dict(block)
    if enabled:
        conflict = http_port_conflict_message(pid, servers, treat_as_enabled=True)
        if conflict:
            return {
                "ok": False,
                "error": conflict,
                "plugin_id": pid,
                "server_id": pid,
                "enabled": False,
                "port_conflict": True,
                "enabled_mcp_plugins": get_enabled_plugin_ids(),
            }
        block.pop("disabled", None)
    else:
        block["disabled"] = True
    servers[pid] = block
    save_mcp_config(cfg)

    # Keep legacy settings field in sync for older readers.
    try:
        from frontend.settings import PanelSettings, replace

        settings = PanelSettings.load()
        current = list(get_enabled_plugin_ids())
        settings = replace(settings, enabled_mcp_plugins=current)
        settings.save()
    except Exception:
        pass

    from backend.mcp_plugins.client_pool import get_plugin_pool

    pool = get_plugin_pool()
    if not enabled:
        pool.close_plugin(pid)
    else:
        pool.invalidate_tools_cache()
    return {"ok": True, "plugin_id": pid, "server_id": pid, "enabled": enabled, "enabled_mcp_plugins": get_enabled_plugin_ids()}


def _resolve_env_value(value: str) -> str:
    m = _SECRET_REF_RE.match(value.strip())
    if not m:
        return value
    from backend.agent.secrets import get_key

    secret = get_key(m.group(1)) or os.environ.get(m.group(1), "")
    return secret


_HTTP_TYPE_ALIASES = {
    "http": "http",
    "streamable-http": "http",
    "streamable_http": "http",
    "streamablehttp": "http",
    "streamable": "http",
    "sse": "sse",
}


def normalize_transport(raw_type: Any) -> str:
    if raw_type in (None, "", "stdio"):
        return "stdio"
    key = str(raw_type).strip().lower()
    if key in _HTTP_TYPE_ALIASES:
        return _HTTP_TYPE_ALIASES[key]
    raise ValueError(f"Unsupported server type: {raw_type}")


def resolve_server_block(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return connection config for the current OS (secrets resolved)."""
    if isinstance(manifest.get("server"), dict):
        block = dict(manifest["server"])
    elif sys.platform == "win32" and isinstance(manifest.get("server_windows"), dict):
        block = dict(manifest["server_windows"])
    elif sys.platform != "win32" and isinstance(manifest.get("server_unix"), dict):
        block = dict(manifest["server_unix"])
    else:
        raise ValueError("server config missing connection block")

    transport = normalize_transport(block.get("type"))

    if transport in ("http", "sse"):
        url = str(block.get("url") or "").strip()
        if not url:
            raise ValueError(f"server.url is required for {transport} transport")
        headers_raw = block.get("headers")
        headers: dict[str, str] = {}
        if isinstance(headers_raw, dict):
            for k, v in headers_raw.items():
                headers[str(k)] = _resolve_env_value(str(v))
        return {"type": transport, "url": url, "headers": headers}

    command = str(block.get("command") or "").strip()
    if not command:
        raise ValueError("server.command is required")
    args = block.get("args")
    if args is None:
        args_list: list[str] = []
    elif isinstance(args, list):
        args_list = [str(a) for a in args]
    else:
        raise ValueError("server.args must be a list")
    env_raw = block.get("env")
    env: dict[str, str] = {}
    if isinstance(env_raw, dict):
        for k, v in env_raw.items():
            env[str(k)] = _resolve_env_value(str(v))
    return {"type": "stdio", "command": command, "args": args_list, "env": env}


def _manifest_transport(manifest: dict[str, Any]) -> str:
    for key in ("server", "server_windows", "server_unix"):
        block = manifest.get(key)
        if isinstance(block, dict):
            try:
                return normalize_transport(block.get("type"))
            except ValueError:
                return "stdio"
    return "stdio"


def list_mcp_plugins() -> list[dict[str, Any]]:
    """UI rows for nested MCP servers (legacy name)."""
    return list_mcp_servers()


def list_mcp_servers() -> list[dict[str, Any]]:
    ensure_mcp_config()
    enabled = set(get_enabled_plugin_ids())
    cfg = load_mcp_config()
    servers = cfg.get("mcpServers", {}) if isinstance(cfg.get("mcpServers"), dict) else {}
    conflicts = find_enabled_http_port_conflicts(servers)
    bind_owners: dict[str, list[str]] = {}
    for sid, block in servers.items():
        if not isinstance(block, dict):
            continue
        bind = _block_http_bind(block)
        if bind:
            bind_owners.setdefault(bind, []).append(str(sid))

    rows: list[dict[str, Any]] = []
    for sid, block in sorted(servers.items()):
        if not isinstance(block, dict):
            continue
        manifest = _manifest_from_server_block(sid, block)
        bind = _block_http_bind(block)
        conflict_with: list[str] = []
        if bind:
            peers = [other for other in bind_owners.get(bind, []) if other != sid]
            # Prefer listing currently-enabled peers (the ones that block enable).
            enabled_peers = [p for p in peers if p in enabled]
            conflict_with = enabled_peers or peers
        url = str(block.get("url") or "").strip()
        rows.append(
            {
                "id": sid,
                "label": str(manifest.get("label") or sid),
                "description": str(manifest.get("description") or ""),
                "version": int(manifest.get("version") or 0),
                "kind": str(manifest.get("kind") or "custom"),
                "transport": _manifest_transport(manifest),
                "tags": list(manifest.get("tags") or []),
                "enabled": sid in enabled,
                "default_enabled": bool(manifest.get("default_enabled")),
                "requirements_note": str(manifest.get("requirements_note") or ""),
                "setup_steps": list(manifest.get("setup_steps") or []),
                "tool_prefix": str(manifest.get("tool_prefix") or sid),
                "intents": list(manifest.get("intents") or []),
                "path": str(mcp_config_path()),
                "url": url,
                "http_bind": bind or "",
                "port_conflict": bool(bind and bind in conflicts and sid in enabled),
                "port_conflict_with": conflict_with,
                "enable_blocked_by_port": bool(
                    sid not in enabled and bind and any(p in enabled for p in conflict_with)
                ),
            }
        )
    return rows


def create_mcp_plugin(
    plugin_id: str,
    label: str,
    *,
    description: str = "",
    command: str = "",
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    tool_prefix: str = "",
    intents: list[str] | None = None,
    transport: str = "stdio",
    url: str = "",
    headers: dict[str, str] | None = None,
) -> Path:
    return create_mcp_server(
        plugin_id,
        label,
        description=description,
        command=command,
        args=args,
        env=env,
        tool_prefix=tool_prefix,
        intents=intents,
        transport=transport,
        url=url,
        headers=headers,
    )


def create_mcp_server(
    server_id: str,
    label: str,
    *,
    description: str = "",
    command: str = "",
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    tool_prefix: str = "",
    intents: list[str] | None = None,
    transport: str = "stdio",
    url: str = "",
    headers: dict[str, str] | None = None,
) -> Path:
    pid = normalize_server_id(server_id)
    from backend.agent.builtin_toolsets import is_builtin_group

    if is_builtin_group(pid):
        raise ValueError(f"Reserved id (built-in tool group): {pid}")
    if pid == "uefn":
        raise ValueError("Reserved id: uefn")
    cfg = load_mcp_config()
    servers = cfg.setdefault("mcpServers", {})
    if pid in servers:
        raise FileExistsError(f"Server already exists: {pid}")
    ttype = normalize_transport(transport)
    if ttype in ("http", "sse"):
        endpoint = (url or "").strip()
        if not endpoint:
            raise ValueError("url is required for http/sse transport")
        block: dict[str, Any] = {
            "type": ttype,
            "url": endpoint,
            "headers": dict(headers or {}),
            "disabled": True,
            "kind": "custom",
            "label": label.strip() or pid.replace("_", " ").title(),
            "description": description.strip(),
            "tool_prefix": (tool_prefix or pid).strip(),
            "intents": list(intents or []),
        }
    else:
        cmd = (command or "").strip()
        if not cmd:
            raise ValueError("command is required")
        block = {
            "type": "stdio",
            "command": cmd,
            "args": list(args or []),
            "env": dict(env or {}),
            "disabled": True,
            "kind": "custom",
            "label": label.strip() or pid.replace("_", " ").title(),
            "description": description.strip(),
            "tool_prefix": (tool_prefix or pid).strip(),
            "intents": list(intents or []),
        }
    servers[pid] = block
    return save_mcp_config(cfg)


def update_mcp_server_manifest(server_id: str, manifest: dict[str, Any]) -> Path:
    """Write a full synthetic manifest back into mcp.json (agent upsert)."""
    pid = normalize_server_id(server_id)
    cfg = load_mcp_config()
    servers = cfg.setdefault("mcpServers", {})
    existing = servers.get(pid) if isinstance(servers.get(pid), dict) else {}
    disabled = bool(existing.get("disabled")) if isinstance(existing, dict) else True
    try:
        block = _server_block_from_legacy_manifest(manifest)
    except ValueError:
        server = manifest.get("server")
        if not isinstance(server, dict):
            raise
        block = dict(server)
    if str(manifest.get("kind") or "custom") != "catalog":
        for key in _META_KEYS:
            if key in manifest:
                block[key] = manifest[key]
        block["kind"] = "custom"
        block["label"] = str(manifest.get("label") or pid)
    if disabled:
        block["disabled"] = True
    else:
        block.pop("disabled", None)
    servers[pid] = block
    return save_mcp_config(cfg)


def delete_mcp_plugin(plugin_id: str) -> bool:
    return delete_mcp_server(plugin_id)


def delete_mcp_server(server_id: str) -> bool:
    pid = normalize_server_id(server_id)
    cfg = load_mcp_config()
    servers = cfg.get("mcpServers") or {}
    if pid not in servers:
        return False
    block = servers[pid]
    kind = str(block.get("kind") or "")
    if kind == "catalog" or (kind != "custom" and _load_bundled_catalog(pid) is not None):
        raise ValueError("Cannot delete catalog server — disable it instead")
    del servers[pid]
    save_mcp_config(cfg)
    from backend.mcp_plugins.client_pool import get_plugin_pool

    get_plugin_pool().close_plugin(pid)
    return True


# Legacy no-op helpers kept for imports that copied trees.
def _copy_plugin_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
