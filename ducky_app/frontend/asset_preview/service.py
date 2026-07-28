"""Orchestrate asset previews: cache, listener, offline fallbacks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from frontend.asset_preview.badge import make_badge_png
from frontend.asset_preview.cache import get_cached, get_latest_cached, put_cached, record_to_dict
from frontend.asset_preview import mesh_cache
from frontend.asset_preview.kinds import (
    clean_listener_error,
    guess_preview_kind,
    supports_material_preview,
    supports_mesh_preview,
    supports_texture_preview,
)
from frontend.asset_preview.paths import disk_path_to_asset_path, is_previewable_binary
from frontend.asset_preview.verse_link import find_verse_source, stem_from_relative_path
from frontend.settings import PANEL_LISTENER_PORT
from frontend.ui_web import project_files as pf


def _listener_online() -> bool:
    try:
        from backend.bridge import post_command_to_listener

        res = post_command_to_listener(PANEL_LISTENER_PORT, "ping", {}, timeout=2.0)
        return str(res.get("status", "")).lower() == "ok"
    except Exception:
        return False


def _content_root() -> str:
    try:
        from backend.bridge import post_command_to_listener

        res = post_command_to_listener(PANEL_LISTENER_PORT, "get_project_info", {}, timeout=4.0)
        root = str(res.get("content_root") or "").strip()
        return root or "/Game/"
    except Exception:
        return "/Game/"


def _fetch_asset_class(asset_path: str) -> str:
    """One lightweight registry lookup — no export, no load of heavy assets."""
    try:
        from backend.bridge import post_command_to_listener

        res = post_command_to_listener(
            PANEL_LISTENER_PORT,
            "get_asset_info",
            {"asset_path": asset_path},
            timeout=8.0,
        )
        asset = res.get("asset") if isinstance(res, dict) else None
        if isinstance(asset, dict):
            return str(asset.get("asset_class") or "").strip()
    except Exception:
        return ""
    return ""


def _annotate_preview(result: dict[str, Any], relative_path: str) -> dict[str, Any]:
    asset_class = str(result.get("asset_class") or "")
    kind = guess_preview_kind(relative_path, asset_class)
    result["preview_kind"] = kind
    result["asset_class"] = asset_class or result.get("asset_class") or ""
    result["supports_mesh_preview"] = kind == "static_mesh"
    result["supports_material_preview"] = kind == "material"
    result["supports_texture_preview"] = kind == "texture"
    return result


def _local_result(
    relative_path: str,
    project_root: str,
    *,
    online: bool,
    asset_class: str = "",
) -> dict[str, Any]:
    """Build a preview record locally, WITHOUT pulling/exporting from UEFN.

    We deliberately never ask the listener to render a thumbnail automatically.
    """
    verse_stem = stem_from_relative_path(relative_path) or ""
    verse_source = find_verse_source(project_root, relative_path=relative_path, verse_class_stem=verse_stem)
    mode = "verse" if verse_source or verse_stem else "asset"
    content_root = _content_root() if online else "/Game/"
    asset_path = disk_path_to_asset_path(relative_path, content_root)
    badge_label = verse_stem or Path(relative_path).stem[:8] or "asset"
    stat = pf.stat_project_file(relative_path)
    mtime_ns = int(stat.get("mtime_ns") or 0)
    size = int(stat.get("size") or 0)
    resolved_class = asset_class or ("Verse" if verse_stem else "")
    if online and not resolved_class:
        resolved_class = _fetch_asset_class(asset_path)
    preview_id = put_cached(
        relative_path,
        mtime_ns,
        size,
        make_badge_png(badge_label),
        mode=mode,
        asset_class=resolved_class,
        asset_path=asset_path,
        metadata={},
        verse_source=verse_source,
    )
    from frontend.asset_preview.cache import preview_url

    result: dict[str, Any] = {
        "mode": mode,
        "asset_class": resolved_class,
        "asset_path": asset_path,
        "preview_url": preview_url(preview_id),
        "preview_id": preview_id,
        "verse_source": verse_source,
        "metadata": {},
        "listener_online": online,
    }
    if not online:
        result["fallback"] = "hex"
    return _annotate_preview(result, relative_path)


def preview_project_asset(relative_path: str) -> dict[str, Any]:
    rel = (relative_path or "").strip().replace("\\", "/")
    if not is_previewable_binary(rel):
        raise ValueError(f"Not a previewable binary asset: {relative_path}")

    stat = pf.stat_project_file(rel)
    if not stat.get("exists"):
        raise ValueError(f"Not a file: {relative_path}")
    mtime_ns = int(stat.get("mtime_ns") or 0)
    size = int(stat.get("size") or 0)

    cached = get_cached(rel, mtime_ns, size)
    online = _listener_online()
    if cached is not None:
        out = record_to_dict(cached, listener_online=online)
        asset_class = str(out.get("asset_class") or "")
        # Older cache entries may lack class — enrich once while online (no re-export).
        # Also refresh ObjectRedirector: registry class is a stub; follow destination.
        if online and (not asset_class or asset_class.lower() == "objectredirector"):
            asset_path = str(out.get("asset_path") or disk_path_to_asset_path(rel, _content_root()))
            asset_class = _fetch_asset_class(asset_path)
            if asset_class:
                out["asset_class"] = asset_class
        return _annotate_preview(out, rel)

    # Offline (or exact-key miss): prefer last real preview over a badge.
    latest = get_latest_cached(rel)
    if latest is not None and latest.mode in {"texture", "material", "image"}:
        out = record_to_dict(latest, listener_online=online, stale=True)
        if not online:
            out["fallback"] = "hex"
        return _annotate_preview(out, rel)

    project_root = str(pf._project_root().resolve())  # noqa: SLF001
    return _local_result(rel, project_root, online=online)


def open_asset_in_uefn(relative_path: str) -> dict[str, Any]:
    """Reveal an asset in UEFN's Content Browser and open its editor."""
    rel = (relative_path or "").strip().replace("\\", "/")
    if not _listener_online():
        return {"ok": False, "error": "UEFN is not running — start UEFN to open assets."}
    asset_path = disk_path_to_asset_path(rel, _content_root())
    try:
        from backend.bridge import post_command_to_listener

        res = post_command_to_listener(
            PANEL_LISTENER_PORT,
            "open_asset_in_uefn",
            {"asset_path": asset_path},
            timeout=15.0,
        )
    except Exception as exc:
        return {"ok": False, "error": clean_listener_error(str(exc)), "asset_path": asset_path}
    ok = bool(res.get("success", True)) if isinstance(res, dict) else True
    opened = bool(res.get("opened")) if isinstance(res, dict) else None
    return {"ok": ok, "asset_path": asset_path, "opened": opened}


def load_static_mesh_preview(relative_path: str) -> dict[str, Any]:
    """On-demand StaticMesh → FBX export for the in-panel 3D viewer."""
    rel = (relative_path or "").strip().replace("\\", "/")
    if not rel.lower().endswith(".uasset"):
        return {"ok": False, "error": "Only .uasset static meshes can be 3D-previewed."}

    if not supports_mesh_preview(rel):
        # Path heuristic reject before hitting UEFN (Materials/, Niagara/, etc.).
        kind = guess_preview_kind(rel)
        return {
            "ok": False,
            "error": f"This looks like a {kind.replace('_', ' ')} asset, not a StaticMesh.",
            "preview_kind": kind,
        }

    stat = pf.stat_project_file(rel)
    if not stat.get("exists"):
        return {"ok": False, "error": f"Not a file: {relative_path}"}
    mtime_ns = int(stat.get("mtime_ns") or 0)
    size = int(stat.get("size") or 0)

    cached = mesh_cache.get_cached_mesh(rel, mtime_ns, size)
    online = _listener_online()
    if cached is not None:
        return mesh_cache.record_to_dict(cached, listener_online=online)

    if not online:
        latest = mesh_cache.get_latest_cached_mesh(rel)
        if latest is not None:
            return mesh_cache.record_to_dict(latest, listener_online=False, stale=True)
        return {
            "ok": False,
            "error": "UEFN is offline — start UEFN to load a 3D preview.",
            "listener_online": False,
        }

    asset_path = disk_path_to_asset_path(rel, _content_root())
    asset_class = _fetch_asset_class(asset_path)
    # ObjectRedirector means the package was moved/renamed — let the listener
    # follow destination_object instead of rejecting by registry class.
    if (
        asset_class
        and asset_class.lower() != "objectredirector"
        and not supports_mesh_preview(rel, asset_class)
    ):
        kind = guess_preview_kind(rel, asset_class)
        return {
            "ok": False,
            "error": f"Not a StaticMesh (got {asset_class}).",
            "asset_class": asset_class,
            "preview_kind": kind,
        }

    cache_id = mesh_cache.cache_key(rel, mtime_ns, size)
    is_leader, event = mesh_cache.begin_inflight(cache_id)
    if not is_leader:
        try:
            return mesh_cache.wait_inflight(cache_id, event)
        except Exception as exc:
            return {"ok": False, "error": clean_listener_error(str(exc)), "listener_online": True}

    out_dir = mesh_cache.cache_dir_for_id(cache_id, for_write=True)
    try:
        from backend.bridge import post_command_to_listener

        payload = post_command_to_listener(
            PANEL_LISTENER_PORT,
            "preview_static_mesh",
            {
                "asset_path": asset_path,
                "output_directory": str(out_dir),
                "filename": "model.fbx",
            },
            timeout=120.0,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected listener response for mesh preview")

        exported = str(payload.get("exported_file") or "")
        filename = str(payload.get("filename") or "model.fbx")
        metadata = dict(payload.get("metadata") or {})
        if not exported:
            candidate = out_dir / "model.fbx"
            if candidate.is_file():
                exported = str(candidate)
            else:
                raise RuntimeError("Mesh export returned no file")

        record = mesh_cache.put_cached_mesh(
            rel,
            mtime_ns,
            size,
            asset_path=str(payload.get("asset_path") or asset_path),
            exported_file=exported,
            filename=filename,
            metadata=metadata,
            siblings_dir=str(payload.get("output_directory") or out_dir),
        )
        result = mesh_cache.record_to_dict(record, listener_online=True)
        mesh_cache.finish_inflight(cache_id, result=result)
        return result
    except Exception as exc:
        mesh_cache.finish_inflight(cache_id, error=exc)
        return {
            "ok": False,
            "error": clean_listener_error(str(exc)),
            "asset_path": asset_path,
            "listener_online": True,
        }


def load_material_preview(relative_path: str) -> dict[str, Any]:
    """On-demand material thumbnail from UEFN (one export, AppData-cached)."""
    return _load_uefn_image_preview(relative_path, expected="material")


def load_texture_preview(relative_path: str) -> dict[str, Any]:
    """On-demand Texture2D PNG from UEFN (one export, AppData-cached per project)."""
    return _load_uefn_image_preview(relative_path, expected="texture")


def _load_uefn_image_preview(relative_path: str, *, expected: str) -> dict[str, Any]:
    """Shared material/texture thumbnail path via listener ``preview_asset``."""
    rel = (relative_path or "").strip().replace("\\", "/")
    supports = supports_material_preview if expected == "material" else supports_texture_preview
    label = "Material" if expected == "material" else "Texture"
    if not supports(rel):
        kind = guess_preview_kind(rel)
        return {
            "ok": False,
            "error": f"{label} preview is for {expected}s only (this looks like {kind.replace('_', ' ')}).",
            "preview_kind": kind,
        }

    stat = pf.stat_project_file(rel)
    if not stat.get("exists"):
        return {"ok": False, "error": f"Not a file: {relative_path}"}
    mtime_ns = int(stat.get("mtime_ns") or 0)
    size = int(stat.get("size") or 0)

    # Exact-key hit: skip re-export (online or offline).
    cached = get_cached(rel, mtime_ns, size)
    online = _listener_online()
    if cached is not None and cached.mode == expected:
        out = record_to_dict(cached, listener_online=online)
        out["ok"] = True
        out["from_cache"] = True
        return _annotate_preview(out, rel)

    if not online:
        latest = get_latest_cached(rel)
        if latest is not None and latest.mode == expected:
            out = record_to_dict(latest, listener_online=False, stale=True)
            out["ok"] = True
            out["from_cache"] = True
            return _annotate_preview(out, rel)
        return {
            "ok": False,
            "error": f"UEFN is offline — start UEFN to preview {expected}s.",
            "listener_online": False,
        }

    asset_path = disk_path_to_asset_path(rel, _content_root())
    asset_class = _fetch_asset_class(asset_path)
    if (
        asset_class
        and asset_class.lower() != "objectredirector"
        and not supports(rel, asset_class)
    ):
        return {
            "ok": False,
            "error": f"Not a {label} (got {asset_class}).",
            "asset_class": asset_class,
            "preview_kind": guess_preview_kind(rel, asset_class),
        }

    try:
        from backend.bridge import post_command_to_listener

        payload = post_command_to_listener(
            PANEL_LISTENER_PORT,
            "preview_asset",
            {"asset_path": asset_path},
            timeout=60.0,
        )
    except Exception as exc:
        return {"ok": False, "error": clean_listener_error(str(exc)), "asset_path": asset_path}

    preview_file = str(payload.get("preview_file") or "") if isinstance(payload, dict) else ""
    if not preview_file or not os.path.isfile(preview_file):
        return {
            "ok": False,
            "error": f"UEFN did not return a {expected} preview image. Use Open in UEFN.",
            "asset_path": asset_path,
            "asset_class": asset_class or (payload.get("asset_class") if isinstance(payload, dict) else ""),
        }

    # Convert TGA/BMP exports to PNG when needed so the HTTP preview path stays .png.
    preview_path = Path(preview_file)
    suffix = preview_path.suffix.lower()
    if suffix == ".png":
        png_bytes = preview_path.read_bytes()
    else:
        try:
            from PIL import Image

            with Image.open(preview_path) as img:
                import io

                buf = io.BytesIO()
                img.convert("RGBA").save(buf, format="PNG")
                png_bytes = buf.getvalue()
        except Exception:
            png_bytes = preview_path.read_bytes()

    meta = dict(payload.get("metadata") or {}) if isinstance(payload, dict) else {}
    cls = asset_class or str(payload.get("asset_class") or label)
    mode = expected
    preview_id = put_cached(
        rel,
        mtime_ns,
        size,
        png_bytes,
        mode=mode,
        asset_class=cls,
        asset_path=asset_path,
        metadata=meta,
    )
    from frontend.asset_preview.cache import preview_url

    return _annotate_preview(
        {
            "ok": True,
            "mode": mode,
            "asset_class": cls,
            "asset_path": asset_path,
            "preview_url": preview_url(preview_id),
            "preview_id": preview_id,
            "metadata": meta,
            "listener_online": True,
            "from_uefn": True,
        },
        rel,
    )
