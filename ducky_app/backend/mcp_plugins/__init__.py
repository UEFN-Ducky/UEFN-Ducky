"""AppData-backed MCP plugin packs for external MCP servers (e.g. Blender)."""

from backend.mcp_plugins.registry import (
    PLUGIN_TOOL_SEP,
    is_plugin_tool,
    namespace_tool_name,
    parse_plugin_tool,
)
from backend.mcp_plugins.store import (
    PLUGIN_MANIFEST,
    appdata_mcp_plugins_dir,
    bundled_mcp_plugins_dir,
    create_mcp_plugin,
    delete_mcp_plugin,
    effective_plugin_ids,
    get_enabled_plugin_ids,
    list_mcp_plugin_manifests,
    list_mcp_plugins,
    load_plugin_manifest,
    normalize_plugin_id,
    normalize_transport,
    resolve_server_block,
    seed_mcp_plugins,
    set_active_plugin_ids,
    set_mcp_plugin_enabled,
)

__all__ = [
    "PLUGIN_MANIFEST",
    "PLUGIN_TOOL_SEP",
    "appdata_mcp_plugins_dir",
    "bundled_mcp_plugins_dir",
    "create_mcp_plugin",
    "delete_mcp_plugin",
    "effective_plugin_ids",
    "get_enabled_plugin_ids",
    "is_plugin_tool",
    "list_mcp_plugin_manifests",
    "list_mcp_plugins",
    "load_plugin_manifest",
    "namespace_tool_name",
    "normalize_plugin_id",
    "normalize_transport",
    "parse_plugin_tool",
    "resolve_server_block",
    "seed_mcp_plugins",
    "set_active_plugin_ids",
    "set_mcp_plugin_enabled",
]
