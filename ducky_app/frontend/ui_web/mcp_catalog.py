"""MCP tool catalog for the Configuration → Skills & MCP → MCPs panel tab."""

from __future__ import annotations

import asyncio
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


def build_mcp_catalog() -> dict[str, Any]:
    tools = asyncio.run(list_mcp_tools())
    by_cat = _tool_category_map()
    categories: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    plugin_destructive = plugin_destructive_tool_names()
    uefn_owner: dict[str, str] = {}
    try:
        from backend.uefn_plugins.host import uefn_agent_tool_rows

        for row in uefn_agent_tool_rows():
            pid = str(row.get("id") or "").strip()
            label = str(row.get("label") or pid).strip() or pid
            for name in row.get("tool_names") or []:
                if isinstance(name, str) and name.strip():
                    uefn_owner[name.strip()] = label
    except Exception:
        pass

    for tool in sorted(tools, key=lambda t: t.name):
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
        "total": len(rows),
        "agent_tools": agent_count,
        "plan_tools": plan_count,
        "categories": ordered_categories,
        "tools": rows,
    }
