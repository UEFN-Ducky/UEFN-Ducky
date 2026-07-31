"""Level-design tools: spatial awareness (bounds, gaps, free space, ground) and precise placement."""

from __future__ import annotations

from typing import Optional

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("leveldesign")
def get_actor_bounds(actor_path: str, pretty: bool = False) -> str:
    """World-space AABB of an actor: origin, extent, min/max corners, size."""
    return tool_json(send_command("get_actor_bounds", {"actor_path": actor_path}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def get_actor_bone_transform(actor_path: str, socket_or_bone: str, pretty: bool = False) -> str:
    """World-space transform of a bone or socket on a placed actor (position props, verify sockets)."""
    return tool_json(send_command("get_actor_bone_transform", {"actor_path": actor_path, "socket_or_bone": socket_or_bone}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def get_level_bounds(class_filter: str = "", pretty: bool = False) -> str:
    """Union AABB of all level actors (optionally one class) — the playable footprint."""
    return tool_json(send_command("get_level_bounds", {"class_filter": class_filter}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def measure_distance(actor_a: str, actor_b: str, pretty: bool = False) -> str:
    """Distances between two actors: centers, per-axis deltas, and the surface GAP between their bounds."""
    return tool_json(send_command("measure_distance", {"actor_a": actor_a, "actor_b": actor_b}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def check_area_clear(
    center: list[float],
    extent: list[float],
    ignore_actors: Optional[list[str]] = None,
    class_filter: str = "",
    pretty: bool = False,
) -> str:
    """Is the box (center ± extent) free of actors? Lists what overlaps it. Run BEFORE spawning into a spot."""
    return tool_json(
        send_command(
            "check_area_clear",
            {"center": center, "extent": extent, "ignore_actors": ignore_actors, "class_filter": class_filter},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("leveldesign")
def find_clear_area(
    near: list[float],
    extent: list[float],
    search_radius: float = 2048.0,
    step: float = 256.0,
    max_results: int = 5,
    pretty: bool = False,
) -> str:
    """Scan outward from a point for spots where a box of the given extent fits — nearest first."""
    return tool_json(
        send_command(
            "find_clear_area",
            {"near": near, "extent": extent, "search_radius": search_radius, "step": step, "max_results": max_results},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("leveldesign")
def get_ground_z(x: float, y: float, from_z: float = 100000.0, pretty: bool = False) -> str:
    """Ground height at (x, y): line trace down, bounds-top fallback when tracing isn't exposed."""
    return tool_json(send_command("get_ground_z", {"x": x, "y": y, "from_z": from_z}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def snap_actor_to_ground(actor_path: str, offset_z: float = 0.0, pretty: bool = False) -> str:
    """Drop an actor so the bottom of its bounds sits on the ground (+ optional offset)."""
    return tool_json(send_command("snap_actor_to_ground", {"actor_path": actor_path, "offset_z": offset_z}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def snap_actor_to_grid(actor_path: str, grid_size: float = 128.0, axes: str = "xyz", pretty: bool = False) -> str:
    """Round an actor's location to the nearest grid multiple on the given axes."""
    return tool_json(
        send_command("snap_actor_to_grid", {"actor_path": actor_path, "grid_size": grid_size, "axes": axes}),
        pretty=pretty,
    )


@plugin_mcp_tool("leveldesign")
def align_actors(
    actor_paths: list[str],
    axis: str = "x",
    mode: str = "center",
    target: Optional[float] = None,
    pretty: bool = False,
) -> str:
    """Align actors on one axis by their bounds min/center/max (target = first actor unless given)."""
    return tool_json(
        send_command("align_actors", {"actor_paths": actor_paths, "axis": axis, "mode": mode, "target": target}),
        pretty=pretty,
    )


@plugin_mcp_tool("leveldesign")
def distribute_actors(
    actor_paths: list[str],
    axis: str = "x",
    spacing: Optional[float] = None,
    pretty: bool = False,
) -> str:
    """Space actors evenly on one axis (first/last anchor), or at a fixed center-to-center spacing."""
    return tool_json(
        send_command("distribute_actors", {"actor_paths": actor_paths, "axis": axis, "spacing": spacing}),
        pretty=pretty,
    )
