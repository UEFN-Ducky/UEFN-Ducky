"""Actor and selection commands."""

from typing import Any, Dict, List, Optional

import unreal

from listener import lookup
from listener.dispatch import register
from listener.serialize import ALL_ACTOR_FIELDS, is_live, rotator_pyr, serialize, serialize_actor


@register("get_all_actors")
def cmd_get_all_actors(
    class_filter: str = "",
    class_prefix: str = "",
    label_filter: str = "",
    offset: int = 0,
    limit: Optional[int] = None,
    fields: Optional[List[str]] = None,
) -> dict:
    actors = lookup.actor_list()
    if class_filter:
        actors = [a for a in actors if a.get_class().get_name() == class_filter]
    if class_prefix:
        actors = [a for a in actors if a.get_class().get_name().startswith(class_prefix)]
    if label_filter:
        needle = label_filter.lower()
        actors = [
            a
            for a in actors
            if needle in (a.get_actor_label() or "").lower()
            or needle in (a.get_name() or "").lower()
        ]
    total = len(actors)
    if offset > 0:
        actors = actors[offset:]
    if limit is not None and limit >= 0:
        actors = actors[:limit]
    field_list = None
    if fields:
        field_list = [f for f in fields if f in ALL_ACTOR_FIELDS]
    return {
        "actors": [serialize_actor(a, field_list) for a in actors],
        "count": len(actors),
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@register("get_selected_actors")
def cmd_get_selected_actors() -> dict:
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = actor_sub.get_selected_level_actors()
    return {"actors": [serialize_actor(a) for a in actors], "count": len(actors)}


def _load_placeable(asset_path: str):
    """Load anything placeable by path: a normal asset, a Blueprint, or a Verse-device class.

    Verse device classes live in packages like ``/Game/_Verse.<mangled-name>`` and load via
    ``load_object`` rather than ``load_asset``. The returned object is fed to the actor factory
    (``spawn_actor_from_object``), which places meshes, Blueprints, and Verse classes alike.
    """
    obj = None
    try:
        obj = unreal.EditorAssetLibrary.load_asset(asset_path)
    except Exception:
        obj = None
    if obj is None:
        try:
            obj = unreal.load_object(None, asset_path)
        except Exception:
            obj = None
    return obj


def _resolve_actor_class(actor_class: str) -> Optional[type]:
    """Resolve an actor class from a native ``unreal.X`` name or a content/Verse/Blueprint path."""
    cls = getattr(unreal, actor_class, None)
    if cls is not None:
        return cls
    if "/" in actor_class or "." in actor_class:
        try:
            loaded = unreal.EditorAssetLibrary.load_blueprint_class(actor_class)
        except Exception:
            loaded = None
        if loaded is not None:
            return loaded
        try:
            return unreal.load_class(None, actor_class)
        except Exception:
            return None
    return None


def _spawn_any(obj, loc, rot):
    """Place ``obj`` (asset, Blueprint, or class) via the actor factory.

    The factory accepts a class object too — crucially, this is the ONLY path that works for
    Verse device classes (``spawn_actor_from_class`` rejects them from Python with a nativize
    error). For native ``unreal.*`` classes we try ``spawn_actor_from_class`` first, then fall back.
    """
    if isinstance(obj, unreal.Class):
        try:
            return unreal.EditorLevelLibrary.spawn_actor_from_class(obj, loc, rot)
        except Exception:
            return unreal.EditorLevelLibrary.spawn_actor_from_object(obj, loc, rot)
    return unreal.EditorLevelLibrary.spawn_actor_from_object(obj, loc, rot)


@register("spawn_actor")
def cmd_spawn_actor(
    asset_path: str = "",
    actor_class: str = "",
    location: Optional[List[float]] = None,
    rotation: Optional[List[float]] = None,
    select: bool = True,
) -> dict:
    loc = unreal.Vector(*location) if location else unreal.Vector(0, 0, 0)
    rot = rotator_pyr(*rotation) if rotation else rotator_pyr(0, 0, 0)

    if asset_path:
        obj = _load_placeable(asset_path)
        if obj is None:
            raise ValueError(f"Asset/class not found: {asset_path}")
        actor = _spawn_any(obj, loc, rot)
    elif actor_class:
        cls = _resolve_actor_class(actor_class)
        if cls is None:
            name = (actor_class or "").strip()
            lower = name.lower()
            if lower.endswith("_device"):
                search = "Teleporter" if "teleporter" in lower else name[: -len("_device")].replace("_", " ").title().replace(" ", "_")
                raise ValueError(
                    f"Class not found: {actor_class}. "
                    f"{name!r} is a Verse API type, not a Blueprint. "
                    f'Use search_assets(search="{search}", directory="/Game/Creative") '
                    f'then spawn_actor(asset_path="…BP_….BP_…_C"). '
                    f'For API: get_verse_api(name="{name}").'
                )
            raise ValueError(f"Class not found: {actor_class}")
        actor = _spawn_any(cls, loc, rot)
    else:
        raise ValueError("Provide either asset_path or actor_class")

    if actor is None:
        raise RuntimeError("Failed to spawn actor")
    # The level changed; drop the per-tick index so later lookups see this actor.
    lookup.invalidate()
    # Select the new actor so it is highlighted and ready to move in the viewport.
    if select:
        try:
            actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
            actor_sub.set_selected_level_actors([actor])
        except Exception:
            pass
    return {"actor": serialize_actor(actor)}


@register("delete_actors")
def cmd_delete_actors(actor_paths: List[str]) -> dict:
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    deleted = []
    for path in actor_paths:
        # Re-resolve every iteration: destroying an actor cascades to its attached
        # children, so a ref cached before the loop may already be destroyed. Calling
        # destroy_actor on that stale ref is an uncatchable native access violation.
        actor = lookup.find_actor(path)
        if not is_live(actor):
            continue
        actor_sub.destroy_actor(actor)
        deleted.append(path)
        # Drop the per-tick index so the next find_actor sees the post-cascade level.
        lookup.invalidate()
    return {"deleted": deleted, "count": len(deleted)}


@register("set_actor_transform")
def cmd_set_actor_transform(
    actor_path: str,
    location: Optional[List[float]] = None,
    rotation: Optional[List[float]] = None,
    scale: Optional[List[float]] = None,
) -> dict:
    target = lookup.require_actor(actor_path)

    if location is not None:
        target.set_actor_location(unreal.Vector(*location), False, False)
    if rotation is not None:
        target.set_actor_rotation(rotator_pyr(*rotation), False)
    if scale is not None:
        target.set_actor_scale3d(unreal.Vector(*scale))
    lookup.invalidate()
    return {"actor": serialize_actor(target)}


@register("get_actor_properties")
def cmd_get_actor_properties(
    actor_path: str,
    properties: Optional[List[str]] = None,
) -> dict:
    target = lookup.require_actor(actor_path)
    props = list(properties or [])
    if not props:
        raise ValueError(
            "properties is required — pass a list of editor property names, "
            "e.g. ['ActorLabel', 'FolderPath']"
        )

    result = {}
    for prop in props:
        try:
            result[prop] = serialize(target.get_editor_property(prop))
        except Exception as e:
            result[prop] = f"<error: {e}>"
    return {"actor_path": actor_path, "properties": result}


@register("set_actor_properties")
def cmd_set_actor_properties(actor_path: str, properties: Dict[str, Any]) -> dict:
    target = lookup.require_actor(actor_path)

    set_results = {}
    for prop, value in properties.items():
        try:
            target.set_editor_property(prop, value)
            set_results[prop] = "ok"
        except Exception as e:
            set_results[prop] = f"<error: {e}>"
    # An arbitrary property write can change the label (index key) or other
    # cached state — drop the index so later lookups this tick see it fresh.
    lookup.invalidate()
    return {"actor_path": actor_path, "properties": set_results}


@register("list_actor_components")
def cmd_list_actor_components(actor_path: str) -> dict:
    """List components on a PLACED actor: name, class, mesh asset, attach parent/socket.

    The safe way to see how an actor is built — never poke Blueprint CDOs via
    execute_python for this (get_default_object + component walks crash UEFN).
    """
    actor = lookup.require_actor(actor_path)
    if not is_live(actor):
        raise ValueError(f"Actor no longer valid: {actor_path}")
    rows = []
    for comp in actor.get_components_by_class(unreal.ActorComponent):
        row: Dict[str, Any] = {"name": comp.get_name(), "class": comp.get_class().get_name()}
        for prop, key in (("static_mesh", "mesh"), ("skeletal_mesh", "mesh")):
            try:
                asset = comp.get_editor_property(prop)
                if asset is not None:
                    row[key] = asset.get_path_name()
                    break
            except Exception:
                continue
        try:
            parent = comp.get_attach_parent()
            if parent is not None:
                row["attach_parent"] = parent.get_name()
                sock = str(comp.get_attach_socket_name())
                if sock and sock != "None":
                    row["attach_socket"] = sock
        except Exception:
            pass
        rows.append(row)
    return {"actor": actor.get_path_name(), "components": rows, "count": len(rows)}


@register("select_actors")
def cmd_select_actors(actor_paths: List[str], add_to_selection: bool = False) -> dict:
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    to_select = []
    found = []
    for path in actor_paths:
        a = lookup.find_actor(path)
        if is_live(a):
            to_select.append(a)
            found.append(path)

    if add_to_selection:
        current = [a for a in actor_sub.get_selected_level_actors() if is_live(a)]
        to_select = current + to_select

    actor_sub.set_selected_level_actors(to_select)
    return {"selected": found, "count": len(found)}


@register("focus_selected")
def cmd_focus_selected() -> dict:
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    selected = actor_sub.get_selected_level_actors()
    if not selected:
        raise ValueError("No actors selected")

    xs, ys, zs = [], [], []
    for a in selected:
        loc = a.get_actor_location()
        xs.append(loc.x)
        ys.append(loc.y)
        zs.append(loc.z)

    center_x = sum(xs) / len(xs)
    center_y = sum(ys) / len(ys)
    center_z = sum(zs) / len(zs)

    spread = max(
        max(xs) - min(xs),
        max(ys) - min(ys),
        max(zs) - min(zs),
        200.0,
    )
    cam_dist = spread * 1.5
    cam_loc = unreal.Vector(center_x - cam_dist * 0.5, center_y - cam_dist * 0.5, center_z + cam_dist * 0.5)
    cam_rot = rotator_pyr(-35, 45, 0)

    unreal.EditorLevelLibrary.set_level_viewport_camera_info(cam_loc, cam_rot)
    return {
        "center": {"x": center_x, "y": center_y, "z": center_z},
        "camera": serialize(cam_loc),
        "actors_count": len(selected),
    }
