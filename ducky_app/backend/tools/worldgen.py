"""Worldgen tools: capability-guarded terrain generation + instanced foliage scatter."""

from __future__ import annotations

from typing import Optional

from backend.bridge import send_command
from backend.json_util import tool_json
from backend.tools.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("leveldesign")
def worldgen_capabilities(pretty: bool = False) -> str:
    """Probe terrain/foliage backends (mesh vs landscape, foliage vs HISM). Run first."""
    return tool_json(send_command("worldgen_capabilities", {}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def terrain_generate(
    location: Optional[list[float]] = None,
    size_uu: float = 12800.0,
    resolution: int = 65,
    amplitude: float = 800.0,
    noise_scale: float = 3500.0,
    seed: int = 42,
    stamps: Optional[list[dict]] = None,
    asset_name: str = "",
    asset_folder: str = "",
    level_folder: str = "",
    replace_existing: bool = True,
    pretty: bool = False,
) -> str:
    """Generate one deterministic heightfield terrain mesh actor (not hundreds of cubes).

    stamps: optional list of {type:hill|valley|flatten, x,y,radius,strength,height}.
    """
    params = {
        "location": location,
        "size_uu": size_uu,
        "resolution": resolution,
        "amplitude": amplitude,
        "noise_scale": noise_scale,
        "seed": seed,
        "stamps": stamps,
        "asset_name": asset_name,
        "asset_folder": asset_folder,
        "level_folder": level_folder,
        "replace_existing": replace_existing,
    }
    return tool_json(send_command("terrain_generate", params, timeout=120.0), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def terrain_get_info(actor_path: str = "", pretty: bool = False) -> str:
    """Inspect a generated terrain actor, or list tagged worldgen terrains when actor_path is empty."""
    return tool_json(send_command("terrain_get_info", {"actor_path": actor_path}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def terrain_remove_generated(
    folder_prefix: str = "",
    asset_name_prefix: str = "",
    pretty: bool = False,
) -> str:
    """Delete tagged worldgen terrain actors and matching generated mesh assets."""
    return tool_json(
        send_command(
            "terrain_remove_generated",
            {"folder_prefix": folder_prefix, "asset_name_prefix": asset_name_prefix},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("leveldesign")
def foliage_list_sources(search: str = "", folder: str = "", limit: int = 40, pretty: bool = False) -> str:
    """Discover StaticMesh / FoliageType assets usable as foliage sources."""
    return tool_json(
        send_command("foliage_list_sources", {"search": search, "folder": folder, "limit": limit}),
        pretty=pretty,
    )


@plugin_mcp_tool("leveldesign")
def foliage_scatter(
    center: Optional[list[float]] = None,
    extent: Optional[list[float]] = None,
    sources: Optional[list[str]] = None,
    density_per_100m2: float = 8.0,
    min_distance: float = 350.0,
    seed: int = 42,
    max_instances: int = 400,
    scale_min: float = 0.85,
    scale_max: float = 1.25,
    max_slope_degrees: float = 35.0,
    level_folder: str = "",
    clear_first: bool = True,
    placement_mode: str = "auto",
    pretty: bool = False,
) -> str:
    """Scatter foliage over a footprint. auto/actors = visible tagged meshes; hism = HISM containers."""
    params = {
        "center": center,
        "extent": extent,
        "sources": sources,
        "density_per_100m2": density_per_100m2,
        "min_distance": min_distance,
        "seed": seed,
        "max_instances": max_instances,
        "scale_min": scale_min,
        "scale_max": scale_max,
        "max_slope_degrees": max_slope_degrees,
        "level_folder": level_folder,
        "clear_first": clear_first,
        "placement_mode": placement_mode,
    }
    return tool_json(send_command("foliage_scatter", params, timeout=180.0), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def foliage_get_stats(folder_prefix: str = "", pretty: bool = False) -> str:
    """Summarize tagged worldgen foliage / HISM actors."""
    return tool_json(send_command("foliage_get_stats", {"folder_prefix": folder_prefix}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def foliage_clear_generated(
    folder_prefix: str = "",
    foliage_type_paths: Optional[list[str]] = None,
    pretty: bool = False,
) -> str:
    """Clear generated foliage instances and HISM container actors (regenerate-safe)."""
    return tool_json(
        send_command(
            "foliage_clear_generated",
            {"folder_prefix": folder_prefix, "foliage_type_paths": foliage_type_paths},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("leveldesign")
def landscape_list(pretty: bool = False) -> str:
    """List Landscape roots and whether their streaming-proxy panels exist."""
    return tool_json(send_command("landscape_list", {}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def landscape_get_info(label: str = "", actor_path: str = "", pretty: bool = False) -> str:
    """Inspect one Landscape (panels are on LandscapeStreamingProxy actors)."""
    return tool_json(
        send_command("landscape_get_info", {"label": label, "actor_path": actor_path}),
        pretty=pretty,
    )


@plugin_mcp_tool("leveldesign")
def landscape_create(
    label: str = "level1 landscape",
    location: Optional[list[float]] = None,
    component_count_xy: int = 8,
    section_size: int = 63,
    scale: float = 100.0,
    pretty: bool = False,
) -> str:
    """Probe/attempt real Landscape create. On UEFN this reports unavailable + UI steps; use terrain_generate for AI create."""
    return tool_json(
        send_command(
            "landscape_create",
            {
                "label": label,
                "location": location,
                "component_count_xy": component_count_xy,
                "section_size": section_size,
                "scale": scale,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("leveldesign")
def landscape_rename(
    new_label: str,
    label: str = "",
    actor_path: str = "",
    pretty: bool = False,
) -> str:
    """Rename a Landscape root (e.g. after clicking Landscape Mode Create)."""
    return tool_json(
        send_command(
            "landscape_rename",
            {"new_label": new_label, "label": label, "actor_path": actor_path},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("leveldesign")
def landscape_sculpt(
    label: str = "",
    actor_path: str = "",
    mode: str = "add",
    stamps: Optional[list[dict]] = None,
    amplitude: float = 0.25,
    seed: int = 7,
    hill: Optional[dict] = None,
    confirm_overwrite: bool = False,
    pretty: bool = False,
) -> str:
    """Sculpt an existing Landscape (has panels) via lossless heightmap RT round-trip.

    mode='verify' re-imports the current shape unchanged (safe lossless proof).
    mode='add' composites soft stamps onto real height (non-destructive/localized).
    mode='set' overwrites the whole heightmap. confirm_overwrite=True required.
    stamps: list of {type: hill|valley|flatten, cx, cy, radius, strength} in 0..1 units.
    """
    return tool_json(
        send_command(
            "landscape_sculpt",
            {
                "label": label,
                "actor_path": actor_path,
                "mode": mode,
                "stamps": stamps,
                "amplitude": amplitude,
                "seed": seed,
                "hill": hill,
                "confirm_overwrite": confirm_overwrite,
            },
            timeout=120.0,
        ),
        pretty=pretty,
    )
