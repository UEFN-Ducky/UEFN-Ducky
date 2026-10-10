"""MCP tool catalog for the Configuration → Skills & MCP → MCPs panel tab."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import re
import sys
import time
import uuid
from typing import Any

from backend.agent.toolsets import is_plan_safe_tool
from backend.agent.toolsets.categories import (
    assets,
    creative_devices,
    memory,
    panel,
    project,
    scene,
    verse_devices,
    workspace,
)
from backend.agent.toolsets.destructive import DESTRUCTIVE_TOOLS
from backend.agent.toolsets.excluded import EXCLUDED_TOOLS
from backend.agent.tool_router import CORE_TOOLS
from backend.agent.toolsets.mcp_plugins import plugin_destructive_tool_names
from backend.agent.tools import is_host_only_tool, list_mcp_tools
from backend.mcp_plugins.registry import is_plugin_tool
from backend.agent.toolsets.tool_index import catalog_revision, search_tool_catalog, tool_catalog_row


def _tool_in_plan(name: str) -> bool:
    return is_plan_safe_tool(name)

_CATEGORY_MODULES: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("panel", "Panel & chats", panel.TOOLS),
    ("project", "Project & listener", project.TOOLS),
    ("workspace", "Workspace files", workspace.TOOLS),
    ("memory", "Project memory", memory.TOOLS),
    ("verse_devices", "Verse devices", verse_devices.TOOLS),
    ("creative_devices", "Creative devices", creative_devices.TOOLS),
    ("scene", "Scene & actors", scene.TOOLS),
    ("assets", "Assets & registry", assets.TOOLS),
)


def _tool_category_map() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for cat_id, label, tools in _CATEGORY_MODULES:
        for name in tools:
            out[name] = (cat_id, label)
    return out


def _schema_parameters(schema: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not schema or not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    required = set(schema.get("required") or [])
    params: list[dict[str, Any]] = []
    for key, spec in props.items():
        if not isinstance(spec, dict):
            continue
        params.append(
            {
                "name": key,
                "type": str(spec.get("type") or "any"),
                "description": str(spec.get("description") or "").strip(),
                "required": key in required,
                "default": spec.get("default"),
            }
        )
    params.sort(key=lambda p: (not p["required"], p["name"]))
    return params


def build_mcp_catalog(*, apply_filters: bool = True, query: str = "", offset: int = 0,
                      limit: int | None = None) -> dict[str, Any]:
    return _catalog_from_tools(asyncio.run(list_mcp_tools(apply_filters=apply_filters)),
                               query=query, offset=offset, limit=limit)


def _diagnostic_observations() -> dict[str, Any]:
    """Read process-local snapshots only; never construct a pool or load plugins.

    The pool has no timestamped health/exposure API. These caches are observations,
    not an atomic readiness check, and a bridge process may have no observations.
    """
    from backend.agent.builtin_toolsets import builtin_group_rows, builtin_group_for_tool
    from backend.mcp_plugins import client_pool
    from backend.mcp_plugins.store import list_mcp_servers
    from backend.uefn_plugins.host import plugins_ready, uefn_plugin_tool_group_rows

    servers = [*builtin_group_rows(), *list_mcp_servers()]
    complete = plugins_ready()
    if complete:
        servers.extend(uefn_plugin_tool_group_rows())
    pool = client_pool._pool  # Existing cache only: get_plugin_pool would start a loop.
    inventories = {sid: list(rows) for sid, rows in dict(getattr(pool, "_inventories", {})).items()}
    failures = set(dict(getattr(pool, "_inventory_failures", {})))
    http_failures = {sid for sid, until in dict(getattr(pool, "_failed_until", {})).items() if until > time.time()}
    connections = dict(getattr(pool, "_connections", {}))
    connected = {sid for sid, conn in connections.items()
                 if not getattr(pool, "_closing", False) and not conn.retired
                 and conn.session is not None and conn.owner is not None and conn.owner.alive}
    server = sys.modules.get("backend.server")
    manager = getattr(getattr(server, "mcp", None), "_tool_manager", None)
    if manager is not None:
        registered = list(manager.list_tools())
        for row in servers:
            if row.get("kind") == "builtin":
                inventories[row["id"]] = [t for t in registered if builtin_group_for_tool(t.name) == row["id"]]
            elif row.get("kind") == "uefn_plugin":
                names = set(row.get("tool_names") or [])
                inventories[row["id"]] = [t for t in registered if t.name in names]
    return {"servers": servers, "inventories": inventories, "failures": failures,
            "http_failures": http_failures, "connected": connected, "complete": complete}


def _diagnostic_identity(value: str) -> str:
    # Keep valid canonical identities; never echo arbitrary URLs, paths or text.
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        return value
    return "redacted-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def build_mcp_diagnostics(server_id: str = "", mode: str = "agent") -> dict[str, Any]:
    """Shared, sanitized Settings/agent report. No connection or tool invocation."""
    from backend.agent.run_context import validate_mode
    from backend.agent.toolsets.plan_safe import mode_tool_block_reason

    mode = validate_mode(mode)
    sid = str(server_id or "").strip()
    observation_error = False
    try:
        observed = _diagnostic_observations()
    except Exception:
        # Configuration/provider exceptions can include credentials. Do not echo them.
        observed = {"servers": [], "inventories": {}, "failures": set(), "complete": False}
        observation_error = True
    servers = [r for r in observed["servers"] if not sid or r["id"] == sid]
    if sid and not servers:
        servers = [{"id": sid, "enabled": None, "absent": True}]
    report_rows = []
    guidance = {
        "missing": "Check the server ID and configured MCP servers before adding anything.",
        "disabled": "Enable this server in MCP settings only if you intend to use it.",
        "offline": "Review connection settings, then retry once after the server recovers. Cached tools are not proof of readiness.",
        "unknown": "No runtime readiness evidence here. Use the existing connection test when appropriate; do not retry in a loop.",
        "connected": "An initialized local session owner is active. This is not a live health test or proof of model exposure.",
    }
    for server in servers:
        identity = server["id"]
        status = "unknown"
        if server.get("absent") and observed["complete"]:
            status = "missing"
        elif server.get("enabled") is False:
            status = "disabled"
        elif identity in observed["failures"] or identity in observed.get("http_failures", set()):
            status = "offline"
        elif identity in observed.get("connected", set()):
            status = "connected"
        tools = observed["inventories"].get(identity)
        by_name = {t.name: t for t in tools or []}
        tool_rows = []
        blocked = excluded = 0
        for name in sorted(by_name):
            reason = mode_tool_block_reason(mode, name, {}, by_name, discovery=True)
            filtered = name in EXCLUDED_TOOLS or server.get("enabled") is False
            blocked += bool(reason)
            excluded += filtered
            tool_rows.append({
                "name": _diagnostic_identity(name),
                "status": "unexposed" if filtered else "policy-blocked" if reason else "unknown",
                "mode_reason": reason,
            })
        report_rows.append({
            "server_id": _diagnostic_identity(identity), "enabled": server.get("enabled"), "status": status,
            "stages": {"started": None, "connected": True if identity in observed.get("connected", set()) else None, "model_exposed": None},
            "counts": {"catalog": len(by_name) if tools is not None else None,
                       "policy_blocked": blocked if tools is not None else None,
                       "unexposed_by_filter": excluded if tools is not None else None},
            "recent_error": "http_connection_failed" if identity in observed.get("http_failures", set()) else "inventory_refresh_failed" if identity in observed["failures"] else "unobserved",
            "guidance": guidance[status], "tools": tool_rows[:50], "tools_truncated": len(tool_rows) > 50,
        })
    return {
        "correlation_id": uuid.uuid4().hex, "mode": mode, "servers": report_rows,
        "stage_counts": {
            "catalog_tools": sum(r["counts"]["catalog"] for r in report_rows) if report_rows and all(r["counts"]["catalog"] is not None for r in report_rows) else None,
            "started_servers": None,
            "connected_servers": len(report_rows) if report_rows and all(r["stages"]["connected"] is True for r in report_rows) else None,
            "model_exposed_tools": None,
        },
        "observation_error": "observation_unavailable" if observation_error else None,
        "scope": "Process-local cached observations; no connections were attempted. Error age is unobserved.",
        "stage_note": "Unobserved stages stay unknown. Connected means an initialized local session owner is active, not a live health test. Catalog presence and policy eligibility do not prove runtime exposure. Unexposed means excluded by the local configuration/filter only.",
        "policy_guidance": "Use verified read tools in Ask/Plan. Request Agent mode for approved mutations; do not bypass a mode block.",
    }


def build_server_catalog(server_id: str) -> dict[str, Any]:
    """One MCP server's tools for its Settings page. A nested server connects only itself
    (the full catalog connects every one, ~1 min, and one dead server stalled the page);
    the app's own tool groups list the app's tools with no nested connects."""
    from backend.agent.builtin_toolsets import filter_builtin_tools, is_builtin_group
    from backend.agent.tools import _ensure_mcp
    from backend.uefn_plugins.host import filter_uefn_plugin_tools, is_uefn_agent_tool_plugin

    sid = (server_id or "").strip()
    if is_builtin_group(sid.lower()) or is_uefn_agent_tool_plugin(sid.lower()):
        core = asyncio.run(_ensure_mcp().list_tools())
        return {"ok": True, **_catalog_from_tools(filter_uefn_plugin_tools(filter_builtin_tools(core)))}
    from backend.mcp_plugins.client_pool import get_plugin_pool

    pool = get_plugin_pool()
    try:
        tools = pool.run_sync(pool.list_tools_for_plugin(sid))
    except Exception as exc:  # noqa: BLE001 - the page shows why
        detail = str(exc).strip() or type(exc).__name__
        return {"ok": False, "error": f"Could not list this server's tools: {detail}", "total": 0, "categories": [], "tools": []}
    return {"ok": True, **_catalog_from_tools(list(tools or []))}


def _catalog_from_tools(tools: list[Any], *, query: str = "", offset: int = 0,
                        limit: int | None = None) -> dict[str, Any]:
    page = search_tool_catalog(tools, query, offset=offset, limit=limit)
    metadata = {r["name"]: r for r in page["matches"]}
    by_name = {t.name: t for t in tools}
    by_cat = _tool_category_map()
    categories: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    plugin_destructive = plugin_destructive_tool_names()
    uefn_owner: dict[str, str] = {}
    try:
        from backend.uefn_plugins.host import uefn_agent_tool_rows, uefn_plugin_tool_group_rows

        for row in list(uefn_agent_tool_rows()) + list(uefn_plugin_tool_group_rows()):
            pid = str(row.get("id") or "").strip()
            label = str(row.get("label") or pid).strip() or pid
            for name in row.get("tool_names") or []:
                if isinstance(name, str) and name.strip():
                    uefn_owner[name.strip()] = label
    except Exception:
        pass

    for name in metadata:
        tool = by_name[name]
        if is_plugin_tool(tool.name):
            prefix = tool.name.split("__", 1)[0]
            cat_id, cat_label = f"plugin_{prefix}", f"MCP plugin: {prefix}"
        elif tool.name in uefn_owner:
            label = uefn_owner[tool.name]
            slug = label.lower().replace(" ", "_")
            cat_id, cat_label = f"uefn_plugin_{slug}", f"Desktop plugin: {label}"
        else:
            cat_id, cat_label = by_cat.get(tool.name, ("other", "Other MCP tools"))
        schema = dict(tool.inputSchema or {"type": "object", "properties": {}})
        in_core = tool.name in CORE_TOOLS
        destructive = tool.name in DESTRUCTIVE_TOOLS or tool.name in plugin_destructive
        row = {
            **metadata[name],
            "name": tool.name,
            "description": (tool.description or tool.name).strip(),
            "category_id": cat_id,
            "category_label": cat_label,
            "in_agent": in_core and tool.name not in EXCLUDED_TOOLS,
            "in_plan": _tool_in_plan(tool.name),
            "agent_excluded": tool.name in EXCLUDED_TOOLS,
            "destructive": destructive,
            "host_only": is_host_only_tool(tool.name),
            "is_plugin": is_plugin_tool(tool.name),
            "parameters": _schema_parameters(schema),
        }
        rows.append(row)
        bucket = categories.setdefault(
            cat_id,
            {"id": cat_id, "label": cat_label, "tools": []},
        )
        bucket["tools"].append(row)

    ordered_categories = []
    seen = set()
    for cat_id, label, _ in _CATEGORY_MODULES:
        if cat_id in categories:
            ordered_categories.append(categories[cat_id])
            seen.add(cat_id)
    if "other" in categories:
        ordered_categories.append(categories["other"])
        seen.add("other")
    desktop_cats = sorted(
        (bucket for cat_id, bucket in categories.items() if cat_id.startswith("uefn_plugin_")),
        key=lambda c: c["label"],
    )
    ordered_categories.extend(desktop_cats)
    seen.update(c["id"] for c in desktop_cats)
    plugin_cats = sorted(
        (bucket for cat_id, bucket in categories.items() if cat_id.startswith("plugin_")),
        key=lambda c: c["label"],
    )
    ordered_categories.extend(plugin_cats)
    seen.update(c["id"] for c in plugin_cats)
    for cat_id, bucket in categories.items():
        if cat_id not in seen and not cat_id.startswith("plugin_"):
            ordered_categories.append(bucket)

    agent_count = sum(1 for r in rows if r["in_agent"])
    plan_count = sum(1 for r in rows if r["in_plan"])

    return {
        "total": page["total"], "count": len(rows), "offset": page["offset"],
        "next_offset": page["next_offset"], "revision": page["revision"],
        "agent_tools": agent_count,
        "plan_tools": plan_count,
        "categories": ordered_categories,
        "tools": rows,
    }


_WORKFLOW_CACHE: dict[str, Any] = {}


def build_workflow_tool_catalog() -> dict[str, Any]:
    """Tools a workflow "Call tool" node can run — host MCP tools only.

    The runner calls ``mcp._tool_manager.get_tool(name)`` directly, so nested
    MCP plugin tools (``blender__…``) are never callable from a node. Listing
    them through ``build_mcp_catalog`` connected every enabled nested server
    (45s timeout each, serial, no cache after one failure) and sync-waited on
    desktop plugin load — the "Loading tools…" hang. This path touches neither.
    """
    import inspect

    from backend.agent.tools import _ensure_mcp
    from backend.uefn_plugins.host import plugins_ready

    mcp = _ensure_mcp()
    tools = list(mcp._tool_manager.list_tools())
    ready = plugins_ready()
    key = (catalog_revision(tools), ready,
           tuple((t.name, bool(getattr(t, "is_async", False) or inspect.iscoroutinefunction(getattr(t, "fn", None)))) for t in tools))
    if _WORKFLOW_CACHE.get("key") == key:
        return copy.deepcopy(_WORKFLOW_CACHE["rows"])

    by_cat = _tool_category_map()
    uefn_owner: dict[str, str] = {}
    if ready:
        # Already loaded → these return without blocking on plugin register().
        try:
            from backend.uefn_plugins.host import uefn_agent_tool_rows, uefn_plugin_tool_group_rows

            for row in list(uefn_agent_tool_rows()) + list(uefn_plugin_tool_group_rows()):
                label = str(row.get("label") or row.get("id") or "").strip()
                for name in row.get("tool_names") or []:
                    if isinstance(name, str) and name.strip() and label:
                        uefn_owner[name.strip()] = label
        except Exception:
            pass

    rows: list[dict[str, Any]] = []
    categories: dict[str, dict[str, Any]] = {}
    for tool in sorted(tools, key=lambda t: t.name):
        fn = getattr(tool, "fn", None)
        if getattr(tool, "is_async", False) or (fn is not None and inspect.iscoroutinefunction(fn)):
            continue  # the runner refuses async tools
        if tool.name in uefn_owner:
            label = uefn_owner[tool.name]
            cat_id, cat_label = f"uefn_plugin_{label.lower().replace(' ', '_')}", f"Desktop plugin: {label}"
        else:
            cat_id, cat_label = by_cat.get(tool.name, ("other", "Other MCP tools"))
        schema = dict(getattr(tool, "parameters", None) or {"type": "object", "properties": {}})
        row = {
            **tool_catalog_row(tool),
            "name": tool.name,
            "description": (tool.description or tool.name).strip(),
            "category_id": cat_id,
            "category_label": cat_label,
            "in_agent": tool.name in CORE_TOOLS and tool.name not in EXCLUDED_TOOLS,
            "in_plan": False,  # plan-safety lookup sync-waits on plugin load; not needed here
            "agent_excluded": tool.name in EXCLUDED_TOOLS,
            "destructive": tool.name in DESTRUCTIVE_TOOLS,
            "host_only": is_host_only_tool(tool.name),
            "is_plugin": False,
            "parameters": [p for p in _schema_parameters(schema) if p["name"] != "pretty"],
        }
        rows.append(row)
        categories.setdefault(cat_id, {"id": cat_id, "label": cat_label, "tools": []})["tools"].append(row)

    order = [cat_id for cat_id, _label, _tools in _CATEGORY_MODULES] + ["other"]
    ordered = [categories[c] for c in order if c in categories]
    ordered += sorted((b for c, b in categories.items() if c not in order), key=lambda c: c["label"])
    out = {
        "revision": key[0],
        "total": len(rows),
        "agent_tools": sum(1 for r in rows if r["in_agent"]),
        "plan_tools": sum(1 for r in rows if r["in_plan"]),
        "categories": ordered,
        "tools": rows,
        "host_only_catalog": True,
    }
    _WORKFLOW_CACHE["key"] = key
    _WORKFLOW_CACHE["rows"] = out
    return copy.deepcopy(out)


def _merge_installed_plugin_tools(full: dict[str, Any]) -> None:
    """Include Store-installed plugin tools even when the local toggle is off."""
    try:
        from backend.uefn_plugins.host import uefn_plugin_tool_group_rows
        from backend.agent.toolsets.destructive import DESTRUCTIVE_TOOLS

        rows = uefn_plugin_tool_group_rows()
        destructive = set(DESTRUCTIVE_TOOLS)
    except Exception:
        return
    have = {str(t.get("name")) for t in (full.get("tools") or []) if isinstance(t, dict)}
    by_id = {str(c.get("id")): c for c in (full.get("categories") or []) if isinstance(c, dict)}
    for row in rows:
        label = str(row.get("label") or row.get("id") or "").strip()
        if not label:
            continue
        slug = label.lower().replace(" ", "_")
        cat_id = f"uefn_plugin_{slug}"
        bucket = by_id.get(cat_id)
        if bucket is None:
            bucket = {"id": cat_id, "label": f"Desktop plugin: {label}", "tools": []}
            full.setdefault("categories", []).append(bucket)
            by_id[cat_id] = bucket
        for name in row.get("tool_names") or []:
            if not isinstance(name, str) or not name.strip() or name in have:
                continue
            have.add(name)
            item = {
                "name": name,
                "description": name,
                "destructive": name in destructive,
            }
            bucket.setdefault("tools", []).append(item)
            full.setdefault("tools", []).append(item)


def build_caps_catalog() -> dict[str, Any]:
    """Slim grouped catalog for the desktop AI permissions editor (no schemas)."""
    full = build_mcp_catalog(apply_filters=False)
    _merge_installed_plugin_tools(full)
    categories = []
    for cat in full.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        tools = []
        for tool in cat.get("tools") or []:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            tools.append(
                {
                    "name": str(tool["name"]),
                    "description": str(tool.get("description") or ""),
                    "destructive": bool(tool.get("destructive")),
                }
            )
        categories.append(
            {
                "id": str(cat.get("id") or ""),
                "label": str(cat.get("label") or cat.get("id") or ""),
                "tools": tools,
            }
        )
    return {"categories": categories}
