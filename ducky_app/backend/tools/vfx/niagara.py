"""Niagara tools: discover, create, place, and drive Niagara VFX systems."""

from __future__ import annotations

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


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
    """Read a NiagaraSystem: user parameters (incl. ones linked during assembly), emitters, open session."""
    return tool_json(send_command("get_niagara_system_info", {"system_path": system_path}), pretty=pretty)


@plugin_mcp_tool("vfx")
def list_niagara_user_parameters(
    actor_path: str,
    names: list[str],
    value_type: str = "float",
    pretty: bool = False,
) -> str:
    """Probe which User.* names a PLACED Niagara actor really exposes (component getters return found flags)."""
    return tool_json(
        send_command(
            "list_niagara_user_parameters",
            {"actor_path": actor_path, "names": names, "value_type": value_type},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("vfx")
def create_niagara_system(asset_name: str, folder: str = "", pretty: bool = False) -> str:
    """Create an empty NiagaraSystem asset (errors if it already exists). Place it with spawn_actor(asset_path=...).

    Throwaway paths (__probe / Temp / NS_*Test) are rejected — ship real Content only.
    """
    return tool_json(send_command("create_niagara_system", {"asset_name": asset_name, "folder": folder}), pretty=pretty)


@plugin_mcp_tool("vfx")
def create_niagara_mesh(
    asset_name: str,
    shape: str = "sphere",
    folder: str = "",
    radius: float = 50.0,
    height: float = 100.0,
    size: list[float] | None = None,
    steps: int = 16,
    scale: list[float] | None = None,
    material: str = "",
    replace: bool = False,
    pretty: bool = False,
) -> str:
    """Create a project-owned StaticMesh (Geometry Script) for Niagara MESH particles.

    shape: sphere|box|cylinder|cone|torus|capsule|disc. Bake the look with `material`
    (project material/MI) instead of Niagara override_materials. Required because UEFN
    mesh particles must not reference /Engine/BasicShapes, which cannot be duplicated.
    """
    return tool_json(
        send_command(
            "create_niagara_mesh",
            {
                "asset_name": asset_name,
                "shape": shape,
                "folder": folder,
                "radius": radius,
                "height": height,
                "size": size,
                "steps": steps,
                "scale": scale,
                "material": material,
                "replace": replace,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("vfx")
def add_niagara_emitter(
    system_path: str,
    emitter_name: str,
    modules: list[dict] | None = None,
    renderers: list[dict] | None = None,
    sim_target: str = "cpu",
    local_space: bool = False,
    enabled: bool = True,
    emitter_state: bool = True,
    loop_duration: float | None = None,
    finalize: bool = True,
    pretty: bool = False,
) -> str:
    """Add ONE emitter (stock modules + renderers) to a saved NiagaraSystem, then finalize and save.

    Never batch a whole scene: a monolithic builder that finalized ~22 emitters at once
    crashed UEFN. One emitter per call.

    modules: [{"name", "module_path": "/Niagara/Modules/...", "category":
    emitter_spawn|emitter_update|particle_spawn|particle_update, "parameters": [...]}].
    Each parameter is {"name", "value_type", "value"} | {"name", "link": "User.X",
    "value_type"} | {"name", "dynamic": {"module_path": "/Niagara/DynamicInputs/...",
    "parameters": [...]}} (nesting capped at depth 4).
    renderers: [{"type": "mesh", "mesh": "/Proj/Fx/Meshes/SM_X", "scale": [1,1,1]}] or
    [{"type": "sprite", "material": "/Proj/Fx/MaterialInstances/MI_X"}].
    An EmitterState module is added automatically unless emitter_state=false.
    """
    return tool_json(
        send_command(
            "add_niagara_emitter",
            {
                "system_path": system_path,
                "emitter_name": emitter_name,
                "modules": modules,
                "renderers": renderers,
                "sim_target": sim_target,
                "local_space": local_space,
                "enabled": enabled,
                "emitter_state": emitter_state,
                "loop_duration": loop_duration,
                "finalize": finalize,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("vfx")
def add_niagara_module(
    system_path: str,
    emitter_name: str,
    module_path: str,
    category: str = "particle_update",
    parameters: list[dict] | None = None,
    module_name: str = "",
    finalize: bool = True,
    pretty: bool = False,
) -> str:
    """Attach one stock module script to an emitter that is OPEN in the current session.

    An emitter is open only until its session finalizes (UEFN exposes no find-emitter),
    so chain these with finalize=false, or pass modules inline to add_niagara_emitter.
    """
    return tool_json(
        send_command(
            "add_niagara_module",
            {
                "system_path": system_path,
                "emitter_name": emitter_name,
                "module_path": module_path,
                "category": category,
                "parameters": parameters,
                "module_name": module_name,
                "finalize": finalize,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("vfx")
def set_niagara_module_parameter(
    system_path: str,
    emitter_name: str,
    module_name: str,
    parameters: list[dict],
    module_path: str = "",
    category: str = "particle_update",
    finalize: bool = True,
    pretty: bool = False,
) -> str:
    """Set inputs on a module of an emitter open in the current session (adds it if missing).

    Same parameter shapes as add_niagara_emitter. A rejected input name is a hard error —
    read the frozen input map in the vfx skill instead of guessing names.
    """
    return tool_json(
        send_command(
            "set_niagara_module_parameter",
            {
                "system_path": system_path,
                "emitter_name": emitter_name,
                "module_name": module_name,
                "parameters": parameters,
                "module_path": module_path,
                "category": category,
                "finalize": finalize,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("vfx")
def add_niagara_renderer(
    system_path: str,
    emitter_name: str,
    renderer_type: str = "sprite",
    mesh: str = "",
    material: str = "",
    scale: list[float] | None = None,
    renderer_name: str = "",
    finalize: bool = True,
    pretty: bool = False,
) -> str:
    """Add a sprite|mesh|ribbon|light renderer to an emitter open in the current session.

    `mesh` must be a project StaticMesh from create_niagara_mesh — Engine shapes are rejected.
    """
    return tool_json(
        send_command(
            "add_niagara_renderer",
            {
                "system_path": system_path,
                "emitter_name": emitter_name,
                "renderer_type": renderer_type,
                "mesh": mesh,
                "material": material,
                "scale": scale,
                "renderer_name": renderer_name,
                "finalize": finalize,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("vfx")
def finalize_niagara_system(system_path: str, pretty: bool = False) -> str:
    """Compile and SAVE the open conversion session for a system (ends a finalize=false chain)."""
    return tool_json(send_command("finalize_niagara_system", {"system_path": system_path}), pretty=pretty)


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
