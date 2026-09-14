"""Builtin + enabled-plugin automation node catalog."""

from __future__ import annotations

from typing import Any

BUILTIN_NODES: list[dict[str, Any]] = [
    {
        "type": "start.manual",
        "label": "Manual",
        "group": "Starting",
        "role": "starter",
        "description": "Run from the editor Test button or run_automation.",
        "config_fields": [],
    },
    {
        "type": "start.cron",
        "label": "Cron / interval",
        "group": "Starting",
        "role": "starter",
        "description": "Fires while the panel is running. interval_seconds or 5-field cron.",
        "config_fields": [
            {"id": "interval_seconds", "label": "Interval (seconds)", "type": "number"},
            {"id": "cron", "label": "Cron (m h dom mon dow)", "type": "string"},
        ],
    },
    {
        "type": "ducky.prompt",
        "label": "Prompt existing ducky",
        "group": "Duckies",
        "role": "action",
        "description": "Send a prompt to a chat id. Does not switch island.",
        "config_fields": [
            {"id": "conv_id", "label": "Chat id", "type": "string"},
            {"id": "prompt", "label": "Prompt", "type": "textarea"},
            {"id": "mode", "label": "Mode", "type": "string"},
            {"id": "model", "label": "Model", "type": "string"},
        ],
    },
    {
        "type": "ducky.spawn",
        "label": "Spawn ducky",
        "group": "Duckies",
        "role": "action",
        "description": "Create a chat on the current island and send a prompt.",
        "config_fields": [
            {"id": "title", "label": "Title", "type": "string"},
            {"id": "prompt", "label": "Prompt", "type": "textarea"},
            {"id": "ducky_style", "label": "Ducky style", "type": "string"},
            {"id": "mode", "label": "Mode", "type": "string"},
            {"id": "model", "label": "Model", "type": "string"},
        ],
    },
    {
        "type": "flow.wait",
        "label": "Wait",
        "group": "Logic",
        "role": "action",
        "description": "Pause this run (capped at 120s).",
        "config_fields": [{"id": "seconds", "label": "Seconds", "type": "number"}],
    },
    {
        "type": "flow.branch",
        "label": "Branch",
        "group": "Logic",
        "role": "action",
        "description": "Follow true/false edges. Field equals value, or field is truthy.",
        "config_fields": [
            {"id": "field", "label": "Payload field", "type": "string"},
            {"id": "equals", "label": "Equals (optional)", "type": "string"},
        ],
    },
    {
        "type": "tool.call",
        "label": "Call tool",
        "group": "Tools",
        "role": "action",
        "description": "Call a named host or plugin MCP tool.",
        "config_fields": [
            {"id": "name", "label": "Tool name", "type": "string"},
            {"id": "arguments_json", "label": "Arguments JSON", "type": "textarea"},
        ],
    },
]


def list_nodes() -> list[dict[str, Any]]:
    out = [dict(n) for n in BUILTIN_NODES]
    try:
        from backend.uefn_plugins.host import get_ui_contributions
        from backend.uefn_plugins.store import get_enabled_plugin_ids

        enabled = set(get_enabled_plugin_ids())
        contrib = get_ui_contributions()
    except Exception:
        return out
    for row in contrib.get("automations_triggers") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("plugin_id") or "")
        if pid and pid not in enabled:
            continue
        ntype = str(row.get("id") or row.get("type") or "").strip()
        if not ntype:
            continue
        out.append(
            {
                "type": ntype,
                "label": str(row.get("label") or ntype),
                "group": str(row.get("group") or "Triggers"),
                "role": "starter",
                "description": str(row.get("description") or ""),
                "plugin_id": pid,
                "config_fields": _fields(row.get("config_fields") or row.get("fields")),
            }
        )
    for row in contrib.get("automations_nodes") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("plugin_id") or "")
        if pid and pid not in enabled:
            continue
        ntype = str(row.get("id") or row.get("type") or "").strip()
        if not ntype:
            continue
        out.append(
            {
                "type": ntype,
                "label": str(row.get("label") or ntype),
                "group": str(row.get("group") or pid or "Plugins"),
                "role": "action",
                "description": str(row.get("description") or ""),
                "plugin_id": pid,
                "config_fields": _fields(row.get("config_fields") or row.get("fields")),
            }
        )
    return out


def starter_types() -> set[str]:
    return {n["type"] for n in list_nodes() if n.get("role") == "starter"}


def _fields(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for f in raw:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or "").strip()
        if not fid:
            continue
        out.append(
            {
                "id": fid,
                "label": str(f.get("label") or fid),
                "type": str(f.get("type") or "string"),
            }
        )
    return out
