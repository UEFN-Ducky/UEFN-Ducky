"""Focused Verse device tools — one job each, any Verse class."""

from __future__ import annotations

from typing import Any

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


def _annotate_stale_listener(result: dict) -> dict:
    """Detect pre-refactor listener still running inside UEFN."""
    if not isinstance(result, dict):
        return result
    if result.get("verse_source_mode") is not None:
        return result
    if result.get("STOP") and result.get("wiring", {}).get("status") == "no_verse_source":
        result = dict(result)
        result["diagnostics"] = {
            "stale_listener": True,
            "message": (
                "UEFN is running an outdated MCP listener (missing verse_source_mode / script-hash fallback). "
                "Verse is likely compiled — the tool code is stale. "
                "Fix: keep UEFN-Ducky.exe running, call reload_listener; if that fails, fully restart UEFN."
            ),
        }
    return result


@plugin_mcp_tool("verse")
def inspect_verse_device(actor_path: str, pretty: bool = False) -> str:
    """READ one Verse device — only the device you are about to write.

    Do NOT loop this over the level (each call hits the UEFN game thread and
    freezes the editor). Census: ``find_devices`` (returns ``kind`` + ``script_class``).
    Field names: ``workspace_read_file`` of that class's ``.verse``.

    Returns every @editable field, wiring.tool, verse_type, array_length, STOP flag.
    Pass the Outliner **label** exactly as returned by find_devices — not the long UAID path.
    If STOP is true, check diagnostics.stale_listener — may need reload_listener (not always compile).
    """
    result = send_command(
        "get_verse_editables",
        {"actor_path": actor_path, "include_wiring_hints": False},
    )
    result = _annotate_stale_listener(result)
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("verse")
def resize_verse_array(
    actor_path: str,
    array_field: str,
    count: int,
    pretty: bool = False,
) -> str:
    """Set row count on any []struct @editable array (empty new rows).

    Call inspect_verse_device first — use exact array_field name (e.g. from editables keys).
    Then patch_verse_array_entry per row for scalars/icons.
    """
    result = send_command(
        "resize_verse_array_field",
        {
            "actor_path": actor_path,
            "array_field": array_field,
            "count": int(count),
        },
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("verse")
def patch_verse_array_entry(
    actor_path: str,
    array_field: str,
    index: int,
    properties: dict[str, Any],
    pretty: bool = False,
) -> str:
    """Set subfields on one row of a []struct array on any Verse device.

    Call inspect_verse_device + workspace_read_file(verse_source) for subfield names.

    properties examples:
    - `{"CurrencyName": "Gold", "DisplayOrder": 0}`
    - `{"CurrencyIcon": {"texture_path": "T_GoldIcon"}}`
    """
    result = send_command(
        "patch_verse_array_entry",
        {
            "actor_path": actor_path,
            "array_field": array_field,
            "index": int(index),
            "properties": properties,
        },
    )
    return tool_json(result, pretty=pretty)
