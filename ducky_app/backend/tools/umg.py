"""UMG / Widget Blueprint tools: create, inspect, tree scaffold, MVVM bindings."""

from __future__ import annotations

from typing import Any

from backend.bridge import send_command
from backend.json_util import tool_json
from backend.tools.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("verse")
def umg_capabilities(pretty: bool = False) -> str:
    """Probe UMG / MVVM / ToolsetRegistry availability (run before other umg tools).

    Never dumps toolset JSON schemas — that crashes UEFN.
    """
    return tool_json(send_command("umg_capabilities", {}), pretty=pretty)


@plugin_mcp_tool("verse")
def list_widget_blueprints(search: str = "", offset: int = 0, limit: int = 50, pretty: bool = False) -> str:
    """List WidgetBlueprint assets in the project (filter with search, paged)."""
    return tool_json(
        send_command("list_widget_blueprints", {"search": search, "offset": offset, "limit": limit}),
        pretty=pretty,
    )


@plugin_mcp_tool("verse")
def get_widget_blueprint_info(widget_path: str, pretty: bool = False) -> str:
    """Inspect a WidgetBlueprint: member vars (Verse fields), event dispatchers, tree, MVVM bindings."""
    return tool_json(send_command("get_widget_blueprint_info", {"widget_path": widget_path}), pretty=pretty)


@plugin_mcp_tool("verse")
def create_widget_blueprint(
    asset_name: str,
    folder: str = "",
    parent_class: str = "UserWidget",
    pretty: bool = False,
) -> str:
    """Create an empty WidgetBlueprint (errors if it already exists). Prefer UW_ name prefix."""
    return tool_json(
        send_command(
            "create_widget_blueprint",
            {"asset_name": asset_name, "folder": folder, "parent_class": parent_class},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("verse")
def add_widget_to_tree(
    widget_path: str,
    widget_class: str,
    widget_name: str,
    parent_ref_path: str = "",
    pretty: bool = False,
) -> str:
    """Add a widget under a panel via UMGToolSet.AddWidget. Scaffold only — polish in the designer."""
    return tool_json(
        send_command(
            "add_widget_to_tree",
            {
                "widget_path": widget_path,
                "widget_class": widget_class,
                "widget_name": widget_name,
                "parent_ref_path": parent_ref_path,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("verse")
def remove_widget_from_tree(widget_path: str, widget_ref_path: str, pretty: bool = False) -> str:
    """Remove a widget instance from the tree via UMGToolSet.RemoveWidget."""
    return tool_json(
        send_command(
            "remove_widget_from_tree",
            {"widget_path": widget_path, "widget_ref_path": widget_ref_path},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("verse")
def set_widget_property(
    widget_path: str,
    target_ref_path: str,
    properties: dict[str, Any],
    list_first: bool = True,
    pretty: bool = False,
) -> str:
    """Set properties on a widget/slot via ObjectTools (list_properties first by default)."""
    return tool_json(
        send_command(
            "set_widget_property",
            {
                "widget_path": widget_path,
                "target_ref_path": target_ref_path,
                "properties": properties,
                "list_first": list_first,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("verse")
def list_widget_bindings(widget_path: str, pretty: bool = False) -> str:
    """List MVVM view bindings on a WidgetBlueprint."""
    return tool_json(send_command("list_widget_bindings", {"widget_path": widget_path}), pretty=pretty)


@plugin_mcp_tool("verse")
def add_widget_binding(
    widget_path: str,
    source_path: str = "",
    destination_path: str = "",
    pretty: bool = False,
) -> str:
    """Add an MVVM binding (best-effort; finish complex binds in the View Bindings panel)."""
    return tool_json(
        send_command(
            "add_widget_binding",
            {
                "widget_path": widget_path,
                "source_path": source_path,
                "destination_path": destination_path,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("verse")
def remove_widget_binding(widget_path: str, binding_index: int = 0, pretty: bool = False) -> str:
    """Remove an MVVM binding by index."""
    return tool_json(
        send_command(
            "remove_widget_binding",
            {"widget_path": widget_path, "binding_index": binding_index},
        ),
        pretty=pretty,
    )
