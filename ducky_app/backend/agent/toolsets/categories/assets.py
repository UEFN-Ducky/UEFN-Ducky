"""Content browser assets and domain editor tools."""

from __future__ import annotations

CORE_TOOLS = frozenset()

EXTENDED_TOOLS = frozenset(
    {
        "list_assets",
        "search_assets",
        "get_asset_info",
        "get_selected_assets",
        "does_asset_exist",
        "save_asset",
        "duplicate_asset",
        "rename_asset",
        "delete_asset",
        "open_asset_in_uefn",
        "validate_uefn_asset",
        "uefn_editor_python_hints",
    }
)

TOOLS = EXTENDED_TOOLS

PLAN_TOOLS = frozenset(
    {
        "list_assets",
        "search_assets",
        "get_asset_info",
        "get_selected_assets",
        "does_asset_exist",
        "uefn_editor_python_hints",
    }
)
