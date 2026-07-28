"""Niagara tools: discover, create, place, and drive Niagara VFX systems."""

from __future__ import annotations

from backend.bridge import send_command
from backend.json_util import tool_json
from backend.tools.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("vfx")
def niagara_capabilities(pretty: bool = False) -> str:
    """Probe which Niagara classes this UEFN build exposes (run before other niagara tools)."""
    return tool_json(send_command("niagara_capabilities", {}), pretty=pretty)


@plugin_mcp_tool("vfx")
def list_niagara_systems(search: str = "", offset: int = 0, limit: int = 50, pretty: bool = False) -> str:
    """List NiagaraSystem assets in the project (filter with search, paged)."""
    return tool_json(send_command("list_niagara_systems", {"search": search, "offset": offset, "limit": limit}), pretty=pretty)


@plugin_mcp_tool("vfx")
def get_niagara_system_info(system_path: str, pretty: bool = False) -> str:
    """Read a NiagaraSystem's exposed user parameters and emitter names."""
    return tool_json(send_command("get_niagara_system_info", {"system_path": system_path}), pretty=pretty)


@plugin_mcp_tool("vfx")
def create_niagara_system(asset_name: str, folder: str = "/Game/VFX", pretty: bool = False) -> str:
    """Create an empty NiagaraSystem asset (errors if it already exists). Place it with spawn_actor(asset_path=...)."""
    return tool_json(send_command("create_niagara_system", {"asset_name": asset_name, "folder": folder}), pretty=pretty)


@plugin_mcp_tool("vfx")
def get_niagara_component_info(actor_path: str, pretty: bool = False) -> str:
    """Read the system + available parameter setters on an actor's NiagaraComponent."""
    return tool_json(send_command("get_niagara_component_info", {"actor_path": actor_path}), pretty=pretty)


@plugin_mcp_tool("vfx")
def set_niagara_component_parameter(
    actor_path: str,
    param_name: str,
    value_type: str,
    value: bool | int | float | list[float],
    pretty: bool = False,
) -> str:
    """Set a Niagara USER parameter on an actor's NiagaraComponent.

    value_type: float|int|bool|vec2|vec3|vec4|color|position. Unknown param names are
    silently ignored by the engine — check names with get_niagara_system_info first.
    """
    return tool_json(
        send_command(
            "set_niagara_component_parameter",
            {"actor_path": actor_path, "param_name": param_name, "value_type": value_type, "value": value},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("vfx")
def control_niagara_actor(actor_path: str, action: str, pretty: bool = False) -> str:
    """Activate/deactivate/reset/reinitialize the NiagaraComponent on an actor."""
    return tool_json(send_command("control_niagara_actor", {"actor_path": actor_path, "action": action}), pretty=pretty)
