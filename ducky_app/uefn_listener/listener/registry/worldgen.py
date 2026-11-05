"""Worldgen registry tools: capability-guarded terrain + instanced foliage.

Composable primitives (probe → generate → scatter → clear):

  PROBE   worldgen_capabilities
  TERRAIN terrain_generate, terrain_get_info, terrain_remove_generated
  FOLIAGE foliage_list_sources, foliage_scatter, foliage_clear_generated
          (foliage_get_stats lives in worldgen_foliage_stats.py)

Terrain prefers GeometryScript heightfield meshes because UEFN Landscape
height write surfaces are incomplete/unsafe to create from scratch. Foliage auto/actors places Content Drawer Actor Blueprints (``_C``) the same
way drag-drop does. HISM is opt-in. InstancedFoliageActor.add_instances is
avoided — it has crashed this UEFN build.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

import unreal

from listener import lookup
from listener.dispatch import register
from listener.island_placeable import (
    FOLIAGE_SOURCE_FOLDERS,
    is_foliage_bake_mesh,
    resolve_content_drawer_placeable,
)
from listener.project_paths import content_root, pin_project_folder
from listener.serialize import is_live, serialize

_WORLDGEN_TAG = "WorldgenGenerated"
_DEFAULT_FOLDER = "Generated/WorldgenDemo"
_DEFAULT_ASSET_LEAF = "Generated/Worldgen"
_MAX_RESOLUTION = 129  # (res-1) must be even-ish; 129x129 = ~32k tris
_MAX_INSTANCES = 2500
_MAX_TERRAIN_SIZE_UU = 51200.0  # 512 m


# ---------------------------------------------------------------------------
# Pure helpers (deterministic; no unreal dependency beyond typing)
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _hash01(ix: int, iy: int, seed: int) -> float:
    n = (ix * 374761393 + iy * 668265263 + seed * 982451653) & 0x7FFFFFFF
    n = (n ^ (n >> 13)) * 1274126177
    return ((n ^ (n >> 16)) & 0x7FFFFFFF) / 2147483647.0


def _smoothstep(t: float) -> float:
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _value_noise(x: float, y: float, seed: int) -> float:
    x0, y0 = math.floor(x), math.floor(y)
    fx, fy = x - x0, y - y0
    sx, sy = _smoothstep(fx), _smoothstep(fy)
    n00 = _hash01(int(x0), int(y0), seed)
    n10 = _hash01(int(x0) + 1, int(y0), seed)
    n01 = _hash01(int(x0), int(y0) + 1, seed)
    n11 = _hash01(int(x0) + 1, int(y0) + 1, seed)
    nx0 = n00 * (1 - sx) + n10 * sx
    nx1 = n01 * (1 - sx) + n11 * sx
    return nx0 * (1 - sy) + nx1 * sy


def _fbm(x: float, y: float, seed: int, octaves: int = 4) -> float:
    amp, freq, total, norm = 1.0, 1.0, 0.0, 0.0
    for i in range(max(1, octaves)):
        total += amp * _value_noise(x * freq, y * freq, seed + i * 1013)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm if norm else 0.0


def build_heightfield(
    resolution: int,
    size_uu: float,
    amplitude: float,
    noise_scale: float,
    seed: int,
    stamps: Optional[Sequence[dict]] = None,
) -> List[List[float]]:
    """Return resolution×resolution heights (Z uu) centered at origin in XY."""
    res = max(3, int(resolution))
    half = size_uu * 0.5
    step = size_uu / max(1, res - 1)
    heights: List[List[float]] = []
    inv_noise = 1.0 / max(1e-3, float(noise_scale))
    for iy in range(res):
        row: List[float] = []
        y = -half + iy * step
        for ix in range(res):
            x = -half + ix * step
            n = _fbm(x * inv_noise, y * inv_noise, seed)
            h = (n * 2.0 - 1.0) * float(amplitude)
            for stamp in stamps or []:
                h = _apply_stamp(h, x, y, stamp)
            row.append(h)
        heights.append(row)
    return heights


def _apply_stamp(h: float, x: float, y: float, stamp: dict) -> float:
    kind = str(stamp.get("type") or stamp.get("kind") or "hill").lower()
    sx = float(stamp.get("x", 0.0))
    sy = float(stamp.get("y", 0.0))
    radius = max(1.0, float(stamp.get("radius", 1000.0)))
    strength = float(stamp.get("strength", 1.0))
    dist = math.hypot(x - sx, y - sy)
    if dist >= radius:
        return h
    w = _smoothstep(1.0 - dist / radius) * strength
    if kind in ("hill", "raise"):
        amount = float(stamp.get("height", stamp.get("amplitude", 500.0)))
        return h + amount * w
    if kind in ("valley", "lower", "crater"):
        amount = float(stamp.get("height", stamp.get("amplitude", 500.0)))
        return h - amount * w
    if kind in ("flatten", "flat"):
        target = float(stamp.get("height", stamp.get("target_height", 0.0)))
        return h * (1.0 - w) + target * w
    return h


def scatter_points(
    center: Sequence[float],
    extent: Sequence[float],
    density_per_100m2: float,
    min_distance: float,
    seed: int,
    max_instances: int = _MAX_INSTANCES,
) -> List[Tuple[float, float]]:
    """Deterministic min-distance scatter in an AABB footprint (XY)."""
    cx, cy = float(center[0]), float(center[1])
    ex, ey = abs(float(extent[0])), abs(float(extent[1]))
    area_m2 = (ex * 2.0 / 100.0) * (ey * 2.0 / 100.0)
    target = int(max(0, density_per_100m2) * area_m2 / 100.0)
    target = min(int(max_instances), max(0, target))
    if target == 0 or ex <= 0 or ey <= 0:
        return []
    rng = random.Random(int(seed))
    accepted: List[Tuple[float, float]] = []
    min_d2 = float(min_distance) ** 2
    attempts = max(target * 30, target + 1)
    for _ in range(attempts):
        if len(accepted) >= target:
            break
        x = cx + rng.uniform(-ex, ex)
        y = cy + rng.uniform(-ey, ey)
        ok = True
        for ax, ay in accepted:
            dx, dy = x - ax, y - ay
            if dx * dx + dy * dy < min_d2:
                ok = False
                break
        if ok:
            accepted.append((x, y))
    return accepted


# ---------------------------------------------------------------------------
# Capability probe
# ---------------------------------------------------------------------------


def _has(name: str) -> bool:
    return getattr(unreal, name, None) is not None


def _callable_on(obj: Any, name: str) -> bool:
    return callable(getattr(obj, name, None))


def worldgen_capabilities() -> dict:
    """Probe which terrain/foliage backends this UEFN build exposes."""
    classes = {
        "Landscape": _has("Landscape"),
        "LandscapeProxy": _has("LandscapeProxy"),
        "LandscapePlaceholder": _has("LandscapePlaceholder"),
        "LandscapeStreamingProxy": _has("LandscapeStreamingProxy"),
        "LandscapeComponent": _has("LandscapeComponent"),
        "LandscapeEditLayer": _has("LandscapeEditLayer"),
        "InstancedFoliageActor": _has("InstancedFoliageActor"),
        "FoliageType_InstancedStaticMesh": _has("FoliageType_InstancedStaticMesh"),
        "FoliageType_InstancedStaticMeshFactory": _has("FoliageType_InstancedStaticMeshFactory"),
        "HierarchicalInstancedStaticMeshComponent": _has("HierarchicalInstancedStaticMeshComponent"),
        "DynamicMesh": _has("DynamicMesh"),
        "GeometryScript_NewAssetUtils": _has("GeometryScript_NewAssetUtils"),
        "GeometryScript_Primitives": _has("GeometryScript_Primitives"),
        "RenderingLibrary": _has("RenderingLibrary"),
    }
    landscape_cls = getattr(unreal, "Landscape", None)
    ifa_cls = getattr(unreal, "InstancedFoliageActor", None)
    methods = {
        "landscape_import_heightmap_from_render_target": bool(
            landscape_cls and _callable_on(landscape_cls, "landscape_import_heightmap_from_render_target")
        ),
        "instanced_foliage_add_instances": bool(ifa_cls and _callable_on(ifa_cls, "add_instances")),
        "instanced_foliage_remove_all_instances": bool(
            ifa_cls and _callable_on(ifa_cls, "remove_all_instances")
        ),
        "create_new_static_mesh_asset_from_mesh": _has("GeometryScript_NewAssetUtils")
        and _callable_on(getattr(unreal, "GeometryScript_NewAssetUtils", None), "create_new_static_mesh_asset_from_mesh"),
        "append_rectangle_xy": _has("GeometryScript_Primitives")
        and _callable_on(getattr(unreal, "GeometryScript_Primitives", None), "append_rectangle_xy"),
        "hism_add_instances": _has("HierarchicalInstancedStaticMeshComponent")
        and _callable_on(
            getattr(unreal, "HierarchicalInstancedStaticMeshComponent", None), "add_instances"
        ),
    }

    mesh_ok = methods["create_new_static_mesh_asset_from_mesh"] and methods["append_rectangle_xy"]
    foliage_hism = methods["hism_add_instances"]
    terrain_backend = "mesh" if mesh_ok else "unavailable"
    if foliage_hism:
        foliage_backend = "hism"
    else:
        foliage_backend = "static_mesh_actors" if _has("StaticMesh") else "unavailable"

    # Real Landscape *create with panels* is NOT exposed to Python in UEFN.
    # spawn_actor(Landscape) → LandscapePlaceholder (0 LandscapeComponents).
    # Panels live on LandscapeStreamingProxy; only Landscape Mode → Create builds them.
    landscape_create = "unavailable"
    # Import API EXISTS and the data round-trips through export, but a verified full-white
    # import left the terrain flat: it does NOT reshape visible/collision geometry in UEFN.
    landscape_sculpt_existing = "noop_no_visible_change"

    return {
        "classes": classes,
        "methods": methods,
        "terrain_backend": terrain_backend,
        "foliage_backend": foliage_backend,
        "landscape_create": landscape_create,
        "landscape_sculpt_existing": landscape_sculpt_existing,
        "limits": {
            "max_resolution": _MAX_RESOLUTION,
            "max_instances": _MAX_INSTANCES,
            "max_terrain_size_uu": _MAX_TERRAIN_SIZE_UU,
        },
        "notes": [
            "UEFN Python cannot Create a Landscape with panels. spawn→LandscapePlaceholder; panels need Landscape Mode Create.",
            "T3D parent Landscape has ComponentSizeQuads/SubsectionSizeQuads/NumSubsections/GridSize but those props are not Python-editable.",
            "Panels are LandscapeStreamingProxy actors (LandscapeComponents), not on the Landscape root actor.",
            "Use terrain_generate (mesh heightfield) for AI create+sculpt. landscape_sculpt is a VERIFIED no-op on visible terrain (import writes a buffer export can read, but geometry stays flat).",
            "InstancedFoliageActor.add_instances crashes this UEFN build — do not use it.",
            "Regenerate with terrain_remove_generated / foliage_clear_generated before re-scatter.",
        ],
        "landscape_t3d_schema": {
            "ComponentSizeQuads": 63,
            "SubsectionSizeQuads": 63,
            "NumSubsections": 1,
            "GridSize": 126,
            "RelativeScale3D": [100.0, 100.0, 100.0],
            "TargetLayers": ["__LANDSCAPE_VISIBILITY__", "Layer1", "Layer2", "Layer3", "Layer4", "Layer5", "Layer6"],
            "EditLayer": "Layer",
        },
    }


def _require_terrain_backend() -> str:
    caps = worldgen_capabilities()
    backend = caps["terrain_backend"]
    if backend == "unavailable":
        raise ValueError(
            "No safe terrain backend in this UEFN build. "
            f"Capabilities: {caps}"
        )
    return backend


def _require_foliage_backend() -> str:
    caps = worldgen_capabilities()
    backend = caps["foliage_backend"]
    if backend == "unavailable":
        raise ValueError(
            "No foliage placement backend in this UEFN build. "
            f"Capabilities: {caps}"
        )
    return backend


def _place_mesh_actors(
    placeable,
    xforms: List[unreal.Transform],
    level_folder: str,
    label_prefix: str,
) -> tuple[int, list[str]]:
    """Spawn tagged actors the way Content Drawer does (Blueprint/Actor class).

    Never ``spawn_actor_from_object(StaticMesh)`` for Fortnite catalog — that
    creates FortStaticMeshActor with a direct mesh ref and cook-fails on BakeData.
    """
    added = 0
    labels: list[str] = []
    for i, xf in enumerate(xforms):
        try:
            loc = xf.translation
            try:
                rot = xf.rotation.rotator()
            except Exception:
                rot = unreal.Rotator(0, 0, 0)
            actor = _spawn_drawer_actor(placeable, loc, rot)
            if actor is None:
                continue
            try:
                sc = xf.scale3d
                actor.set_actor_scale3d(sc)
            except Exception:
                pass
            label = f"{label_prefix}_{i:03d}"
            try:
                actor.set_actor_label(label)
            except Exception:
                try:
                    label = actor.get_actor_label()
                except Exception:
                    label = ""
            _tag_actor(actor, level_folder)
            added += 1
            if label:
                labels.append(label)
        except Exception:
            break
    return added, labels


def _placeable_name(obj) -> str:
    try:
        return str(obj.get_name())
    except Exception:
        return "Prop"


def _placeable_path(obj) -> str:
    try:
        return str(obj.get_path_name())
    except Exception:
        return _placeable_name(obj)


def _spawn_drawer_actor(placeable, loc, rot):
    if isinstance(placeable, unreal.Class):
        try:
            return unreal.EditorLevelLibrary.spawn_actor_from_class(placeable, loc, rot)
        except Exception:
            return unreal.EditorLevelLibrary.spawn_actor_from_object(placeable, loc, rot)
    return unreal.EditorLevelLibrary.spawn_actor_from_object(placeable, loc, rot)


# ---------------------------------------------------------------------------
# Terrain
# ---------------------------------------------------------------------------


def _content_root() -> str:
    """Active project mount (e.g. /MyProject) — never invent /Game or other islands."""
    root = (content_root() or "").rstrip("/")
    if root:
        return root
    raise RuntimeError(
        "No active UEFN project content_root — open an island before worldgen asset creates"
    )


def _asset_folder(folder: str = "") -> str:
    f = (folder or "").strip()
    if not f:
        base = pin_project_folder(_DEFAULT_ASSET_LEAF, default_leaf=_DEFAULT_ASSET_LEAF)
    else:
        base = pin_project_folder(f, default_leaf=_DEFAULT_ASSET_LEAF)
    unreal.EditorAssetLibrary.make_directory(base)
    return base


def _tag_actor(actor, folder: str) -> None:
    try:
        actor.set_actor_label(actor.get_actor_label())
    except Exception:
        pass
    try:
        actor.tags = [unreal.Name(_WORLDGEN_TAG)]
    except Exception:
        try:
            actor.set_editor_property("tags", [unreal.Name(_WORLDGEN_TAG)])
        except Exception:
            pass
    try:
        actor.set_folder_path(folder or _DEFAULT_FOLDER)
    except Exception:
        pass


def _find_tagged_actors(folder_prefix: str = "") -> List[Any]:
    out = []
    folder_prefix = (folder_prefix or "").strip()
    for actor in lookup.actor_list():
        if not is_live(actor):
            continue
        tagged = False
        try:
            tags = list(actor.tags or [])
            tagged = any(str(t) == _WORLDGEN_TAG for t in tags)
        except Exception:
            tagged = False
        if not tagged:
            try:
                label = actor.get_actor_label() or ""
                tagged = label.startswith("WG_")
            except Exception:
                tagged = False
        if not tagged:
            continue
        if folder_prefix:
            try:
                fp = str(actor.get_folder_path() or "")
                if not fp.startswith(folder_prefix):
                    continue
            except Exception:
                continue
        out.append(actor)
    return out


def terrain_generate(
    location: Optional[List[float]] = None,
    size_uu: float = 12800.0,
    resolution: int = 65,
    amplitude: float = 800.0,
    noise_scale: float = 3500.0,
    seed: int = 42,
    stamps: Optional[List[dict]] = None,
    asset_name: str = "",
    asset_folder: str = "",
    level_folder: str = "",
    replace_existing: bool = True,
) -> dict:
    """Generate a single heightfield terrain mesh actor (deterministic)."""
    backend = _require_terrain_backend()
    size_uu = float(size_uu)
    if size_uu <= 0 or size_uu > _MAX_TERRAIN_SIZE_UU:
        raise ValueError(f"size_uu must be in (0, {_MAX_TERRAIN_SIZE_UU}]")
    resolution = int(resolution)
    if resolution < 3 or resolution > _MAX_RESOLUTION:
        raise ValueError(f"resolution must be in [3, {_MAX_RESOLUTION}]")
    # Prefer odd resolutions for a true center vertex.
    if resolution % 2 == 0:
        resolution += 1

    loc = location or [0.0, 0.0, 0.0]
    if len(loc) != 3:
        raise ValueError("location must be [x,y,z]")
    folder = level_folder or _DEFAULT_FOLDER
    if replace_existing:
        terrain_remove_generated(folder_prefix=folder, asset_name_prefix=asset_name or "SM_WG_Terrain")

    heights = build_heightfield(resolution, size_uu, amplitude, noise_scale, int(seed), stamps)
    mesh_path, actor_path, label = _create_heightfield_mesh_actor(
        heights=heights,
        size_uu=size_uu,
        location=loc,
        seed=int(seed),
        asset_name=asset_name,
        asset_folder=asset_folder,
        level_folder=folder,
    )
    lookup.invalidate()
    return {
        "ok": True,
        "terrain_backend": backend,
        "asset_path": mesh_path,
        "actor_path": actor_path,
        "label": label,
        "seed": int(seed),
        "size_uu": size_uu,
        "resolution": resolution,
        "amplitude": float(amplitude),
        "noise_scale": float(noise_scale),
        "stamp_count": len(stamps or []),
        "folder": folder,
    }


def _create_heightfield_mesh_actor(
    heights: List[List[float]],
    size_uu: float,
    location: Sequence[float],
    seed: int,
    asset_name: str,
    asset_folder: str,
    level_folder: str,
) -> Tuple[str, str, str]:
    dyn_cls = getattr(unreal, "DynamicMesh", None)
    prim = getattr(unreal, "GeometryScript_Primitives", None)
    new_utils = getattr(unreal, "GeometryScript_NewAssetUtils", None)
    if dyn_cls is None or prim is None or new_utils is None:
        raise ValueError("GeometryScript DynamicMesh pipeline unavailable")

    res = len(heights)
    steps = max(1, res - 1)
    dm = dyn_cls()
    opts = unreal.GeometryScriptPrimitiveOptions()
    xf = unreal.Transform()
    # Build a unit-centered grid, then displace Z.
    dm = prim.append_rectangle_xy(
        dm,
        opts,
        xf,
        float(size_uu),
        float(size_uu),
        steps,
        steps,
        None,
    )

    # Map grid vertices to height samples. append_rectangle_xy lays out a regular grid.
    _dm2, position_list, _gaps = dm.get_all_vertex_positions(True)
    count = int(position_list.get_vector_list_length())
    half = size_uu * 0.5
    step = size_uu / max(1, res - 1)

    def _sample_height(x: float, y: float) -> float:
        ix = int(round((x + half) / step))
        iy = int(round((y + half) / step))
        ix = int(_clamp(ix, 0, res - 1))
        iy = int(_clamp(iy, 0, res - 1))
        return float(heights[iy][ix])

    if count == res * res:
        # Direct row-major mapping when vertex count matches the sample grid.
        idx = 0
        for iy in range(res):
            for ix in range(res):
                vec, ok = position_list.get_vector_list_item(idx)
                if not ok:
                    idx += 1
                    continue
                position_list.set_vector_list_item(
                    idx, unreal.Vector(float(vec.x), float(vec.y), float(heights[iy][ix]))
                )
                idx += 1
    else:
        for i in range(count):
            vec, ok = position_list.get_vector_list_item(i)
            if not ok:
                continue
            z = _sample_height(float(vec.x), float(vec.y))
            position_list.set_vector_list_item(i, unreal.Vector(float(vec.x), float(vec.y), z))

    dm.set_all_mesh_vertex_positions(position_list, None)

    # Recompute normals when available.
    try:
        normals = getattr(unreal, "GeometryScript_Normals", None)
        if normals is not None and hasattr(normals, "recompute_normals"):
            normals.recompute_normals(dm, unreal.GeometryScriptCalculateNormalsOptions(), False, None)
    except Exception:
        pass

    folder = _asset_folder(asset_folder)
    name = (asset_name or f"SM_WG_Terrain_{seed}").strip()
    if not name.startswith("SM_"):
        name = f"SM_{name}"
    asset_path = f"{folder}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        unreal.EditorAssetLibrary.delete_asset(asset_path)

    create_opts = unreal.GeometryScriptCreateNewStaticMeshAssetOptions()
    try:
        create_opts.set_editor_property("enable_collision", True)
    except Exception:
        pass
    try:
        create_opts.set_editor_property("enable_recompute_normals", True)
        create_opts.set_editor_property("enable_recompute_tangents", True)
    except Exception:
        pass
    try:
        create_opts.set_editor_property("enable_nanite", False)
    except Exception:
        pass

    mesh, outcome = new_utils.create_new_static_mesh_asset_from_mesh(dm, asset_path, create_opts, None)
    if mesh is None:
        raise RuntimeError(f"create_new_static_mesh_asset_from_mesh failed ({outcome}) for {asset_path}")

    # Ensure complex collision for ground traces.
    try:
        sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        if sub is not None and hasattr(sub, "set_convex_decomposition_collisions"):
            sub.set_convex_decomposition_collisions(mesh, 4, 8, 100000)
    except Exception:
        pass
    try:
        body = mesh.get_editor_property("body_setup")
        if body is not None:
            body.set_editor_property(
                "collision_trace_flag",
                unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE,
            )
    except Exception:
        pass
    unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False)

    loc = unreal.Vector(float(location[0]), float(location[1]), float(location[2]))
    actor = unreal.EditorLevelLibrary.spawn_actor_from_object(mesh, loc)
    if actor is None:
        raise RuntimeError(f"Failed to spawn terrain actor from {asset_path}")
    label = f"WG_Terrain_{seed}"
    try:
        actor.set_actor_label(label)
    except Exception:
        pass
    _tag_actor(actor, level_folder)
    lookup.invalidate()
    return asset_path, actor.get_path_name(), label


def terrain_get_info(actor_path: str = "") -> dict:
    """Inspect a generated terrain actor (or list all tagged terrain actors)."""
    if actor_path:
        actor = lookup.require_actor(actor_path)
        return _terrain_actor_info(actor)
    actors = []
    for a in _find_tagged_actors():
        label = ""
        try:
            label = a.get_actor_label() or ""
        except Exception:
            pass
        if "Terrain" in label or a.get_class().get_name() in ("StaticMeshActor", "FortStaticMeshActor"):
            actors.append(_terrain_actor_info(a))
    return {"terrains": actors, "count": len(actors)}


def _terrain_actor_info(actor) -> dict:
    info = {
        "actor_path": actor.get_path_name(),
        "label": actor.get_actor_label(),
        "class": actor.get_class().get_name(),
        "location": serialize(actor.get_actor_location()),
    }
    try:
        info["folder"] = str(actor.get_folder_path())
    except Exception:
        pass
    try:
        comps = actor.get_components_by_class(unreal.StaticMeshComponent)
        if comps:
            mesh = comps[0].static_mesh
            if mesh is not None:
                info["mesh_path"] = mesh.get_path_name()
    except Exception:
        pass
    try:
        origin, extent = actor.get_actor_bounds(False)
        info["bounds"] = {"origin": serialize(origin), "extent": serialize(extent)}
    except Exception:
        pass
    return info


def terrain_remove_generated(folder_prefix: str = "", asset_name_prefix: str = "") -> dict:
    """Delete tagged worldgen terrain actors (and optional matching mesh assets)."""
    folder_prefix = folder_prefix or _DEFAULT_FOLDER
    removed_actors = []
    for actor in list(_find_tagged_actors(folder_prefix=folder_prefix)):
        label = ""
        try:
            label = actor.get_actor_label() or ""
        except Exception:
            pass
        if asset_name_prefix and asset_name_prefix not in label and "Terrain" not in label:
            # Still allow WG_Terrain_* always
            if not label.startswith("WG_Terrain"):
                continue
        path = actor.get_path_name()
        try:
            unreal.EditorLevelLibrary.destroy_actor(actor)
            removed_actors.append(path)
        except Exception:
            continue
    lookup.invalidate()

    removed_assets = []
    prefix = asset_name_prefix or "SM_WG_Terrain"
    folder = _asset_folder("")
    try:
        for ap in unreal.EditorAssetLibrary.list_assets(folder, recursive=True) or []:
            name = str(ap).rsplit("/", 1)[-1].split(".")[0]
            if name.startswith(prefix) or name.startswith("SM_WG_Terrain"):
                if unreal.EditorAssetLibrary.delete_asset(str(ap).split(".")[0]):
                    removed_assets.append(str(ap))
    except Exception:
        pass
    return {
        "ok": True,
        "removed_actors": removed_actors,
        "removed_actor_count": len(removed_actors),
        "removed_assets": removed_assets,
        "removed_asset_count": len(removed_assets),
    }


# ---------------------------------------------------------------------------
# Foliage
# ---------------------------------------------------------------------------


_FOLIAGE_KEYWORDS = (
    "tree",
    "pine",
    "oak",
    "palm",
    "bush",
    "shrub",
    "fern",
    "grass",
    "weed",
    "flower",
    "rock",
    "stone",
    "boulder",
    "foliage",
    "plant",
    "cactus",
)


def foliage_list_sources(
    search: str = "",
    folder: str = "",
    limit: int = 40,
) -> dict:
    """Discover Content Drawer foliage props (Actor Blueprints) plus project meshes.

    Default folders are ``/Game/Creative/Environments`` (what drag-drop uses).
    BR BakeData static meshes are skipped — those are not Content Drawer items.
    """
    limit = max(1, min(int(limit), 100))
    needle = (search or "").strip().lower()
    try:
        project_root = _content_root()
    except RuntimeError:
        project_root = ""
    folders = []
    if folder:
        folders.append(folder)
    else:
        if project_root:
            folders.append(project_root.rstrip("/") + "/")
        folders.extend(list(FOLIAGE_SOURCE_FOLDERS))

    meshes: List[dict] = []
    foliage_types: List[dict] = []
    seen = set()

    # FoliageType assets
    try:
        from listener.registry.asset_registry import assets_by_class

        for data in assets_by_class("/Script/Foliage", "FoliageType_InstancedStaticMesh"):
            try:
                full = f"{data.package_name}.{data.asset_name}"
            except Exception:
                continue
            low = full.lower()
            if needle and needle not in low:
                continue
            if is_foliage_bake_mesh(full):
                continue
            foliage_types.append({"path": full, "kind": "foliage_type"})
            if len(foliage_types) >= limit:
                break
    except Exception:
        pass

    # Static meshes with foliage-ish names
    for root in folders:
        if len(meshes) >= limit:
            break
        try:
            assets = unreal.EditorAssetLibrary.list_assets(root, recursive=True) or []
        except Exception:
            continue
        for ap in assets:
            if len(meshes) >= limit:
                break
            s = str(ap)
            low = s.lower()
            if "/generated/worldgen" in low:
                continue
            if needle and needle not in low:
                continue
            if not any(k in low for k in _FOLIAGE_KEYWORDS):
                continue
            if ".foliagetype" in low:
                continue
            if is_foliage_bake_mesh(s):
                continue
            key = s.split(".")[0]
            if key in seen:
                continue
            seen.add(key)
            # Validate class cheaply via asset data when possible
            try:
                data = unreal.EditorAssetLibrary.find_asset_data(key)
                cls_name = ""
                if data is not None:
                    if hasattr(data, "asset_class_path"):
                        cls_name = str(data.asset_class_path.asset_name)
                    else:
                        cls_name = str(getattr(data, "asset_class", ""))
                if cls_name in ("Blueprint", "BlueprintGeneratedClass"):
                    bp = s if s.lower().endswith("_c") else f"{key}.{key.rsplit('/', 1)[-1]}_C"
                    meshes.append({"path": bp, "kind": "blueprint"})
                    continue
                if cls_name and cls_name not in (
                    "StaticMesh",
                    "FoliageType_InstancedStaticMesh",
                    "Blueprint",
                    "BlueprintGeneratedClass",
                ):
                    continue
                if cls_name == "FoliageType_InstancedStaticMesh":
                    foliage_types.append({"path": s if "." in s else f"{key}.{key.rsplit('/',1)[-1]}", "kind": "foliage_type"})
                    continue
            except Exception:
                pass
            meshes.append({"path": key, "kind": "static_mesh"})

    return {
        "meshes": meshes[:limit],
        "foliage_types": foliage_types[:limit],
        "mesh_count": len(meshes[:limit]),
        "foliage_type_count": len(foliage_types[:limit]),
        "search": search,
    }


def _ensure_foliage_type(mesh_or_type_path: str, asset_folder: str = "") -> Any:
    """Load an existing FoliageType, or return None if only a StaticMesh is available.

    Creating brand-new FoliageType assets via factory has crashed some UEFN builds;
    callers should fall back to HISM containers for raw meshes.
    """
    path = (mesh_or_type_path or "").strip()
    if not path:
        raise ValueError("mesh_or_type_path is required")
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if asset is None and "." not in path:
        asset = unreal.EditorAssetLibrary.load_asset(f"{path}.{path.rsplit('/', 1)[-1]}")
    if asset is None:
        raise ValueError(f"Asset not found: {path}")

    ft_cls = getattr(unreal, "FoliageType_InstancedStaticMesh", None)
    if ft_cls is not None and isinstance(asset, ft_cls):
        return asset
    # Do not create new FoliageType assets here — return None so scatter uses HISM.
    return None


def _load_static_mesh(mesh_or_type_path: str) -> Any:
    path = (mesh_or_type_path or "").strip()
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if asset is None and "." not in path:
        asset = unreal.EditorAssetLibrary.load_asset(f"{path}.{path.rsplit('/', 1)[-1]}")
    if asset is None:
        raise ValueError(f"Asset not found: {path}")
    ft_cls = getattr(unreal, "FoliageType_InstancedStaticMesh", None)
    if ft_cls is not None and isinstance(asset, ft_cls):
        mesh = asset.get_editor_property("mesh")
        if mesh is None:
            raise ValueError(f"FoliageType has no mesh: {path}")
        return mesh
    if not isinstance(asset, unreal.StaticMesh):
        raise ValueError(f"Expected StaticMesh or FoliageType, got {asset.get_class().get_name()}")
    return asset


def _spawn_hism_container(mesh, center: Sequence[float], base_z: float, level_folder: str) -> Any:
    """Spawn one empty Actor and attach a HISM component for instancing.

    UEFN Python omits ``add_component_by_class`` / ``register_component``.
    ``new_object`` + ``attach_to_component`` + ``activate`` is the safe path —
    do NOT promote HISM to ``root_component`` (that can freeze the editor).
    """
    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.Actor, unreal.Vector(float(center[0]), float(center[1]), float(base_z))
    )
    if actor is None:
        raise RuntimeError("Failed to spawn HISM container actor")
    try:
        actor.set_actor_label(f"WG_HISM_{mesh.get_name()}")
    except Exception:
        pass
    _tag_actor(actor, level_folder)

    hism_cls = unreal.HierarchicalInstancedStaticMeshComponent
    safe_name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in mesh.get_name()) or "Mesh"
    hism = unreal.new_object(hism_cls, outer=actor, name=f"HISM_{safe_name}")
    root = actor.root_component
    if root is None:
        raise RuntimeError("HISM container actor has no root component")
    hism.attach_to_component(
        root,
        unreal.Name(""),
        unreal.AttachmentRule.KEEP_RELATIVE,
        unreal.AttachmentRule.KEEP_RELATIVE,
        unreal.AttachmentRule.KEEP_RELATIVE,
        False,
    )
    hism.set_static_mesh(mesh)
    try:
        hism.set_editor_property("mobility", unreal.ComponentMobility.STATIC)
    except Exception:
        pass
    try:
        hism.set_editor_property("auto_activate", True)
    except Exception:
        pass
    try:
        flags = getattr(unreal, "ObjectFlags", None)
        if flags is not None and hasattr(hism, "set_flags"):
            hism.set_flags(flags.RF_TRANSACTIONAL)
    except Exception:
        pass
    for call, args in (
        ("activate", (True,)),
        ("set_active", (True, True)),
    ):
        fn = getattr(hism, call, None)
        if callable(fn):
            try:
                fn(*args)
            except Exception:
                pass
    try:
        hism.set_visibility(True, True)
    except Exception:
        try:
            hism.set_visibility(True)
        except Exception:
            pass
    return actor, hism


def _add_hism_instances(hism, xforms: List[unreal.Transform]) -> int:
    added = 0
    # Prefer world-space batch add — container actors sit at the scatter center,
    # so local-space transforms would double-offset XY.
    try:
        hism.add_instances(xforms, False, True)
        return len(xforms)
    except TypeError:
        pass
    except Exception:
        pass
    try:
        hism.add_instances(xforms, False)
        return len(xforms)
    except Exception:
        pass
    for xf in xforms:
        try:
            hism.add_instance_world_space(xf)
            added += 1
            continue
        except Exception:
            pass
        try:
            hism.add_instance(xf)
            added += 1
        except Exception:
            break
    return added


_GROUND_Z_CACHE: Dict[Tuple[int, int], float] = {}


def _ground_z_simple(x: float, y: float, fallback_z: float) -> float:
    """Line-trace ground under (x,y) with a coarse cache (avoids 3×N traces)."""
    key = (int(round(x / 100.0)), int(round(y / 100.0)))
    cached = _GROUND_Z_CACHE.get(key)
    if cached is not None:
        return cached
    z = float(fallback_z)
    try:
        world = unreal.EditorLevelLibrary.get_editor_world()
        # Short traces are much cheaper than ±50km sweeps.
        start = unreal.Vector(float(x), float(y), float(fallback_z) + 8000.0)
        end = unreal.Vector(float(x), float(y), float(fallback_z) - 4000.0)
        hit = unreal.SystemLibrary.line_trace_single(
            world,
            start,
            end,
            unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
            True,
            [],
            unreal.DrawDebugTrace.NONE,
            True,
            unreal.LinearColor(1.0, 0.0, 0.0, 1.0),
            unreal.LinearColor(0.0, 1.0, 0.0, 1.0),
            0.0,
        )
        if hit is not None and hasattr(hit, "to_tuple"):
            tup = hit.to_tuple()
            if tup and tup[0]:
                loc = tup[4]
                if isinstance(loc, dict):
                    z = float(loc.get("z", fallback_z))
                else:
                    z = float(loc.z)
    except Exception:
        pass
    _GROUND_Z_CACHE[key] = z
    return z


def foliage_scatter(
    center: Optional[List[float]] = None,
    extent: Optional[List[float]] = None,
    sources: Optional[List[str]] = None,
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
) -> dict:
    """Scatter foliage over a footprint (deterministic).

    placement_mode:
      auto/actors — Content Drawer Actor Blueprints (`_C`), same as drag-drop
      hism — one HISM container per source (often non-renderable via Python here)
    """
    _require_foliage_backend()
    global _GROUND_Z_CACHE
    _GROUND_Z_CACHE = {}
    center = center or [0.0, 0.0, 0.0]
    extent = extent or [4000.0, 4000.0, 2000.0]
    if len(center) < 2 or len(extent) < 2:
        raise ValueError("center/extent need at least XY")
    mode = (placement_mode or "auto").strip().lower()
    if mode in ("auto", "actors"):
        max_instances = max(1, min(int(max_instances), 120))
    else:
        max_instances = max(1, min(int(max_instances), _MAX_INSTANCES))
    folder = level_folder or _DEFAULT_FOLDER
    source_paths = [s for s in (sources or []) if s]
    source_paths = [s for s in source_paths if not is_foliage_bake_mesh(s)]
    if not source_paths:
        listed = foliage_list_sources(limit=8)
        source_paths = [m["path"] for m in listed.get("meshes") or []][:4]
        source_paths += [f["path"] for f in listed.get("foliage_types") or []][:2]
    if not source_paths:
        raise ValueError("No foliage sources found — pass sources=[mesh_or_foliage_type_paths]")

    if clear_first:
        foliage_clear_generated(folder_prefix=folder)

    points = scatter_points(
        center=center,
        extent=extent,
        density_per_100m2=float(density_per_100m2),
        min_distance=float(min_distance),
        seed=int(seed),
        max_instances=max_instances,
    )
    rng = random.Random(int(seed) + 17)
    resolved = []
    for sp in source_paths:
        try:
            if mode == "hism":
                resolved.append(_load_static_mesh(sp))
            else:
                resolved.append(resolve_content_drawer_placeable(sp))
        except Exception:
            continue
    if not resolved:
        raise ValueError(
            f"Could not resolve Content Drawer props from {source_paths}. "
            'Pass Actor Blueprint `_C` paths from /Game/Creative/Environments '
            "(same assets you drag from the Fortnite Content Drawer)."
        )

    transforms_by_key: Dict[int, List[unreal.Transform]] = {i: [] for i in range(len(resolved))}
    skipped_slope = 0
    base_z = float(center[2] if len(center) > 2 else 0.0)

    for x, y in points:
        z = _ground_z_simple(x, y, base_z)
        if mode == "hism":
            z2 = _ground_z_simple(x + 100.0, y, base_z)
            z3 = _ground_z_simple(x, y + 100.0, base_z)
            slope = math.degrees(math.atan(max(abs(z2 - z), abs(z3 - z)) / 100.0))
            if slope > float(max_slope_degrees):
                skipped_slope += 1
                continue
        src_i = rng.randrange(len(resolved))
        yaw = rng.uniform(0.0, 360.0)
        sc = rng.uniform(float(scale_min), float(scale_max))
        t = unreal.Transform()
        t.translation = unreal.Vector(x, y, z)
        t.rotation = unreal.Rotator(0.0, yaw, 0.0).quaternion()
        t.scale3d = unreal.Vector(sc, sc, sc)
        transforms_by_key[src_i].append(t)

    use_hism = mode == "hism"
    used_backend = "hism" if use_hism else "content_drawer_actors"
    added = 0
    details = []
    placed_labels: list[str] = []
    for i, obj in enumerate(resolved):
        xforms = transforms_by_key[i]
        if not xforms:
            continue
        try:
            if use_hism:
                _actor, hism = _spawn_hism_container(obj, center, base_z, folder)
                n = _add_hism_instances(hism, xforms)
                try:
                    placed_labels.append(_actor.get_actor_label())
                except Exception:
                    pass
            else:
                n, labels = _place_mesh_actors(
                    obj, xforms, folder, f"WG_Foliage_{_placeable_name(obj)}"
                )
                placed_labels.extend(labels)
            added += n
            details.append({"source": _placeable_path(obj), "instances": n, "backend": used_backend})
        except Exception as e:
            details.append({"source": serialize(obj), "instances": 0, "error": str(e), "backend": used_backend})

    lookup.invalidate()
    return {
        "ok": True,
        "labels": placed_labels,
        "foliage_backend": used_backend,
        "placement_mode": mode,
        "seed": int(seed),
        "requested_points": len(points),
        "instances_added": added,
        "skipped_slope": skipped_slope,
        "sources_used": details,
        "folder": folder,
        "center": center,
        "extent": extent,
    }


def foliage_clear_generated(folder_prefix: str = "", foliage_type_paths: Optional[List[str]] = None) -> dict:
    """Clear generated foliage actors (HISM containers + tagged mesh actors).

    Never calls InstancedFoliageActor APIs — those crash this UEFN build.
    """
    folder_prefix = folder_prefix or _DEFAULT_FOLDER
    removed = []
    _ = foliage_type_paths  # API compat; IFA path intentionally unused

    for actor in list(_find_tagged_actors(folder_prefix=folder_prefix)):
        label = ""
        try:
            label = actor.get_actor_label() or ""
        except Exception:
            pass
        if label.startswith("WG_Terrain"):
            continue
        if not (
            label.startswith("WG_HISM")
            or label.startswith("WG_Foliage")
            or label.startswith("WG_VisProbe")
            or label.startswith("WG_InstancedFoliage")
            or "HISM" in label
        ):
            continue
        path = actor.get_path_name()
        try:
            unreal.EditorLevelLibrary.destroy_actor(actor)
            removed.append(path)
        except Exception:
            continue
    lookup.invalidate()
    return {"ok": True, "removed_actors": removed, "removed_count": len(removed), "folder_prefix": folder_prefix}


# ---------------------------------------------------------------------------
# Register (landscape_* commands live in landscape_heightmap.py)
# ---------------------------------------------------------------------------

register("worldgen_capabilities")(worldgen_capabilities)
register("terrain_generate")(terrain_generate)
register("terrain_get_info")(terrain_get_info)
register("terrain_remove_generated")(terrain_remove_generated)
register("foliage_list_sources")(foliage_list_sources)
register("foliage_scatter")(foliage_scatter)
register("foliage_clear_generated")(foliage_clear_generated)

# Re-exported so old imports keep working; importing it registers the landscape_* commands.
from listener.registry.landscape_heightmap import (  # noqa: E402,F401
    landscape_create,
    landscape_get_info,
    landscape_list,
    landscape_rename,
    landscape_sculpt,
)
