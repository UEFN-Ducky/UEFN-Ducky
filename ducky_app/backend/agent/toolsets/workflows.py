"""Named tool bundles for common UEFN workflows.

Intent regexes in intents.py are legacy; placement tools are reached via
ducky_call_tool. LISTENER_ONLINE_CORE is kept as a named set for docs/tests but
is no longer merged into tools[] every online turn.
"""

from __future__ import annotations

# Placement / editor tools — invoke via ducky_call_tool (not auto-merged into tools[]).
LISTENER_ONLINE_CORE: frozenset[str] = frozenset(
    {
        "spawn_actor",
        "search_assets",
        "set_actor_label",
        "get_viewport_camera",
        "save_current_level",
        "execute_python",
        "uefn_editor_python_hints",
    }
)

# Low-level listener command names → agent-facing MCP tool names.
_AGENT_TOOL_ALIASES: dict[str, str] = {
    "get_verse_editables": "inspect_verse_device",
}

PLACEMENT_WORKFLOW: frozenset[str] = frozenset(
    {
        "spawn_actor",
        "search_assets",
        "set_actor_label",
        "get_viewport_camera",
        "set_actor_transform",
        "save_current_level",
    }
)

WIRING_WORKFLOW: frozenset[str] = frozenset(
    {
        "inspect_verse_device",
        "wire_verse_device_ref",
        "wire_verse_device_array",
        "set_verse_editable",
        "resize_verse_array",
        "patch_verse_array_entry",
        "save_current_level",
    }
)

FILE_EDIT_WORKFLOW: frozenset[str] = frozenset(
    {
        "workspace_list_dir",
        "workspace_read_file",
        "workspace_write_file",
        "workspace_list_verse_errors",
        "workspace_compile_verse",
    }
)

MATERIAL_WORKFLOW: frozenset[str] = frozenset(
    {
        "create_material",
        "connect_material_nodes",
        "assign_material_to_mesh",
        "list_material_expressions",
        "list_uefn_material_expression_classes",
        "recompile_material",
        "search_assets",
        "get_asset_info",
    }
)


def agent_tool_name(name: str) -> str | None:
    """Map a listener-suggested tool to the agent-safe MCP name, if any."""
    n = (name or "").strip()
    if not n:
        return None
    return _AGENT_TOOL_ALIASES.get(n, n)
