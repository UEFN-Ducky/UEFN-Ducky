"""Tool namespacing and plugin metadata helpers."""

from __future__ import annotations

import re
from typing import Any

PLUGIN_TOOL_SEP = "__"
_PREFIX_TO_PLUGIN: dict[str, str] = {}


def tool_prefix(manifest: dict[str, Any]) -> str:
    raw = manifest.get("tool_prefix") or manifest.get("id")
    return str(raw or "").strip()


def register_plugin_prefix(manifest: dict[str, Any]) -> None:
    pid = str(manifest.get("id") or "").strip()
    prefix = tool_prefix(manifest)
    if pid and prefix:
        _PREFIX_TO_PLUGIN[prefix] = pid


def clear_plugin_prefix_cache() -> None:
    _PREFIX_TO_PLUGIN.clear()


def rebuild_plugin_prefix_cache(manifests: list[dict[str, Any]]) -> None:
    clear_plugin_prefix_cache()
    for manifest in manifests:
        register_plugin_prefix(manifest)


def namespace_tool_name(manifest: dict[str, Any], tool_name: str) -> str:
    prefix = tool_prefix(manifest)
    if not prefix:
        return tool_name
    if PLUGIN_TOOL_SEP in tool_name and tool_name.startswith(f"{prefix}{PLUGIN_TOOL_SEP}"):
        return tool_name
    return f"{prefix}{PLUGIN_TOOL_SEP}{tool_name}"


def is_plugin_tool(name: str) -> bool:
    return PLUGIN_TOOL_SEP in name and name.split(PLUGIN_TOOL_SEP, 1)[0] in _PREFIX_TO_PLUGIN


def parse_plugin_tool(name: str) -> tuple[str, str] | None:
    if PLUGIN_TOOL_SEP not in name:
        return None
    prefix, original = name.split(PLUGIN_TOOL_SEP, 1)
    plugin_id = _PREFIX_TO_PLUGIN.get(prefix)
    if not plugin_id or not original:
        return None
    return plugin_id, original


def plugin_intent_pattern(manifest: dict[str, Any]) -> re.Pattern[str] | None:
    intents = manifest.get("intents")
    if not isinstance(intents, list) or not intents:
        return None
    parts = [re.escape(str(i).strip()) for i in intents if str(i).strip()]
    if not parts:
        return None
    return re.compile(r"\b(" + "|".join(parts) + r")\b", re.I)


def plugin_destructive_tools(manifest: dict[str, Any]) -> frozenset[str]:
    raw = manifest.get("destructive_tools")
    if not isinstance(raw, list):
        raw = []
    out: set[str] = set()
    for item in raw:
        name = str(item).strip()
        if name:
            out.add(namespace_tool_name(manifest, name))
    return frozenset(out)


def plugin_plan_tools(manifest: dict[str, Any]) -> frozenset[str]:
    """Optional nested-MCP ``plan_tools`` (bare or already namespaced names)."""
    raw = manifest.get("plan_tools")
    if not isinstance(raw, list):
        raw = []
    out: set[str] = set()
    for item in raw:
        name = str(item).strip()
        if not name:
            continue
        out.add(namespace_tool_name(manifest, name) if "__" not in name else name)
    return frozenset(out)
