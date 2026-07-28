"""Level registry tools: batch spawn, duplicate, organize, and query level actors."""

from __future__ import annotations

from typing import List, Optional

import unreal

from listener import lookup
from listener.dispatch import register
from listener.serialize import rotator_pyr, serialize_actor


def _spawn_one(asset_path: str = "", actor_class: str = "", location=None, rotation=None) -> unreal.Actor:
    loc = unreal.Vector(*location) if location else unreal.Vector(0, 0, 0)
    rot = rotator_pyr(*rotation) if rotation else rotator_pyr(0, 0, 0)
    if asset_path:
        asset = unreal.EditorAssetLibrary.load_asset(asset_path)
        if asset is None:
            raise ValueError(f"Asset not found: {asset_path}")
        actor = unreal.EditorLevelLibrary.spawn_actor_from_object(asset, loc, rot)
    elif actor_class:
        cls = getattr(unreal, actor_class, None)
        if cls is None:
            raise ValueError(f"Class not found: {actor_class}")
        actor = unreal.EditorLevelLibrary.spawn_actor_from_class(cls, loc, rot)
    else:
        raise ValueError("Provide either asset_path or actor_class")
    if actor is None:
        raise RuntimeError("Failed to spawn actor")
    return actor


def spawn_actor_batch(items: List[dict]) -> dict:
    """Removed — use spawn_actor once per actor."""
    from listener.bulk_removed import bulk_removed_error

    return bulk_removed_error()


def duplicate_actor(actor_path: str, location_offset: Optional[List[float]] = None) -> dict:
    """Duplicate an actor, optionally offsetting the copy's location."""
    src = lookup.require_actor(actor_path)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    dup = actor_sub.duplicate_actor(src)
    if dup is None:
        raise RuntimeError("duplicate_actor returned None")
    if location_offset and len(location_offset) >= 3:
        base = dup.get_actor_location()
        dup.set_actor_location(
            unreal.Vector(base.x + location_offset[0], base.y + location_offset[1], base.z + location_offset[2]),
            False,
            False,
        )
    lookup.invalidate()
    return {"source": actor_path, "actor": serialize_actor(dup)}


def set_actor_folder(actor_path: str, folder: str) -> dict:
    """Move an actor into a World Outliner folder path."""
    actor = lookup.require_actor(actor_path)
    actor.set_folder_path(unreal.Name(folder))
    lookup.invalidate()
    return {"actor_path": actor.get_path_name(), "folder": folder}


def set_actor_label(actor_path: str, label: str) -> dict:
    """Rename an actor's display label."""
    actor = lookup.require_actor(actor_path)
    actor.set_actor_label(label)
    lookup.invalidate()  # label is part of the index key
    return {"actor_path": actor.get_path_name(), "label": label}


def set_actor_tags(actor_path: str, tags: List[str]) -> dict:
    """Replace an actor's gameplay tags."""
    actor = lookup.require_actor(actor_path)
    actor.set_editor_property("tags", [unreal.Name(t) for t in tags])
    lookup.invalidate()
    return {"actor_path": actor.get_path_name(), "tags": tags}


def get_actors_in_radius(center: List[float], radius: float, class_filter: str = "") -> dict:
    """List actors within ``radius`` of a world-space ``center`` point."""
    if not center or len(center) < 3:
        raise ValueError("center must be [x, y, z]")
    cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
    r2 = float(radius) * float(radius)
    out = []
    for a in lookup.actor_list():
        if class_filter and a.get_class().get_name() != class_filter:
            continue
        loc = a.get_actor_location()
        d2 = (loc.x - cx) ** 2 + (loc.y - cy) ** 2 + (loc.z - cz) ** 2
        if d2 <= r2:
            out.append(serialize_actor(a, ["label", "class", "path", "location"]))
    return {"center": {"x": cx, "y": cy, "z": cz}, "radius": float(radius), "actors": out, "count": len(out)}


def _parent_socket_names(parent, cap: int = 40) -> list:
    """Socket + bone names across the parent's scene components (for error hints)."""
    names: list = []
    try:
        for comp in parent.get_components_by_class(unreal.SceneComponent):
            try:
                for n in comp.get_all_socket_names():
                    names.append(str(n))
                    if len(names) >= cap:
                        return names
            except Exception:
                continue
    except Exception:
        pass
    return names


def attach_actor(child_path: str, parent_path: str, socket: str = "", rule: str = "keep_world") -> dict:
    """Attach one actor to another, optionally at a socket/bone.

    rule = "keep_world" (child stays put, then follows the parent) or
    "snap_to_target" (child jumps onto the socket/bone — use this to put a prop
    on a skeleton socket). Scale always keeps world; snapping it distorts the prop.
    """
    child = lookup.require_actor(child_path)
    parent = lookup.require_actor(parent_path)
    rule_l = (rule or "keep_world").lower()
    if rule_l not in ("keep_world", "snap_to_target"):
        raise ValueError("rule must be keep_world or snap_to_target")

    # A bad socket name silently attaches to the root — confusing. Fail loud with hints.
    if socket:
        found = False
        try:
            for comp in parent.get_components_by_class(unreal.SceneComponent):
                if comp.does_socket_exist(unreal.Name(socket)):
                    found = True
                    break
        except Exception:
            found = True  # can't verify — let the engine decide rather than block
        if not found:
            return {
                "success": False,
                "error": f"Socket/bone not found on parent: {socket}",
                "parent_sockets": _parent_socket_names(parent),
            }

    move = unreal.AttachmentRule.SNAP_TO_TARGET if rule_l == "snap_to_target" else unreal.AttachmentRule.KEEP_WORLD
    child.attach_to_actor(
        parent,
        unreal.Name(socket) if socket else unreal.Name(""),
        move,
        move,
        unreal.AttachmentRule.KEEP_WORLD,
        False,
    )
    lookup.invalidate()
    return {
        "child": child.get_path_name(),
        "parent": parent.get_path_name(),
        "socket": socket,
        "rule": rule_l,
    }


register("spawn_actor_batch")(spawn_actor_batch)
register("duplicate_actor")(duplicate_actor)
register("set_actor_folder")(set_actor_folder)
register("set_actor_label")(set_actor_label)
register("set_actor_tags")(set_actor_tags)
register("get_actors_in_radius")(get_actors_in_radius)
register("attach_actor")(attach_actor)
