"""Per-area landscapes + greybox blockout presets.

One named area = one terrain mesh (via terrain_generate) on a 100k-uu slot grid,
plus optional Cube greybox under Areas/<id>/…. Real Landscape create is unavailable
from Python — do not call landscape_create for AI layouts.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Pure helpers (tested without unreal) — keep unreal imports below this block
# ---------------------------------------------------------------------------

SLOT_SPACING_UU = 100_000.0
CUBE_MESH = "/Engine/BasicShapes/Cube.Cube"
# Engine cube is 100 uu; scale = world_uu / 100
CELL = 512.0
STOREY = 384.0
WALL_THICK = 50.0  # world uu
FLOOR_THICK = 20.0
OPENING = 256.0  # door gap width

_AREA_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_BLOCKOUT_TAG = "BlockoutGenerated"
_AREA_TAG_PREFIX = "Area:"


def normalize_area_id(area_id: str) -> str:
    raw = (area_id or "").strip().lower().replace(" ", "_").replace("-", "_")
    cleaned = re.sub(r"[^a-z0-9_]", "", raw)
    if not cleaned or not _AREA_ID_RE.match(cleaned):
        raise ValueError(
            f"area_id must be a slug like hub/store/arena (got {area_id!r})"
        )
    return cleaned


def slot_origin(slot_index: int) -> List[float]:
    i = max(0, int(slot_index))
    return [float(i) * SLOT_SPACING_UU, 0.0, 0.0]


def origin_to_slot(origin: Sequence[float]) -> int:
    if not origin:
        return 0
    x = float(origin[0])
    return max(0, int(round(x / SLOT_SPACING_UU)))


def area_root(area_id: str) -> str:
    return f"Areas/{normalize_area_id(area_id)}"


def area_subfolder(area_id: str, sub: str) -> str:
    return f"{area_root(area_id)}/{sub}"


def next_free_slot(used_slots: Sequence[int]) -> int:
    used = set(int(s) for s in used_slots)
    i = 0
    while i in used:
        i += 1
    return i


def _sc(world_uu: float) -> float:
    """World uu → Unreal scale for 100-uu cube."""
    return float(world_uu) / 100.0


def _wall_segments(
    *,
    axis: str,
    center: float,
    half_span: float,
    z: float,
    height: float,
    gap_half: float = 0.0,
    prefix: str,
) -> List[dict]:
    """Build 1–2 wall pieces along X or Y with optional centered opening."""
    thick = WALL_THICK
    pieces: List[dict] = []
    if gap_half <= 0:
        if axis == "x":
            length = half_span * 2
            pieces.append(
                {
                    "suffix": prefix,
                    "loc": [0.0, center, z],
                    "scale": [_sc(length), _sc(thick), _sc(height)],
                    "yaw": 0.0,
                }
            )
        else:
            length = half_span * 2
            pieces.append(
                {
                    "suffix": prefix,
                    "loc": [center, 0.0, z],
                    "scale": [_sc(thick), _sc(length), _sc(height)],
                    "yaw": 0.0,
                }
            )
        return pieces

    # Two segments leaving a gap of 2*gap_half in the middle
    seg = half_span - gap_half
    if seg <= 0:
        return pieces
    if axis == "x":
        # wall along X at y=center
        mid_neg = -(gap_half + seg / 2)
        mid_pos = gap_half + seg / 2
        for i, mx in enumerate((mid_neg, mid_pos)):
            pieces.append(
                {
                    "suffix": f"{prefix}_{i}",
                    "loc": [mx, center, z],
                    "scale": [_sc(seg), _sc(thick), _sc(height)],
                    "yaw": 0.0,
                }
            )
    else:
        mid_neg = -(gap_half + seg / 2)
        mid_pos = gap_half + seg / 2
        for i, my in enumerate((mid_neg, mid_pos)):
            pieces.append(
                {
                    "suffix": f"{prefix}_{i}",
                    "loc": [center, my, z],
                    "scale": [_sc(thick), _sc(seg), _sc(height)],
                    "yaw": 0.0,
                }
            )
    return pieces


def _box_shell(
    cells_x: int,
    cells_y: int,
    *,
    openings: Sequence[str] = (),
    height: float = STOREY,
) -> List[dict]:
    """Floor + four walls; openings is subset of N/S/E/W (door gaps)."""
    hx = cells_x * CELL * 0.5
    hy = cells_y * CELL * 0.5
    z_wall = height * 0.5
    gap = OPENING * 0.5
    opens = {o.upper() for o in openings}
    pieces: List[dict] = [
        {
            "suffix": "Floor",
            "loc": [0.0, 0.0, FLOOR_THICK * 0.5],
            "scale": [_sc(hx * 2), _sc(hy * 2), _sc(FLOOR_THICK)],
            "yaw": 0.0,
        }
    ]
    pieces += _wall_segments(
        axis="x",
        center=hy,
        half_span=hx,
        z=z_wall,
        height=height,
        gap_half=gap if "N" in opens else 0.0,
        prefix="Wall_N",
    )
    pieces += _wall_segments(
        axis="x",
        center=-hy,
        half_span=hx,
        z=z_wall,
        height=height,
        gap_half=gap if "S" in opens else 0.0,
        prefix="Wall_S",
    )
    pieces += _wall_segments(
        axis="y",
        center=hx,
        half_span=hy,
        z=z_wall,
        height=height,
        gap_half=gap if "E" in opens else 0.0,
        prefix="Wall_E",
    )
    pieces += _wall_segments(
        axis="y",
        center=-hx,
        half_span=hy,
        z=z_wall,
        height=height,
        gap_half=gap if "W" in opens else 0.0,
        prefix="Wall_W",
    )
    return pieces


def _build_presets() -> Dict[str, dict]:
    hub = _box_shell(8, 8, openings=("N", "S", "E", "W"))
    # Landmark mass in center for orientation
    hub.append(
        {
            "suffix": "Landmark",
            "loc": [0.0, 0.0, STOREY],
            "scale": [_sc(CELL), _sc(CELL), _sc(STOREY * 2)],
            "yaw": 0.0,
        }
    )

    store = _box_shell(4, 4, openings=("S",))
    # Counter volume near back wall
    store.append(
        {
            "suffix": "Counter",
            "loc": [0.0, CELL, 96.0],
            "scale": [_sc(CELL * 2), _sc(WALL_THICK * 2), _sc(192.0)],
            "yaw": 0.0,
        }
    )

    arena = _box_shell(6, 6, openings=("N", "S"))
    # Low cover cubes
    for i, (x, y) in enumerate(((-512, 0), (512, 0), (0, -512), (0, 512))):
        arena.append(
            {
                "suffix": f"Cover_{i}",
                "loc": [float(x), float(y), 48.0],
                "scale": [_sc(256), _sc(256), _sc(96)],
                "yaw": 0.0,
            }
        )

    corridor = _box_shell(2, 6, openings=("N", "S"), height=STOREY)

    lobby = _box_shell(4, 4, openings=("N",))
    lobby.append(
        {
            "suffix": "SpawnPad",
            "loc": [0.0, 0.0, 8.0],
            "scale": [_sc(256), _sc(256), _sc(16)],
            "yaw": 0.0,
        }
    )

    return {
        "hub": {
            "id": "hub",
            "description": "Central plaza (8×8 cells) with four spoke openings + landmark",
            "footprint_cells": [8, 8],
            "pieces": hub,
        },
        "store": {
            "id": "store",
            "description": "Shop box (4×4) with south door and counter volume",
            "footprint_cells": [4, 4],
            "pieces": store,
        },
        "arena": {
            "id": "arena",
            "description": "Symmetric fight box (6×6) with N/S doors and low cover",
            "footprint_cells": [6, 6],
            "pieces": arena,
        },
        "corridor": {
            "id": "corridor",
            "description": "Modular hallway (2×6 cells), open on N/S",
            "footprint_cells": [2, 6],
            "pieces": corridor,
        },
        "lobby": {
            "id": "lobby",
            "description": "Spawn lobby shell (4×4) with north exit + pad marker",
            "footprint_cells": [4, 4],
            "pieces": lobby,
        },
    }


PRESETS = _build_presets()


def list_preset_meta() -> List[dict]:
    out = []
    for p in PRESETS.values():
        out.append(
            {
                "id": p["id"],
                "description": p["description"],
                "footprint_cells": list(p["footprint_cells"]),
                "piece_count": len(p["pieces"]),
            }
        )
    return out


def get_preset(preset: str) -> dict:
    key = (preset or "").strip().lower()
    if key not in PRESETS:
        raise ValueError(f"Unknown preset {preset!r}. Valid: {sorted(PRESETS)}")
    return PRESETS[key]


def pieces_world(
    pieces: Sequence[dict], origin: Sequence[float]
) -> List[dict]:
    ox, oy, oz = float(origin[0]), float(origin[1]), float(origin[2])
    out = []
    for p in pieces:
        loc = p["loc"]
        out.append(
            {
                "suffix": p["suffix"],
                "loc": [ox + float(loc[0]), oy + float(loc[1]), oz + float(loc[2])],
                "scale": list(p["scale"]),
                "yaw": float(p.get("yaw") or 0.0),
            }
        )
    return out


# --- END PURE HELPERS ---

import unreal  # noqa: E402

from listener import lookup  # noqa: E402
from listener.dispatch import register  # noqa: E402
from listener.registry.worldgen import terrain_generate  # noqa: E402
from listener.serialize import is_live  # noqa: E402


def _load_cube():
    mesh = unreal.EditorAssetLibrary.load_asset(CUBE_MESH)
    if mesh is None:
        # Fallback path without .Cube suffix
        mesh = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cube")
    if mesh is None:
        raise RuntimeError(f"Failed to load cube mesh {CUBE_MESH}")
    return mesh


def _tag_area_actor(actor, area_id: str, folder: str, *, blockout: bool = False) -> None:
    tags = [unreal.Name(f"{_AREA_TAG_PREFIX}{area_id}")]
    if blockout:
        tags.append(unreal.Name(_BLOCKOUT_TAG))
    try:
        existing = list(actor.tags or [])
        for t in existing:
            s = str(t)
            if s and s not in {str(x) for x in tags}:
                tags.append(t if isinstance(t, unreal.Name) else unreal.Name(s))
    except Exception:
        pass
    try:
        actor.tags = tags
    except Exception:
        try:
            actor.set_editor_property("tags", tags)
        except Exception:
            pass
    try:
        actor.set_folder_path(folder)
    except Exception:
        pass


def _area_id_from_folder(folder: str) -> Optional[str]:
    folder = (folder or "").replace("\\", "/").strip("/")
    if not folder.startswith("Areas/"):
        return None
    parts = folder.split("/")
    if len(parts) < 2:
        return None
    try:
        return normalize_area_id(parts[1])
    except ValueError:
        return None


def _area_id_from_tags(actor) -> Optional[str]:
    try:
        for t in list(actor.tags or []):
            s = str(t)
            if s.startswith(_AREA_TAG_PREFIX):
                return normalize_area_id(s[len(_AREA_TAG_PREFIX) :])
    except Exception:
        pass
    return None


def _iter_area_actors():
    for actor in lookup.actor_list():
        if not is_live(actor):
            continue
        folder = ""
        try:
            folder = str(actor.get_folder_path() or "")
        except Exception:
            folder = ""
        aid = _area_id_from_folder(folder) or _area_id_from_tags(actor)
        if aid:
            yield actor, aid, folder


def _discover_areas() -> Dict[str, dict]:
    areas: Dict[str, dict] = {}
    for actor, aid, folder in _iter_area_actors():
        entry = areas.setdefault(
            aid,
            {
                "id": aid,
                "origin": None,
                "folder": area_root(aid),
                "has_terrain": False,
                "has_blockout": False,
                "slot": None,
            },
        )
        label = ""
        try:
            label = actor.get_actor_label() or ""
        except Exception:
            pass
        if "/Terrain" in folder or "Terrain" in label or label.startswith("WG_Terrain"):
            entry["has_terrain"] = True
            try:
                loc = actor.get_actor_location()
                entry["origin"] = [float(loc.x), float(loc.y), float(loc.z)]
                entry["slot"] = origin_to_slot(entry["origin"])
            except Exception:
                pass
        if "/Blockout" in folder or _BLOCKOUT_TAG in [
            str(t) for t in (list(actor.tags or []) if hasattr(actor, "tags") else [])
        ]:
            entry["has_blockout"] = True
        if entry["origin"] is None:
            try:
                loc = actor.get_actor_location()
                # Rough: round to slot grid on X
                entry["slot"] = origin_to_slot([float(loc.x), 0, 0])
                entry["origin"] = slot_origin(entry["slot"])
            except Exception:
                pass
    for aid, entry in areas.items():
        if entry["origin"] is None:
            entry["slot"] = 0
            entry["origin"] = slot_origin(0)
        elif entry["slot"] is None:
            entry["slot"] = origin_to_slot(entry["origin"])
    return areas


def _clear_blockout(area_id: str) -> int:
    folder = area_subfolder(area_id, "Blockout")
    removed = 0
    for actor, aid, afolder in list(_iter_area_actors()):
        if aid != area_id:
            continue
        if not str(afolder).startswith(folder):
            continue
        try:
            unreal.EditorLevelLibrary.destroy_actor(actor)
            removed += 1
        except Exception:
            pass
    if removed:
        lookup.invalidate()
    return removed


def _spawn_blockout_pieces(
    area_id: str, pieces: Sequence[dict], origin: Sequence[float]
) -> List[str]:
    mesh = _load_cube()
    folder = area_subfolder(area_id, "Blockout")
    labels: List[str] = []
    title = area_id[:1].upper() + area_id[1:]
    for p in pieces_world(pieces, origin):
        loc = unreal.Vector(p["loc"][0], p["loc"][1], p["loc"][2])
        rot = unreal.Rotator(0.0, p["yaw"], 0.0)
        actor = unreal.EditorLevelLibrary.spawn_actor_from_object(mesh, loc, rot)
        if actor is None:
            continue
        label = f"{title}_{p['suffix']}"
        try:
            actor.set_actor_label(label)
        except Exception:
            pass
        try:
            sx, sy, sz = p["scale"]
            actor.set_actor_scale3d(unreal.Vector(float(sx), float(sy), float(sz)))
        except Exception:
            pass
        _tag_area_actor(actor, area_id, folder, blockout=True)
        labels.append(label)
    lookup.invalidate()
    return labels


def area_list() -> dict:
    areas = list(_discover_areas().values())
    areas.sort(key=lambda a: (a.get("slot") is None, a.get("slot") or 0, a["id"]))
    return {"ok": True, "areas": areas, "count": len(areas), "slot_spacing_uu": SLOT_SPACING_UU}


def blockout_list_presets() -> dict:
    return {"ok": True, "presets": list_preset_meta()}


def blockout_layout(
    area_id: str,
    preset: str,
    origin: Optional[List[float]] = None,
    replace: bool = True,
) -> dict:
    aid = normalize_area_id(area_id)
    recipe = get_preset(preset)
    areas = _discover_areas()
    if origin and len(origin) == 3:
        org = [float(origin[0]), float(origin[1]), float(origin[2])]
    elif aid in areas and areas[aid].get("origin"):
        org = list(areas[aid]["origin"])
    else:
        used = [a["slot"] for a in areas.values() if a.get("slot") is not None]
        org = slot_origin(next_free_slot(used))
    cleared = _clear_blockout(aid) if replace else 0
    labels = _spawn_blockout_pieces(aid, recipe["pieces"], org)
    return {
        "ok": True,
        "area_id": aid,
        "preset": recipe["id"],
        "origin": org,
        "folder": area_subfolder(aid, "Blockout"),
        "cleared": cleared,
        "placed": len(labels),
        "labels": labels,
        "footprint_cells": list(recipe["footprint_cells"]),
    }


def area_create(
    area_id: str,
    preset: str = "",
    size_uu: float = 12800.0,
    seed: int = 42,
    stamps: Optional[List[dict]] = None,
    replace_terrain: bool = True,
    replace_blockout: bool = True,
) -> dict:
    aid = normalize_area_id(area_id)
    areas = _discover_areas()
    if aid in areas and areas[aid].get("origin"):
        org = list(areas[aid]["origin"])
        slot = int(areas[aid].get("slot") or origin_to_slot(org))
    else:
        used = [a["slot"] for a in areas.values() if a.get("slot") is not None]
        slot = next_free_slot(used)
        org = slot_origin(slot)

    # Flatten a playable pad under the greybox footprint
    pad_radius = max(CELL * 4, float(size_uu) * 0.35)
    default_stamps = [
        {
            "type": "flatten",
            "x": 0,
            "y": 0,
            "radius": pad_radius,
            "height": 0,
            "strength": 1,
        }
    ]
    terrain = terrain_generate(
        location=org,
        size_uu=float(size_uu),
        resolution=65,
        amplitude=200.0,
        noise_scale=4000.0,
        seed=int(seed),
        stamps=stamps if stamps is not None else default_stamps,
        asset_name=f"SM_Area_{aid}_Terrain",
        level_folder=area_subfolder(aid, "Terrain"),
        replace_existing=replace_terrain,
    )

    # Retag terrain with Area:<id>
    try:
        path = terrain.get("actor_path") or ""
        if path:
            actor = lookup.require_actor(path)
            _tag_area_actor(actor, aid, area_subfolder(aid, "Terrain"), blockout=False)
            # Keep WorldgenGenerated — merge tags
            try:
                tags = list(actor.tags or [])
                if not any(str(t) == "WorldgenGenerated" for t in tags):
                    tags.append(unreal.Name("WorldgenGenerated"))
                    actor.tags = tags
            except Exception:
                pass
    except Exception:
        pass

    blockout = None
    if (preset or "").strip():
        blockout = blockout_layout(
            area_id=aid,
            preset=preset,
            origin=org,
            replace=replace_blockout,
        )

    return {
        "ok": True,
        "area_id": aid,
        "slot": slot,
        "origin": org,
        "folder": area_root(aid),
        "subfolders": {
            "terrain": area_subfolder(aid, "Terrain"),
            "blockout": area_subfolder(aid, "Blockout"),
            "devices": area_subfolder(aid, "Devices"),
            "props": area_subfolder(aid, "Props"),
            "foliage": area_subfolder(aid, "Foliage"),
        },
        "terrain": terrain,
        "blockout": blockout,
        "note": "AI landscapes use terrain_generate mesh heightfields. Real Landscape Mode Create is manual-only.",
    }


register("area_list")(area_list)
register("area_create")(area_create)
register("blockout_list_presets")(blockout_list_presets)
register("blockout_layout")(blockout_layout)
