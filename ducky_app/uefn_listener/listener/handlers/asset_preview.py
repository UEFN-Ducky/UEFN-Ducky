"""Asset preview for the Ducky panel — export thumbnails and metadata."""

from __future__ import annotations

import os
import re
from typing import Any, Optional

import unreal

from listener.asset_resolve import resolve_asset_class
from listener.dispatch import register
from listener.serialize import serialize


_PREVIEW_DIR_NAME = "DuckyPreviews"


def _preview_dir() -> str:
    path = os.path.join(str(unreal.Paths.project_saved_dir()), _PREVIEW_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _asset_class_name(data: unreal.AssetData) -> str:
    if hasattr(data, "asset_class_path"):
        return str(data.asset_class_path.asset_name)
    return str(getattr(data, "asset_class", ""))


def _is_verse_asset(asset_path: str, asset_class: str) -> bool:
    if "/_Verse" in asset_path.replace("\\", "/"):
        return True
    return asset_class.startswith("Verse-") or asset_class.startswith("Verse_")


def _verse_class_stem(asset_path: str, asset_class: str) -> str:
    base = asset_path.rsplit("/", 1)[-1]
    if "." in base:
        base = base.split(".", 1)[0]
    if asset_class.startswith("Verse-"):
        return asset_class.split("-", 1)[-1]
    if asset_class.startswith("Verse_"):
        return asset_class.split("_", 1)[-1]
    m = re.search(r"Verse[-_](.+)$", base, re.I)
    return m.group(1) if m else base


def _try_export_png(asset_path: str, out_dir: str) -> Optional[str]:
    """Export asset to out_dir; return first PNG/TGA/BMP path if any."""
    try:
        unreal.AssetToolsHelpers.get_asset_tools().export_assets([asset_path], out_dir)
    except Exception:
        return None
    stem = asset_path.rsplit("/", 1)[-1].split(".", 1)[0]
    for name in (f"{stem}.png", f"{stem}.tga", f"{stem}.bmp"):
        candidate = os.path.join(out_dir, name)
        if os.path.isfile(candidate):
            return candidate
    try:
        for root, _dirs, files in os.walk(out_dir):
            for fn in files:
                lower = fn.lower()
                if lower.endswith((".png", ".tga", ".bmp")) and stem.lower() in lower:
                    return os.path.join(root, fn)
    except OSError:
        pass
    return None


def _try_export_texture(asset_path: str, out_dir: str) -> Optional[str]:
    """Export a Texture2D via TextureExporterPNG (generic export_assets nests oddly)."""
    from listener.asset_resolve import load_asset_resolved

    tex, resolved = load_asset_resolved(asset_path)
    if tex is None:
        return None
    stem = (resolved or asset_path).rsplit("/", 1)[-1].split(".", 1)[0] or "texture"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{stem}.png")

    exporter = None
    for cls_name in ("TextureExporterPNG", "TextureExporterTGA", "TextureExporterBMP"):
        cls = getattr(unreal, cls_name, None)
        if cls is None:
            continue
        try:
            exporter = cls()
            if "TGA" in cls_name:
                out_path = os.path.join(out_dir, f"{stem}.tga")
            elif "BMP" in cls_name:
                out_path = os.path.join(out_dir, f"{stem}.bmp")
            break
        except Exception:
            exporter = None
    if exporter is None:
        return _try_export_png(resolved or asset_path, out_dir)

    task = unreal.AssetExportTask()
    task.object = tex
    task.filename = out_path
    task.selected = False
    task.replace_identical = True
    task.prompt = False
    task.automated = True
    task.exporter = exporter
    ok = False
    try:
        ok = bool(unreal.Exporter.run_asset_export_task(task))
    except Exception:
        ok = False
    if ok and os.path.isfile(out_path):
        return out_path
    return _try_export_png(resolved or asset_path, out_dir)


def _editor_world():
    try:
        return unreal.EditorLevelLibrary.get_editor_world()
    except Exception:
        return None


def _try_export_material(asset_path: str, out_dir: str, size: int = 256) -> Optional[str]:
    """Draw a material onto a transient render target and export PNG."""
    from listener.asset_resolve import load_asset_resolved

    mat, resolved = load_asset_resolved(asset_path)
    if mat is None:
        return None
    world = _editor_world()
    if world is None:
        return None

    stem = (resolved or asset_path).rsplit("/", 1)[-1].split(".", 1)[0] or "material"
    os.makedirs(out_dir, exist_ok=True)
    filename = f"{stem}.png"
    out_path = os.path.join(out_dir, filename)
    dim = max(64, min(int(size or 256), 1024))

    rt = None
    try:
        rt = unreal.TextureRenderTarget2D()
        try:
            rt.set_editor_property("size_x", dim)
            rt.set_editor_property("size_y", dim)
        except Exception:
            pass
        try:
            rt.set_editor_property("render_target_format", unreal.TextureRenderTargetFormat.RTF_RGBA8)
        except Exception:
            pass
        unreal.RenderingLibrary.clear_render_target2d(
            world, rt, unreal.LinearColor(0.08, 0.08, 0.1, 1.0)
        )
        unreal.RenderingLibrary.draw_material_to_render_target(world, rt, mat)
        unreal.RenderingLibrary.export_render_target(world, rt, out_dir, filename)
    except Exception:
        out_path = ""
    finally:
        if rt is not None:
            try:
                unreal.RenderingLibrary.release_render_target2d(rt)
            except Exception:
                pass

    if out_path and os.path.isfile(out_path):
        return out_path
    return None


def _try_badge_png(label: str, out_dir: str, asset_path: str) -> Optional[str]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    out_path = os.path.join(out_dir, f"badge_{abs(hash(asset_path)) % 10**8}.png")
    size = 64
    img = Image.new("RGBA", (size, size), (40, 44, 52, 255))
    draw = ImageDraw.Draw(img)
    hue = abs(hash(label)) % 360
    draw.rectangle([4, 4, size - 5, size - 5], fill=(50 + hue % 80, 80 + hue % 60, 120 + hue % 50, 255))
    text = label[:6].upper()
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    draw.text((8, 22), text, fill=(240, 240, 240, 255), font=font)
    try:
        img.save(out_path, format="PNG")
        return out_path
    except OSError:
        return None


def _texture_metadata(asset_path: str) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    from listener.asset_resolve import load_asset_resolved

    tex, _ = load_asset_resolved(asset_path)
    if tex is None:
        return extra
    for getter, key in (("blueprint_get_size_x", "size_x"), ("blueprint_get_size_y", "size_y")):
        try:
            if hasattr(tex, getter):
                extra[key] = int(getattr(tex, getter)())
        except Exception:
            pass
    if "size_x" not in extra or "size_y" not in extra:
        try:
            extra["size_x"] = int(tex.get_editor_property("source_size_x"))
            extra["size_y"] = int(tex.get_editor_property("source_size_y"))
        except Exception:
            pass
    return extra


def _static_mesh_metadata(asset_path: str) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    from listener.asset_resolve import load_asset_resolved

    mesh, _ = load_asset_resolved(asset_path)
    if mesh is None:
        return extra
    try:
        body = mesh.get_editor_property("body_setup")
        if body is not None:
            extra["collision_primitives"] = len(body.aggregate_geom.sphere_elems) + len(
                body.aggregate_geom.box_elems
            )
    except Exception:
        pass
    try:
        extra["material_slots"] = len(mesh.get_editor_property("static_materials"))
    except Exception:
        pass
    return extra


@register("preview_asset")
def cmd_preview_asset(asset_path: str, max_size: int = 256) -> dict:
    """Generate a preview image + metadata for a content-browser asset path."""
    data = unreal.EditorAssetLibrary.find_asset_data(asset_path)
    if data is None:
        raise ValueError(f"Asset not found: {asset_path}")

    asset_class = _asset_class_name(data)
    resolved_cls, dest_path = resolve_asset_class(asset_path, asset_class)
    preview_path = dest_path or asset_path
    if resolved_cls:
        asset_class = resolved_cls
    metadata: dict[str, Any] = dict(serialize(data))
    if dest_path and dest_path != asset_path:
        metadata["destination_path"] = dest_path
        metadata["followed_redirector"] = True
    out_dir = _preview_dir()
    preview_file: Optional[str] = None
    preview_mode = "metadata"
    verse_class_stem: Optional[str] = None
    thumb_size = max(64, min(int(max_size or 256), 1024))

    if asset_class == "Texture2D" or asset_class.startswith("Texture"):
        preview_file = _try_export_texture(preview_path, out_dir)
        if preview_file:
            preview_mode = "image"
        metadata.update(_texture_metadata(preview_path))
    elif asset_class in ("Material", "MaterialInstanceConstant", "MaterialInstance"):
        preview_file = _try_export_material(preview_path, out_dir, size=thumb_size)
        if not preview_file:
            preview_file = _try_export_png(preview_path, out_dir)
        if preview_file:
            preview_mode = "image"
        else:
            preview_mode = "badge"
            preview_file = _try_badge_png(asset_class[:8] or "MAT", out_dir, asset_path)
    elif asset_class == "StaticMesh":
        preview_file = _try_export_png(preview_path, out_dir)
        preview_mode = "image" if preview_file else "badge"
        metadata.update(_static_mesh_metadata(preview_path))
    elif _is_verse_asset(asset_path, asset_class):
        verse_class_stem = _verse_class_stem(asset_path, asset_class)
        preview_mode = "verse"
        preview_file = _try_badge_png(verse_class_stem or "Verse", out_dir, asset_path)
        if not preview_file:
            preview_mode = "badge"
    elif asset_path.lower().endswith(".umap") or asset_class == "World":
        preview_mode = "badge"
        preview_file = _try_badge_png("MAP", out_dir, asset_path)
    else:
        preview_mode = "badge"
        preview_file = _try_badge_png(asset_class[:8] or "ASSET", out_dir, asset_path)

    if preview_mode == "badge" and not preview_file:
        preview_file = _try_badge_png(asset_class[:8] or "ASSET", out_dir, asset_path)

    result: dict[str, Any] = {
        "asset_path": preview_path,
        "asset_class": asset_class,
        "preview_mode": preview_mode,
        "preview_file": preview_file,
        "metadata": metadata,
    }
    if dest_path and dest_path != asset_path:
        result["requested_path"] = asset_path
        result["followed_redirector"] = True
    if verse_class_stem:
        result["verse_class_stem"] = verse_class_stem
    return result
