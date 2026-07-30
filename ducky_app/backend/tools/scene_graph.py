"""Scene Graph tools: entities, components, and prefabs in the UEFN editor level."""

from __future__ import annotations

from backend.bridge import send_command
from backend.json_util import tool_json
from backend.tools.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("scenegraph")
def scene_graph_capabilities(pretty: bool = False) -> str:
    """Probe Scene Graph editor scripting availability (run before other scene graph tools)."""
    return tool_json(send_command("scene_graph_capabilities", {}), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def list_entities(name_filter: str = "", offset: int = 0, limit: int = 50, pretty: bool = False) -> str:
    """List Scene Graph entities in the level (filter with name_filter, paged)."""
    return tool_json(
        send_command("list_entities", {"name_filter": name_filter, "offset": offset, "limit": limit}), pretty=pretty
    )


@plugin_mcp_tool("scenegraph")
def get_entity_info(entity: str, pretty: bool = False) -> str:
    """Read one entity: components, children, parent, local transform, and bounds."""
    return tool_json(send_command("get_entity_info", {"entity": entity}), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def list_scene_component_classes(search: str = "", offset: int = 0, limit: int = 50, pretty: bool = False) -> str:
    """List Verse component classes addable to entities (builtin / project / asset_generated)."""
    return tool_json(
        send_command("list_scene_component_classes", {"search": search, "offset": offset, "limit": limit}),
        pretty=pretty,
    )


@plugin_mcp_tool("scenegraph")
def get_selected_entities(pretty: bool = False) -> str:
    """Read the entities currently selected in the level editor."""
    return tool_json(send_command("get_selected_entities", {}), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def select_entities(entity_names: list[str], pretty: bool = False) -> str:
    """Select the named entities in the level editor viewport/outliner."""
    return tool_json(send_command("select_entities", {"entity_names": entity_names}), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def create_entity(
    name: str,
    parent_entity: str = "",
    translation: list[float] | None = None,
    rotation_quat: list[float] | None = None,
    scale: list[float] | None = None,
    pretty: bool = False,
) -> str:
    """Create an empty Scene Graph entity in the level (optionally parented and placed).

    translation/scale are SpatialMath [forward, left, up]; rotation_quat is [x,y,z,w].
    Add visuals/behavior with add_entity_component; save_current_level once at the end.
    """
    params: dict = {"name": name, "parent_entity": parent_entity}
    if translation is not None:
        params["translation"] = translation
    if rotation_quat is not None:
        params["rotation_quat"] = rotation_quat
    if scale is not None:
        params["scale"] = scale
    return tool_json(send_command("create_entity", params), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def set_entity_transform(
    entity: str,
    translation: list[float] | None = None,
    rotation_quat: list[float] | None = None,
    scale: list[float] | None = None,
    pretty: bool = False,
) -> str:
    """Set an entity's local transform (SpatialMath [forward, left, up]; quaternion [x,y,z,w])."""
    params: dict = {"entity": entity}
    if translation is not None:
        params["translation"] = translation
    if rotation_quat is not None:
        params["rotation_quat"] = rotation_quat
    if scale is not None:
        params["scale"] = scale
    return tool_json(send_command("set_entity_transform", params), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def add_entity_component(entity: str, component_class: str, asset_path: str = "", pretty: bool = False) -> str:
    """Add a component to an entity.

    component_class: alias (mesh_component, sound_component, spot_light_component, ...),
    Verse class name, or full class object path. Pass asset_path (a PROJECT content asset)
    to create an asset-generated component (mesh/particle/sound) referencing that asset.
    """
    return tool_json(
        send_command(
            "add_entity_component",
            {"entity": entity, "component_class": component_class, "asset_path": asset_path},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("scenegraph")
def remove_entity_component(entity: str, component_class: str, pretty: bool = False) -> str:
    """Remove the component of the given class from an entity."""
    return tool_json(
        send_command("remove_entity_component", {"entity": entity, "component_class": component_class}), pretty=pretty
    )


@plugin_mcp_tool("scenegraph")
def set_entity_component_property(
    entity: str, component_class: str, prop: str, value: bool | int | float | str | list[float], pretty: bool = False
) -> str:
    """Set an @editable property on an entity's component by its Verse (digest) name.

    bool for logic, numbers for float/int, [f,l,u] list for vector3. Property names are
    case-sensitive digest names (check with get_verse_api). Transforms: set_entity_transform.
    """
    return tool_json(
        send_command(
            "set_entity_component_property",
            {"entity": entity, "component_class": component_class, "prop": prop, "value": value},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("scenegraph")
def get_entity_component_property(entity: str, component_class: str, prop: str, pretty: bool = False) -> str:
    """Read an @editable property on an entity's component by its Verse (digest) name."""
    return tool_json(
        send_command(
            "get_entity_component_property",
            {"entity": entity, "component_class": component_class, "prop": prop},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("scenegraph")
def rename_entity(entity: str, new_name: str, pretty: bool = False) -> str:
    """Rename an entity (names must stay unique within the level)."""
    return tool_json(send_command("rename_entity", {"entity": entity, "new_name": new_name}), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def set_entity_parent(entity: str, parent_entity: str, pretty: bool = False) -> str:
    """Re-parent an entity under another entity."""
    return tool_json(send_command("set_entity_parent", {"entity": entity, "parent_entity": parent_entity}), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def duplicate_entity(entity: str, new_name: str = "", pretty: bool = False) -> str:
    """Duplicate an entity (with its components and children); optionally rename the copy."""
    return tool_json(send_command("duplicate_entity", {"entity": entity, "new_name": new_name}), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def destroy_entity(entity: str, pretty: bool = False) -> str:
    """Destroy an entity and everything under it (children + components)."""
    return tool_json(send_command("destroy_entity", {"entity": entity}), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def create_prefab_from_entities(entity_names: list[str], prefab_name: str, folder: str = "", pretty: bool = False) -> str:
    """Package existing level entities into a new Prefab asset (they become an instance of it).

    folder is a content path in the PROJECT mount (e.g. /MyProject/Prefabs).
    """
    return tool_json(
        send_command(
            "create_prefab_from_entities",
            {"entity_names": entity_names, "prefab_name": prefab_name, "folder": folder},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("scenegraph")
def instantiate_prefab(prefab_path: str, name: str = "", parent_entity: str = "", pretty: bool = False) -> str:
    """Place an instance of a Prefab asset into the level (best effort — prefab scripting is WIP in UEFN)."""
    return tool_json(
        send_command(
            "instantiate_prefab", {"prefab_path": prefab_path, "name": name, "parent_entity": parent_entity}
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("scenegraph")
def convert_actors_to_entities(actor_paths: list[str], pretty: bool = False) -> str:
    """Convert level actors to Scene Graph entities (guarded; irreversible — confirm intent first)."""
    return tool_json(send_command("convert_actors_to_entities", {"actor_paths": actor_paths}), pretty=pretty)
