"""MCP server pathway: list, enable/disable, add/update/remove nested servers, apply to IDEs.

Thin wrappers over :mod:`backend.mcp_plugins.store` and :mod:`frontend.ide_apply`.
Guards: the core ``uefn`` bridge and built-in tool groups can't be clobbered
(``force`` required), removals need explicit ``confirm``, and Apply merges rather
than overwrites so other MCP servers in an IDE config survive.

Nested servers live in AppData ``mcp.json`` and are proxied under the single
IDE-facing ``uefn-ducky`` bridge.
"""

from __future__ import annotations

from typing import Any

from backend.json_util import tool_json
from backend.server import mcp

_RESERVED_IDS = {"uefn"}


def _notify_changed() -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "mcp_plugins_changed"})
    except Exception:
        pass


def set_plugin_scoped(plugin_id: str, enabled: bool, *, scope: str = "global", chat_id: str = "") -> dict[str, Any]:
    """Enable/disable a nested MCP server globally or for one chat."""
    from backend.mcp_plugins.store import normalize_server_id

    try:
        pid = normalize_server_id(plugin_id)
    except ValueError as exc:
        return {"error": str(exc)}
    scope = (scope or "global").strip().lower()
    if scope == "chat":
        cid = (chat_id or "").strip()
        if not cid:
            return {"error": "chat_id required for scope=chat"}
        from frontend.ui_web.project_chats import load_conversation, save_conversation

        conv = load_conversation(cid)
        if not conv:
            return {"error": f"conversation not found: {cid}"}
        current = list(getattr(conv, "mcp_plugins", None) or [])
        if enabled and pid not in current:
            current.append(pid)
        elif not enabled:
            current = [p for p in current if p != pid]
        conv.mcp_plugins = current
        save_conversation(conv, touch_updated=False)
        _notify_changed()
        return {"ok": True, "plugin_id": pid, "server_id": pid, "enabled": bool(enabled), "scope": "chat", "chat_id": cid, "mcp_plugins": current}
    if scope != "global":
        return {"error": "scope must be global or chat"}
    from backend.mcp_plugins.store import set_mcp_server_enabled

    try:
        result = set_mcp_server_enabled(pid, bool(enabled))
    except (FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}
    _notify_changed()
    return {"scope": "global", **result}


def apply_ide_bridges() -> dict[str, Any]:
    """Re-point every IDE's MCP config at the current bridge (merges, never clobbers uefn)."""
    from frontend.ide_apply import apply_all_ide_bridges

    lines = apply_all_ide_bridges()
    return {"ok": True, "applied": lines}


def _server_block_from_config(config: dict[str, Any]) -> dict[str, Any]:
    from backend.mcp_plugins.store import normalize_transport

    transport = normalize_transport(config.get("transport") or config.get("type") or "stdio")
    if transport in ("http", "sse"):
        url = str(config.get("url") or "").strip()
        if not url:
            raise ValueError("url is required for http/sse transport")
        headers = config.get("headers")
        return {"type": transport, "url": url, "headers": dict(headers) if isinstance(headers, dict) else {}}
    command = str(config.get("command") or "").strip()
    if not command:
        raise ValueError("command is required for stdio transport")
    args = config.get("args")
    env = config.get("env")
    return {
        "type": "stdio",
        "command": command,
        "args": [str(a) for a in args] if isinstance(args, list) else [],
        "env": {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
    }


@mcp.tool()
def ducky_mcp_list_plugins(pretty: bool = False) -> str:
    """List nested MCP servers: {id, label, kind, transport, enabled, tool_prefix, ...}.

    These servers nest under the single IDE connection ``uefn-ducky`` (not separate
    IDE mcp.json entries). Also see built-in tool groups in Settings → MCPs.
    """
    from backend.mcp_plugins.store import list_mcp_servers

    servers = list_mcp_servers()
    return tool_json({"plugins": servers, "servers": servers}, pretty=pretty)


@mcp.tool()
def ducky_mcp_list_tools(server_id: str = "", pretty: bool = False) -> str:
    """List tools for nested MCP servers (Settings → MCPs → Show all tools).

    Pass ``server_id`` (e.g. my_tool) for one server, or omit to list all enabled
    nested servers' tools. Works even when inspecting a disabled server (connects
    temporarily). Returns {ok, tools: [{name, description, server_id, inputSchema?}]}`.
    """
    import asyncio

    from backend.mcp_plugins.client_pool import get_plugin_pool
    from backend.mcp_plugins.store import (
        get_enabled_plugin_ids,
        load_plugin_manifest,
        normalize_server_id,
    )

    async def _run() -> dict[str, Any]:
        pool = get_plugin_pool()
        sid = (server_id or "").strip()
        tools_out: list[dict[str, Any]] = []
        if sid:
            try:
                pid = normalize_server_id(sid)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            if load_plugin_manifest(pid) is None:
                return {"ok": False, "error": f"MCP server not found: {pid}"}
            try:
                tools = await pool.list_tools_for_plugin(pid)
            except Exception as exc:
                return {"ok": False, "error": str(exc), "server_id": pid}
            for t in tools:
                tools_out.append(
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "server_id": pid,
                        "inputSchema": getattr(t, "inputSchema", None),
                    }
                )
            return {"ok": True, "server_id": pid, "tool_count": len(tools_out), "tools": tools_out}

        for pid in get_enabled_plugin_ids():
            try:
                tools = await pool.list_tools_for_plugin(pid)
            except Exception:
                continue
            for t in tools:
                tools_out.append(
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "server_id": pid,
                    }
                )
        return {"ok": True, "tool_count": len(tools_out), "tools": tools_out}

    return tool_json(asyncio.run(_run()), pretty=pretty)


@mcp.tool()
def ducky_mcp_set_plugin(
    plugin_id: str, enabled: bool, scope: str = "global", chat_id: str = "", pretty: bool = False
) -> str:
    """Enable or disable a nested MCP server (shows/hides its tools on the uefn-ducky index).

    scope: "global" (default) or "chat" (requires chat_id).
    Example: ducky_mcp_set_plugin("my_tool", true).
    """
    return tool_json(set_plugin_scoped(plugin_id, bool(enabled), scope=scope, chat_id=chat_id), pretty=pretty)


@mcp.tool()
def ducky_mcp_test_server(server_id: str, pretty: bool = False) -> str:
    """Test connectivity for a nested MCP server (list tools + optional health probe)."""
    import asyncio

    from backend.mcp_plugins.client_pool import get_plugin_pool
    from backend.mcp_plugins.store import normalize_server_id

    try:
        pid = normalize_server_id(server_id)
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json(asyncio.run(get_plugin_pool().test_plugin(pid)), pretty=pretty)


@mcp.tool()
def ducky_mcp_upsert_server(
    id: str, config: dict[str, Any], apply: bool = False, force: bool = False, pretty: bool = False
) -> str:
    """Add or update a nested MCP server in AppData mcp.json.

    config: {transport: "stdio"|"http"|"sse", command, args, env, url, headers,
    label, description, tool_prefix, intents}. Set `apply` to re-apply IDE configs after.
    Refuses to clobber the core `uefn` bridge or a built-in group unless force=true.
    Secrets go in env/headers as ${SECRET:NAME} refs, never inline tokens.
    """
    from backend.mcp_plugins.store import (
        create_mcp_server,
        load_plugin_manifest,
        normalize_server_id,
        update_mcp_server_manifest,
    )
    from backend.builtin_toolsets import is_builtin_group

    try:
        pid = normalize_server_id(id)
    except ValueError as exc:
        return tool_json({"error": str(exc)}, pretty=pretty)
    if (pid in _RESERVED_IDS or is_builtin_group(pid)) and not force:
        return tool_json({"error": f"reserved id '{pid}' — pass force=true to override"}, pretty=pretty)
    if not isinstance(config, dict):
        return tool_json({"error": "config must be an object"}, pretty=pretty)

    try:
        server = _server_block_from_config(config)
    except ValueError as exc:
        return tool_json({"error": str(exc)}, pretty=pretty)

    existing = load_plugin_manifest(pid)
    try:
        if existing is None:
            create_mcp_server(
                pid,
                str(config.get("label") or ""),
                description=str(config.get("description") or ""),
                command=server.get("command", ""),
                args=server.get("args", []),
                env=server.get("env", {}),
                tool_prefix=str(config.get("tool_prefix") or ""),
                intents=list(config.get("intents") or []),
                transport=server["type"],
                url=server.get("url", ""),
                headers=server.get("headers", {}),
            )
            action = "created"
        else:
            if str(existing.get("kind")) == "catalog" and not force:
                return tool_json({"error": "catalog server is read-only — force=true to override"}, pretty=pretty)
            manifest = dict(existing)
            manifest["server"] = server
            manifest.pop("server_windows", None)
            manifest.pop("server_unix", None)
            if config.get("label"):
                manifest["label"] = str(config["label"])
            if config.get("description") is not None:
                manifest["description"] = str(config["description"])
            if config.get("tool_prefix"):
                manifest["tool_prefix"] = str(config["tool_prefix"])
            if config.get("intents") is not None:
                manifest["intents"] = list(config["intents"])
            manifest["kind"] = "custom" if str(existing.get("kind")) != "catalog" else existing.get("kind")
            manifest["version"] = int(manifest.get("version") or 1) + 1
            update_mcp_server_manifest(pid, manifest)
            action = "updated"
    except (ValueError, FileExistsError, OSError) as exc:
        return tool_json({"error": str(exc)}, pretty=pretty)

    result: dict[str, Any] = {"ok": True, "plugin_id": pid, "server_id": pid, "action": action}
    if apply:
        result["apply"] = apply_ide_bridges()
    _notify_changed()
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_mcp_remove_server(id: str, confirm: bool = False, pretty: bool = False) -> str:
    """Delete a custom nested MCP server by id. Destructive — requires confirm=true.

    Won't remove the core `uefn` bridge, built-in groups, or catalog servers.
    """
    from backend.mcp_plugins.store import normalize_server_id
    from backend.builtin_toolsets import is_builtin_group

    try:
        pid = normalize_server_id(id)
    except ValueError as exc:
        return tool_json({"error": str(exc)}, pretty=pretty)
    if pid in _RESERVED_IDS or is_builtin_group(pid):
        return tool_json({"error": f"cannot remove reserved id: {pid}"}, pretty=pretty)
    if not confirm:
        return tool_json({"error": "pass confirm=true to delete", "server_id": pid}, pretty=pretty)
    from backend.mcp_plugins.store import delete_mcp_server

    try:
        removed = delete_mcp_server(pid)
    except ValueError as exc:
        return tool_json({"error": str(exc)}, pretty=pretty)
    if not removed:
        return tool_json({"error": f"server not found: {pid}"}, pretty=pretty)
    _notify_changed()
    return tool_json({"ok": True, "plugin_id": pid, "server_id": pid, "removed": True}, pretty=pretty)


@mcp.tool()
def ducky_mcp_apply(pretty: bool = False) -> str:
    """Apply the uefn bridge to all IDEs (Cursor/Claude/Antigravity).

    Merges into each IDE's mcp.json (preserving other servers). Nested MCP servers
    stay under UEFN Ducky — they are not written into the IDE config.
    """
    return tool_json(apply_ide_bridges(), pretty=pretty)


@mcp.tool()
def ducky_mcp_validate(id: str = "", config: dict[str, Any] | None = None, pretty: bool = False) -> str:
    """Dry-run check a server config: does it resolve, and does the id collide?

    Pass `config` (a server config object) and/or `id`. Does not start the server.
    Returns {ok, transport, id_exists, errors}.
    """
    from backend.mcp_plugins.store import load_plugin_manifest, normalize_server_id, resolve_server_block

    out: dict[str, Any] = {"ok": True, "errors": []}
    if id:
        try:
            pid = normalize_server_id(id)
            out["id_exists"] = load_plugin_manifest(pid) is not None
        except ValueError as exc:
            out["ok"] = False
            out["errors"].append(str(exc))
    if config is not None:
        if not isinstance(config, dict):
            out["ok"] = False
            out["errors"].append("config must be an object")
        else:
            try:
                block = _server_block_from_config(config)
                resolved = resolve_server_block({"server": block})
                out["transport"] = resolved.get("type")
            except ValueError as exc:
                out["ok"] = False
                out["errors"].append(str(exc))
    return tool_json(out, pretty=pretty)
