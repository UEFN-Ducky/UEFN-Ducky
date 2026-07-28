"""Dynamic plugin tool intents and destructive tool names."""

from __future__ import annotations

from typing import Any

from backend.mcp_plugins.registry import (
    plugin_destructive_tools,
    plugin_intent_pattern,
    plugin_plan_tools,
)
from backend.mcp_plugins.store import (
    effective_plugin_ids,
    ensure_plugin_prefix_cache,
    load_plugin_manifest,
)


def _enabled_manifests() -> list[dict[str, Any]]:
    """Manifests for the current run's effective plugin set (per-chat aware)."""
    ensure_plugin_prefix_cache()
    out: list[dict[str, Any]] = []
    for pid in effective_plugin_ids():
        manifest = load_plugin_manifest(pid)
        if manifest:
            out.append(manifest)
    return out


def enabled_mcp_plugins_prompt_block() -> str:
    manifests = _enabled_manifests()
    if not manifests:
        return ""
    lines = [
        "## Enabled nested MCP servers",
        "These nest under the uefn-ducky bridge. Tools use `{prefix}__{tool_name}` naming.",
    ]
    for manifest in manifests:
        pid = str(manifest.get("id") or "")
        label = str(manifest.get("label") or pid)
        prefix = str(manifest.get("tool_prefix") or pid)
        lines.append(f"- **{label}** (`{prefix}__*`) — {manifest.get('description', '').strip()}")
    lines.append(
        "Use nested server tools when the user mentions a matching workflow. "
        "Blender / other Store desktop plugins appear in **Enabled Store desktop plugins** "
        "above (blender_*), not as nested MCP servers — never wait on nested MCP for them."
    )
    return "\n".join(lines) + "\n"


def plugin_destructive_tool_names() -> frozenset[str]:
    names: set[str] = set()
    for manifest in _enabled_manifests():
        names.update(plugin_destructive_tools(manifest))
    return frozenset(names)


def nested_mcp_plan_tool_names() -> frozenset[str]:
    """Union of optional ``plan_tools`` from enabled nested MCP manifests."""
    names: set[str] = set()
    for manifest in _enabled_manifests():
        names.update(plugin_plan_tools(manifest))
    return frozenset(names)


def plugin_tools_matching_message(all_tool_names: set[str], user_message: str) -> frozenset[str]:
    selected: set[str] = set()
    text = user_message or ""
    for manifest in _enabled_manifests():
        pattern = plugin_intent_pattern(manifest)
        if pattern is None or not pattern.search(text):
            continue
        prefix = str(manifest.get("tool_prefix") or manifest.get("id") or "")
        needle = f"{prefix}__"
        for name in all_tool_names:
            if name.startswith(needle):
                selected.add(name)
    return frozenset(selected)
