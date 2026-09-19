"""Content Browser / asset registry tools."""

from __future__ import annotations

from typing import Optional

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool

DEFAULT_QUERY_LIMIT = 200
SEARCH_DEFAULT_LIMIT = 50


def _fortnite_ui_gallery_hint(directory: str) -> Optional[str]:
    """Content Drawer 'Fortnite' is not an Asset Registry mount."""
    d = (directory or "").rstrip("/").lower()
    if d == "/fortnite" or d.startswith("/fortnite/"):
        return (
            'Content Drawer "Fortnite → Props/Prefabs/Devices" is a UI gallery, not '
            'directory="/Fortnite" (empty). Search under /Game/Creative — e.g. '
            "/Game/Creative/BuildingActors/Walls|Floors|Props, /Game/Creative/Sets/<Theme>, "
            '/Game/Creative/Devices. Load skill_read_subskill("leveldesign", "content_catalog").'
        )
    return None


@plugin_mcp_tool("uefn")
def list_assets(
    directory: str = "/Game/",
    recursive: bool = True,
    class_filter: str = "",
    offset: int = 0,
    limit: Optional[int] = DEFAULT_QUERY_LIMIT,
    fields: Optional[list[str]] = None,
    pretty: bool = False,
) -> str:
    """List assets in a directory (paginated; default limit 200).

    Fortnite Creative props/devices: use ``/Game/Creative/...`` — not ``/Fortnite``.
    """
    hint = _fortnite_ui_gallery_hint(directory)
    if hint:
        return tool_json(
            {
                "error": "invalid_directory",
                "directory": directory,
                "hint": hint,
                "assets": [],
                "count": 0,
            },
            pretty=pretty,
        )
    params: dict = {
        "directory": directory,
        "recursive": recursive,
        "class_filter": class_filter,
        "offset": offset,
    }
    if limit is not None:
        params["limit"] = limit
    if fields:
        params["fields"] = fields
    result = send_command("list_assets", params)
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def get_asset_info(asset_path: str, pretty: bool = False) -> str:
    """Get detailed info about an asset."""
    result = send_command("get_asset_info", {"asset_path": asset_path})
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def get_selected_assets(pretty: bool = False) -> str:
    """Get assets selected in the Content Browser."""
    result = send_command("get_selected_assets")
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def rename_asset(old_path: str, new_path: str, pretty: bool = False) -> str:
    """Rename or move an asset."""
    result = send_command("rename_asset", {"old_path": old_path, "new_path": new_path})
    return tool_json(result, pretty=pretty)


def _panel_project_root() -> str:
    try:
        from frontend.settings import PanelSettings

        return (PanelSettings.load().uefn_project_root or "").strip()
    except Exception:
        return ""


def _lore_after_gone(asset_path: str) -> dict:
    root = _panel_project_root()
    if not root:
        return {"skipped": "no_project"}
    from frontend.ui_web.verse_editor.urc.lore_host import after_asset_gone, has_lore

    if not has_lore(root):
        return {"skipped": "no_lore"}
    return after_asset_gone(root, asset_path)


@plugin_mcp_tool("uefn")
def delete_asset(asset_path: str, pretty: bool = False) -> str:
    """Delete one unreferenced project asset. Call only when the user asked to delete it.

    Fix / reimport / relink otherwise. Refuses Engine / Fortnite / catalog
    paths and anything still referenced (including mesh/skel/mat siblings).
    Already-gone still drops a Lore pending add so CheckIn cannot rehash a missing file.
    Whole character folder: ``delete_project_folder``.
    """
    result = send_command("delete_asset", {"asset_path": asset_path})
    if result.get("ok"):
        result = {**result, "lore": _lore_after_gone(asset_path)}
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def delete_project_folder(directory: str, pretty: bool = False) -> str:
    """Delete one project folder the user asked to remove (character/unit).

    Lore-drops pending adds under that tree first, then removes the folder
    when nothing outside it still references it. ``delete_directory`` stays refused.
    """
    root = _panel_project_root()
    from frontend.ui_web.verse_editor.urc.lore_host import (
        content_rel_from_asset_path,
        drop_missing_orphans,
        drop_pending_adds,
        has_lore,
    )

    rel = content_rel_from_asset_path(directory)
    folder = rel[: -len(".uasset")] if rel.lower().endswith(".uasset") else rel
    if root and has_lore(root):
        drop_pending_adds(root, prefixes=[folder])
    result = send_command("delete_project_folder", {"directory": directory})
    if result.get("ok") and root and has_lore(root):
        result = {**result, "lore": drop_missing_orphans(root, prefixes=[folder])}
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def duplicate_asset(source_path: str, dest_path: str, pretty: bool = False) -> str:
    """Duplicate an asset."""
    result = send_command("duplicate_asset", {"source_path": source_path, "dest_path": dest_path})
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def does_asset_exist(asset_path: str, pretty: bool = False) -> str:
    """Check if an asset exists."""
    result = send_command("does_asset_exist", {"asset_path": asset_path})
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def save_asset(asset_path: str, pretty: bool = False) -> str:
    """Save a modified asset."""
    result = send_command("save_asset", {"asset_path": asset_path})
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def open_asset_in_uefn(asset_path: str, open_editor: bool = True, pretty: bool = False) -> str:
    """Reveal an asset in the Content Browser and optionally open its editor."""
    result = send_command(
        "open_asset_in_uefn",
        {"asset_path": asset_path, "open_editor": open_editor},
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def search_assets(
    search: str = "",
    class_name: str = "",
    directory: str = "/Game/",
    recursive: bool = True,
    offset: int = 0,
    limit: Optional[int] = SEARCH_DEFAULT_LIMIT,
    fields: Optional[list[str]] = None,
    pretty: bool = False,
) -> str:
    """Search assets by name/path substring (search=) or exact class (class_name=).

    Use ``search=`` (not query/name_filter). Fortnite Creative props/devices/kits:
    ``directory="/Game/Creative"`` (or BuildingActors / Sets / Devices) — never
    ``/Fortnite``. Small ``limit`` (default 50). Full map:
    ``skill_read_subskill("leveldesign", "content_catalog")``.
    """
    hint = _fortnite_ui_gallery_hint(directory)
    if hint:
        return tool_json(
            {
                "error": "invalid_directory",
                "directory": directory,
                "hint": hint,
                "assets": [],
                "count": 0,
            },
            pretty=pretty,
        )
    params: dict = {
        "search": search,
        "class_name": class_name,
        "directory": directory,
        "recursive": recursive,
        "offset": offset,
    }
    if limit is not None:
        params["limit"] = limit
    if fields:
        params["fields"] = fields
    result = send_command("search_assets", params)
    return tool_json(result, pretty=pretty)
