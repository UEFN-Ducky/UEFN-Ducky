"""Actor and viewport-selection tools."""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool

DEFAULT_QUERY_LIMIT = 200

CREATIVE_DEVICE_SCALE_REFUSED = (
    "Refused: Creative devices break when scaled. "
    "Resize with SetDeviceProperty Width/Height/Depth."
)
# Last path segment, minus the Blueprint ``_C`` and Outliner ``_N`` suffixes.
_ACTOR_SUFFIX = re.compile(r"(_C)?(_\d+)?$", re.IGNORECASE)


@plugin_mcp_tool("uefn")
def get_all_actors(
    class_filter: str = "",
    class_prefix: str = "",
    label_filter: str = "",
    offset: int = 0,
    limit: Optional[int] = DEFAULT_QUERY_LIMIT,
    fields: Optional[list[str]] = None,
    pretty: bool = False,
) -> str:
    """List level actors (paged; label_filter='wallet' finds an Outliner name). Not a device census (use Epic).

    Never class_prefix='Fort' for devices — that only returns player starts.
    No filters = full inventory.
    """
    params: dict[str, Any] = {
        "class_filter": class_filter,
        "class_prefix": class_prefix,
        "label_filter": label_filter,
        "offset": offset,
    }
    if limit is not None:
        params["limit"] = limit
    if fields:
        params["fields"] = fields
    result = send_command("get_all_actors", params)
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def get_selected_actors(pretty: bool = False) -> str:
    """List the actors selected in the UEFN viewport. Never changes the selection (select_actors does)."""
    result = send_command("get_selected_actors")
    return tool_json(result, pretty=pretty)


def _verse_device_spawn_hint(actor_class: str) -> str | None:
    """Hint when agents pass a Verse *_device API name as actor_class."""
    name = (actor_class or "").strip()
    if not name:
        return None
    lower = name.lower()
    if not (lower.endswith("_device") or lower.endswith("_device_base")):
        return None
    # teleporter_device → Teleporter; item_granter_device → Item_Granter-ish
    stem = name[: -len("_device")] if lower.endswith("_device") else name
    search = stem.replace("_", " ").strip().title().replace(" ", "_")
    if "teleporter" in lower:
        search = "Teleporter"
    return (
        f"{name!r} is a Verse API type, not a Blueprint class. "
        f'Use search_assets(search="{search}", directory="/Game/Creative", limit=10) '
        f'then spawn_actor(asset_path="…BP_….BP_…_C"). '
        f'For API members: get_verse_api(name="{name}").'
    )


def _is_creative_device_name(actor_path: str) -> bool:
    """True when an actor path or class names a Creative device (FortCreative*, BP_Creative_*, *_device)."""
    name = re.split(r"[/.:]", (actor_path or "").strip())[-1]
    lower = name.lower()
    if lower.startswith("fortcreative") or lower.startswith("bp_creative_"):
        return True
    return _ACTOR_SUFFIX.sub("", name).lower().endswith("_device")


def _is_rescale(scale: Optional[list[float]]) -> bool:
    """Scale [1,1,1] rides along on move/revert snapshots; only a real change counts."""
    if scale is None:
        return False
    try:
        return any(abs(float(v) - 1.0) > 1e-3 for v in list(scale)[:3])
    except (TypeError, ValueError):
        return True


@plugin_mcp_tool("uefn")
def spawn_actor(
    asset_path: str = "",
    actor_class: str = "",
    location: Optional[list[float]] = None,
    rotation: Optional[list[float]] = None,
    select: bool = False,
    label: str = "",
    folder: str = "",
    tags: Optional[list[str]] = None,
    pretty: bool = False,
) -> str:
    """Place a prop, mesh, Blueprint or device by asset_path (…_C). Never a Verse *_device name as actor_class.

    Prefer ``label`` + ``folder`` here (same tick) instead of separate
    set_actor_label / set_actor_folder calls. The person's selection is left
    alone; pass ``select=true`` only when they asked to have the new actor selected.
    Do not pass Verse ``*_device`` names as ``actor_class`` — search under
    ``/Game/Creative`` for the Blueprint path. Fortnite catalog props: spawn the
    Content Drawer ``_C`` class, not a BakeData StaticMesh.
    """
    # Teach before the listener round-trip when the mistake is obvious.
    if not asset_path and actor_class:
        hint = _verse_device_spawn_hint(actor_class)
        if hint:
            return tool_json(
                {
                    "error": f"Class not found: {actor_class}",
                    "hint": hint,
                },
                pretty=pretty,
            )
    params: dict[str, Any] = {"select": select}
    if asset_path:
        params["asset_path"] = asset_path
    if actor_class:
        params["actor_class"] = actor_class
    if location is not None:
        params["location"] = location
    if rotation is not None:
        params["rotation"] = rotation
    if label:
        params["label"] = label
    if folder:
        params["folder"] = folder
    if tags:
        params["tags"] = tags
    try:
        result = send_command("spawn_actor", params)
    except Exception as exc:
        msg = str(exc)
        hint = _verse_device_spawn_hint(actor_class) if actor_class else None
        if hint and ("Class not found" in msg or "not found" in msg.lower()):
            return tool_json({"error": msg, "hint": hint}, pretty=pretty)
        raise
    if isinstance(result, dict) and result.get("error") and actor_class:
        hint = _verse_device_spawn_hint(actor_class)
        if hint:
            result = {**result, "hint": hint}
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def delete_actors(actor_paths: list[str], pretty: bool = False) -> str:
    """Delete named Outliner actors. Never call it unless the user asked to delete them."""
    result = send_command("delete_actors", {"actor_paths": actor_paths})
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def set_actor_transform(
    actor_path: str,
    location: Optional[list[float]] = None,
    rotation: Optional[list[float]] = None,
    scale: Optional[list[float]] = None,
    pretty: bool = False,
) -> str:
    """Move, rotate or scale an actor. Never scales Creative devices (resize with SetDeviceProperty).

    Scale is for props, meshes, and custom assets only. Fortnite Creative
    devices (buttons, triggers, volumes, barriers, pads, granters, Island
    Settings) refuse scale — resize those via Details Width/Height/zone
    with SetDeviceProperty. Location and rotation are fine on devices.
    """
    # The listener checks the real class too; this catches the named cases without a round-trip.
    if _is_rescale(scale) and _is_creative_device_name(actor_path):
        return tool_json({"error": CREATIVE_DEVICE_SCALE_REFUSED}, pretty=pretty)
    params: dict[str, Any] = {"actor_path": actor_path}
    if location is not None:
        params["location"] = location
    if rotation is not None:
        params["rotation"] = rotation
    if scale is not None:
        params["scale"] = scale
    result = send_command("set_actor_transform", params)
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def get_actor_properties(actor_path: str, properties: list[str], pretty: bool = False) -> str:
    """Read named properties from a placed actor. Not for Verse @editables (use get_verse_editables).

    See uefn_editor_python_hints for Fort* quirks.
    """
    result = send_command("get_actor_properties", {"actor_path": actor_path, "properties": properties})
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def set_actor_properties(actor_path: str, properties: dict[str, Any], pretty: bool = False) -> str:
    """Change property values on a placed light, mesh or prop. Not for Verse @editables or device settings.

    Uses set_editor_property.

    See uefn_editor_python_hints for Fort* quirks.
    """
    result = send_command("set_actor_properties", {"actor_path": actor_path, "properties": properties})
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def select_actors(actor_paths: list[str], add_to_selection: bool = False, pretty: bool = False) -> str:
    """Select actors in the viewport. Never moves or edits them."""
    result = send_command("select_actors", {"actor_paths": actor_paths, "add_to_selection": add_to_selection})
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def focus_selected(pretty: bool = False) -> str:
    """Point the viewport camera at the selected actors. Does not change the selection."""
    result = send_command("focus_selected")
    return tool_json(result, pretty=pretty)
