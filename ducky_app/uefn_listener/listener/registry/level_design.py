"""Level-design registry tools: spatial awareness and precise placement.

Composable primitives — NOT one do-it-all tool. They give the agent the spatial
facts (sizes, gaps, free space, ground height) it needs to place things
CORRECTLY instead of guessing coordinates:

  READ    get_actor_bounds, get_level_bounds, measure_distance,
          check_area_clear, find_clear_area, get_ground_z
  CHANGE  snap_actor_to_ground, snap_actor_to_grid, align_actors,
          distribute_actors

All overlap math is axis-aligned bounding boxes (AABB) from
``actor.get_actor_bounds`` — editor-safe, no physics scene needed. Ground
queries try a line trace first and fall back to AABB tops when the trace API
isn't exposed (result reports which ``method`` answered). Spawning stays with
the generic ``spawn_actor`` — one call per actor, then snap/align/verify.
"""

from __future__ import annotations

from typing import List, Optional

import unreal

from listener import lookup
from listener.dispatch import register
from listener.serialize import is_live

_MAX_GROUP = 50          # actors per align/distribute call
_MAX_OVERLAPS = 20       # overlapping actors reported by check_area_clear
_MAX_CANDIDATES = 500    # grid cells scanned by find_clear_area
_MAX_RESULTS = 10        # spots returned by find_clear_area
_TRACE_DROP = 1_000_000.0


def _vec(v: Optional[List[float]], name: str) -> unreal.Vector:
    if not v or len(v) < 3:
        raise ValueError(f"{name} must be [x, y, z]")
    return unreal.Vector(float(v[0]), float(v[1]), float(v[2]))


def _bounds(actor):
    """(origin, extent) world-space AABB — tolerant of get_actor_bounds arity differences."""
    try:
        origin, extent = actor.get_actor_bounds(False, True)
    except TypeError:
        origin, extent = actor.get_actor_bounds(False)
    return origin, extent


def _aabb_overlaps(ca, ea, cb, eb) -> bool:
    return (
        abs(ca.x - cb.x) <= (ea.x + eb.x)
        and abs(ca.y - cb.y) <= (ea.y + eb.y)
        and abs(ca.z - cb.z) <= (ea.z + eb.z)
    )


def _editor_world():
    try:
        subsys = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        if subsys is not None:
            return subsys.get_editor_world()
    except Exception:
        pass
    try:
        return unreal.EditorLevelLibrary.get_editor_world()
    except Exception:
        return None


def _trace_ground(x: float, y: float, from_z: float, ignore: list) -> Optional[dict]:
    """Line-trace straight down; None when the trace API is unavailable or nothing hit."""
    world = _editor_world()
    if world is None:
        return None
    try:
        hit = unreal.SystemLibrary.line_trace_single(
            world,
            unreal.Vector(x, y, from_z),
            unreal.Vector(x, y, from_z - _TRACE_DROP),
            unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
            False,
            ignore or [],
            unreal.DrawDebugTrace.NONE,
            True,
        )
    except Exception:
        return None
    if not hit:
        return None
    for attr in ("impact_point", "location"):
        try:
            point = hit.get_editor_property(attr)
        except Exception:
            continue
        if point is not None:
            return {"z": float(point.z), "method": "trace"}
    return None


def _aabb_ground(x: float, y: float, from_z: float, ignore_paths: set) -> Optional[dict]:
    """Fallback ground: highest actor-bounds top under (x, y) at or below from_z."""
    best = None
    best_label = ""
    for a in lookup.actor_list():
        if a.get_path_name() in ignore_paths:
            continue
        try:
            origin, extent = _bounds(a)
        except Exception:
            continue
        if extent.x < 0.5 and extent.y < 0.5 and extent.z < 0.5:
            continue
        if abs(origin.x - x) > extent.x or abs(origin.y - y) > extent.y:
            continue
        top = origin.z + extent.z
        if top <= from_z and (best is None or top > best):
            best = top
            best_label = a.get_actor_label()
    if best is None:
        return None
    return {"z": float(best), "method": "aabb_fallback", "actor": best_label}


def get_actor_bounds(actor_path: str) -> dict:
    """World-space AABB of an actor: origin, extent, min/max corners, size."""
    actor = lookup.require_actor(actor_path)
    origin, extent = _bounds(actor)
    return {
        "actor_path": actor.get_path_name(),
        "label": actor.get_actor_label(),
        "origin": [origin.x, origin.y, origin.z],
        "extent": [extent.x, extent.y, extent.z],
        "min": [origin.x - extent.x, origin.y - extent.y, origin.z - extent.z],
        "max": [origin.x + extent.x, origin.y + extent.y, origin.z + extent.z],
        "size": [extent.x * 2, extent.y * 2, extent.z * 2],
    }


def get_level_bounds(class_filter: str = "") -> dict:
    """Union AABB of all level actors (optionally one class) — the playable footprint."""
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    count = 0
    for a in lookup.actor_list():
        if class_filter and a.get_class().get_name() != class_filter:
            continue
        try:
            origin, extent = _bounds(a)
        except Exception:
            continue
        if extent.x < 0.5 and extent.y < 0.5 and extent.z < 0.5:
            continue
        for i, (o, e) in enumerate(((origin.x, extent.x), (origin.y, extent.y), (origin.z, extent.z))):
            lo[i] = min(lo[i], o - e)
            hi[i] = max(hi[i], o + e)
        count += 1
    if count == 0:
        raise ValueError("No actors with bounds matched")
    return {
        "min": lo,
        "max": hi,
        "center": [(lo[i] + hi[i]) / 2 for i in range(3)],
        "size": [hi[i] - lo[i] for i in range(3)],
        "actor_count": count,
        "class_filter": class_filter,
    }


def measure_distance(actor_a: str, actor_b: str) -> dict:
    """Distances between two actors: centers, per-axis deltas, and the surface GAP between their AABBs."""
    a = lookup.require_actor(actor_a)
    b = lookup.require_actor(actor_b)
    ca, ea = _bounds(a)
    cb, eb = _bounds(b)
    delta = [cb.x - ca.x, cb.y - ca.y, cb.z - ca.z]
    gaps = [
        max(0.0, abs(delta[0]) - (ea.x + eb.x)),
        max(0.0, abs(delta[1]) - (ea.y + eb.y)),
        max(0.0, abs(delta[2]) - (ea.z + eb.z)),
    ]
    return {
        "actor_a": a.get_actor_label(),
        "actor_b": b.get_actor_label(),
        "center_distance": float(sum(d * d for d in delta) ** 0.5),
        "delta": delta,
        "gap_per_axis": gaps,
        "surface_gap": float(sum(g * g for g in gaps) ** 0.5),
        "overlapping": _aabb_overlaps(ca, ea, cb, eb),
    }


def check_area_clear(
    center: List[float],
    extent: List[float],
    ignore_actors: Optional[List[str]] = None,
    class_filter: str = "",
) -> dict:
    """Is the AABB (center ± extent) free of actors? Lists what overlaps it."""
    c = _vec(center, "center")
    e = _vec(extent, "extent")
    ignore = set()
    for path in ignore_actors or []:
        try:
            ignore.add(lookup.require_actor(path).get_path_name())
        except Exception:
            continue
    overlaps = []
    for a in lookup.actor_list():
        if a.get_path_name() in ignore:
            continue
        if class_filter and a.get_class().get_name() != class_filter:
            continue
        try:
            origin, ext = _bounds(a)
        except Exception:
            continue
        if ext.x < 0.5 and ext.y < 0.5 and ext.z < 0.5:
            continue
        if _aabb_overlaps(c, e, origin, ext):
            overlaps.append({"label": a.get_actor_label(), "class": a.get_class().get_name()})
            if len(overlaps) >= _MAX_OVERLAPS:
                break
    return {
        "center": [c.x, c.y, c.z],
        "extent": [e.x, e.y, e.z],
        "clear": not overlaps,
        "overlapping_actors": overlaps,
        "overlap_count": len(overlaps),
        "truncated": len(overlaps) >= _MAX_OVERLAPS,
    }


def find_clear_area(
    near: List[float],
    extent: List[float],
    search_radius: float = 2048.0,
    step: float = 256.0,
    max_results: int = 5,
) -> dict:
    """Scan outward from ``near`` for spots where an AABB of ``extent`` fits — nearest first."""
    n = _vec(near, "near")
    e = _vec(extent, "extent")
    search_radius = min(max(float(search_radius), 0.0), 20000.0)
    step = max(float(step), 64.0)
    max_results = max(1, min(int(max_results), _MAX_RESULTS))
    # Cache obstacle bounds once; only actors near enough to ever overlap a candidate.
    reach = search_radius + max(e.x, e.y) * 2
    obstacles = []
    for a in lookup.actor_list():
        try:
            origin, ext = _bounds(a)
        except Exception:
            continue
        if ext.x < 0.5 and ext.y < 0.5 and ext.z < 0.5:
            continue
        if abs(origin.x - n.x) > reach + ext.x or abs(origin.y - n.y) > reach + ext.y:
            continue
        obstacles.append((origin, ext))
    spots = []
    checked = 0
    ring = 0
    while ring * step <= search_radius and checked < _MAX_CANDIDATES and len(spots) < max_results:
        if ring == 0:
            cells = [(0, 0)]
        else:
            r = ring
            cells = (
                [(i, -r) for i in range(-r, r + 1)]
                + [(i, r) for i in range(-r, r + 1)]
                + [(-r, j) for j in range(-r + 1, r)]
                + [(r, j) for j in range(-r + 1, r)]
            )
        for i, j in cells:
            if checked >= _MAX_CANDIDATES or len(spots) >= max_results:
                break
            checked += 1
            c = unreal.Vector(n.x + i * step, n.y + j * step, n.z)
            if any(_aabb_overlaps(c, e, o, x) for o, x in obstacles):
                continue
            dist = ((c.x - n.x) ** 2 + (c.y - n.y) ** 2) ** 0.5
            spots.append({"location": [c.x, c.y, c.z], "distance": float(dist)})
        ring += 1
    return {
        "near": [n.x, n.y, n.z],
        "extent": [e.x, e.y, e.z],
        "spots": spots,
        "count": len(spots),
        "cells_checked": checked,
        "obstacles_considered": len(obstacles),
    }


def get_ground_z(x: float, y: float, from_z: float = 100000.0) -> dict:
    """Ground height at (x, y): line trace down, AABB-top fallback when tracing isn't exposed."""
    result = _trace_ground(float(x), float(y), float(from_z), [])
    if result is None:
        result = _aabb_ground(float(x), float(y), float(from_z), set())
    if result is None:
        raise ValueError(
            f"No ground found under ({x}, {y}) below z={from_z} — trace API unavailable and no actor bounds beneath"
        )
    result.update({"x": float(x), "y": float(y)})
    return result


def snap_actor_to_ground(actor_path: str, offset_z: float = 0.0) -> dict:
    """Drop an actor so the BOTTOM of its bounds sits on the ground (+ optional offset)."""
    actor = lookup.require_actor(actor_path)
    origin, extent = _bounds(actor)
    top = origin.z + extent.z
    ground = _trace_ground(origin.x, origin.y, top + 10.0, [actor])
    if ground is None:
        ground = _aabb_ground(origin.x, origin.y, top + 10.0, {actor.get_path_name()})
    if ground is None:
        raise ValueError(f"No ground found under {actor.get_actor_label()}")
    bottom = origin.z - extent.z
    dz = (ground["z"] + float(offset_z)) - bottom
    loc = actor.get_actor_location()
    actor.set_actor_location(unreal.Vector(loc.x, loc.y, loc.z + dz), False, False)
    lookup.invalidate()
    return {
        "actor_path": actor.get_path_name(),
        "moved_z": float(dz),
        "ground_z": ground["z"],
        "method": ground["method"],
    }


def snap_actor_to_grid(actor_path: str, grid_size: float = 128.0, axes: str = "xyz") -> dict:
    """Round an actor's location to the nearest grid multiple on the given axes."""
    grid = float(grid_size)
    if grid <= 0:
        raise ValueError("grid_size must be > 0")
    actor = lookup.require_actor(actor_path)
    loc = actor.get_actor_location()
    coords = {"x": loc.x, "y": loc.y, "z": loc.z}
    for axis in axes.lower():
        if axis in coords:
            coords[axis] = round(coords[axis] / grid) * grid
    actor.set_actor_location(unreal.Vector(coords["x"], coords["y"], coords["z"]), False, False)
    lookup.invalidate()
    return {
        "actor_path": actor.get_path_name(),
        "location": [coords["x"], coords["y"], coords["z"]],
        "grid_size": grid,
        "axes": axes,
    }


def _group(actor_paths: Optional[List[str]]) -> list:
    if not actor_paths or len(actor_paths) < 2:
        raise ValueError("Need at least 2 actor paths")
    if len(actor_paths) > _MAX_GROUP:
        raise ValueError(f"Too many actors (max {_MAX_GROUP} per call)")
    actors = [lookup.require_actor(p) for p in actor_paths]
    # Fail loud instead of dereferencing a destroyed ref (uncatchable native crash).
    dead = [p for p, a in zip(actor_paths, actors) if not is_live(a)]
    if dead:
        raise ValueError(f"Actor(s) no longer valid: {dead}")
    return actors


def align_actors(actor_paths: List[str], axis: str = "x", mode: str = "center", target: Optional[float] = None) -> dict:
    """Align actors on one axis by their bounds min/center/max (target = first actor unless given)."""
    axis = axis.lower()
    if axis not in ("x", "y", "z"):
        raise ValueError("axis must be x, y, or z")
    if mode not in ("min", "center", "max"):
        raise ValueError("mode must be min, center, or max")
    actors = _group(actor_paths)

    def feature(actor) -> float:
        origin, extent = _bounds(actor)
        o = getattr(origin, axis)
        e = getattr(extent, axis)
        return o - e if mode == "min" else o + e if mode == "max" else o

    value = float(target) if target is not None else feature(actors[0])
    moved = []
    for actor in actors:
        delta = value - feature(actor)
        loc = actor.get_actor_location()
        coords = [loc.x, loc.y, loc.z]
        coords["xyz".index(axis)] += delta
        actor.set_actor_location(unreal.Vector(*coords), False, False)
        moved.append({"label": actor.get_actor_label(), "delta": float(delta)})
    lookup.invalidate()
    return {"axis": axis, "mode": mode, "value": value, "moved": moved, "count": len(moved)}


def distribute_actors(actor_paths: List[str], axis: str = "x", spacing: Optional[float] = None) -> dict:
    """Space actors evenly on one axis (first/last anchor), or at a fixed center-to-center ``spacing``."""
    axis = axis.lower()
    if axis not in ("x", "y", "z"):
        raise ValueError("axis must be x, y, or z")
    actors = _group(actor_paths)
    idx = "xyz".index(axis)
    actors.sort(key=lambda a: getattr(a.get_actor_location(), axis))
    first = getattr(actors[0].get_actor_location(), axis)
    if spacing is not None:
        positions = [first + i * float(spacing) for i in range(len(actors))]
    else:
        if len(actors) < 3:
            raise ValueError("Even distribution needs at least 3 actors (or pass spacing)")
        last = getattr(actors[-1].get_actor_location(), axis)
        gap = (last - first) / (len(actors) - 1)
        positions = [first + i * gap for i in range(len(actors))]
    moved = []
    for actor, pos in zip(actors, positions):
        loc = actor.get_actor_location()
        coords = [loc.x, loc.y, loc.z]
        coords[idx] = pos
        actor.set_actor_location(unreal.Vector(*coords), False, False)
        moved.append({"label": actor.get_actor_label(), axis: float(pos)})
    lookup.invalidate()
    return {"axis": axis, "spacing": spacing, "moved": moved, "count": len(moved)}


def get_actor_bone_transform(actor_path: str, socket_or_bone: str) -> dict:
    """World-space transform of a bone or socket on a PLACED actor.

    Works for skeletal bones, skeleton sockets, and static-mesh sockets — use it to
    position props before attaching, or to verify a socket after add_skeleton_socket.
    (Placed actors only: their components are registered, so transforms are real.)
    """
    actor = lookup.require_actor(actor_path)
    if not is_live(actor):
        raise ValueError(f"Actor no longer valid: {actor_path}")
    name = unreal.Name(socket_or_bone)
    checked = []
    try:
        comps = list(actor.get_components_by_class(unreal.SceneComponent))
    except Exception as e:
        raise ValueError(f"Cannot read components: {e}")
    for comp in comps:
        try:
            if not comp.does_socket_exist(name):
                checked.append(comp.get_name())
                continue
            t = comp.get_socket_transform(name, unreal.RelativeTransformSpace.RTS_WORLD)
            loc, rot, scale = t.translation, t.rotation.rotator(), t.scale3d
            return {
                "ok": True,
                "actor": actor.get_path_name(),
                "socket_or_bone": socket_or_bone,
                "component": comp.get_name(),
                "location": [loc.x, loc.y, loc.z],
                "rotation": [rot.pitch, rot.yaw, rot.roll],
                "scale": [scale.x, scale.y, scale.z],
            }
        except Exception:
            checked.append(comp.get_name())
            continue
    # Not found — list what IS there so the agent self-corrects without probing.
    names: list = []
    for comp in comps:
        try:
            for n in comp.get_all_socket_names():
                names.append(str(n))
                if len(names) >= 40:
                    break
        except Exception:
            continue
        if len(names) >= 40:
            break
    return {
        "ok": False,
        "error": f"No socket/bone named {socket_or_bone!r} on {actor_path}",
        "components_checked": checked,
        "available": names,
    }


register("get_actor_bone_transform")(get_actor_bone_transform)
register("get_actor_bounds")(get_actor_bounds)
register("get_level_bounds")(get_level_bounds)
register("measure_distance")(measure_distance)
register("check_area_clear")(check_area_clear)
register("find_clear_area")(find_clear_area)
register("get_ground_z")(get_ground_z)
register("snap_actor_to_ground")(snap_actor_to_ground)
register("snap_actor_to_grid")(snap_actor_to_grid)
register("align_actors")(align_actors)
register("distribute_actors")(distribute_actors)
