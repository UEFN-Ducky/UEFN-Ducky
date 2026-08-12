"""Content browser / asset registry."""

from typing import Any, List, Optional

import unreal

from listener.asset_resolve import resolve_asset_class
from listener.dispatch import register
from listener.project_paths import pin_project_asset_path
from listener.serialize import serialize

# Bounds so a bad call never walks the entire Fortnite install.
_SEARCH_SCAN_CAP = 20000  # max AssetData rows considered per call
_SEARCH_RESULT_CAP = 200  # max matches gathered when the caller passes no limit


def _asset_path_from_data(ad: Any) -> str:
    return f"{ad.package_name}.{ad.asset_name}"


def _normalize_registry_dir(directory: str) -> str:
    """ARFilter package_paths want '/Game' not '/Game/' — trailing slash matches nothing."""
    path = (directory or "").strip().rstrip("/")
    return path or "/Game"


def _registry_assets(directory: str, recursive: bool, class_name: str = "") -> list:
    """Single AssetRegistry query — never list_assets + per-path find_asset_data."""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    package_path = _normalize_registry_dir(directory)
    kwargs: dict[str, Any] = {
        "package_paths": [package_path],
        "recursive_paths": recursive,
    }
    if class_name:
        kwargs["class_names"] = [class_name]
    ar_filter = unreal.ARFilter(**kwargs)
    found = list(registry.get_assets(ar_filter) or [])
    # Registry order is unspecified — sort so offset pagination is stable.
    found.sort(
        key=lambda ad: (
            str(getattr(ad, "package_name", "") or ""),
            str(getattr(ad, "asset_name", "") or ""),
        )
    )
    return found


def _serialize_from_data(ad: Any, path_str: str, fields: Optional[List[str]]) -> Any:
    if not fields or fields == ["path"]:
        return path_str
    full = serialize(ad)
    if not isinstance(full, dict):
        return {"path": path_str}
    out: dict = {"path": path_str}
    for field in fields:
        if field in full:
            out[field] = full[field]
    return out


@register("list_assets")
def cmd_list_assets(
    directory: str = "/Game/",
    recursive: bool = True,
    class_filter: str = "",
    offset: int = 0,
    limit: Optional[int] = None,
    fields: Optional[List[str]] = None,
) -> dict:
    found = _registry_assets(directory, recursive, class_filter)
    truncated = len(found) > _SEARCH_SCAN_CAP
    found = found[:_SEARCH_SCAN_CAP]
    total = len(found)
    if offset > 0:
        found = found[offset:]
    if limit is not None and limit >= 0:
        found = found[:limit]
    if fields:
        serialized = [
            _serialize_from_data(ad, _asset_path_from_data(ad), fields) for ad in found
        ]
    else:
        serialized = [_asset_path_from_data(ad) for ad in found]
    return {
        "assets": serialized,
        "count": len(found),
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
    """One AssetRegistry ARFilter query, then filter names in Python.

    Never list_assets + per-path find_asset_data (that was a 20k-hit freeze path).
    """
    found = _registry_assets(directory, recursive, class_name)
    needle = (search or "").strip().lower()
    want = (offset + limit) if (limit is not None and limit >= 0) else _SEARCH_RESULT_CAP
    results = []
    scanned = 0
    truncated = False
    for ad in found:
        if scanned >= _SEARCH_SCAN_CAP:
            truncated = True
            break
        scanned += 1
        path_str = _asset_path_from_data(ad)
        if needle:
            asset_name = str(getattr(ad, "asset_name", "") or "")
            if needle not in path_str.lower() and needle not in asset_name.lower():
                continue
        if fields:
            results.append(_serialize_from_data(ad, path_str, fields))
        else:
            results.append(serialize(ad))
        if len(results) >= want:
            truncated = truncated or scanned < len(found)
            break
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
