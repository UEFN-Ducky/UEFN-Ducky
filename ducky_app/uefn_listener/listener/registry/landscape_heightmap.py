"""Landscape tools: real UE Landscape actors / proxies (split out of worldgen.py).

  landscape_list, landscape_get_info, landscape_create, landscape_rename,
  landscape_sculpt (heightmap render-target round-trip; verify mode only).
"""

from __future__ import annotations

from typing import List, Optional

import unreal

from listener.dispatch import register


def _iter_landscape_roots():
    for a in unreal.EditorLevelLibrary.get_all_level_actors() or []:
        try:
            if a.get_class().get_name() == "Landscape":
                yield a
        except Exception:
            continue


def _proxy_panel_stats_for_landscape(land) -> dict:
    """Count LandscapeStreamingProxy panels linked to this Landscape root."""
    proxies = []
    total_lc = 0
    land_path = land.get_path_name()
    for a in unreal.EditorLevelLibrary.get_all_level_actors() or []:
        try:
            if a.get_class().get_name() != "LandscapeStreamingProxy":
                continue
        except Exception:
            continue
        linked = False
        try:
            ref = a.get_editor_property("landscape_actor_ref")
            if ref is not None and ref.get_path_name() == land_path:
                linked = True
        except Exception:
            pass
        if not linked:
            try:
                # Some builds expose get_landscape_actor()
                root = a.get_landscape_actor()
                if root is not None and root.get_path_name() == land_path:
                    linked = True
            except Exception:
                pass
        if not linked:
            continue
        lc = 0
        try:
            lc = len(a.get_components_by_class(unreal.LandscapeComponent) or [])
        except Exception:
            lc = 0
        total_lc += lc
        proxies.append(
            {
                "label": a.get_actor_label(),
                "path": a.get_path_name(),
                "landscape_components": lc,
            }
        )
    return {"proxies": proxies, "proxy_count": len(proxies), "landscape_component_count": total_lc}


def _linked_proxies_for_landscape(land):
    """Yield LandscapeStreamingProxy actors linked to this Landscape root."""
    land_path = land.get_path_name()
    for a in unreal.EditorLevelLibrary.get_all_level_actors() or []:
        try:
            if a.get_class().get_name() != "LandscapeStreamingProxy":
                continue
        except Exception:
            continue
        linked = False
        try:
            ref = a.get_editor_property("landscape_actor_ref")
            if ref is not None and ref.get_path_name() == land_path:
                linked = True
        except Exception:
            pass
        if not linked:
            try:
                root = a.get_landscape_actor()
                if root is not None and root.get_path_name() == land_path:
                    linked = True
            except Exception:
                pass
        if linked:
            yield a


def _landscape_heightmap_resolution(land) -> dict:
    """Reconstruct heightmap vertex resolution from LandscapeComponent section bases.

    ComponentSizeQuads/GridSize are not Python-readable on this UEFN build, but each
    LandscapeComponent exposes ``section_base_x/y``. The step between adjacent bases is
    the component quad size; ``max_base + step + 1`` is the vertex resolution. This is
    the exact RT size needed for a lossless export/import heightmap round-trip.
    """
    xs = set()
    ys = set()
    for a in _linked_proxies_for_landscape(land):
        for c in a.get_components_by_class(unreal.LandscapeComponent) or []:
            try:
                xs.add(int(c.get_editor_property("section_base_x")))
                ys.add(int(c.get_editor_property("section_base_y")))
            except Exception:
                continue
    if not xs or not ys:
        return {}
    xs = sorted(xs)
    ys = sorted(ys)
    step = 0
    if len(xs) > 1:
        step = xs[1] - xs[0]
    elif len(ys) > 1:
        step = ys[1] - ys[0]
    if step <= 0:
        step = 63
    return {
        "res_x": xs[-1] + step + 1,
        "res_y": ys[-1] + step + 1,
        "component_size_quads": step,
    }


def landscape_list() -> dict:
    """List Landscape roots and how many panel components their proxies have."""
    rows = []
    for land in _iter_landscape_roots():
        loc = land.get_actor_location()
        stats = _proxy_panel_stats_for_landscape(land)
        mat = None
        try:
            m = land.get_editor_property("landscape_material")
            mat = m.get_path_name() if m else None
        except Exception:
            pass
        rows.append(
            {
                "label": land.get_actor_label(),
                "path": land.get_path_name(),
                "location": [float(loc.x), float(loc.y), float(loc.z)],
                "landscape_material": mat,
                "has_panels": stats["landscape_component_count"] > 0,
                **stats,
            }
        )
    return {"landscapes": rows, "count": len(rows)}


def landscape_get_info(label: str = "", actor_path: str = "") -> dict:
    """Inspect one Landscape root (panels live on streaming proxies)."""
    land = None
    for a in _iter_landscape_roots():
        if actor_path and a.get_path_name() == actor_path:
            land = a
            break
        if label and (a.get_actor_label() or "") == label:
            land = a
            break
    if land is None and not label and not actor_path:
        # Default to first
        for a in _iter_landscape_roots():
            land = a
            break
    if land is None:
        raise ValueError(f"Landscape not found (label={label!r} path={actor_path!r})")
    loc = land.get_actor_location()
    scale = land.get_actor_scale3d()
    stats = _proxy_panel_stats_for_landscape(land)
    mat = None
    try:
        m = land.get_editor_property("landscape_material")
        mat = m.get_path_name() if m else None
    except Exception:
        pass
    layers = []
    try:
        layers = [str(x) for x in (land.get_target_layer_names() or [])]
    except Exception:
        pass
    reso = {}
    try:
        reso = _landscape_heightmap_resolution(land)
    except Exception:
        reso = {}
    return {
        "label": land.get_actor_label(),
        "path": land.get_path_name(),
        "location": [float(loc.x), float(loc.y), float(loc.z)],
        "scale": [float(scale.x), float(scale.y), float(scale.z)],
        "landscape_material": mat,
        "target_layers": layers,
        "has_panels": stats["landscape_component_count"] > 0,
        "heightmap_resolution": [reso.get("res_x"), reso.get("res_y")] if reso else None,
        "component_size_quads": reso.get("component_size_quads") if reso else None,
        "note": "ComponentSizeQuads/GridSize exist in T3D but are not Python-editable on this build.",
        **stats,
    }


def landscape_create(
    label: str = "level1 landscape",
    location: Optional[List[float]] = None,
    component_count_xy: int = 8,
    section_size: int = 63,
    scale: float = 100.0,
) -> dict:
    """Attempt to create a real Landscape with panels — reports unavailable on UEFN.

    UEFN Python ``spawn_actor(Landscape)`` yields ``LandscapePlaceholder`` with
    zero ``LandscapeComponent`` panels. The Landscape Mode **Create** button is
    the only path that builds streaming-proxy panels (see landscape_t3d_schema).
    """
    from listener.registry.worldgen import worldgen_capabilities

    caps = worldgen_capabilities()
    location = location or [180000.0, 60000.0, 0.0]
    return {
        "ok": False,
        "landscape_create": caps.get("landscape_create", "unavailable"),
        "requested": {
            "label": label,
            "location": location,
            "component_count_xy": int(component_count_xy),
            "section_size": int(section_size),
            "scale": float(scale),
        },
        "reason": (
            "UEFN does not expose Landscape Editor Create to Python. "
            "spawn_actor(Landscape) creates LandscapePlaceholder (no panels). "
            "T3D shows ComponentSizeQuads/NumSubsections/GridSize on the root, but "
            "those properties are not settable via get/set_editor_property; panels "
            "are LandscapeStreamingProxy actors with LandscapeComponents."
        ),
        "ui_create_steps": [
            "Landscape Mode → Manage → New",
            f"Set Number of Components to {int(component_count_xy)}x{int(component_count_xy)}, Section Size {int(section_size)}x{int(section_size)}",
            "Click Create",
            f"Then call landscape_rename(new_label={label!r})",
        ],
        "ai_create_alternative": {
            "tool": "terrain_generate",
            "why": "GeometryScript mesh heightfield can be created+sculpted fully from Python",
        },
        "landscape_t3d_schema": caps.get("landscape_t3d_schema"),
        "landscape_sculpt_existing": caps.get("landscape_sculpt_existing"),
    }


def landscape_rename(new_label: str, label: str = "", actor_path: str = "") -> dict:
    """Rename a Landscape root actor (after UI Create, or any existing Landscape)."""
    if not (new_label or "").strip():
        raise ValueError("new_label is required")
    info = landscape_get_info(label=label, actor_path=actor_path)
    # Re-find actor
    land = None
    for a in _iter_landscape_roots():
        if a.get_path_name() == info["path"]:
            land = a
            break
    if land is None:
        raise ValueError("Landscape disappeared")
    old = land.get_actor_label()
    land.set_actor_label(new_label.strip())
    return {"ok": True, "old_label": old, "label": land.get_actor_label(), "path": land.get_path_name()}


_WHITE_TEX_PATHS = [
    "/Engine/EngineResources/WhiteSquareTexture.WhiteSquareTexture",
    "/Engine/EditorResources/S_Actor.S_Actor",
]


def _load_white_texture():
    for p in _WHITE_TEX_PATHS:
        try:
            t = unreal.load_asset(p)
            if t is not None:
                return t
        except Exception:
            continue
    return None


def _paint_stamps_on_canvas(canvas, size, stamps):
    """Draw soft height stamps as stacked translucent white/black/gray quads.

    Each stamp is normalized: {type: hill|valley|flatten, cx, cy, radius, strength}.
    cx/cy/radius are 0..1 of the heightmap; strength is 0..1 in normalized height.
    Height lives in the R channel (0.5 == current mid). Translucent stacking builds a
    soft radial dome toward the target color (white=raise, black=lower, gray=flatten).
    """
    # None → Canvas uses its default white texture, which is exactly what we want.
    white = _load_white_texture()
    sx = float(size.x)
    sy = float(size.y)
    rings = 10
    for st in stamps:
        kind = str(st.get("type", "hill")).lower()
        cx = float(st.get("cx", 0.5)) * sx
        cy = float(st.get("cy", 0.5)) * sy
        rad = float(st.get("radius", 0.2)) * min(sx, sy)
        strength = max(0.0, min(1.0, float(st.get("strength", 0.25))))
        if kind == "valley":
            target = 0.0
        elif kind == "flatten":
            target = 0.5
        else:
            target = 1.0
        # Per-ring alpha so overlapping quads asymptote toward the target height.
        for i in range(rings):
            t = i / float(rings - 1) if rings > 1 else 0.0
            r = rad * (1.0 - t * 0.9)
            if r <= 0.5:
                continue
            # Stronger center, soft edge; scaled by requested strength.
            a = strength * (0.10 + 0.30 * (1.0 - t))
            a = max(0.0, min(1.0, a))
            col = unreal.LinearColor(target, target, target, a)
            canvas.draw_texture(
                white,
                unreal.Vector2D(cx - r, cy - r),
                unreal.Vector2D(r * 2.0, r * 2.0),
                unreal.Vector2D(0.0, 0.0),
                unreal.Vector2D(1.0, 1.0),
                col,
                unreal.BlendMode.BLEND_TRANSLUCENT,
                0.0,
                unreal.Vector2D(0.5, 0.5),
            )


#: add/set are refused (verified no-op that holds VRAM); tests flip this to reach them.
_SCULPT_MODES_ENABLED = False


def _release_render_targets(*targets) -> None:
    for rt in targets:
        if rt is None:
            continue
        try:
            unreal.RenderingLibrary.release_render_target2d(rt)
        except Exception as exc:
            try:
                from listener.logutil import log_msg

                log_msg(f"landscape_sculpt: release_render_target2d failed: {exc}", "warn")
            except Exception:
                pass


def landscape_sculpt(
    label: str = "",
    actor_path: str = "",
    mode: str = "add",
    stamps: Optional[List[dict]] = None,
    amplitude: float = 0.25,
    seed: int = 7,
    hill: Optional[dict] = None,
    confirm_overwrite: bool = False,
) -> dict:
    """Import a heightmap into an existing Landscape via RT round-trip.

    ⚠️ VERIFIED UEFN LIMITATION: on this build ``landscape_import_heightmap_from_render_target``
    returns ok=True and the data round-trips through *export* (you can read back what you
    wrote), but it does **NOT** composite into the visible/collision geometry — even after
    ``force_layers_full_update()``. A full white (max-height) import left the terrain
    perfectly flat and proxy bounds Z unchanged. So this tool CANNOT actually reshape a
    UEFN Landscape. For AI-driven sculpted terrain, use ``terrain_generate`` (mesh heightfield).

    Only ``verify`` runs; ``add``/``set`` are refused (no visible effect, and each call
    held up to ~0.5 GB of float render targets). Both targets are released on every path.

    Modes (kept for parity / future engine fixes):
      * ``verify`` — export → re-import unchanged (NOTE: indistinguishable from a no-op).
      * ``add``    — export real height, composite soft stamps, re-import.
      * ``set``    — clear to mid-height, paint stamps, import (full overwrite of the buffer).

    All modes write the heightmap buffer, so ``confirm_overwrite=True`` is required.
    Uses RTF_RGBA32F with height in the R channel at the landscape's auto-detected
    native resolution.
    """
    if mode not in ("verify", "add", "set"):
        raise ValueError("mode must be one of: verify, add, set")
    if mode != "verify" and not _SCULPT_MODES_ENABLED:
        # Each call holds up to ~0.5 GB of float render targets, and the result is
        # invisible, so agents retried it: refuse before anything is allocated.
        return {
            "ok": False,
            "refused": True,
            "mode": mode,
            "error": (
                "landscape_sculpt is a VERIFIED no-op on visible terrain in UEFN: the import "
                "writes a heightmap buffer that export can read back, but the Landscape "
                "geometry stays flat. Do not retry. Use terrain_generate (mesh heightfield) "
                "for AI-sculpted terrain; mode='verify' still runs the round-trip check."
            ),
            "use_instead": "terrain_generate",
        }
    if not confirm_overwrite:
        raise ValueError(
            "landscape_sculpt writes the Landscape heightmap. Pass confirm_overwrite=True "
            "to proceed. Tip: run mode='verify' first — it re-imports the current shape "
            "unchanged to prove the round-trip is lossless before you sculpt."
        )
    info = landscape_get_info(label=label, actor_path=actor_path)
    if not info.get("has_panels"):
        raise ValueError(
            f"Landscape {info.get('label')!r} has 0 panels — cannot sculpt. "
            "Create panels with Landscape Mode → Create first."
        )
    land = None
    for a in _iter_landscape_roots():
        if a.get_path_name() == info["path"]:
            land = a
            break
    if land is None:
        raise ValueError("Landscape not found")

    reso = _landscape_heightmap_resolution(land)
    if not reso:
        raise ValueError("could not determine landscape heightmap resolution")
    res_x = int(reso["res_x"])
    res_y = int(reso["res_y"])

    # Legacy single-hill arg → one stamp.
    if stamps is None:
        if hill is not None:
            stamps = [
                {
                    "type": "hill",
                    "cx": float(hill.get("cx", 0.5)),
                    "cy": float(hill.get("cy", 0.5)),
                    "radius": float(hill.get("radius", 0.25)),
                    "strength": float(hill.get("height", amplitude)),
                }
            ]
        else:
            stamps = [{"type": "hill", "cx": 0.5, "cy": 0.5, "radius": 0.25, "strength": amplitude}]

    world = unreal.EditorLevelLibrary.get_editor_world()
    fmt = unreal.TextureRenderTargetFormat.RTF_RGBA32F

    # 16 bytes a pixel at native size (~260 MB at 4033^2): release both targets on
    # every path instead of waiting for a garbage collection to find them.
    rt_src = None
    work = None
    try:
        rt_src = unreal.RenderingLibrary.create_render_target2d(world, res_x, res_y, fmt)
        # Height is internally stored as R & G; export into a single R channel (needs float RT).
        exported = bool(land.landscape_export_heightmap_to_render_target(rt_src, True, True))
        if not exported:
            return {"ok": False, "error": "export_heightmap returned False", "path": info["path"]}

        if mode == "verify":
            target_rt = rt_src
        else:
            work = unreal.RenderingLibrary.create_render_target2d(world, res_x, res_y, fmt)
            ctx = unreal.RenderingLibrary.begin_draw_canvas_to_render_target(world, work)
            canvas, size, draw_ctx = ctx[0], ctx[1], ctx[2]
            try:
                if mode == "add":
                    # Copy real height in first (opaque), so stamps only alter their footprint.
                    canvas.draw_texture(
                        rt_src,
                        unreal.Vector2D(0.0, 0.0),
                        unreal.Vector2D(float(res_x), float(res_y)),
                        unreal.Vector2D(0.0, 0.0),
                        unreal.Vector2D(1.0, 1.0),
                        unreal.LinearColor(1.0, 1.0, 1.0, 1.0),
                        unreal.BlendMode.BLEND_OPAQUE,
                        0.0,
                        unreal.Vector2D(0.5, 0.5),
                    )
                else:
                    # set: flat mid-height base, then stamps define the whole shape.
                    canvas.draw_texture(
                        _load_white_texture(),
                        unreal.Vector2D(0.0, 0.0),
                        unreal.Vector2D(float(res_x), float(res_y)),
                        unreal.Vector2D(0.0, 0.0),
                        unreal.Vector2D(1.0, 1.0),
                        unreal.LinearColor(0.5, 0.5, 0.5, 1.0),
                        unreal.BlendMode.BLEND_OPAQUE,
                        0.0,
                        unreal.Vector2D(0.5, 0.5),
                    )
                _paint_stamps_on_canvas(canvas, size, stamps)
            finally:
                unreal.RenderingLibrary.end_draw_canvas_to_render_target(world, draw_ctx)
            target_rt = work

        # Height was exported into R only → import reads from R only (rg flag False).
        ok = bool(land.landscape_import_heightmap_from_render_target(target_rt, False, 0))
    except Exception as e:
        return {"ok": False, "error": str(e), "path": info["path"]}
    finally:
        target_rt = None
        _release_render_targets(work, rt_src)
        work = None
        rt_src = None
        try:
            from listener import render_trim

            render_trim.request_trim()  # the canvas draw went through the RT pool
        except Exception:
            pass

    return {
        "ok": ok,
        "wrote_heightmap_buffer": ok,
        "changed_visible_terrain": False,
        "label": info["label"],
        "path": info["path"],
        "mode": mode,
        "resolution": [res_x, res_y],
        "stamps": stamps if mode != "verify" else [],
        "seed": int(seed),
        "warning": (
            "UEFN no-op: import writes the heightmap buffer (export reads it back) but does "
            "NOT reshape the visible/collision Landscape geometry. Verified with a full "
            "white import (terrain stayed flat). Use terrain_generate for real AI terrain."
        ),
    }


register("landscape_list")(landscape_list)
register("landscape_get_info")(landscape_get_info)
register("landscape_create")(landscape_create)
register("landscape_rename")(landscape_rename)
register("landscape_sculpt")(landscape_sculpt)
