"""Scene Graph prefab helpers. Entity CRUD is nested Epic MCP (`unreal__*`)."""

from __future__ import annotations

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("scenegraph")
def scene_graph_capabilities(pretty: bool = False) -> str:
    """Probe Scene Graph editor scripting (prefab helpers). Entity CRUD uses nested Epic `unreal__*` tools."""
    return tool_json(send_command("scene_graph_capabilities", {}), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def create_empty_prefab(prefab_name: str, folder: str = "", pretty: bool = False) -> str:
    """Create a blank EntityPrefab asset under the PROJECT mount (e.g. /MyProject/Prefabs).

    Does not place anything in the level. Use instantiate_prefab to place, or
    create_prefab_from_entities to package existing level entities into a NEW named prefab.
    """
    return tool_json(
        send_command("create_empty_prefab", {"prefab_name": prefab_name, "folder": folder}),
        pretty=pretty,
    )


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
def instantiate_prefab(
    prefab_path: str,
    name: str = "",
    parent_entity: str = "",
    translation: list[float] | None = None,
    pretty: bool = False,
) -> str:
    """Place an EntityPrefab into the level (same as Content Browser drag → EntityProxyActor).

    prefab_path: project asset path (e.g. /MyProject/SolarSystem/EP_SolarSystem).
    translation: optional SpatialMath [forward, left, up]. name renames the entity after place.
    """
    params: dict = {"prefab_path": prefab_path, "name": name, "parent_entity": parent_entity}
    if translation is not None:
        params["translation"] = translation
    return tool_json(send_command("instantiate_prefab", params), pretty=pretty)


@plugin_mcp_tool("scenegraph")
def convert_actors_to_entities(actor_paths: list[str], pretty: bool = False) -> str:
    """Convert level actors to Scene Graph entities (guarded; irreversible — confirm intent first)."""
    return tool_json(send_command("convert_actors_to_entities", {"actor_paths": actor_paths}), pretty=pretty)
