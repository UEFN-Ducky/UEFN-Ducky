"""Content browser / asset registry."""

from typing import List, Optional

import unreal

from listener.asset_resolve import resolve_asset_class
from listener.dispatch import register
from listener.project_paths import pin_project_asset_path
from listener.serialize import serialize, serialize_asset_entry

# Bounds for search_assets so it never scans every asset in /Game/ (that froze/timed
# out on large projects). Agents paginate, so we only need the current page.
_SEARCH_SCAN_CAP = 20000  # max registry lookups per call
_SEARCH_RESULT_CAP = 200  # max matches gathered when the caller passes no limit


@register("list_assets")
def cmd_list_assets(
    directory: str = "/Game/",
    recursive: bool = True,
    class_filter: str = "",
    offset: int = 0,
    limit: Optional[int] = None,
    fields: Optional[List[str]] = None,
) -> dict:
    truncated = False
    if class_filter:
        # Registry-indexed class lookup instead of listing every asset then
        # calling find_asset_data per path (each a separate registry hit).
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
        ar_filter = unreal.ARFilter(
            package_paths=[directory],
            recursive_paths=recursive,
            class_names=[class_filter],
        )
        found = registry.get_assets(ar_filter) or []
        truncated = len(found) > _SEARCH_SCAN_CAP
        assets = [f"{ad.package_name}.{ad.asset_name}" for ad in found[:_SEARCH_SCAN_CAP]]
    else:
        assets = [str(a) for a in unreal.EditorAssetLibrary.list_assets(directory, recursive=recursive)]
    total = len(assets)
    if offset > 0:
        assets = assets[offset:]
    if limit is not None and limit >= 0:
        assets = assets[:limit]
    if fields:
        serialized = [serialize_asset_entry(p, fields) for p in assets]
    else:
        serialized = assets
    return {
        "assets": serialized,
        "count": len(assets),
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": truncated,
    }


@register("get_asset_info")
def cmd_get_asset_info(asset_path: str) -> dict:
    data = unreal.EditorAssetLibrary.find_asset_data(asset_path)
    if data is None:
        raise ValueError(f"Asset not found: {asset_path}")
    asset = serialize(data)
    if isinstance(asset, dict):
        cls = str(asset.get("asset_class") or "")
        resolved_cls, dest_path = resolve_asset_class(asset_path, cls)
        if resolved_cls and resolved_cls != cls:
            asset["asset_class"] = resolved_cls
            asset["redirector"] = True
            if dest_path:
                asset["destination_path"] = dest_path
    return {"asset": asset}


@register("get_selected_assets")
def cmd_get_selected_assets() -> dict:
    selected = unreal.EditorUtilityLibrary.get_selected_assets()
    return {
        "assets": [serialize(a) for a in selected],
        "count": len(selected),
    }


@register("rename_asset")
def cmd_rename_asset(old_path: str, new_path: str) -> dict:
    # Never move/rename into invented /Game/... — pin destination to content_root.
    new_path = pin_project_asset_path(new_path, default_leaf="Assets")
    success = unreal.EditorAssetLibrary.rename_asset(old_path, new_path)
    return {"success": success, "old_path": old_path, "new_path": new_path}


@register("delete_asset")
def cmd_delete_asset(asset_path: str) -> dict:
    success = unreal.EditorAssetLibrary.delete_asset(asset_path)
    return {"success": success, "asset_path": asset_path}


@register("duplicate_asset")
def cmd_duplicate_asset(source_path: str, dest_path: str) -> dict:
    dest_path = pin_project_asset_path(dest_path, default_leaf="Assets")
    result = unreal.EditorAssetLibrary.duplicate_asset(source_path, dest_path)
    return {"success": result is not None, "source": source_path, "dest": dest_path}


@register("does_asset_exist")
def cmd_does_asset_exist(asset_path: str) -> dict:
    exists = unreal.EditorAssetLibrary.does_asset_exist(asset_path)
    return {"exists": exists, "asset_path": asset_path}


@register("save_asset")
def cmd_save_asset(asset_path: str) -> dict:
    success = unreal.EditorAssetLibrary.save_asset(asset_path)
    return {"success": success, "asset_path": asset_path}


@register("open_asset_in_uefn")
def cmd_open_asset_in_uefn(asset_path: str, open_editor: bool = True) -> dict:
    """Reveal an asset in the Content Browser and (optionally) open its editor."""
    if not unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        raise ValueError(f"Asset not found: {asset_path}")
    revealed = False
    try:
        unreal.EditorAssetLibrary.sync_browser_to_objects([asset_path])
        revealed = True
    except Exception:
        revealed = False
    opened = False
    if open_editor:
        asset = unreal.EditorAssetLibrary.load_asset(asset_path)
        if asset is not None:
            try:
                subsystem = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem)
                subsystem.open_editor_for_assets([asset])
                opened = True
            except Exception:
                opened = False
    return {"success": True, "asset_path": asset_path, "revealed": revealed, "opened": opened}


@register("search_assets")
def cmd_search_assets(
    class_name: str = "",
    directory: str = "/Game/",
    recursive: bool = True,
    offset: int = 0,
    limit: Optional[int] = None,
    fields: Optional[List[str]] = None,
    search: str = "",
) -> dict:
    assets = unreal.EditorAssetLibrary.list_assets(directory, recursive=recursive)
    needle = (search or class_name or "").strip().lower()
    # Stop once we have enough for the requested page, or after a hard scan cap.
    want = (offset + limit) if (limit is not None and limit >= 0) else _SEARCH_RESULT_CAP
    results = []
    scanned = 0
    truncated = False
    for asset_path in assets:
        if len(results) >= want or scanned >= _SEARCH_SCAN_CAP:
            truncated = True
            break
        scanned += 1
        path_str = str(asset_path)
        data = unreal.EditorAssetLibrary.find_asset_data(path_str)
        if data is None:
            continue
        if class_name:
            cls = str(data.asset_class_path.asset_name) if hasattr(data, "asset_class_path") else str(getattr(data, "asset_class", ""))
            if cls != class_name:
                continue
        if search and needle not in path_str.lower():
            asset_name = str(getattr(data, "asset_name", ""))
            if needle not in asset_name.lower():
                continue
        if fields:
            results.append(serialize_asset_entry(path_str, fields))
        else:
            results.append(serialize(data))
    total = len(results)
    if offset > 0:
        results = results[offset:]
    if limit is not None and limit >= 0:
        results = results[:limit]
    return {
        "assets": results,
        "count": len(results),
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": truncated,
        "scanned": scanned,
    }
