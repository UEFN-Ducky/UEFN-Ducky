"""Actor and selection commands."""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import unreal

from listener import lookup, paging
from listener.device_editor import _device_kind
from listener.device_scale import refuse_if_creative_device_scale
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
    """A page of level actors (default :data:`paging.DEFAULT_LIMIT`): filters read only the
    class name / label of each actor, and only the page's rows are serialized."""
    needle = (label_filter or "").lower()

    def _keep(a) -> bool:
        if class_filter or class_prefix:
            cls = a.get_class().get_name()
            if class_filter and cls != class_filter:
                return False
            if class_prefix and not cls.startswith(class_prefix):
                return False
        if needle:
            return needle in (a.get_actor_label() or "").lower() or needle in (a.get_name() or "").lower()
        return True

    field_list = [f for f in fields if f in ALL_ACTOR_FIELDS] if fields else None
    paged = paging.page(
        lookup.actor_list(),
        lambda a: serialize_actor(a, field_list),
        offset=offset,
        limit=limit,
        keep=_keep if (class_filter or class_prefix or needle) else None,
    )
    return paging.reply("actors", paged)


@register("get_selected_actors")
def cmd_get_selected_actors(offset: int = 0, limit: Optional[int] = None) -> dict:
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = actor_sub.get_selected_level_actors() or []
    return paging.reply("actors", paging.page(actors, serialize_actor, offset=offset, limit=limit))


def _load_placeable(asset_path: str):
    """Load the Content Drawer placeable (Actor Blueprint ``_C``) when it exists.

    Naked StaticMesh spawn creates FortStaticMeshActor — that cook-fails on BR
    BakeData foliage. Verse device classes still load via ``load_object``.
    """
    from listener.island_placeable import resolve_content_drawer_placeable

    return resolve_content_drawer_placeable(asset_path)


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
    select: bool = False,
    label: str = "",
    folder: str = "",
    tags: Optional[List[str]] = None,
) -> dict:
    """Place an actor. Optional label/folder/tags apply in the same tick/transaction.

    Prefer passing label+folder here instead of separate set_actor_label /
    set_actor_folder calls (3 round-trips → 1). Fortnite catalog: pass the
    Content Drawer Actor Blueprint ``_C`` — not a BakeData StaticMesh.
    The person's selection is left alone unless ``select`` is true: they may be
    working in the same editor while an agent places things.
    """
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

    # Same-tick organize-as-you-place (avoids 2–3 extra heavy commands).
    _organize(actor, label, folder, tags)

    # The level changed; drop the per-tick index so later lookups see this actor.
    lookup.invalidate()
    # Only on request: selecting would replace whatever the person had selected.
    if select:
        try:
            actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
            actor_sub.set_selected_level_actors([actor])
        except Exception:
            pass
    # Include the guid: change tracking uses it to identify what this run created.
    return {"actor": serialize_actor(actor, ALL_ACTOR_FIELDS)}


def _organize(actor, label: str = "", folder: str = "", tags: Optional[List[str]] = None) -> None:
    """Label, Outliner folder and tags on a freshly placed actor; each one is best effort."""
    if label:
        try:
            actor.set_actor_label(str(label))
        except Exception:
            pass
    if folder:
        try:
            actor.set_folder_path(unreal.Name(str(folder)))
        except Exception:
            pass
    if tags:
        try:
            actor.set_editor_property("tags", [unreal.Name(str(t)) for t in tags])
        except Exception:
            pass


# One spawn_layout call stops after this long so the reply beats the listener's 30 s
# HTTP timeout; the caller passes next_offset back to go on.
_LAYOUT_BUDGET_SEC = 20.0
_LAYOUT_CHUNK = 200


def _layout_entries(path: str) -> List[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("placements", data.get("entries"))
    if not isinstance(data, list):
        raise ValueError("layout must be a list of {cls, loc, rot, scale, label, folder, tags} or {placements: [...]}")
    return data


def _layout_class(cls: str, cache: Dict[str, Any]):
    """The placeable for one layout ``cls``, loaded once per call."""
    if cls not in cache:
        cache[cls] = _load_placeable(cls) if "/" in cls else _resolve_actor_class(cls)
    return cache[cls]


def _actor_tags(actor) -> List[str]:
    try:
        return [str(t) for t in (actor.get_editor_property("tags") or [])]
    except Exception:
        return []


@register("spawn_layout")
def cmd_spawn_layout(
    path: str,
    undo_tag: str = "",
    offset: int = 0,
    chunk: int = _LAYOUT_CHUNK,
    budget_sec: float = _LAYOUT_BUDGET_SEC,
) -> dict:
    """Place a JSON layout ``[{cls, loc, rot, scale, label, folder, tags}]`` in one call.

    ``cls`` is a Content Drawer ``_C`` path (or a native ``unreal`` class name). Entries go in
    chunks of ``chunk``, one editor transaction each, through the same spawn path as
    ``spawn_actor``. Every placed actor gets ``undo_tag`` (default ``layout:<file stem>``) so
    ``remove_layout`` takes the whole layout back out. When ``budget_sec`` runs out the reply
    has ``done: false`` and ``next_offset``: call again with ``offset=next_offset``.
    """
    if not path:
        raise ValueError("path is required (a layout JSON file)")
    entries = _layout_entries(path)
    tag = undo_tag or f"layout:{Path(path).stem}"
    chunk = max(1, int(chunk or _LAYOUT_CHUNK))
    start = time.monotonic()
    cache: Dict[str, Any] = {}
    placed = 0
    errors: List[dict] = []
    i = max(0, int(offset or 0))
    total = len(entries)
    while i < total:
        end = min(i + chunk, total)
        with unreal.ScopedEditorTransaction(f"Ducky spawn layout {tag}"):
            for idx in range(i, end):
                row = entries[idx] if isinstance(entries[idx], dict) else {}
                try:
                    cls = str(row.get("cls") or "").strip()
                    if not cls:
                        raise ValueError("cls is missing")
                    obj = _layout_class(cls, cache)
                    if obj is None:
                        raise ValueError(f"class not found: {cls}")
                    loc = unreal.Vector(*(row.get("loc") or (0, 0, 0)))
                    rot = rotator_pyr(*(row.get("rot") or (0, 0, 0)))
                    actor = _spawn_any(obj, loc, rot)
                    if actor is None:
                        raise RuntimeError("spawn returned nothing")
                    scale = row.get("scale")
                    if scale is not None and list(scale) != [1, 1, 1]:
                        try:
                            refuse_if_creative_device_scale(_device_kind(actor), list(scale))
                            actor.set_actor_scale3d(unreal.Vector(*scale))
                        except Exception as e:
                            errors.append({"index": idx, "error": f"scale skipped: {e}"})
                    tags = [str(t) for t in (row.get("tags") or [])]
                    if tag not in tags:
                        tags.append(tag)
                    _organize(actor, row.get("label") or "", row.get("folder") or "", tags)
                    placed += 1
                except Exception as e:
                    errors.append({"index": idx, "error": str(e)})
        i = end
        if i < total and time.monotonic() - start >= budget_sec:
            break
    lookup.invalidate()
    failed = len({e["index"] for e in errors if not e["error"].startswith("scale skipped")})
    return {
        "placed": placed,
        "failed": failed,
        "errors": errors[:20],
        "undo_tag": tag,
        "total": total,
        "next_offset": i,
        "done": i >= total,
        "seconds": round(time.monotonic() - start, 2),
    }


@register("remove_layout")
def cmd_remove_layout(undo_tag: str, budget_sec: float = _LAYOUT_BUDGET_SEC) -> dict:
    """Delete every level actor carrying ``undo_tag`` (what ``spawn_layout`` placed).

    ``done: false`` means the time budget ran out first: call again for the rest.
    """
    if not undo_tag:
        raise ValueError("undo_tag is required")
    start = time.monotonic()
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    targets = [a for a in lookup.actor_list() if is_live(a) and undo_tag in _actor_tags(a)]
    removed = 0
    with unreal.ScopedEditorTransaction(f"Ducky remove layout {undo_tag}"):
        for actor in targets:
            if time.monotonic() - start >= budget_sec:
                break
            actor_sub.destroy_actor(actor)
            removed += 1
    lookup.invalidate()
    return {"removed": removed, "remaining": len(targets) - removed, "done": removed == len(targets), "undo_tag": undo_tag}


@register("delete_actors")
def cmd_delete_actors(actor_paths: List[str]) -> dict:
    paths = [p for p in (actor_paths or []) if str(p).strip()]
    if not paths:
        raise ValueError("actor_paths is required")
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    removed: List[str] = []
    missing: List[str] = []
    with unreal.ScopedEditorTransaction("Ducky delete actors"):
        for ident in paths:
            actor = lookup.find_actor(ident)
            if actor is None or not is_live(actor):
                missing.append(ident)
                continue
            label = ""
            try:
                label = actor.get_actor_label()
            except Exception:
                pass
            actor_sub.destroy_actor(actor)
            removed.append(label or ident)
    lookup.invalidate()
    if not removed:
        raise ValueError(f"actor not found: {missing[0] if missing else paths[0]}")
    return {"success": True, "removed": removed, "missing": missing}


@register("set_actor_transform")
def cmd_set_actor_transform(
    actor_path: str,
    location: Optional[List[float]] = None,
    rotation: Optional[List[float]] = None,
    scale: Optional[List[float]] = None,
) -> dict:
    target = lookup.require_actor(actor_path)
    refuse_if_creative_device_scale(_device_kind(target), scale)

    # modify() inside a transaction marks the actor's package dirty: without it the move
    # is never saved (nor locked and checked in, nor undoable with Ctrl+Z).
    with unreal.ScopedEditorTransaction("MCP Set Actor Transform"):
        target.modify()
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
            try:
                set_results[prop] = serialize(target.get_editor_property(prop))
            except Exception:
                set_results[prop] = value
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
